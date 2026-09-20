"""Screen recording: a paced capture loop piped straight into ffmpeg, plus the on-screen frame.

The recording master is deliberately over-good: yuv444p x264 at a low CRF. Everything the GIF
pipeline does later is a reduction, so the master is the one place where quality must not be
thrown away - and for screen content 4:4:4 is what keeps text edges from smearing.

The frame you see while recording is a set of click-through strips placed *outside* the captured
rectangle - a soft hairline edge with solid viewfinder brackets at the corners - so it shows you
exactly what is being recorded without ever appearing in the recording, and without shouting.

Borrowed from OBS
  * A 1 ms system timer for the duration of the capture. Windows' default scheduling granularity
    is 15.6 ms, so an unadorned sleep cannot pace a 25 fps loop at all - frames land in clumps.
    `timeBeginPeriod(1)` plus a short spin at the end of each wait is what makes the interval real.
  * Above-normal thread priority while recording, so an unrelated busy process cannot shear the
    capture.
  * Timing anchored to the wall clock, not to a frame counter. If a capture overruns, the loop
    repeats the previous frame to keep the timeline honest rather than letting the recording drift
    slowly out of sync with what actually happened. Repeats are counted and reported.

Not borrowed
  OBS captures through Windows Graphics Capture / DXGI desktop duplication. That is genuinely
  better - it is hardware-accelerated and it can see GPU-composited and fullscreen-exclusive
  windows that GDI returns as black - but it needs WinRT and D3D11 interop, which is a long way
  past what ctypes should be asked to do. GDI BitBlt with CAPTUREBLT covers desktop and
  application UI, which is what this tool is for; full-screen games are the known gap.
"""
import ctypes
import os
import subprocess
import threading
import time
import tkinter as tk

from . import win
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
            hwnd = win.toplevel_hwnd(w_)
            win.set_overlay_styles(hwnd)
            win.set_click_through(hwnd, True, alpha)
            win.raise_topmost(hwnd)
        except Exception:
            pass
        return w_

    def _build_bar(self):
        """Sits below the region, or above it when there is no room underneath."""
        x, y, wd, ht = self.rect
        # the monitor the region is on, minus its taskbar - not the whole virtual desktop, or the
        # bar lands on another screen or underneath the taskbar
        vx, vy, vw, vh = win.work_area_for(self.rect)
        c, F = self.c, self.fonts
        bw, bh = int(250 * self.s), int(self.BAR_H * self.s)
        bx = min(max(x + wd - bw, vx + 8), vx + vw - bw - 8)
        by = y + ht + int(10 * self.s)
        if by + bh > vy + vh - 8:
            by = y - bh - int(10 * self.s)
        if by < vy + 8:
            by = vy + 8

        bar = tk.Toplevel(self.root)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg=c["bg"])
        bar.geometry("%dx%d+%d+%d" % (bw, bh, bx, by))
        panel = Panel(bar)
        panel.configure(bg=c["bg"], highlightthickness=1, highlightbackground=c["hair"])
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
            win.set_overlay_styles(win.toplevel_hwnd(bar))   # app being recorded
        except Exception:
            pass
        self.bar = bar
        self._blink()

    def keep_on_top(self):
        """Push every strip back into the topmost band, in case something covered it."""
        for w_ in self.windows:
            try:
                win.raise_topmost(win.toplevel_hwnd(w_))
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
            if os.name == "nt":
                try:
                    ctypes.windll.winmm.timeBeginPeriod(1)      # 15.6 ms -> 1 ms scheduling
                    timer_raised = True
                except Exception:
                    pass
                try:
                    ctypes.windll.kernel32.SetThreadPriority(
                        ctypes.windll.kernel32.GetCurrentThread(), 1)   # ABOVE_NORMAL
                except Exception:
                    pass
            proc = popen(self._ffmpeg_args(wd, ht), stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            grabber = win.Grabber(wd, ht)
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
            if timer_raised:
                try:
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass
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
