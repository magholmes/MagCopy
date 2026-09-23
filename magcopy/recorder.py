"""Screen recording: a paced capture loop piped straight into ffmpeg, plus the on-screen frame.

The recording master is deliberately over-good: yuv444p x264 at a low CRF. Everything the GIF
pipeline does later is a reduction, so the master is the one place where quality must not be
thrown away - and for screen content 4:4:4 is what keeps text edges from smearing.

The frame you see while recording is a set of click-through strips placed *outside* the captured
rectangle - a soft hairline edge with solid viewfinder brackets at the corners - so it shows you
exactly what is being recorded without ever appearing in the recording, and without shouting.

Borrowed from OBS
  * Scheduling fit to pace a frame loop, asked for by `plat.begin_precise_timing` and given back
    at the end. On Windows that is a 1 ms system timer, because the default 15.6 ms granularity
    cannot pace a 25 fps loop at all - frames land in clumps. macOS sleeps accurately already and
    wants the quality-of-service class instead. Both are the same request: do not let an
    unrelated busy process shear this.
  * A short spin at the end of each wait, which is what makes the interval real once the
    scheduler is willing.
  * Timing anchored to the wall clock, not to a frame counter. If a capture overruns, the loop
    repeats the previous frame to keep the timeline honest rather than letting the recording drift
    slowly out of sync with what actually happened. Repeats are counted and reported.

The capture itself
  All of the above is platform-independent, and everything below `plat.Grabber` is not. Windows
  blits through GDI, which cannot see fullscreen-exclusive games or some GPU-composited surfaces;
  those come out black, and that is the known gap there. macOS reads a ScreenCaptureKit stream,
  which has no such blind spot but only sends a frame when something actually changed - so a
  `grab` returning the same picture twice is the screen genuinely standing still, and the repeat
  accounting above already says the right thing about it.
"""
import os
import subprocess
import threading
import time
import tkinter as tk

from . import plat
from .binaries import TOOLS, popen
from .settings import log_exc
from .theme import Label, Panel, round_rect


