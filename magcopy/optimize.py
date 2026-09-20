"""Turning a recording into the best GIF that still fits under a size limit.

The search
  Size is roughly pixels x frames x quality, so the optimiser measures rather than guesses:

    1. extract frames at the current (scale, fps) and binary-search gifski's quality for the
       largest file that still fits the budget;
    2. if the best quality that fits is below the floor - the point where a GIF starts to look
       chewed - step down the ladder, using the measurement to skip rungs that cannot fit;
    3. finish with a lossless `gifsicle -O3`, which is free.

  Screen recordings degrade differently from video: text stops being readable long before motion
  stops reading as motion, so the ladder spends frame rate before it spends pixels. That is the
  opposite of the right call for camera footage, and it is deliberate.

  Every prediction is made against a size measured at the same reference quality, because a
  rung's size at q42 says nothing useful about the next rung's size at q85.

Frame rate
  A GIF stores each frame's delay in hundredths of a second, so only rates of 100/n play back at
  true speed - 30 fps becomes 3 cs, which runs 11% fast. Every rung of the ladder is both an exact
  integer decimation of the recording and an exact centisecond delay.

Finding the starting rung cheaply
  Probing the top rung of a long recording is the single most expensive thing here: extracting
  and encoding 500 frames to learn "much too big" costs minutes. So a scout encodes a contiguous
  few seconds from the middle instead and extrapolates by duration. Contiguous matters - sampling
  every Nth frame would break the frame-to-frame similarity that GIF compression lives on, and
  over-estimate the size badly.

Fitting is not optional
  The last rung does not "accept whatever it weighs": it keeps shrinking until the file is under
  the limit, because a GIF that Discord refuses is not a result.
"""
import os
import re
import shutil
import struct
import subprocess
import tempfile

from .binaries import TOOLS, run, CREATE_NO_WINDOW
from .settings import log_exc

QUALITY_FLOOR = 58          # below this, gifski output starts to look visibly chewed
QUALITY_MIN = 30
QUALITY_CEIL = 98
QUALITY_START = 85          # the reference quality every prediction is anchored to
MIN_FPS = 8.0
MIN_SCALE = 0.40
MAX_RUNGS = 3               # distinct (scale, fps) pairs to try; each needs a frame extraction
SCOUT_SECONDS = 4.0         # length of the sample used to estimate the full encode
SCOUT_MIN_SPAN = 7.0        # below this the real thing is cheap enough to just encode
HOPELESS = 2.6              # a probe this far over budget cannot be rescued by quality alone
MAX_FRAMES = 2400           # gifski takes every frame as an argument; Windows caps a command
                            # line at ~32k characters, and 2400 names is about 21k of that


class Cancelled(Exception):
    pass


class Result:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return "<Result %s %dx%d @%.4gfps q%s %.2fMB %s>" % (
            os.path.basename(getattr(self, "path", "?")), self.width, self.height,
            self.fps, self.quality, self.bytes / 1e6, "fits" if self.fits else "OVER")


# ----------------------------------------------------------------------------- reading a gif back
def gif_info(path):
    """Canvas size, stored frame count, delays and loop flag, by walking the block structure.

    Scanning for the 21 F9 04 byte pattern instead of parsing is tempting and wrong: that
    sequence occurs by chance inside LZW image data, which invents frames and absurd delays.
    This walks header -> colour table -> blocks properly, so the numbers are the real ones.

    gifski drops duplicate frames and extends the previous delay instead, so a file usually
    holds fewer frames than were extracted - especially for screen recordings, where most of
    the picture is still.
    """
    try:
        d = open(path, "rb").read()
    except Exception:
        return {}
    if len(d) < 13 or not d.startswith((b"GIF87a", b"GIF89a")):
        return {}
    width, height = struct.unpack("<HH", d[6:10])
    flags = d[10]
    i = 13
    if flags & 0x80:                                     # global colour table
        i += 3 * (2 ** ((flags & 0x07) + 1))

    def skip_sub_blocks(j):
        while j < len(d):
            n = d[j]
            j += 1
            if n == 0:
                break
            j += n
        return j

    delays, frames, loops, pending = {}, 0, False, 0
    while i < len(d):
        b = d[i]
        if b == 0x3B:                                    # trailer
            break
        if b == 0x21:                                    # extension
            if i + 1 >= len(d):
                break
            label = d[i + 1]
            j = i + 2
            if label == 0xF9 and j < len(d) and d[j] == 4:
                pending = struct.unpack("<H", d[j + 2:j + 4])[0]
            elif label == 0xFF and j < len(d):           # application extension
                size = d[j]
                if d[j + 1:j + 1 + size].startswith(b"NETSCAPE2.0"):
                    loops = True
            i = skip_sub_blocks(j)        # GCE and app extensions are both sub-block chains
            continue
        if b == 0x2C:                                    # image descriptor
            if i + 10 > len(d):
                break
            lflags = d[i + 9]
            j = i + 10
            if lflags & 0x80:                            # local colour table
                j += 3 * (2 ** ((lflags & 0x07) + 1))
            j += 1                                       # LZW minimum code size
            j = skip_sub_blocks(j)
            frames += 1
            delays[pending] = delays.get(pending, 0) + 1
            pending = 0
            i = j
            continue
        i += 1                                           # unknown byte: resync conservatively
    total_cs = sum(k * v for k, v in delays.items())
    return dict(width=width, height=height, frames=frames, delays=delays,
                duration=total_cs / 100.0, loops=loops, bytes=len(d))


