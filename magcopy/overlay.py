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

The crosshair is two thin windows that get moved, not two lines on the canvas. Moving a
full-screen line item forces Tk to repaint - and, on a layered window, recomposite - a damage
region the size of the screen, which measured 24 ms per mouse move: visibly laggy just hovering
around deciding where to drag. Moving two 1-pixel windows costs 0.2 ms.
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

    def __init__(self, root, theme, fonts, title="", accent=None, live=False):
        self.root = root
        self.c = dict(theme.c)
        self.fonts = fonts
        self.title = title
        self.accent = accent or self.c["focus"]
        self.live = live
        self.result = None
        self.start = None
        self.cur = None
        self.dragging = False
        self.shift = False
        self.bright = None                # frozen mode only: the still the screenshot is cut from
        self.chrome = None
        self.cross = []                   # the two crosshair windows

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
        self.top.configure(bg="#000000", cursor="none")
        self.cv = tk.Canvas(self.top, width=vw, height=vh, bd=0, highlightthickness=0, bg="#05060A")
        self.cv.pack(fill="both", expand=True)

        if self.live:
            self.top.attributes("-alpha", LIVE_DIM)
            self._build_chrome_layer()
            draw_on = self.chrome_cv
        else:
            bgra = win.grab_once(vx, vy, vw, vh, cursor=False)
            # PIL reads the BGRA buffer directly in BGRX raw mode, skipping a full-screen swap
            self.bright = Image.frombuffer("RGB", (vw, vh), bgra.tobytes(), "raw", "BGRX", 0, 1)
            self._dim_photo = ImageTk.PhotoImage(self.bright.point(_lut(DIM)))
            self.cv.create_image(0, 0, image=self._dim_photo, anchor="nw")
            self.sel = tk.Canvas(self.top, bd=0, highlightthickness=0, bg="#000000")
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

        self._build_cross()
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
        try:                                  # keep_layer: do not clobber the colour key with alpha
            hwnd = win.toplevel_hwnd(self.chrome)
            win.set_overlay_styles(hwnd)
            win.set_click_through(hwnd, True, keep_layer=True)
        except Exception:
            pass

    def _destroy(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        for t, _ in self.cross:
            try:
                t.destroy()
            except Exception:
                pass
        self.cross = []
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
    def _build_cross(self):
        """Two thin click-through windows. Moving them is ~100x cheaper than redrawing lines."""
        for wd, ht in ((1, self.vh), (self.vw, 1)):
            try:
                t = tk.Toplevel(self.root)
                t.overrideredirect(True)
                t.geometry("%dx%d+%d+%d" % (wd, ht, self.vx, self.vy))
                t.configure(bg=self.c["mute"])
                t.attributes("-topmost", True)
                t.deiconify()
                hwnd = win.toplevel_hwnd(t)
                win.set_overlay_styles(hwnd)
                win.set_click_through(hwnd, True, 130)
                self.cross.append((t, hwnd))
            except Exception:
                pass

    def _hide_cross(self):
        for t, _ in self.cross:
            try:
                t.withdraw()
            except Exception:
                pass

    def _build_chrome(self):
        c, cv = self.c, self.draw_cv
        self.outline = cv.create_rectangle(0, 0, 0, 0, outline=self.accent, width=1, state="hidden")
        self.ticks = [cv.create_line(0, 0, 0, 0, fill=self.accent, width=2, state="hidden")
                      for _ in range(8)]
        self.badge_bg = cv.create_rectangle(0, 0, 0, 0, fill=c["bg"], outline=c["hair"], state="hidden")
        self.badge_tx = cv.create_text(0, 0, text="", fill=c["ink"], font=self.fonts.mono9,
                                       anchor="nw", state="hidden")
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
        for item in (self.hint_bg, self.hint_tx):
            cv.itemconfigure(item, state="hidden")
        self._hide_cross()
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
        if not self.start or not self.cur:
            return None
        x1, y1 = self.start
        x2, y2 = self.cur
        if self.shift:                                   # square, following the longer side
            side = max(abs(x2 - x1), abs(y2 - y1))
            x2 = x1 + (side if x2 >= x1 else -side)
            y2 = y1 + (side if y2 >= y1 else -side)
        x, y = min(x1, x2), min(y1, y2)
        wd, ht = abs(x2 - x1), abs(y2 - y1)
        x, y = max(0, x), max(0, y)
        wd, ht = min(wd, self.vw - x), min(ht, self.vh - y)
        return (x, y, wd, ht)

    # ---- painting
    def _draw_idle(self, x, y):
        if len(self.cross) == 2:
            win.move_window(self.cross[0][1], self.vx + x, self.vy)
            win.move_window(self.cross[1][1], self.vx, self.vy + y)
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


def select_region(root, theme, fonts, title="", live=False):
    return RegionSelector(root, theme, fonts, title, live=live).run()
