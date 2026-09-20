"""The GIF editor: watch the recording back, trim it, set speed, then save.

Preview frames are extracted once into a scratch folder as small JPEGs and paged in on demand.
Scrubbing a video file frame-by-frame with a decoder is far too slow to feel like scrubbing;
a folder of small stills is instant, and the trim itself is still expressed in seconds against
the full-quality master, so nothing about the preview limits the output.

The filmstrip behind the timeline is built from about two dozen of those frames. It costs one
image at open time and makes finding the moment you wanted a glance rather than a hunt.

Preview extraction and encoding both run on worker threads and report back through the app's
event queue, never by touching Tk directly - see the note in app.py.
"""
import os
import shutil
import tempfile
import threading
import tkinter as tk

from PIL import Image, ImageTk

from . import win
from .binaries import TOOLS, run
from .optimize import GifOptimizer, Cancelled, fps_ladder, gif_info, probe
from .settings import log_exc, save_dir, stamped_name
from .theme import (Button, DotToggle, Hairline, Label, Panel, Pills, ProgressLine,
                    TextLink, IconButton, round_rect)

PREVIEW_W = 460
PREVIEW_FPS_CAP = 25.0
STRIP_THUMBS = 24


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
        self.create_line(px, top - 3 * self.s, px, bot + 3 * self.s, fill=c["error"], width=1)
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

    # ---- lifecycle
    def open(self):
        wd, ht, dur, fps = probe(self.master)
        self.src = (wd, ht, dur, fps)
        self.duration = dur
        if not wd or dur <= 0:
            self.app.toast("that recording could not be read", error=True)
            self.cleanup()
            return
        self._build()
        threading.Thread(target=self._prepare, name="magcopy-preview", daemon=True).start()

    def cleanup(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def close(self):
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
                     "-vf", "fps=%.6f,scale=%d:-2:flags=bilinear" % (self.preview_fps, PREVIEW_W),
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
        self.canvas.create_image(cw / 2, ch / 2, image=photo, anchor="c")
        self.timeline.set_play(t)
        self.pos_label.configure(text="%.2fs" % t)

    def toggle_play(self):
        self.playing = not self.playing
        self.btn_play.set_text("pause" if self.playing else "play")
        if self.playing:
            if self.timeline.play >= self.timeline.end - 0.02:
                self.timeline.play = self.timeline.start
            self._tick()
        elif self._play_job:
            self.top.after_cancel(self._play_job)
            self._play_job = None

    def _tick(self):
        if not self.playing:
            return
        step = 1.0 / self.preview_fps
        t = self.timeline.play + step * self.speed
        if t >= self.timeline.end:
            t = self.timeline.start
        self.show_frame(t)
        self._play_job = self.top.after(int(1000 / self.preview_fps), self._tick)

    def seek(self, t):
        self.show_frame(t)

    def on_range(self, a, b):
        self._update_estimate()

    # ---- estimate
    def _update_estimate(self):
        wd, ht, dur, fps = self.src
        span = (self.timeline.end - self.timeline.start) / self.speed
        out_fps = min(fps or 25.0, max(fps_ladder(fps or 25.0)))
        self.meta.configure(text="source %d × %d  ·  %.2fs after trim  ·  up to %.4g fps out"
                                 % (wd, ht, span, out_fps))

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
        out = os.path.join(save_dir(self.settings), stamped_name("magcopy", "gif"))
        threading.Thread(target=self._save_worker, args=(out,), name="magcopy-save",
                         daemon=True).start()

    def _save_worker(self, out):
        def progress(text, frac=None):
            self.app.post(lambda: self._progress(text))

        opt = GifOptimizer(
            self.master, out,
            start=self.timeline.start, end=self.timeline.end, speed=self.speed,
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
        self.saving = False
        self.result = res
        self.prep.set(1.0)
        note = "%d × %d · %.4g fps · %d frames · %.2f MB" % (
            res.width, res.height, res.fps, res.frames, res.bytes / 1e6)
        if not res.fits:
            note += "  (over the limit)"
        self.status.configure(text=note)
        self.app.gif_saved(res)
        self.top.after(1100, self.close)

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
        pw = PREVIEW_W
        ph = max(120, int(round(pw * ht / max(1, wd))))
        ph = min(ph, S(420))
        self.canvas = tk.Canvas(root, width=S(pw), height=ph, bd=0, highlightthickness=0,
                                bg=c["bg2"])
        self.canvas.pack(padx=self.PADX, pady=(S(16), S(10)))

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
        discard = TextLink(foot, "discard", self.close, F, role="mute")
        discard.pack(side="left", padx=(S(14), 0))
        T.add(discard)
        limit = Label(foot, role="mute2",
                      text="target: under %.0f mb" % self.settings["size_limit_mb"],
                      font=F.mono8, anchor="e")
        limit.pack(side="right")
        T.add(limit)

        top.update_idletasks()
        w_, h_ = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry("%dx%d+%d+%d" % (w_, h_, (sw - w_) // 2, max(20, (sh - h_) // 2)))
        top.deiconify()
        top.after(30, self._frameless)
        top.bind("<space>", lambda e: self.toggle_play())
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<Control-s>", lambda e: self.save())
        self.timeline.bind("<Configure>", lambda e: self._fit_strip())

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
