"""The GIF editor: watch the recording back, trim it, set speed, then save.

Preview frames are extracted once into a scratch folder as small JPEGs and paged in on demand.
Scrubbing a video file frame-by-frame with a decoder is far too slow to feel like scrubbing;
a folder of small stills is instant, and the trim itself is still expressed in seconds against
the full-quality master, so nothing about the preview limits the output.

The filmstrip behind the timeline is built from about two dozen of those frames. It costs one
image at open time and makes finding the moment you wanted a glance rather than a hunt.

Preview extraction and encoding both run on worker threads and report back through the app's
event queue, never by touching Tk directly - see the note in app.py.

Playback runs at the recorded frame rate and is anchored to the wall clock. Stepping a fixed
number of milliseconds per tick makes the preview drift slower than real time - every late tick
is time the clip never gets back - so a 50 fps recording looked like half that. Decoding a preview
frame costs about 6 ms even at 1280 px, so 50 fps has plenty of headroom; what it needed was
honest timing.
"""
import os
import shutil
import time
import tempfile
import threading
import tkinter as tk

from PIL import Image, ImageTk

from . import win
from .binaries import TOOLS, run
from .optimize import GifOptimizer, Cancelled, fps_ladder, gif_info, probe
from .settings import log_exc, save_dir, capture_path
from .theme import (Button, DotToggle, Hairline, Label, Panel, Pills, ProgressLine,
                    TextLink, IconButton, round_rect)

PREVIEW_MIN_W = 360
PREVIEW_FPS_CAP = 50.0          # the preview plays at the recorded rate, not half of it
STRIP_THUMBS = 24
SCREEN_FRACTION = 0.80          # how much of the monitor the editor window may occupy


def fit_preview(src_w, src_h, avail_w, avail_h):
    """Largest preview of the source's shape that fits in the space left for it.

    Never larger than the recording itself: blowing a 400 px capture up to fill a monitor only
    makes it soft, and the editor is where the result is judged.
    """
    if src_w <= 0 or src_h <= 0:
        return PREVIEW_MIN_W, int(PREVIEW_MIN_W * 9 / 16)
    wd = max(PREVIEW_MIN_W, min(avail_w, src_w))
    ht = wd * src_h / src_w
    if ht > avail_h:                                 # too tall for the space: fit to height
        ht = max(120, avail_h)
        wd = ht * src_w / src_h
    wd, ht = int(round(wd)), int(round(ht))
    return max(2, wd - wd % 2), max(2, ht - ht % 2)


class Timeline(tk.Canvas):
    """Filmstrip track with in/out handles and a playhead. Values are seconds."""

    HANDLE = 9

    def __init__(self, parent, fonts, duration, on_change, on_seek, scale=1.0, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(66 * scale), **kw)
        self.fonts, self.duration, self.s = fonts, max(0.01, duration), scale
        self.on_change, self.on_seek = on_change, on_seek
        self.c = None
        self.start, self.end, self.play = 0.0, self.duration, 0.0
        self.strip = None                 # PhotoImage, set by set_strip
        self._drag = None
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: self._release())
        self.bind("<Motion>", self._hover)
        self.configure(cursor="hand2")

    def set_strip(self, photo):
        self.strip = photo
        self.draw()

    def set_play(self, t):
        self.play = max(self.start, min(self.end, t))
        self.draw()

    def set_range(self, start, end):
        self.start, self.end = start, end
        self.draw()

    # -- geometry
    def _track(self):
        wd = max(self.winfo_width(), 10)
        pad = int(12 * self.s)
        return pad, wd - pad

    def _x(self, t):
        a, b = self._track()
        return a + (b - a) * (t / self.duration)

    def _t(self, x):
        a, b = self._track()
        return max(0.0, min(self.duration, (x - a) / max(1, b - a) * self.duration))

    def _press(self, e):
        xs, xe = self._x(self.start), self._x(self.end)
        grab = self.HANDLE * self.s * 1.6
        if abs(e.x - xs) <= grab:
            self._drag = "start"
        elif abs(e.x - xe) <= grab:
            self._drag = "end"
        else:
            self._drag = "play"
            self.on_seek(self._t(e.x))
        self._motion(e)

    def _motion(self, e):
        if not self._drag:
            return
        t = self._t(e.x)
        if self._drag == "start":
            self.start = min(t, self.end - 0.05)
            self.play = max(self.play, self.start)
            self.on_change(self.start, self.end)
        elif self._drag == "end":
            self.end = max(t, self.start + 0.05)
            self.play = min(self.play, self.end)
            self.on_change(self.start, self.end)
        else:
            self.on_seek(max(self.start, min(self.end, t)))
        self.draw()

    def _release(self):
        self._drag = None

    def _hover(self, e):
        xs, xe = self._x(self.start), self._x(self.end)
        grab = self.HANDLE * self.s * 1.6
        near = abs(e.x - xs) <= grab or abs(e.x - xe) <= grab
        self.configure(cursor="sb_h_double_arrow" if near else "hand2")

    # -- painting
    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        wd, ht = max(self.winfo_width(), 10), self.winfo_height()
        a, b = self._track()
        top, bot = int(8 * self.s), ht - int(20 * self.s)
        if self.strip is not None:
            self.create_image(a, top, image=self.strip, anchor="nw")
        else:
            self.create_rectangle(a, top, b, bot, fill=c["bg2"], outline="")
        xs, xe = self._x(self.start), self._x(self.end)
        # dim what will be cut
        for x1, x2 in ((a, xs), (xe, b)):
            if x2 > x1:
                self.create_rectangle(x1, top, x2, bot, fill=c["bg"], outline="", stipple="gray50")
        self.create_rectangle(xs, top, xe, bot, outline=c["ink"], width=1)
        for x in (xs, xe):
            self.create_rectangle(x - 2 * self.s, top, x + 2 * self.s, bot,
                                  fill=c["ink"], outline=c["ink"])
            self.create_oval(x - self.HANDLE * self.s / 2, (top + bot) / 2 - self.HANDLE * self.s / 2,
                             x + self.HANDLE * self.s / 2, (top + bot) / 2 + self.HANDLE * self.s / 2,
                             fill=c["ink"], outline=c["bg"])
        px = self._x(self.play)
        # the accent, not the error colour: red is reserved for "this is recording"
        self.create_line(px, top - 3 * self.s, px, bot + 3 * self.s, fill=c["focus"], width=1)
        self.create_text(a, ht - int(8 * self.s), text="0.0s", anchor="w",
                         font=self.fonts.mono8, fill=c["mute2"])
        self.create_text(b, ht - int(8 * self.s), text="%.1fs" % self.duration, anchor="e",
                         font=self.fonts.mono8, fill=c["mute2"])
        self.create_text((xs + xe) / 2, ht - int(8 * self.s),
                         text="%.2f – %.2f s" % (self.start, self.end), anchor="c",
                         font=self.fonts.mono8, fill=c["ink2"])

    def restyle(self, c):
        self.c = c
        self.draw()


