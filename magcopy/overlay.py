"""The region selector: drag a rectangle over the screen.

Two modes, because the two captures want different things.

**Frozen** (screenshots). The desktop is grabbed once and shown as a still. Freezing is a feature
here: a menu or tooltip stays put while you frame it, and what you framed is exactly what you get.

**Live** (recordings). Nothing is frozen - you are about to record motion, so the screen has to
keep moving while you choose. That is built from two stacked windows:

  * a translucent *dim* layer that darkens the screen and receives every click, with a hole cut
    out of it where the selection is. A uniformly translucent window cannot paint part of itself
    brighter, so instead the dim simply stops existing over the selection and the real screen
    shows through at full brightness.
  * a *chrome* layer above it, colour-keyed so only the lines drawn on it are visible, carrying
    the outline, crosshair, corner ticks and size readout. It is click-through, so the dim layer
    underneath still gets the mouse. Chrome needs its own layer because anything drawn on the dim
    window would be dimmed along with it.

Tk's image subsystem costs ~300 ms on its very first PhotoImage; `prewarm` pays that once at
startup so the first frozen capture is as fast as the tenth. The live mode needs no image at all,
so it opens instantly.

Aiming: a real crosshair cursor, plus a small drawn cross at the pointer. Two earlier attempts
are worth not repeating. Full-screen guide lines on the canvas cost 24 ms per mouse move, because
moving a line that long forces a repaint - and on a layered window a recomposite - the size of the
screen; that is visible lag just hovering around deciding where to drag. Moving thin always-on-top
windows instead is fast, but Windows reports a one- or two-pixel layered window as mapped while
compositing nothing at all, which left the screen with no indication of where the pointer was.
A short cross drawn near the cursor costs 0.08 ms and actually appears, and the OS cursor is left
visible so there is always something to aim with.
"""
import tkinter as tk

from PIL import Image, ImageTk

from . import win
from .settings import log_exc

DIM = 0.42                      # how far the un-selected desktop is knocked back (frozen mode)
LIVE_DIM = 0.45                 # window alpha of the dim layer (live mode)
KEY_COLOR = "#010203"           # colour-key for the chrome layer; nothing real is this colour
MIN_SIDE = 8                    # smaller than this is treated as a mis-click, not a selection
_PREWARMED = [False]


def prewarm(root):
    """Pay Tk's one-off PhotoImage setup cost now, not on the first capture."""
    if _PREWARMED[0]:
        return
    try:
        ImageTk.PhotoImage(Image.new("RGB", (8, 8)))
        _PREWARMED[0] = True
    except Exception:
        pass


def _lut(factor):
    return [min(255, int(i * factor)) for i in range(256)] * 3