def probe(path):
    """(width, height, duration_seconds, fps) read out of ffmpeg's own banner."""
    wd = ht = 0
    dur = fps = 0.0
    try:
        r = run([TOOLS.ffmpeg, "-hide_banner", "-i", path], timeout=60)
        text = (r.stderr or b"").decode("utf-8", "replace")
        m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d\.?\d*)", text)
        if m:
            dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        m = re.search(r"Video:.*?,\s*(\d+)x(\d+)", text)
        if m:
            wd, ht = int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
        if m:
            fps = float(m.group(1))
    except Exception:
        log_exc("probe")
    return wd, ht, dur, fps


CLEAN_RATES = [50.0, 25.0, 20.0, 100 / 6.0, 12.5, 10.0]      # 2, 4, 5, 6, 8 and 10 cs


def fps_ladder(source_fps):
    """Output rates worth trying, best first.

    Two things matter. A rate must divide 100 exactly, or the stored delay is rounded and the
    GIF plays at the wrong speed - 30 fps becomes 3 cs and runs 11% fast. And a rate that is an
    exact division of the recording drops whole frames evenly, with no judder.

    Exact divisions of the source come first because they satisfy both. The standard clean rates
    below the source rate follow, because resampling 25 -> 20 with a slight stutter still beats
    falling all the way to the next exact division.
    """
    exact, seen = [], set()
    for k in range(1, 13):
        f = round(source_fps / k, 4)
        if f < MIN_FPS:
            break
        if abs(100.0 / f - round(100.0 / f)) < 1e-6 and f not in seen:
            seen.add(f)
            exact.append(f)
    extra = [round(f, 4) for f in CLEAN_RATES
             if MIN_FPS <= f <= source_fps + 1e-6 and round(f, 4) not in seen]
    out = sorted(set(exact) | set(extra), reverse=True)
    return out or [round(source_fps, 4)]


SCALES = [1.0, 0.85, 0.72, 0.6, 0.5, MIN_SCALE]