class GifEditor:
    """A modal-ish window over one recording. Calls `on_saved(result)` when a GIF is written."""

    def __init__(self, app, master_path, rect=None):
        self.app = app
        self.master = master_path
        self.rect = rect
        self.theme, self.fonts = app.theme, app.fonts
        self.s = app.scale
        self.settings = app.settings
        self.work = tempfile.mkdtemp(prefix="magcopy-edit-")
        self.frames = []                  # preview jpeg paths
        self.preview_fps = 25.0
        self.duration = 0.0
        self.src = (0, 0, 0.0, 0.0)
        self._photo = None
        self._cache = {}
        self.playing = False
        self._play_job = None
        self.speed = 1.0
        self.saving = False
        self._cancel_save = False
        self.result = None
        self.top = None
        self._done_win = None
        self.pw = PREVIEW_MIN_W           # preview pixel size, computed from the screen
        self.ph = 240
        self.crop = None                  # (x, y, w, h) in SOURCE pixels, or None for the lot
        self.crop_locked = False          # saved: handles hidden, crop still applied
        self._crop_drag = None            # (kind, anchor, grab-point) while dragging

    # ---- lifecycle
    def open(self):
        wd, ht, dur, fps = probe(self.master)
        self.src = (wd, ht, dur, fps)
        self.duration = dur
        if not wd or dur <= 0:
            self.app.toast("that recording could not be read", error=True)
            self.cleanup()
            return
        self._build()      # sizes the preview itself, by measuring the chrome while hidden
        threading.Thread(target=self._prepare, name="magcopy-preview", daemon=True).start()

    def cleanup(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def close(self):
        self._dismiss_done()
        self.playing = False
        if self._play_job:
            try:
                self.top.after_cancel(self._play_job)
            except Exception:
                pass
        self._cancel_save = True
        try:
            if self.top:
                self.top.destroy()
        except Exception:
            pass
        self.cleanup()
        self.app.editor_closed(self)

    # ---- preview extraction
    def _prepare(self):
        try:
            wd, ht, dur, fps = self.src
            self.preview_fps = min(PREVIEW_FPS_CAP, fps or 25.0)
            d = os.path.join(self.work, "prev")
            os.makedirs(d, exist_ok=True)
            r = run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", self.master,
                     "-vf", "fps=%.6f,scale=%d:-2:flags=bilinear" % (self.preview_fps, self.pw),
                     "-q:v", "4", os.path.join(d, "p%05d.jpg")], timeout=600)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:200])
            self.frames = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jpg"))
            self.app.post(self._ready)
        except Exception:
            log_exc("preview extract")
            self.app.post(lambda: self.app.toast("could not build the preview", error=True))

    def _build_strip(self, track_w, track_h):
        """Fill the track with frames at their own aspect ratio.

        Squeezing a fixed number of thumbnails into the track distorts them into stripes; the
        count has to come from the track width instead, so each thumbnail keeps its shape and
        the strip actually reads as the recording.
        """
        if not self.frames or track_w < 20 or track_h < 8:
            return None
        try:
            src_w, src_h = self.src[0], self.src[1]
            tw = max(8, int(round(track_h * src_w / max(1, src_h))))
            n = max(1, min(STRIP_THUMBS, int(round(track_w / tw))))
            tw = int(track_w / n) + 1                      # share out any rounding slack
            idx = [int(i * (len(self.frames) - 1) / max(1, n - 1)) for i in range(n)]
            strip = Image.new("RGB", (track_w, track_h))
            for k, i in enumerate(idx):
                im = Image.open(self.frames[i]).convert("RGB")
                # cover the cell, then centre-crop, so nothing is stretched
                sc = max(tw / im.width, track_h / im.height)
                im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.BILINEAR)
                left = max(0, (im.width - tw) // 2)
                top = max(0, (im.height - track_h) // 2)
                strip.paste(im.crop((left, top, left + tw, top + track_h)), (k * tw, 0))
            return strip
        except Exception:
            log_exc("filmstrip")
            return None

    def _ready(self):
        if not self.frames:
            return
        self.timeline.duration = self.duration
        self.timeline.set_range(0.0, self.duration)
        self._fit_strip()
        self.prep.set(None)
        self.prep.pack_forget()
        self.show_frame(0.0)
        self._update_estimate()
        self.btn_save.set_enabled(True)
        self.status.configure(text="drag the handles to trim · space plays · ctrl+s saves")

    def _fit_strip(self):
        if not self.frames:
            return
        try:
            self.timeline.update_idletasks()
            wd = max(50, self.timeline.winfo_width() - int(24 * self.s))
            ht = max(8, self.timeline.winfo_height() - int(28 * self.s))
            if (wd, ht) == getattr(self, "_strip_size", None):
                return
            img = self._build_strip(wd, ht)
            if img is None:
                return
            self._strip_size = (wd, ht)
            self._strip_photo = ImageTk.PhotoImage(img)
            self.timeline.set_strip(self._strip_photo)
        except Exception:
            log_exc("fit strip")

    # ---- playback
    def show_frame(self, t):
        if not self.frames:
            return
        i = int(round(t * self.preview_fps))
        i = max(0, min(len(self.frames) - 1, i))
        photo = self._cache.get(i)
        if photo is None:
            try:
                photo = ImageTk.PhotoImage(Image.open(self.frames[i]))
            except Exception:
                return
            if len(self._cache) > 400:
                self._cache.clear()
            self._cache[i] = photo
        self._photo = photo
        self.canvas.delete("all")
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = photo.width(), photo.height()
        self._img_box = ((cw - iw) / 2, (ch - ih) / 2, iw, ih)      # where the picture sits
        self.canvas.create_image(cw / 2, ch / 2, image=photo, anchor="c")
        self._draw_crop()
        self.timeline.set_play(t)
        self.pos_label.configure(text="%.2fs" % t)

    # ---- crop
    HANDLE = 5                            # half-size of a grab square, in canvas pixels
    GRAB = 9                              # how close a click must be to count as grabbing an edge

    def _to_source(self, cx, cy):
        """Canvas point -> source pixel, clamped to the picture."""
        ox, oy, iw, ih = getattr(self, "_img_box", (0, 0, self.pw, self.ph))
        sw, sh = self.src[0], self.src[1]
        fx = min(max((cx - ox) / max(1, iw), 0.0), 1.0)
        fy = min(max((cy - oy) / max(1, ih), 0.0), 1.0)
        return fx * sw, fy * sh

    def _to_canvas(self, sx, sy):
        ox, oy, iw, ih = getattr(self, "_img_box", (0, 0, self.pw, self.ph))
        sw, sh = self.src[0], self.src[1]
        return ox + (sx / max(1, sw)) * iw, oy + (sy / max(1, sh)) * ih

    def _handles(self):
        """Canvas positions of the eight grab points, keyed by which edges they move."""
        if not self.crop:
            return {}
        x0, y0 = self._to_canvas(self.crop[0], self.crop[1])
        x1, y1 = self._to_canvas(self.crop[0] + self.crop[2], self.crop[1] + self.crop[3])
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my),
                "se": (x1, y1), "s": (mx, y1), "sw": (x0, y1), "w": (x0, my)}

    def _hit(self, cx, cy):
        """What is under the pointer: a handle name, "move", or None."""
        if not self.crop:
            return None
        for name, (hx, hy) in self._handles().items():
            if abs(cx - hx) <= self.GRAB and abs(cy - hy) <= self.GRAB:
                return name
        x0, y0 = self._to_canvas(self.crop[0], self.crop[1])
        x1, y1 = self._to_canvas(self.crop[0] + self.crop[2], self.crop[1] + self.crop[3])
        if x0 < cx < x1 and y0 < cy < y1:
            return "move"
        return None

    CURSORS = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
               "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
               "e": "sb_h_double_arrow", "w": "sb_h_double_arrow", "move": "fleur"}

    def _crop_hover(self, e):
        if self._crop_drag or not self.frames:
            return
        hit = None if self.crop_locked else self._hit(e.x, e.y)
        self.canvas.configure(cursor=self.CURSORS.get(hit, "crosshair"))

    def _crop_press(self, e):
        if not self.frames:
            return
        hit = None if self.crop_locked else self._hit(e.x, e.y)
        if hit:
            # adjusting: remember all four edges so dragging one never disturbs another
            self._crop_drag = (hit, tuple(self.crop), self._to_source(e.x, e.y))
        else:
            self.crop_locked = False
            self._crop_drag = ("new", self._to_source(e.x, e.y), None)
            self.crop = None
        self._draw_crop()

    def _crop_move(self, e):
        if self._crop_drag is None:
            return
        kind, anchor, grab = self._crop_drag
        sw, sh = float(self.src[0]), float(self.src[1])
        px, py = self._to_source(e.x, e.y)
        if kind == "new":
            x0, y0 = anchor
            self.crop = (min(x0, px), min(y0, py), abs(px - x0), abs(py - y0))
        elif kind == "move":
            ox, oy, ow, oh = anchor
            nx = min(max(ox + (px - grab[0]), 0.0), sw - ow)
            ny = min(max(oy + (py - grab[1]), 0.0), sh - oh)
            self.crop = (nx, ny, ow, oh)
        else:
            ox, oy, ow, oh = anchor
            left, top, right, bottom = ox, oy, ox + ow, oy + oh
            if "w" in kind:
                left = min(max(px, 0.0), right - 16)
            if "e" in kind:
                right = max(min(px, sw), left + 16)
            if "n" in kind:
                top = min(max(py, 0.0), bottom - 16)
            if "s" in kind:
                bottom = max(min(py, sh), top + 16)
            self.crop = (left, top, right - left, bottom - top)
        self._draw_crop()
        self._update_estimate()

    def _crop_release(self, e):
        kind = self._crop_drag[0] if self._crop_drag else None
        self._crop_drag = None
        if self.crop:
            x, y, wd, ht = (int(round(v)) for v in self.crop)
            wd, ht = wd - wd % 2, ht - ht % 2
            sw, sh = int(self.src[0]), int(self.src[1])
            x, y = max(0, min(x, sw - 2)), max(0, min(y, sh - 2))
            wd, ht = max(0, min(wd, sw - x)), max(0, min(ht, sh - y))
            # a tiny box from a stray click is not a crop; a deliberate resize may be small
            floor = 32 if kind == "new" else 16
            self.crop = (x, y, wd, ht) if wd >= floor and ht >= floor else None
        self._draw_crop()
        self._update_estimate()
        self._sync_crop_buttons()

    def _save_crop(self):
        """Lock the crop in: the handles come off, the crop stays applied to the save."""
        if not self.crop:
            return
        self.crop_locked = True
        self._crop_drag = None
        self.canvas.configure(cursor="crosshair")
        self._draw_crop()
        self._sync_crop_buttons()
        self.status.configure(text="crop saved - %d x %d - drag the picture to start another"
                                   % (self.crop[2], self.crop[3]))

    def _reset_crop(self):
        self.crop = None
        self.crop_locked = False
        self._crop_drag = None
        self.canvas.configure(cursor="crosshair")
        self._draw_crop()
        self._update_estimate()
        self._sync_crop_buttons()

    def _sync_crop_buttons(self):
        try:
            self.btn_crop.set_enabled(bool(self.crop) and not self.crop_locked)
            self.btn_crop.set_text("crop saved" if self.crop_locked else "save crop")
            self.link_reset_crop.set_enabled(bool(self.crop))
        except Exception:
            pass

    def _draw_crop(self):
        """Dim what will be cut, outline what will be kept, and show the grab handles."""
        self.canvas.delete("crop")
        if not self.crop:
            return
        c = self.theme.c
        ox, oy, iw, ih = getattr(self, "_img_box", (0, 0, self.pw, self.ph))
        x0, y0 = self._to_canvas(self.crop[0], self.crop[1])
        x1, y1 = self._to_canvas(self.crop[0] + self.crop[2], self.crop[1] + self.crop[3])
        for a, b, cc, d in ((ox, oy, ox + iw, y0), (ox, y1, ox + iw, oy + ih),
                            (ox, y0, x0, y1), (x1, y0, ox + iw, y1)):
            if cc > a and d > b:
                # the palette's darkening colour, not its background: over dark footage a
                # bg-coloured stipple is almost invisible, and the point is to show what goes
                self.canvas.create_rectangle(a, b, cc, d, fill=c["shade"], outline="",
                                             stipple="gray75", tags="crop")
        accent = c["mute"] if self.crop_locked else c["focus"]
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=accent, width=1, tags="crop")
        self.canvas.create_text(x0 + 6, y0 + 6, anchor="nw", tags="crop", fill=c["ink"],
                                font=self.fonts.mono8,
                                text="%d x %d" % (int(self.crop[2]), int(self.crop[3])))
        if self.crop_locked:
            return
        h = self.HANDLE
        for hx, hy in self._handles().values():
            self.canvas.create_rectangle(hx - h, hy - h, hx + h, hy + h, fill=c["focus"],
                                         outline=c["bg"], tags="crop")

    def toggle_play(self):
        self.playing = not self.playing
        self.btn_play.set_text("pause" if self.playing else "play")
        if self.playing:
            if self.timeline.play >= self.timeline.end - 0.02:
                self.timeline.play = self.timeline.start
            self._anchor(self.timeline.play)
            self._tick()
        elif self._play_job:
            self.top.after_cancel(self._play_job)
            self._play_job = None

    def _anchor(self, t):
        self._play_from = t
        self._play_t0 = time.perf_counter()

    def _tick(self):
        if not self.playing:
            return
        # where playback should be by the clock, not by how many ticks happened to fire
        t = self._play_from + (time.perf_counter() - self._play_t0) * self.speed
        if t >= self.timeline.end:
            t = self.timeline.start
            self._anchor(t)
        self.show_frame(t)
        self._play_job = self.top.after(max(1, int(1000 / self.preview_fps)), self._tick)

    def seek(self, t):
        self.show_frame(t)
        if self.playing:
            self._anchor(t)

    def on_range(self, a, b):
        self._update_estimate()

    # ---- estimate
    def _update_estimate(self):
        wd, ht, dur, fps = self.src
        span = (self.timeline.end - self.timeline.start) / self.speed
        out_fps = min(fps or 25.0, max(fps_ladder(fps or 25.0)))
        if self.crop:
            frame = "%d × %d cropped from %d × %d" % (self.crop[2], self.crop[3], wd, ht)
        else:
            frame = "source %d × %d" % (wd, ht)
        self.meta.configure(text="%s  ·  %.2fs after trim  ·  up to %.4g fps out"
                                 % (frame, span, out_fps))

    # ---- saving
    def save(self):
        if self.saving or not self.frames:
            return
        self.saving = True
        self._cancel_save = False
        self.btn_save.set_enabled(False)
        self.btn_play.set_enabled(False)
        self.playing = False
        self.btn_play.set_text("play")
        self.prep.pack(fill="x", padx=self.PADX, pady=(0, int(4 * self.s)))
        self.prep.set(0.05)
        out = capture_path(save_dir(self.settings), "gif")
        threading.Thread(target=self._save_worker, args=(out,), name="magcopy-save",
                         daemon=True).start()

    def _save_worker(self, out):
        def progress(text, frac=None):
            self.app.post(lambda: self._progress(text))

        opt = GifOptimizer(
            self.master, out,
            start=self.timeline.start, end=self.timeline.end, speed=self.speed, crop=self.crop,
            size_limit_bytes=int(self.settings["size_limit_mb"] * 1_000_000),
            headroom=self.settings["target_headroom"],
            on_progress=progress, should_cancel=lambda: self._cancel_save)
        try:
            res = opt.run()
            self.app.post(lambda: self._saved(res))
        except Cancelled:
            pass
        except Exception as e:
            log_exc("gif save")
            msg = str(e)[:160]
            self.app.post(lambda: self._save_failed(msg))
        finally:
            opt.cleanup()

    def _progress(self, text):
        try:
            self.status.configure(text=text)
        except Exception:
            pass

    def _save_failed(self, msg):
        self.saving = False
        self.btn_save.set_enabled(True)
        self.btn_play.set_enabled(True)
        self.prep.set(None)
        self.status.configure(text="could not save: %s" % msg)

    def _saved(self, res):
        """Saving is finished: show the folder, then ask whether that is that.

        The editor used to close itself a second later, which is the wrong default - a GIF is
        often nearly right, and finding out means looking at the file. Nothing is thrown away
        until it is asked for.
        """
        self.saving = False
        self.result = res
        self.prep.set(1.0)
        note = "%d x %d  ·  %.4g fps  ·  %d frames  ·  %.2f MB" % (
            res.width, res.height, res.fps, res.frames, res.bytes / 1e6)
        if not res.fits:
            note += "   (over the limit)"
        self.status.configure(text=note)
        self.app.gif_saved(res)
        self.btn_play.set_enabled(True)
        self.btn_save.set_enabled(True)
        if self.settings.get("open_folder_after_gif", True):
            self.app.reveal(res.path)
        self._show_done(res, note)

    def _show_done(self, res, note):
        """A small themed panel over the editor: done, keep editing, or show the file again."""
        S = lambda px: int(round(px * self.s))
        T, F, c = self.theme, self.fonts, self.theme.c
        try:
            if getattr(self, "_done_win", None) is not None:
                self._done_win.destroy()
        except Exception:
            pass
        win_ = tk.Toplevel(self.top)
        self._done_win = win_
        win_.withdraw()
        win_.transient(self.top)
        win_.title("saved")
        win_.configure(bg=c["bg"], highlightthickness=0)
        win_.resizable(False, False)

        panel = Panel(win_)
        # both colours: Tk paints highlightcolor while the widget has focus and
        # highlightbackground while it does not, and the default focused colour is near-white
        panel.configure(highlightthickness=1, highlightbackground=c["hair"], highlightcolor=c["hair"])
        panel.pack(fill="both", expand=True)
        T.add(panel)
        inner = Panel(panel)
        inner.pack(fill="both", expand=True, padx=S(22), pady=(S(18), S(16)))
        T.add(inner)

        head = Label(inner, role="ink", text="all done?", font=F.head, anchor="w")
        head.pack(fill="x")
        T.add(head)
        name = Label(inner, role="ink2", text=os.path.basename(res.path), font=F.mono9, anchor="w")
        name.pack(fill="x", pady=(S(8), 0))
        T.add(name)
        meta = Label(inner, role="mute", text=note, font=F.mono8, anchor="w")
        meta.pack(fill="x", pady=(S(3), 0))
        T.add(meta)
        where = Label(inner, role="mute2", text="saved to %s" % os.path.dirname(res.path),
                      font=F.mono8, anchor="w")
        where.pack(fill="x", pady=(S(6), 0))
        T.add(where)
        hl = Hairline(inner)
        hl.pack(fill="x", pady=(S(14), S(12)))
        T.add(hl)

        row = Panel(inner)
        row.pack(fill="x")
        T.add(row)

        def finish():
            self._dismiss_done()
            self.close()

        def keep():
            self._dismiss_done()
            self.status.configure(text="still open - change the trim or crop and save again")

        btn = Button(row, F, "done", finish, "primary", self.s)
        btn.pack(side="left")
        T.add(btn)
        keep_btn = Button(row, F, "keep editing", keep, "ghost", self.s)
        keep_btn.pack(side="left", padx=(S(8), 0))
        T.add(keep_btn)
        show = TextLink(row, "show the file", lambda: self.app.reveal(res.path), F)
        show.pack(side="right", pady=(S(6), 0))
        T.add(show)

        win_.update_idletasks()
        w_, h_ = win_.winfo_reqwidth(), win_.winfo_reqheight()
        tx, ty = self.top.winfo_rootx(), self.top.winfo_rooty()
        tw, th = self.top.winfo_width(), self.top.winfo_height()
        win_.geometry("%dx%d+%d+%d" % (w_, h_, tx + (tw - w_) // 2, ty + (th - h_) // 3))
        win_.deiconify()
        win_.lift()
        try:
            win.make_frameless(win_, c["hair"], c["bg"])
        except Exception:
            pass
        win_.bind("<Escape>", lambda e: keep())
        win_.bind("<Return>", lambda e: finish())
        try:
            win_.grab_set()
            btn.focus_set()
        except Exception:
            pass

    def _dismiss_done(self):
        w_ = getattr(self, "_done_win", None)
        self._done_win = None
        if w_ is None:
            return
        try:
            w_.grab_release()
        except Exception:
            pass
        try:
            w_.destroy()
        except Exception:
            pass

    # ---- layout
    PADX = 0

    def _build(self):
        S = lambda px: int(round(px * self.s))
        self.PADX = S(24)
        T, F = self.theme, self.fonts
        c = T.c
        top = tk.Toplevel(self.app.root)
        self.top = top
        top.withdraw()
        top.title("MagCopy · edit")
        top.configure(bg=c["bg"])
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self.close)

        root = Panel(top)
        root.pack(fill="both", expand=True)
        T.add(root)

        # title band doubles as the drag handle
        band = Panel(root)
        band.pack(fill="x")
        T.add(band)
        chrome = Panel(band)
        chrome.pack(fill="x", padx=self.PADX, pady=(S(14), S(10)))
        T.add(chrome)
        word = Label(chrome, role="ink", text="edit recording", font=F.body_med, anchor="w")
        word.pack(side="left")
        T.add(word)
        close = IconButton(chrome, "close", self.close, self.s)
        close.pack(side="right")
        T.add(close)
        hl = Hairline(band)
        hl.pack(fill="x", padx=self.PADX)
        T.add(hl)
        for widget in (band, chrome, word, hl):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.configure(cursor="fleur")

        # preview
        wd, ht, dur, fps = self.src
        # Start the picture at a placeholder size. Everything below it is laid out first, then the
        # window is asked how tall it is without a preview - that measurement is the chrome height,
        # which is the only honest way to know how much room is left. Guessing it is what made the
        # window overshoot. All of this happens while the window is withdrawn, so nothing is seen
        # to resize: it is mapped once, at its final size.
        self.canvas = tk.Canvas(root, width=16, height=16, bd=0, highlightthickness=0,
                                bg=c["bg2"], cursor="crosshair")
        self.canvas.pack(padx=self.PADX, pady=(S(16), S(10)))
        self.canvas.bind("<ButtonPress-1>", self._crop_press)
        self.canvas.bind("<B1-Motion>", self._crop_move)
        self.canvas.bind("<ButtonRelease-1>", self._crop_release)
        self.canvas.bind("<Motion>", self._crop_hover)

        self.timeline = Timeline(root, F, dur, self.on_range, self.seek, self.s)
        self.timeline.pack(fill="x", padx=self.PADX)
        T.add(self.timeline)

        # transport
        row = Panel(root)
        row.pack(fill="x", padx=self.PADX, pady=(S(8), 0))
        T.add(row)
        self.btn_play = Button(row, F, "play", self.toggle_play, "ghost", self.s)
        self.btn_play.pack(side="left")
        T.add(self.btn_play)
        self.pos_label = Label(row, role="mute", text="0.00s", font=F.mono9, anchor="w")
        self.pos_label.pack(side="left", padx=(S(10), 0))
        T.add(self.pos_label)
        trim_in = TextLink(row, "set in", lambda: self._set_edge("start"), F)
        trim_in.pack(side="left", padx=(S(16), 0))
        T.add(trim_in)
        trim_out = TextLink(row, "set out", lambda: self._set_edge("end"), F)
        trim_out.pack(side="left", padx=(S(12), 0))
        T.add(trim_out)
        reset = TextLink(row, "reset trim", self._reset_trim, F)
        reset.pack(side="left", padx=(S(12), 0))
        T.add(reset)
        self.link_reset_crop = TextLink(row, "reset crop", self._reset_crop, F, role="mute")
        self.link_reset_crop.pack(side="right")
        T.add(self.link_reset_crop)
        self.btn_crop = Button(row, F, "save crop", self._save_crop, "ghost", self.s)
        self.btn_crop.pack(side="right", padx=(0, S(10)))
        T.add(self.btn_crop)
        hintc = Label(row, role="mute2", text="drag on the picture, then drag its edges",
                      font=F.mono8, anchor="e")
        hintc.pack(side="right", padx=(0, S(12)))
        T.add(hintc)

        # options
        opts = Panel(root)
        opts.pack(fill="x", padx=self.PADX, pady=(S(12), 0))
        T.add(opts)
        lab = Label(opts, role="mute", text="speed", font=F.mono9, anchor="w", width=8)
        lab.pack(side="left")
        T.add(lab)
        self.pill_speed = Pills(opts, F, [(0.5, "0.5×"), (1.0, "1×"), (1.5, "1.5×"), (2.0, "2×")],
                                1.0, self._set_speed, self.s)
        self.pill_speed.pack(side="left")
        T.add(self.pill_speed)
        self.meta = Label(root, role="mute2", text="", font=F.mono8, anchor="w")
        self.meta.pack(fill="x", padx=self.PADX, pady=(S(8), 0))
        T.add(self.meta)

        hl2 = Hairline(root)
        hl2.pack(fill="x", padx=self.PADX, pady=(S(14), 0))
        T.add(hl2)

        self.prep = ProgressLine(root, self.s)
        self.prep.pack(fill="x", padx=self.PADX, pady=(S(8), S(2)))
        T.add(self.prep)
        self.prep.set(0.02)
        self.status = Label(root, role="mute", text="preparing preview…", font=F.mono9, anchor="w")
        self.status.pack(fill="x", padx=self.PADX)
        T.add(self.status)

        foot = Panel(root)
        foot.pack(fill="x", padx=self.PADX, pady=(S(12), S(16)))
        T.add(foot)
        self.btn_save = Button(foot, F, "save gif", self.save, "primary", self.s)
        self.btn_save.pack(side="left")
        self.btn_save.set_enabled(False)
        T.add(self.btn_save)
        self._sync_crop_buttons()
        discard = TextLink(foot, "discard", self.close, F, role="mute")
        discard.pack(side="left", padx=(S(14), 0))
        T.add(discard)
        limit = Label(foot, role="mute2",
                      text="target: under %.0f mb" % self.settings["size_limit_mb"],
                      font=F.mono8, anchor="e")
        limit.pack(side="right")
        T.add(limit)

        # ---- size the preview to what is actually left, on the monitor this will open on
        top.update_idletasks()
        # The chrome sits BELOW the picture, so only its height eats into the space the preview
        # can have; horizontally the picture just needs its own side padding, and the window ends
        # up as wide as whichever of the two needs more.
        chrome_h = max(0, root.winfo_reqheight() - 16)
        wx, wy, ww, wh = self._target_work_area()
        avail_w = int(ww * SCREEN_FRACTION) - 2 * self.PADX
        avail_h = int(wh * SCREEN_FRACTION) - chrome_h
        self.pw, self.ph = fit_preview(self.src[0], self.src[1], max(avail_w, PREVIEW_MIN_W),
                                       max(avail_h, 120))
        self.canvas.configure(width=self.pw, height=self.ph)

        # Measuring with a 16 px placeholder is close but not exact - a label can wrap differently
        # once the picture has its real width - so check the finished size and take the overshoot
        # off the preview. Still withdrawn, so this settles before anything is on screen.
        budget_w, budget_h = int(ww * SCREEN_FRACTION), int(wh * SCREEN_FRACTION)
        for _ in range(3):
            top.update_idletasks()
            over_h = root.winfo_reqheight() - budget_h
            over_w = root.winfo_reqwidth() - budget_w
            if over_h <= 0 and over_w <= 0:
                break
            shrink_h = self.ph - max(120, self.ph - max(0, over_h))
            shrink_w = self.pw - max(PREVIEW_MIN_W, self.pw - max(0, over_w))
            if not shrink_h and not shrink_w:
                break
            self.pw, self.ph = fit_preview(self.src[0], self.src[1],
                                           max(PREVIEW_MIN_W, self.pw - shrink_w),
                                           max(120, self.ph - shrink_h))
            self.canvas.configure(width=self.pw, height=self.ph)

        # Strip the title bar BEFORE fixing the size. Removing WS_CAPTION does not shrink the
        # window, it hands the caption's rows to the client area - so a geometry set beforehand
        # comes out about 30 px taller than asked for. Doing it while still withdrawn means the
        # window is mapped exactly once, at exactly the right size, with nothing seen to resize.
        top.update_idletasks()
        self._frameless()
        top.update_idletasks()
        w_, h_ = root.winfo_reqwidth(), root.winfo_reqheight()
        w_, h_ = min(w_, ww - 16), min(h_, wh - 16)
        top.geometry("%dx%d+%d+%d" % (w_, h_, wx + (ww - w_) // 2, wy + max(8, (wh - h_) // 2)))
        top.deiconify()
        top.after(30, self._frameless)      # again once mapped: rounding and border colour
        top.bind("<space>", lambda e: self.toggle_play())
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Control-s>", lambda e: self.save())
        self.timeline.bind("<Configure>", lambda e: self._fit_strip())

    def _target_work_area(self):
        """Work area of the monitor the recording came from, falling back to the pointer's.

        Sizing against the primary monitor is wrong the moment there are two: a clip recorded on a
        1152x2048 portrait panel would be measured against a 2560x1440 one and open off the edge.
        """
        try:
            if self.rect:
                return win.work_area_for(self.rect)
            px, py = self.app.root.winfo_pointerx(), self.app.root.winfo_pointery()
            return win.work_area_for((px, py, 1, 1))
        except Exception:
            r = self.app.root
            return (0, 0, r.winfo_screenwidth(), r.winfo_screenheight())

    def _frameless(self):
        c = self.theme.c
        win.make_frameless(self.top, c["hair"], c["bg"])
        self.top.attributes("-topmost", True)
        self.top.after(250, lambda: self.top.attributes("-topmost", False))

    def _drag_start(self, e):
        self._d = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def _drag_move(self, e):
        d = getattr(self, "_d", None)
        if d:
            hwnd = win.toplevel_hwnd(self.top)
            x, y = e.x_root - d[0], e.y_root - d[1]
            if hwnd:
                win.move_window(hwnd, x, y)
            else:
                self.top.geometry("+%d+%d" % (x, y))

    # ---- small actions
    def _set_edge(self, which):
        t = self.timeline.play
        if which == "start":
            self.timeline.start = min(t, self.timeline.end - 0.05)
        else:
            self.timeline.end = max(t, self.timeline.start + 0.05)
        self.timeline.draw()
        self._update_estimate()

    def _reset_trim(self):
        self.timeline.set_range(0.0, self.duration)
        self._update_estimate()

    def _set_speed(self, value):
        self.speed = float(value)
        self._update_estimate()