class RecordFrame:
    """The border drawn around the region while recording, plus the little control bar.

    Every strip sits just outside the capture rectangle and is click-through, so the app being
    recorded still receives every click and nothing red can land in a frame. The control bar is
    not click-through - it is how you stop - and it is also placed clear of the captured area.
    """

    BAR_H = 34

    def __init__(self, root, theme, fonts, rect, on_stop, on_cancel, scale=1.0):
        self.root, self.c, self.fonts = root, dict(theme.c), fonts
        self.rect = rect
        self.on_stop, self.on_cancel = on_stop, on_cancel
        self.s = scale
        self.windows = []
        self.bar = None
        self.bar_excluded = False       # hidden from capture at the OS level
        self.bar_where = ""             # where it ended up, for the log and the tests
        self.time_label = None
        self.dot = None
        self._dot_on = True

    EDGE_ALPHA = 90             # the continuous edge is a whisper...
    CORNER_ALPHA = 255          # ...the corners are what you actually read

    def show(self):
        """A soft continuous edge with crisp viewfinder brackets at the corners.

        Every piece sits strictly outside the captured rectangle, so none of it can end up in the
        recording - that is why this is strips rather than one outlined window.

        The look comes from the hierarchy rather than from weight. A uniformly bold red box is
        easy to see and looks like a warning; here the full edge is one thin, mostly transparent
        line that just states where the boundary is, and only the four corner brackets are solid.
        The eye reads the corners and infers the rectangle, which is how a viewfinder works - and
        it leaves whatever is being recorded visually undisturbed.
        """
        x, y, wd, ht = self.rect
        t = max(1, int(round(1.5 * self.s)))                            # hairline edge
        ct = max(3, int(round(3 * self.s)))                             # bracket thickness
        cl = max(16, min(int(min(wd, ht) * 0.15), int(42 * self.s)))    # bracket arm length
        c = self.c
        red = c.get("record", c["error"])
        for ex, ey, ew, eh in ((x - t, y - t, wd + 2 * t, t),           # top
                               (x - t, y + ht, wd + 2 * t, t),          # bottom
                               (x - t, y, t, ht),                       # left
                               (x + wd, y, t, ht)):                     # right
            self.windows.append(self._strip(ex, ey, ew, eh, red, self.EDGE_ALPHA))
        for ex, ey, ew, eh in (
                (x - ct, y - ct, cl + ct, ct), (x - ct, y - ct, ct, cl + ct),           # top-left
                (x + wd - cl, y - ct, cl + ct, ct), (x + wd, y - ct, ct, cl + ct),      # top-right
                (x - ct, y + ht, cl + ct, ct), (x - ct, y + ht - cl, ct, cl + ct),      # bottom-left
                (x + wd - cl, y + ht, cl + ct, ct), (x + wd, y + ht - cl, ct, cl + ct)):  # bottom-right
            self.windows.append(self._strip(ex, ey, ew, eh, red, self.CORNER_ALPHA))
        self._build_bar()

    def _strip(self, x, y, wd, ht, color, alpha=255):
        w_ = tk.Toplevel(self.root)
        w_.withdraw()
        w_.overrideredirect(True)
        w_.configure(bg=color)
        w_.attributes("-topmost", True)
        w_.geometry("%dx%d+%d+%d" % (max(1, wd), max(1, ht), x, y))
        w_.deiconify()
        try:
            hwnd = plat.toplevel_hwnd(w_)
            plat.set_overlay_styles(hwnd)
            plat.set_click_through(hwnd, True, alpha)
            plat.raise_topmost(hwnd)
        except Exception:
            pass
        return w_

    def _bar_spot(self, bw, bh, excluded):
        """Where the bar goes: outside the region wherever there is room, inside only when safe.

        Below, then above, then beside it, all on the region's own monitor and clear of its
        taskbar. A recording of the whole screen leaves none of those, and the old last resort -
        pinned to the top of the monitor - was simply inside the frame: the bar, its timer and its
        stop button were recorded into the GIF. Now that last resort is taken only when the bar is
        also excluded from capture, where it sits over the recording for the person making it and
        is absent from every frame. On a Windows too old to exclude it, another monitor comes
        first, and inside the region is left for the case where there is no other monitor at all.
        """
        x, y, wd, ht = self.rect
        # the monitor the region is on, minus its taskbar - not the whole virtual desktop, or the
        # bar lands on another screen or underneath the taskbar
        area = plat.work_area_for(self.rect)
        vx, vy, vw, vh = area
        g, m = int(10 * self.s), 8
        right_aligned = min(max(x + wd - bw, vx + m), vx + vw - bw - m)
        beside_y = min(max(y, vy + m), vy + vh - bh - m)

        def on(a, bx, by):
            ax, ay, aw, ah = a
            return ax + 2 <= bx and bx + bw <= ax + aw - 2 and ay + 2 <= by and by + bh <= ay + ah - 2

        def clear(bx, by):
            return bx + bw <= x or bx >= x + wd or by + bh <= y or by >= y + ht

        for bx, by in ((right_aligned, y + ht + g),          # below
                       (right_aligned, y - bh - g),          # above
                       (x + wd + g, beside_y),               # to the right
                       (x - bw - g, beside_y)):              # to the left
            if on(area, bx, by) and clear(bx, by):
                return bx, by, "outside"
        inside = (min(max(x + wd - bw - m, vx + m), vx + vw - bw - m), max(vy + m, y + m))
        if excluded:
            return inside[0], inside[1], "inside, excluded from capture"
        for mon in plat.monitors():            # (monitor rect, work area, ...) - index, never unpack
            work = mon[1]
            ax, ay, aw, ah = work
            bx, by = ax + aw - bw - 16, ay + ah - bh - 16
            if on(work, bx, by) and clear(bx, by):
                return bx, by, "another monitor"
        return inside[0], inside[1], "inside - will be recorded"

    def _build_bar(self):
        """The timer and stop/cancel, kept out of the recording by exclusion or by placement."""
        c, F = self.c, self.fonts
        bw, bh = int(250 * self.s), int(self.BAR_H * self.s)

        bar = tk.Toplevel(self.root)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg=c["bg"])
        bar.update_idletasks()                 # the real window has to exist to be excluded
        try:
            self.bar_excluded = bool(plat.exclude_window(plat.toplevel_hwnd(bar)))
        except Exception:
            self.bar_excluded = False
        bx, by, self.bar_where = self._bar_spot(bw, bh, self.bar_excluded)
        bar.geometry("%dx%d+%d+%d" % (bw, bh, bx, by))
        panel = Panel(bar)
        panel.configure(bg=c["bg"], highlightthickness=1, highlightbackground=c["hair"],
                        highlightcolor=c["hair"])
        panel.pack(fill="both", expand=True)

        self.dot = tk.Canvas(panel, width=int(18 * self.s), height=bh, bd=0, highlightthickness=0, bg=c["bg"])
        self.dot.pack(side="left", padx=(int(10 * self.s), 0))
        self.time_label = Label(panel, role="ink", text="0.0s", font=F.mono9, bg=c["bg"], fg=c["ink"])
        self.time_label.pack(side="left", padx=(int(6 * self.s), 0))
        self.limit_label = Label(panel, role="mute", text="", font=F.mono8, bg=c["bg"], fg=c["mute"])
        self.limit_label.pack(side="left", padx=(int(6 * self.s), 0))

        for text, cmd, role in (("stop", self.on_stop, "ink"), ("cancel", self.on_cancel, "mute")):
            lb = tk.Label(panel, text=text, font=F.mono9, bd=0, cursor="hand2",
                          bg=c["bg"], fg=c[role])
            lb.pack(side="right", padx=(0, int(12 * self.s)))
            lb.bind("<Button-1>", lambda e, f=cmd: f())
            lb.bind("<Enter>", lambda e, l=lb: l.configure(fg=c["ink"]))
            lb.bind("<Leave>", lambda e, l=lb, r=role: l.configure(fg=c[r]))
        bar.deiconify()
        try:                       # the bar takes clicks but must never pull focus off the
            hwnd = plat.toplevel_hwnd(bar)                     # app being recorded
            plat.set_overlay_styles(hwnd)
            if self.bar_excluded:
                plat.exclude_window(hwnd)      # again once mapped; the call is idempotent
        except Exception:
            pass
        self.bar = bar
        self._blink()

    def keep_on_top(self):
        """Push every strip back into the topmost band, in case something covered it."""
        for w_ in self.windows:
            try:
                plat.raise_topmost(plat.toplevel_hwnd(w_))
            except Exception:
                pass

    def _blink(self):
        if not self.bar:
            return
        try:
            self.keep_on_top()
            self.dot.delete("all")
            if self._dot_on:
                d = int(9 * self.s)
                cx, cy = int(9 * self.s), int(self.BAR_H * self.s / 2)
                red = self.c.get("record", self.c["error"])
                self.dot.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2,
                                     fill=red, outline=red)
            self._dot_on = not self._dot_on
            self.bar.after(500, self._blink)
        except Exception:
            pass

    def update_time(self, seconds, limit):
        try:
            if self.time_label:
                self.time_label.configure(text="%.1fs" % seconds)
            if self.limit_label:
                self.limit_label.configure(text="/ %ds" % limit)
        except Exception:
            pass

    def destroy(self):
        for w_ in self.windows:
            try:
                w_.destroy()
            except Exception:
                pass
        self.windows = []
        if self.bar:
            try:
                self.bar.destroy()
            except Exception:
                pass
            self.bar = None