def _even(n):
    return max(2, int(round(n)) // 2 * 2)


class GifOptimizer:
    def __init__(self, master, out_path, start=0.0, end=None, speed=1.0,
                 size_limit_bytes=10_000_000, headroom=0.985, max_width=None,
                 on_progress=None, should_cancel=None, workdir=None):
        self.master = master
        self.out_path = out_path
        self.start = max(0.0, float(start))
        self.end = end
        self.speed = max(0.1, min(5.0, float(speed)))
        self.limit = int(size_limit_bytes)
        self.budget = int(size_limit_bytes * headroom)
        self.max_width = max_width
        self.on_progress = on_progress or (lambda *a, **k: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.workdir = workdir or tempfile.mkdtemp(prefix="magcopy-gif-")
        self._own_workdir = workdir is None
        self.attempts = []
        self._frames = {}

    # ---- plumbing
    def cleanup(self):
        if self._own_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)

    def _say(self, text, frac=None):
        try:
            self.on_progress(text, frac)
        except Exception:
            pass

    def _check(self):
        if self.should_cancel():
            raise Cancelled()

    def _extract(self, width, fps):
        """PNG frames at one (width, fps). Cached: the quality search reuses them."""
        key = (width, round(fps, 4))
        if key in self._frames:
            return self._frames[key]
        self._check()
        d = os.path.join(self.workdir, "f%d_%s" % (width, str(key[1]).replace(".", "")))
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            vf = []
            if self.speed != 1.0:
                vf.append("setpts=PTS/%.6f" % self.speed)
            vf.append("fps=%.6f" % fps)
            vf.append("scale=%d:-2:flags=lanczos" % width)
            args = [TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
            if self.start:
                args += ["-ss", "%.3f" % self.start]
            if self.end is not None:
                args += ["-to", "%.3f" % self.end]
            args += ["-i", self.master, "-vf", ",".join(vf), "-fps_mode", "cfr",
                     os.path.join(d, "f%05d.png")]
            r = run(args, timeout=900)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:300])
        files = sorted(f for f in os.listdir(d) if f.endswith(".png"))
        if not files:
            raise RuntimeError("no frames were extracted")
        self._frames[key] = (d, files)
        return d, files

    def _encode(self, width, fps, quality, out):
        """gifski over the cached frames, then a free lossless gifsicle pass.

        `-W` is mandatory: without it gifski silently caps output at roughly 800x600, which
        halves the resolution of anything larger without saying so.
        """
        self._check()
        frame_dir, files = self._extract(width, fps)
        args = [TOOLS.gifski, "-q", "-Q", str(int(quality)), "-r", "%.6f" % fps,
                "-W", str(int(width)), "--no-sort", "-o", os.path.abspath(out)] + files
        if sum(len(a) + 1 for a in args) > 30000:
            raise RuntimeError("too many frames for one command line (%d)" % len(files))
        kw = dict(creationflags=CREATE_NO_WINDOW) if os.name == "nt" else {}
        r = subprocess.run(args, cwd=frame_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=1800, **kw)
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:300] or "gifski failed")
        if TOOLS.gifsicle:
            tmp = out + ".opt"
            o = run([TOOLS.gifsicle, "-O3", "--no-warnings", out, "-o", tmp], timeout=900)
            if o.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                if os.path.getsize(tmp) < os.path.getsize(out):
                    os.replace(tmp, out)
                else:
                    os.remove(tmp)
        size = os.path.getsize(out)
        self.attempts.append((width, fps, quality, size))
        self._say("%d px · %.4g fps · q%d → %.2f MB" % (width, fps, quality, size / 1e6))
        return size

    # ---- cheap estimate
    def _scout(self, width, fps, span):
        """Encode a few seconds from the middle and extrapolate. Returns predicted full bytes."""
        if span < SCOUT_MIN_SPAN:
            return None
        mid = self.start + (span * self.speed) / 2.0
        half = (SCOUT_SECONDS * self.speed) / 2.0
        a = max(self.start, mid - half)
        b = min(self.end if self.end is not None else a + 2 * half, mid + half)
        if b - a < 1.0:
            return None
        sub = GifOptimizer(self.master, os.path.join(self.workdir, "scout.gif"),
                           start=a, end=b, speed=self.speed,
                           size_limit_bytes=self.limit, headroom=1.0,
                           should_cancel=self.should_cancel,
                           workdir=os.path.join(self.workdir, "scoutwork"))
        os.makedirs(sub.workdir, exist_ok=True)
        try:
            self._say("sizing up the recording…")
            size = sub._encode(width, fps, QUALITY_START, os.path.join(sub.workdir, "s.gif"))
        except Cancelled:
            raise
        except Exception:
            log_exc("scout")
            return None
        finally:
            self.attempts.extend(sub.attempts)
        sampled = (b - a) / self.speed
        return size * (span / max(0.1, sampled))

    # ---- one rung
    def _rung(self, width, fps, keep):
        """Best quality that fits at this (width, fps).

        Returns (quality, size) on success, or (None, reference_size) where reference_size is
        always measured at QUALITY_START so the caller can predict the next rung from it.
        """
        scratch = os.path.join(self.workdir, "try.gif")
        ref = self._encode(width, fps, QUALITY_START, scratch)
        if ref <= self.budget:
            shutil.copyfile(scratch, keep)
            best_q, best_size = QUALITY_START, ref
            lo, hi = QUALITY_START, QUALITY_CEIL
            for _ in range(2):                                  # spend the headroom upward
                if hi - lo <= 2:
                    break
                mid = (lo + hi) // 2
                size = self._encode(width, fps, mid, scratch)
                if size <= self.budget:
                    best_q, best_size, lo = mid, size, mid
                    shutil.copyfile(scratch, keep)
                else:
                    hi = mid
            return best_q, best_size
        if ref > self.budget * HOPELESS:
            return None, ref                                    # no quality setting rescues this
        lo, hi = QUALITY_MIN, QUALITY_START
        best_q = best_size = None
        for _ in range(4):
            if hi - lo <= 2:
                break
            mid = (lo + hi) // 2
            size = self._encode(width, fps, mid, scratch)
            if size <= self.budget:
                best_q, best_size, lo = mid, size, mid
                shutil.copyfile(scratch, keep)
            else:
                hi = mid
        return (best_q, best_size) if best_q is not None else (None, ref)

    # ---- the walk
    def run(self):
        wd0, ht0, dur, src_fps = probe(self.master)
        if not wd0 or not ht0:
            raise RuntimeError("could not read the recording")
        src_fps = src_fps or 25.0
        end = self.end if self.end is not None else dur
        span = max(0.05, (end - self.start) / self.speed)
        target_w = min(wd0, int(self.max_width)) if self.max_width else wd0

        ladder_f = fps_ladder(src_fps)
        rungs = []
        for s in SCALES:                                        # frame rate before pixels
            wpx = _even(target_w * s)
            if wpx < 60:
                continue
            for f in ladder_f:
                if f * span > MAX_FRAMES:                       # see MAX_FRAMES
                    continue
                if (wpx, f) not in [(r[0], r[1]) for r in rungs]:
                    rungs.append((wpx, f, s))
        if not rungs:                                           # a very long clip: take the slowest rate
            f = min(ladder_f)
            rungs = [(_even(target_w), f, 1.0)]

        keep = os.path.join(self.workdir, "best.gif")
        best = None
        ref = None                                              # (width, fps, size at QUALITY_START)
        tried = 0

        top_w, top_f = rungs[0][0], rungs[0][1]
        predicted = self._scout(top_w, top_f, span)
        if predicted:
            self._say("estimated %.1f MB at full size" % (predicted / 1e6))
            ref = (top_w, top_f, predicted)
        for wpx, f, s in rungs:
            if tried >= MAX_RUNGS:
                break
            if ref:
                rw, rf, rsize = ref
                pred = rsize * (wpx / rw) ** 2 * (f / rf)
                if pred > self.budget * 1.35:                   # out of reach; skip the extraction
                    continue
            tried += 1
            self._say("trying %d px at %.4g fps…" % (wpx, f))
            try:
                q, size = self._rung(wpx, f, keep)
            except Cancelled:
                raise
            except Exception:
                log_exc("gif rung %dx%.4g" % (wpx, f))
                continue
            if q is not None:
                best = dict(width=wpx, fps=f, quality=q, bytes=size, scale=s)
                if q >= QUALITY_FLOOR:
                    break                                       # good enough; stop spending quality
            else:
                ref = (wpx, f, size)

        if not best:
            best = self._force_fit(rungs, keep, ladder_f, target_w)

        os.makedirs(os.path.dirname(os.path.abspath(self.out_path)) or ".", exist_ok=True)
        shutil.copyfile(keep, self.out_path)
        info = gif_info(self.out_path)
        size = os.path.getsize(self.out_path)
        return Result(path=self.out_path, bytes=size,
                      width=info.get("width", best["width"]),
                      height=info.get("height", _even(best["width"] * ht0 / wd0)),
                      fps=best["fps"], quality=best["quality"],
                      frames=info.get("frames", 0), stored_duration=info.get("duration", span),
                      duration=span, attempts=len(self.attempts), fits=size <= self.limit,
                      source=(wd0, ht0, src_fps), scale=best["scale"], info=info)

    def _force_fit(self, rungs, keep, ladder_f, target_w):
        """Nothing on the ladder fit. Shrink past the floor until it does - a file over the
        limit is a failure, a small file is merely a compromise."""
        wpx, f, s = rungs[-1]
        scratch = os.path.join(self.workdir, "try.gif")
        for _ in range(7):
            self._check()
            self._say("shrinking to fit: %d px at %.4g fps…" % (wpx, f))
            try:
                size = self._encode(wpx, f, QUALITY_MIN, scratch)
            except Cancelled:
                raise
            except Exception:
                log_exc("force fit")
                size = None
            if size is not None:
                shutil.copyfile(scratch, keep)
                if size <= self.budget:
                    return dict(width=wpx, fps=f, quality=QUALITY_MIN, bytes=size, scale=s)
                over = size / self.budget
            else:
                over = 2.0
            lower_f = [x for x in ladder_f if x < f]
            if over > 1.6 and lower_f:                           # a big miss: drop frame rate
                f = lower_f[0]
            else:
                wpx = _even(wpx / max(1.15, over ** 0.5))         # otherwise take it out of pixels
                if wpx < 60:
                    break
        return dict(width=wpx, fps=f, quality=QUALITY_MIN,
                    bytes=os.path.getsize(keep) if os.path.exists(keep) else 0, scale=s)


def optimize(master, out_path, **kw):
    opt = GifOptimizer(master, out_path, **kw)
    try:
        return opt.run()
    finally:
        opt.cleanup()
