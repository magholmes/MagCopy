"""The region selector: a frozen, dimmed desktop you drag a rectangle over.

Why it is built this way
  * The desktop is captured once and shown as a still. Selecting against a frozen frame is what
    makes the rectangle feel solid, and it means the thing you framed is exactly the thing you get.
  * The dim layer is one full-screen image. The selection is *not* a second full-screen image:
    it is a small child canvas holding a crop of the bright frame, which costs about 2 ms to
    rebuild, so dragging stays smooth even across a 10-megapixel desktop.
  * Tk's image subsystem costs ~300 ms on its very first PhotoImage. `prewarm` pays that once at
    startup so the first Ctrl+Shift+A is as fast as the tenth.
"""
import tkinter as tk

import numpy as np
from PIL import Image, ImageTk

from . import win
from .settings import log_exc

DIM = 0.42                      # how far the un-selected desktop is knocked back
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

    def __init__(self, root, theme, fonts, title="", accent=None):
        self.root = root
        self.c = dict(theme.c)
        self.fonts = fonts
        self.title = title
        self.accent = accent or self.c["focus"]
        self.result = None
        self.start = None
        self.cur = None
        self.dragging = False
        self.shift = False

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
        bgra = win.grab_once(vx, vy, vw, vh, cursor=False)
        # PIL reads the BGRA buffer directly in BGRX raw mode, which skips a full-screen numpy swap
        self.bright = Image.frombuffer("RGB", (vw, vh), bgra.tobytes(), "raw", "BGRX", 0, 1)
        dim_img = self.bright.point(_lut(DIM))

        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.geometry("%dx%d+%d+%d" % (vw, vh, vx, vy))
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#000000", cursor="none")

        self.cv = tk.Canvas(self.top, width=vw, height=vh, bd=0, highlightthickness=0, bg="#000000")
        self.cv.pack(fill="both", expand=True)
        self._dim_photo = ImageTk.PhotoImage(dim_img)
        self.cv.create_image(0, 0, image=self._dim_photo, anchor="nw")

        # the bright selection lives in its own canvas so resizing it is pure geometry
        self.sel = tk.Canvas(self.top, bd=0, highlightthickness=0, bg="#000000")
        self._sel_photo = None

        self._build_chrome()
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
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
        self.top.bind("<KeyPress-Shift_L>", lambda e: self._set_shift(True))
        self.top.bind("<KeyRelease-Shift_L>", lambda e: self._set_shift(False))
        self.top.bind("<KeyPress-Shift_R>", lambda e: self._set_shift(True))
        self.top.bind("<KeyRelease-Shift_R>", lambda e: self._set_shift(False))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.top.bind("<Key-%s>" % key, lambda e, a=dx, b=dy: self._nudge(a, b))
        self.top.bind("<Control-a>", lambda e: self._select_all())

        px, py = self.top.winfo_pointerx(), self.top.winfo_pointery()
        self._draw_idle(px - vx, py - vy)
        self.root.wait_window(self.top)
        return self.result

    def _destroy(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass

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
    def _build_chrome(self):
        c = self.c
        self.cross_v = self.cv.create_line(0, 0, 0, 0, fill=c["mute"], width=1, dash=(3, 4))
        self.cross_h = self.cv.create_line(0, 0, 0, 0, fill=c["mute"], width=1, dash=(3, 4))
        self.outline = self.cv.create_rectangle(0, 0, 0, 0, outline=self.accent, width=1, state="hidden")
        self.ticks = [self.cv.create_line(0, 0, 0, 0, fill=self.accent, width=2, state="hidden")
                      for _ in range(8)]
        self.badge_bg = self.cv.create_rectangle(0, 0, 0, 0, fill=c["bg"], outline=c["hair"], state="hidden")
        self.badge_tx = self.cv.create_text(0, 0, text="", fill=c["ink"], font=self.fonts.mono9,
                                            anchor="nw", state="hidden")
        hint = self.title or "drag to select   ·   shift = square   ·   esc cancels"
        self.hint_bg = self.cv.create_rectangle(0, 0, 0, 0, fill=c["bg"], outline=c["hair"])
        self.hint_tx = self.cv.create_text(0, 0, text=hint, fill=c["ink2"], font=self.fonts.mono9, anchor="nw")
        self._place_hint()

    def _place_hint(self):
        """Centre the hint on whichever monitor the pointer is on, near its bottom edge."""
        px = self.top.winfo_pointerx() - self.vx
        py = self.top.winfo_pointery() - self.vy
        bbox = self.cv.bbox(self.hint_tx)
        tw = (bbox[2] - bbox[0]) if bbox else 300
        th = (bbox[3] - bbox[1]) if bbox else 16
        x = min(max(px - tw / 2, 20), self.vw - tw - 20)
        y = min(max(py + 120, 20), self.vh - th - 40)
        self.cv.coords(self.hint_tx, x, y)
        self.cv.coords(self.hint_bg, x - 12, y - 8, x + tw + 12, y + th + 8)
        self.cv.tag_raise(self.hint_bg)
        self.cv.tag_raise(self.hint_tx)

    # ---- interaction
    def _set_shift(self, on):
        self.shift = on
        if self.dragging:
            self._redraw()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.cur = (e.x, e.y)
        self.dragging = True
        self.cv.itemconfigure(self.hint_bg, state="hidden")
        self.cv.itemconfigure(self.hint_tx, state="hidden")
        self.cv.itemconfigure(self.cross_v, state="hidden")
        self.cv.itemconfigure(self.cross_h, state="hidden")
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
        self.cv.coords(self.cross_v, x, 0, x, self.vh)
        self.cv.coords(self.cross_h, 0, y, self.vw, y)
        self.cv.itemconfigure(self.cross_v, state="normal")
        self.cv.itemconfigure(self.cross_h, state="normal")
        self._place_hint()

    def _redraw(self):
        r = self._rect()
        if not r:
            return
        x, y, wd, ht = r
        if wd < 1 or ht < 1:
            self.sel.place_forget()
            self.cv.itemconfigure(self.outline, state="hidden")
            for t in self.ticks:
                self.cv.itemconfigure(t, state="hidden")
            return
        # bright crop: rebuild only the pixels inside the selection
        try:
            crop = self.bright.crop((x, y, x + wd, y + ht))
            self._sel_photo = ImageTk.PhotoImage(crop)
            self.sel.configure(width=wd, height=ht)
            self.sel.delete("all")
            self.sel.create_image(0, 0, image=self._sel_photo, anchor="nw")
            self.sel.place(x=x, y=y, width=wd, height=ht)
        except Exception:
            pass
        self.cv.coords(self.outline, x - 1, y - 1, x + wd, y + ht)
        self.cv.itemconfigure(self.outline, state="normal")
        self.cv.tag_raise(self.outline)
        self._corner_ticks(x, y, wd, ht)
        self._badge(x, y, wd, ht)

    def _corner_ticks(self, x, y, wd, ht):
        """Short marks at each corner: enough to read the frame, not enough to clutter it."""
        n = max(6, min(18, int(min(wd, ht) * 0.14)))
        segs = [(x, y, x + n, y), (x, y, x, y + n),
                (x + wd, y, x + wd - n, y), (x + wd, y, x + wd, y + n),
                (x, y + ht, x + n, y + ht), (x, y + ht, x, y + ht - n),
                (x + wd, y + ht, x + wd - n, y + ht), (x + wd, y + ht, x + wd, y + ht - n)]
        for item, (a, b, cc, d) in zip(self.ticks, segs):
            self.cv.coords(item, a, b, cc, d)
            self.cv.itemconfigure(item, state="normal")
            self.cv.tag_raise(item)

    def _badge(self, x, y, wd, ht):
        text = "%d × %d" % (wd, ht)
        self.cv.itemconfigure(self.badge_tx, text=text, state="normal")
        bbox = self.cv.bbox(self.badge_tx)
        tw = (bbox[2] - bbox[0]) if bbox else 60
        th = (bbox[3] - bbox[1]) if bbox else 14
        bx, by = x, y - th - 16                          # above the selection...
        if by < 4:
            by = y + ht + 8                              # ...unless it would fall off the top
        bx = min(max(bx, 4), self.vw - tw - 20)
        self.cv.coords(self.badge_tx, bx + 8, by + 5)
        self.cv.coords(self.badge_bg, bx, by, bx + tw + 16, by + th + 10)
        self.cv.itemconfigure(self.badge_bg, state="normal")
        self.cv.tag_raise(self.badge_bg)
        self.cv.tag_raise(self.badge_tx)


def select_region(root, theme, fonts, title=""):
    return RegionSelector(root, theme, fonts, title).run()