class Recorder:
    """Captures `rect` into an x264 master at `fps`, stopping on request or at `max_seconds`.

    Runs on its own thread; `on_tick` and `on_done` are invoked from that thread, so the UI
    hands them straight back to Tk with `after`.
    """

    def __init__(self, rect, out_path, fps=30, max_seconds=20, cursor=True,
                 on_tick=None, on_done=None):
        self.rect = rect
        self.out_path = out_path
        self.fps = max(5, min(60, int(fps)))
        self.max_seconds = max(1, int(max_seconds))
        self.cursor = cursor
        self.on_tick, self.on_done = on_tick, on_done
        self._stop = threading.Event()
        self._cancel = False
        self.thread = None
        self.frames = 0
        self.dropped = 0
        self.repeated = 0
        self.duration = 0.0
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="magcopy-record", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def cancel(self):
        self._cancel = True
        self._stop.set()

    def _ffmpeg_args(self, wd, ht):
        return [TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgra", "-s", "%dx%d" % (wd, ht),
                "-r", str(self.fps), "-i", "-",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
                "-pix_fmt", "yuv444p", "-movflags", "+faststart", self.out_path]

    @staticmethod
    def _precise_sleep(until):
        """Sleep to `until` (perf_counter seconds): coarse sleep first, then spin the last 1.5 ms."""
        while True:
            remaining = until - time.perf_counter()
            if remaining <= 0:
                return
            if remaining > 0.0015:
                time.sleep(remaining - 0.0012)
            else:
                while time.perf_counter() < until:
                    pass
                return

    def _run(self):
        x, y, wd, ht = self.rect
        wd, ht = int(wd) & ~1, int(ht) & ~1                  # x264 wants even dimensions
        if wd < 2 or ht < 2:
            self.error = "region too small"
            if self.on_done:
                self.on_done(self)
            return
        proc = None
        grabber = None
        timer_raised = False
        try:
            timer_raised = plat.begin_precise_timing()
            # The grabber comes first now, because it decides the frame size. The region was
            # dragged in points; on a Retina screen the frames are twice that in each direction,
            # and ffmpeg has to be told the size it is actually being fed.
            grabber = plat.Grabber(wd, ht, fps=self.fps, origin=(x, y))
            cw, ch = getattr(grabber, "out_w", wd), getattr(grabber, "out_h", ht)
            proc = popen(self._ffmpeg_args(cw, ch), stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            interval = 1.0 / self.fps
            t0 = time.perf_counter()
            deadline = t0 + self.max_seconds
            last_bytes = None
            n = 0                                              # index of the next frame slot to fill
            while not self._stop.is_set():
                due = t0 + n * interval                        # slot times come from the wall clock
                if due > deadline:
                    break
                now = time.perf_counter()
                if now < due:
                    self._precise_sleep(due)
                    if self._stop.is_set():
                        break
                elif now > due + interval and last_bytes is not None:
                    # the capture overran its slot: repeat the last frame so the timeline stays
                    # true to real time instead of quietly slowing the recording down
                    behind = int((now - due) / interval)
                    for _ in range(min(behind, 8)):
                        try:
                            proc.stdin.write(last_bytes)
                        except (BrokenPipeError, OSError):
                            behind = 0
                            break
                        self.frames += 1
                        self.repeated += 1
                        n += 1
                    continue
                buf = grabber.grab(x, y, self.cursor).tobytes()
                try:
                    proc.stdin.write(buf)
                except (BrokenPipeError, OSError):
                    break
                last_bytes = buf
                self.frames += 1
                n += 1
                if self.on_tick and self.frames % 3 == 0:
                    self.on_tick(time.perf_counter() - t0)
            self.duration = time.perf_counter() - t0
        except Exception:
            log_exc("recorder")
            self.error = "recording failed"
        finally:
            plat.end_precise_timing(timer_raised)
            if grabber:
                grabber.close()
            if proc:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=20)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                if proc.returncode not in (0, None) and not self.error:
                    err = b""
                    try:
                        err = proc.stderr.read() or b""
                    except Exception:
                        pass
                    self.error = "ffmpeg: %s" % err.decode("utf-8", "replace").strip()[:200]
        if self._cancel:
            try:
                os.remove(self.out_path)
            except Exception:
                pass
        elif not self.error and self.frames < 2:
            self.error = "nothing was recorded"
        if self.on_done:
            self.on_done(self)

    @property
    def cancelled(self):
        return self._cancel