class RegionSelector:
    """Modal region picker. `run()` returns (x, y, w, h) in virtual-screen pixels, or None."""

    def __init__(self, root, theme, fonts, title="", accent=None, live=False, ratio=None):
        self.root = root
        self.c = dict(theme.c)
        self.fonts = fonts
        self.title = title
        self.accent = accent or self.c["focus"]
        self.live = live
        self.ratio = ratio                # width/height the drag is held to, or None for free
        self.ratio_name = ""              # how to say it in the hint, e.g. "4:5"
        self.result = None
        self.start = None
        self.cur = None
        self.dragging = False
        self.shift = False
        self.bright = None                # frozen mode only: the still the screenshot is cut from
        self.chrome = None

    # ---- lifecycle
    def run(self):
        try:
            return self._run()
        except Exception:
            log_exc("region selector")
            try:
                self._destroy()
            except Exception:
                pass
            return None

    def _run(self):
        vx, vy, vw, vh = win.virtual_screen()
        self.vx, self.vy, self.vw, self.vh = vx, vy, vw, vh

        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.geometry("%dx%d+%d+%d" % (vw, vh, vx, vy))
        self.top.attributes("-topmost", True)
        self.top.configure(bg=self.c["shade"], cursor="crosshair")
        # "shade" is the palette's own darkening colour - the deep end of a dark ladder, the
        # ink end of a light one - so the dim belongs to the theme rather than being flat black
        self.cv = tk.Canvas(self.top, width=vw, height=vh, bd=0, highlightthickness=0,
                            bg=self.c["shade"])
        self.cv.pack(fill="both", expand=True)

        if self.live:
            self.top.attributes("-alpha", LIVE_DIM)
            self._build_chrome_layer()
            # If the chrome layer could not be made click-through, it must not exist. It sits on
            # top of the layer that takes the mouse, and the grab means events it does receive are
            # discarded rather than handled - so anything drawn on it would swallow presses
            # outright. Drawing on the dim layer instead costs a little contrast and always works.
            draw_on = self.chrome_cv if self.chrome is not None else self.cv
        else:
            bgra = win.grab_once(vx, vy, vw, vh, cursor=False)
            # PIL reads the BGRA buffer directly in BGRX raw mode, skipping a full-screen swap
            self.bright = Image.frombuffer("RGB", (vw, vh), bgra.tobytes(), "raw", "BGRX", 0, 1)
            self._dim_photo = ImageTk.PhotoImage(self.bright.point(_lut(DIM)))
            self.cv.create_image(0, 0, image=self._dim_photo, anchor="nw")
            self.sel = tk.Canvas(self.top, bd=0, highlightthickness=0, bg=self.c["shade"])
            self._sel_photo = None
            draw_on = self.cv

        self.draw_cv = draw_on
        self._build_chrome()
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        if self.chrome:
            self.chrome.lift()
        try:
            self.top.grab_set_global()
        except Exception:
            self.top.grab_set()

        # Only the dim layer takes the mouse. Binding the chrome layer as a safety net does not
        # work and is worth not trying again: the grab below discards events aimed at windows
        # outside it, and the chrome layer is a sibling, not a child. Its click-through style is
        # what keeps presses landing on the dim layer, and it is destroyed above if that failed.
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._motion)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<ButtonPress-3>", lambda e: self._cancel())
        for seq in ("<Escape>", "<Key-q>"):
            self.top.bind(seq, lambda e: self._cancel())
        self.top.bind("<Return>", lambda e: self._commit())
        for seq, val in (("<KeyPress-Shift_L>", True), ("<KeyRelease-Shift_L>", False),
                         ("<KeyPress-Shift_R>", True), ("<KeyRelease-Shift_R>", False)):
            self.top.bind(seq, lambda e, v=val: self._set_shift(v))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.top.bind("<Key-%s>" % key, lambda e, a=dx, b=dy: self._nudge(a, b))
        self.top.bind("<Control-a>", lambda e: self._select_all())

        px, py = self.top.winfo_pointerx(), self.top.winfo_pointery()
        self._draw_idle(px - vx, py - vy)
        self.root.wait_window(self.top)
        return self.result

    def _build_chrome_layer(self):
        """The click-through layer the live mode draws on, so its lines are not dimmed."""
        self.chrome = tk.Toplevel(self.root)
        self.chrome.withdraw()
        self.chrome.overrideredirect(True)
        self.chrome.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))
        self.chrome.attributes("-topmost", True)
        self.chrome.configure(bg=KEY_COLOR)
        try:
            self.chrome.attributes("-transparentcolor", KEY_COLOR)
        except Exception:
            pass
        self.chrome_cv = tk.Canvas(self.chrome, width=self.vw, height=self.vh, bd=0,
                                   highlightthickness=0, bg=KEY_COLOR)
        self.chrome_cv.pack(fill="both", expand=True)
        self.chrome.deiconify()
        self.chrome.update_idletasks()        # the wrapper window must exist before it is styled
        try:                                  # keep_layer: do not clobber the colour key with alpha
            hwnd = win.toplevel_hwnd(self.chrome)
            win.set_overlay_styles(hwnd)
            self._chrome_passthrough = bool(win.set_click_through(hwnd, True, keep_layer=True))
        except Exception:
            self._chrome_passthrough = False
        if not self._chrome_passthrough:
            try:
                self.chrome.destroy()
            except Exception:
                pass
            self.chrome = None
            self.chrome_cv = None

    def _destroy(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        for w_ in (self.chrome, getattr(self, "top", None)):
            try:
                if w_ is not None:
                    w_.destroy()
            except Exception:
                pass
        self.chrome = None

    def _cancel(self):
        self.result = None
        self._destroy()

    def _commit(self):
        r = self._rect()
        if r and r[2] >= MIN_SIDE and r[3] >= MIN_SIDE:
            self.result = (r[0] + self.vx, r[1] + self.vy, r[2], r[3])
        else:
            self.result = None
        self._destroy()

    # ---- chrome
    CROSS_ARM = 16                    # half-length of the drawn cross, in pixels

    def _build_chrome(self):
        c, cv = self.c, self.draw_cv
        self.cross_v = cv.create_line(0, 0, 0, 0, fill=self.accent, width=1, state="hidden")
        self.cross_h = cv.create_line(0, 0, 0, 0, fill=self.accent, width=1, state="hidden")
        self.outline = cv.create_rectangle(0, 0, 0, 0, outline=self.accent, width=1, state="hidden")
        self.ticks = [cv.create_line(0, 0, 0, 0, fill=self.accent, width=2, state="hidden")
                      for _ in range(8)]
        self.badge_bg = cv.create_rectangle(0, 0, 0, 0, fill=c["bg"], outline=c["hair"], state="hidden")
        self.badge_tx = cv.create_text(0, 0, text="", fill=c["ink"], font=self.fonts.mono9,
                                       anchor="nw", state="hidden")
        if self.ratio:
            hint = self.title or "drag to select   ·   held to %s   ·   shift frees it   ·   esc cancels" % self.ratio_name
        else:
            hint = self.title or "drag to select   ·   shift = square   ·   esc cancels"
        self.hint_bg = cv.create_rectangle(0, 0, 0, 0, fill=c["bg"], outline=c["hair"])
        self.hint_tx = cv.create_text(0, 0, text=hint, fill=c["ink2"], font=self.fonts.mono9, anchor="nw")
        self._place_hint()

    def _place_hint(self):
        """Centre the hint on whichever monitor the pointer is on, near its bottom edge."""
        cv = self.draw_cv
        px = self.top.winfo_pointerx() - self.vx
        py = self.top.winfo_pointery() - self.vy
        bbox = cv.bbox(self.hint_tx)
        tw = (bbox[2] - bbox[0]) if bbox else 300
        th = (bbox[3] - bbox[1]) if bbox else 16
        x = min(max(px - tw / 2, 20), self.vw - tw - 20)
        y = min(max(py + 120, 20), self.vh - th - 40)
        cv.coords(self.hint_tx, x, y)
        cv.coords(self.hint_bg, x - 12, y - 8, x + tw + 12, y + th + 8)
        cv.tag_raise(self.hint_bg)
        cv.tag_raise(self.hint_tx)

    # ---- interaction
    def _set_shift(self, on):
        self.shift = on
        if self.dragging:
            self._redraw()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.cur = (e.x, e.y)
        self.dragging = True
        cv = self.draw_cv
        for item in (self.hint_bg, self.hint_tx, self.cross_v, self.cross_h):
            cv.itemconfigure(item, state="hidden")
        self._redraw()

    def _motion(self, e):
        if self.dragging:
            self.cur = (e.x, e.y)
            self._redraw()
        else:
            self._draw_idle(e.x, e.y)

    def _release(self, e):
        if not self.dragging:
            return
        self.cur = (e.x, e.y)
        self.dragging = False
        self._commit()

    def _nudge(self, dx, dy):
        """Arrow keys move the far corner one pixel - the escape hatch for an exact edge."""
        if not self.start or not self.cur:
            return
        self.cur = (self.cur[0] + dx, self.cur[1] + dy)
        self._redraw()

    def _select_all(self):
        self.start, self.cur = (0, 0), (self.vw, self.vh)
        self.dragging = True
        self._redraw()

    # ---- geometry
    def _rect(self):
        """The selection, after any shape constraint and after clamping to the desktop.

        Order matters: constrain first, then clamp, then constrain again. Clamping a ratio-locked
        box at the screen edge otherwise silently changes its shape - which is the one thing a
        locked ratio is supposed to prevent.
        """
        if not self.start or not self.cur:
            return None
        x1, y1 = self.start
        x2, y2 = self.cur
        ratio = None
        if self.ratio and not self.shift:                # shift is the escape hatch from a lock
            ratio = self.ratio
        elif self.shift and not self.ratio:              # plain shift means square
            ratio = 1.0
        if ratio:
            x2, y2 = self._fit_ratio(x1, y1, x2, y2, ratio)
        x, y = min(x1, x2), min(y1, y2)
        wd, ht = abs(x2 - x1), abs(y2 - y1)
        x, y = max(0, x), max(0, y)
        wd, ht = min(wd, self.vw - x), min(ht, self.vh - y)
        if ratio and wd > 0 and ht > 0:                  # clamping may have bent it: fit inside
            if wd / ht > ratio:
                wd = ht * ratio
            else:
                ht = wd / ratio
        return (x, y, int(round(wd)), int(round(ht)))

    def _fit_ratio(self, x1, y1, x2, y2, ratio):
        """Move the dragged corner so the box is exactly `ratio`, keeping the drag's direction."""
        wd, ht = abs(x2 - x1), abs(y2 - y1)
        if ht <= 0 or wd / max(ht, 1e-6) > ratio:        # the drag is wider than the shape wants
            ht = wd / ratio
        else:
            wd = ht * ratio
        return (x1 + (wd if x2 >= x1 else -wd), y1 + (ht if y2 >= y1 else -ht))

    # ---- painting
    def _draw_idle(self, x, y):
        cv, a = self.draw_cv, self.CROSS_ARM
        cv.coords(self.cross_v, x, y - a, x, y + a)
        cv.coords(self.cross_h, x - a, y, x + a, y)
        cv.itemconfigure(self.cross_v, state="normal")
        cv.itemconfigure(self.cross_h, state="normal")
        cv.tag_raise(self.cross_v)
        cv.tag_raise(self.cross_h)
        self._place_hint()

    def _redraw(self):
        r = self._rect()
        if not r:
            return
        x, y, wd, ht = r
        cv = self.draw_cv
        if wd < 1 or ht < 1:
            if not self.live:
                self.sel.place_forget()
            cv.itemconfigure(self.outline, state="hidden")
            for t in self.ticks:
                cv.itemconfigure(t, state="hidden")
            return
        if self.live:
            # cut the selection out of the dim layer: what shows through is the live screen
            try:
                win.set_window_hole(win.toplevel_hwnd(self.top), self.vw, self.vh, (x, y, wd, ht))
            except Exception:
                pass
        else:
            try:
                crop = self.bright.crop((x, y, x + wd, y + ht))
                self._sel_photo = ImageTk.PhotoImage(crop)
                self.sel.configure(width=wd, height=ht)
                self.sel.delete("all")
                self.sel.create_image(0, 0, image=self._sel_photo, anchor="nw")
                self.sel.place(x=x, y=y, width=wd, height=ht)
            except Exception:
                pass
        cv.coords(self.outline, x - 1, y - 1, x + wd, y + ht)
        cv.itemconfigure(self.outline, state="normal")
        cv.tag_raise(self.outline)
        self._corner_ticks(x, y, wd, ht)
        self._badge(x, y, wd, ht)

    def _corner_ticks(self, x, y, wd, ht):
        """Short marks at each corner: enough to read the frame, not enough to clutter it."""
        cv = self.draw_cv
        n = max(6, min(18, int(min(wd, ht) * 0.14)))
        segs = [(x, y, x + n, y), (x, y, x, y + n),
                (x + wd, y, x + wd - n, y), (x + wd, y, x + wd, y + n),
                (x, y + ht, x + n, y + ht), (x, y + ht, x, y + ht - n),
                (x + wd, y + ht, x + wd - n, y + ht), (x + wd, y + ht, x + wd, y + ht - n)]
        for item, (a, b, cc, d) in zip(self.ticks, segs):
            cv.coords(item, a, b, cc, d)
            cv.itemconfigure(item, state="normal")
            cv.tag_raise(item)

    def _badge(self, x, y, wd, ht):
        cv = self.draw_cv
        text = "%d × %d" % (wd, ht)
        cv.itemconfigure(self.badge_tx, text=text, state="normal")
        bbox = cv.bbox(self.badge_tx)
        tw = (bbox[2] - bbox[0]) if bbox else 60
        th = (bbox[3] - bbox[1]) if bbox else 14
        bx, by = x, y - th - 16                          # above the selection...
        if by < 4:
            by = y + ht + 8                              # ...unless it would fall off the top
        bx = min(max(bx, 4), self.vw - tw - 20)
        cv.coords(self.badge_tx, bx + 8, by + 5)
        cv.coords(self.badge_bg, bx, by, bx + tw + 16, by + th + 10)
        cv.itemconfigure(self.badge_bg, state="normal")
        cv.tag_raise(self.badge_bg)
        cv.tag_raise(self.badge_tx)


def selector_for(root, theme, fonts, settings, title="", live=False):
    """Build a selector with the aspect ratio the settings ask for."""
    from .settings import aspect_value
    name = settings.get("aspect_ratio", "free")
    sel = RegionSelector(root, theme, fonts, title, live=live, ratio=aspect_value(name))
    sel.ratio_name = name if sel.ratio else ""
    return sel


def select_region(root, theme, fonts, title="", live=False, ratio=None):
    return RegionSelector(root, theme, fonts, title, live=live, ratio=ratio).run()
