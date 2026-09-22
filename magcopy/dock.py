"""The floating controller: a small capsule that lives on top of everything.

At rest it is a thin pill with two hints on it. Hovered, it opens into two buttons - a frame for
a screenshot, a red dot for a recording - and clicking either goes straight to the region picker,
exactly as the shortcut would. Drag it anywhere; where it sits is remembered.

Two things shape the implementation more than anything else.

It must not appear in its own captures. The dock is topmost by construction, so anything it
overlaps it would be baked into: the screenshot picker freezes the screen, and a recording reads
the real desktop. The app withdraws it for the length of a capture rather than trying to keep it
out of frame - see `hide_for_capture` in app.py.

And it has to be a real shape, not a rectangle pretending. A translucent window (`-alpha`) dims
its corners along with everything else and reads as a grey slab; a colour-keyed window really is
absent where the key colour shows, so the capsule has edges. The two are mutually exclusive in
Tk, so the rest-to-hover fade is done by interpolating the drawn colours instead of the window's
opacity, which looks the same and keeps the shape.
"""
import tkinter as tk

from . import plat
from .theme import round_rect

# A colour nothing here draws, so no real pixel is ever punched out by accident.
KEY = "#FF00FE"

COLLAPSED = (54, 20)                # the pill at rest
EXPANDED = (126, 44)                # opened, with both buttons
GRIP = 15                           # the draggable strip down the left of the open dock
STEP_MS = 12                        # animation tick
OPEN_MS = 130                       # how long the open/close tween runs
LEAVE_MS = 170                      # grace before closing, so a wobble does not shut it
DRAG_SLOP = 4                       # a press that moves less than this is a click


def _mix(a, b, t):
    """Blend two #rrggbb colours; t=0 gives a, t=1 gives b."""
    t = max(0.0, min(1.0, t))
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(x + (y - x) * t)) for x, y in zip(av, bv))


class Dock:
    """One always-on-top capsule with a screenshot button and a record button."""

    def __init__(self, root, theme, settings, on_shot, on_gif, scale=1.0, on_moved=None):
        self.root, self.theme, self.settings = root, theme, settings
        self.on_shot, self.on_gif, self.on_moved = on_shot, on_gif, on_moved
        self.s = scale
        self.c = dict(theme.c)
        self.open = 0.0                 # 0 closed, 1 open; the tween runs between them
        self.want_open = False
        self._job = self._leave_job = None
        self._drag = None
        self._zone = None               # which button the pointer is over: "shot", "gif", None
        self._hidden_for_capture = False
        self.top = None
        self.cv = None

    # ---- geometry
    def _size(self):
        cw, ch = (int(round(v * self.s)) for v in COLLAPSED)
        ew, eh = (int(round(v * self.s)) for v in EXPANDED)
        t = self.open
        return int(round(cw + (ew - cw) * t)), int(round(ch + (eh - ch) * t))

    def _default_pos(self):
        """Bottom right of the work area the pointer is on, a comfortable margin in."""
        try:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        except Exception:
            x = y = 0
        vx, vy, vw, vh = plat.work_area_for((x, y, 1, 1))
        ew, eh = (int(round(v * self.s)) for v in EXPANDED)
        return vx + vw - ew - int(38 * self.s), vy + vh - eh - int(38 * self.s)

    def _place(self):
        """Put the window where it should be, clamped on screen.

        The dock grows rightward from its anchor so the pointer, which is over the closed pill,
        stays inside the open one - a window that opened out from under the cursor would flicker
        between states. Against the right edge there is no room to grow that way, so it opens
        leftward instead and the anchor shifts with it.
        """
        w, h = self._size()
        x, y = int(self.settings.get("dock_x", -1)), int(self.settings.get("dock_y", -1))
        if x < -10000 or y < -10000 or (x, y) == (-1, -1):
            x, y = self._default_pos()
            self.settings["dock_x"], self.settings["dock_y"] = x, y
        vx, vy, vw, vh = plat.work_area_for((x, y, 1, 1))
        ew, _ = (int(round(v * self.s)) for v in EXPANDED)
        # Against the right edge the open dock would run off, so the anchor slides left by
        # however much it needs; the closed pill stays put and the open one reaches back over it.
        draw_x = min(x, vx + vw - ew - 4) if x + ew > vx + vw - 4 else x
        draw_x = max(vx + 2, min(draw_x, vx + vw - w - 2))
        draw_y = max(vy + 2, min(y, vy + vh - h - 2))
        self.top.geometry("%dx%d+%d+%d" % (w, h, draw_x, draw_y))
        self.cv.configure(width=w, height=h)

    # ---- lifecycle
    def show(self):
        if self.top is not None:
            return
        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.cv = tk.Canvas(self.top, bd=0, highlightthickness=0, bg=self.c["bg"])
        self.cv.pack(fill="both", expand=True)
        self._make_shaped()
        self._place()
        self.top.deiconify()
        try:
            hwnd = plat.toplevel_hwnd(self.top)
            # NOACTIVATE above all: clicking the dock must not take focus off whatever is being
            # captured, and TOOLWINDOW keeps it out of the taskbar and alt-tab.
            plat.set_overlay_styles(hwnd)
            plat.raise_topmost(hwnd)
        except Exception:
            pass
        for seq, fn in (("<Enter>", self._enter), ("<Leave>", self._leave),
                        ("<Motion>", self._motion), ("<ButtonPress-1>", self._press),
                        ("<B1-Motion>", self._move), ("<ButtonRelease-1>", self._release)):
            self.cv.bind(seq, fn)
        self.draw()
        # Another topmost window mapped around the same moment can end up above this one, and
        # then the controller is simply missing with nothing to say why. Claim the top again once
        # the window is settled; after that nothing competes for it.
        self.root.after(250, self._reassert_top)

    def _reassert_top(self):
        if self.top is None or not self.top.winfo_viewable():
            return
        try:
            self.top.attributes("-topmost", True)
            plat.raise_topmost(plat.toplevel_hwnd(self.top))
        except Exception:
            pass

    def _make_shaped(self):
        """Real rounded edges rather than a rectangle painted to look like one."""
        try:
            if plat.IS_MAC:
                self.top.attributes("-transparent", True)
                self.cv.configure(bg="systemTransparent")
            else:
                self.top.attributes("-transparentcolor", KEY)
                self.cv.configure(bg=KEY)
        except Exception:
            self.cv.configure(bg=self.c["bg"])      # a plain slab still works

    def destroy(self):
        for job in (self._job, self._leave_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._job = self._leave_job = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
        self.top = self.cv = None

    def hide_for_capture(self):
        """Take it off screen for the length of a capture. Returns True if it was showing."""
        if self.top is None or not self.top.winfo_viewable():
            return False
        self._hidden_for_capture = True
        self.want_open = False
        self.open = 0.0
        try:
            self.top.withdraw()
            self.top.update_idletasks()
        except Exception:
            pass
        return True

    def restore_after_capture(self):
        if self.top is None or not self._hidden_for_capture:
            return
        self._hidden_for_capture = False
        try:
            self._place()
            self.top.deiconify()
            plat.raise_topmost(plat.toplevel_hwnd(self.top))
            self.root.after(120, self._reassert_top)
        except Exception:
            pass
        self.draw()

    # ---- hover and the open/close tween
    def _enter(self, _e=None):
        if self._leave_job:
            self.root.after_cancel(self._leave_job)
            self._leave_job = None
        self.want_open = True
        self._animate()

    def _leave(self, _e=None):
        if self._drag is not None:
            return
        if self._leave_job:
            self.root.after_cancel(self._leave_job)

        def go():
            self._leave_job = None
            self._zone = None
            self.want_open = False
            self._animate()
        self._leave_job = self.root.after(LEAVE_MS, go)

    def _animate(self):
        if self._job:
            return
        step = STEP_MS / float(OPEN_MS)

        def tick():
            self._job = None
            target = 1.0 if self.want_open else 0.0
            if abs(self.open - target) < step:
                self.open = target
            else:
                self.open += step if target > self.open else -step
            if self.top is None:
                return
            self._place()
            self.draw()
            if self.open != target:
                self._job = self.root.after(STEP_MS, tick)
        tick()

    # ---- mouse
    def _zones(self):
        """(name, x1, y1, x2, y2) for each button, in the open dock's coordinates."""
        w, h = self._size()
        if self.open < 0.55:
            return []
        left = int(GRIP * self.s)
        half = (w - left) / 2.0
        pad = int(4 * self.s)
        return [("shot", left, pad, left + half, h - pad),
                ("gif", left + half, pad, w - pad, h - pad)]

    def _hit(self, x, y):
        for name, x1, y1, x2, y2 in self._zones():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _motion(self, e):
        was = self._zone
        self._zone = self._hit(e.x, e.y)
        try:
            self.cv.configure(cursor="hand2" if self._zone else "fleur")
        except Exception:
            pass
        if was != self._zone:
            self.draw()

    def _press(self, e):
        self._drag = (e.x_root, e.y_root, self.top.winfo_x(), self.top.winfo_y(),
                      self._hit(e.x, e.y), False)

    def _move(self, e):
        if self._drag is None:
            return
        sx, sy, wx, wy, zone, moved = self._drag
        dx, dy = e.x_root - sx, e.y_root - sy
        if not moved and abs(dx) < DRAG_SLOP and abs(dy) < DRAG_SLOP:
            return
        self._drag = (sx, sy, wx, wy, zone, True)
        self.settings["dock_x"], self.settings["dock_y"] = wx + dx, wy + dy
        self._place()

    def _release(self, e):
        if self._drag is None:
            return
        _, _, _, _, zone, moved = self._drag
        self._drag = None
        if moved:
            # Settle inside the work area, then remember where it ended up. The idle pass is not
            # optional: geometry() only queues the move, so reading the position straight after
            # returns where the window still is and writes the pre-drag spot back to settings.
            self._place()
            try:
                self.top.update_idletasks()
            except Exception:
                pass
            self.settings["dock_x"] = self.top.winfo_x()
            self.settings["dock_y"] = self.top.winfo_y()
            if self.on_moved:
                self.on_moved()
            return
        if zone and zone == self._hit(e.x, e.y):
            (self.on_shot if zone == "shot" else self.on_gif)()

    # ---- painting
    def draw(self):
        if self.cv is None:
            return
        cv, c, s, t = self.cv, self.c, self.s, self.open
        w, h = self._size()
        cv.delete("all")
        # At rest the capsule sits back: the page's own background, a hairline edge. Opening
        # lifts it a step - never to a colour the app does not already use elsewhere.
        fill = _mix(c["bg"], c["bg2"], 0.35 + 0.65 * t)
        edge = _mix(c["hair"], c["mute"], t)
        r = h / 2.0
        round_rect(cv, 1, 1, w - 1, h - 1, r, fill=fill, outline=edge)

        if t < 0.5:
            self._draw_resting(w, h, t)
        if t > 0.35:
            self._draw_open(w, h, t)

    def _draw_resting(self, w, h, t):
        """Two hints of what is inside, fading out as it opens."""
        cv, c = self.cv, self.c
        k = 1.0 - t / 0.5
        mid = h / 2.0
        frame = _mix(self.c["bg"], c["mute"], k)
        dot = _mix(self.c["bg"], c["record"], k)
        a = max(2.0, 3.0 * self.s)
        cx = w * 0.37
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):     # a tiny frame
            cv.create_line(cx + sx * a, mid + sy * a, cx + sx * a - sx * a, mid + sy * a,
                           fill=frame, width=1)
            cv.create_line(cx + sx * a, mid + sy * a, cx + sx * a, mid + sy * a - sy * a,
                           fill=frame, width=1)
        rx = w * 0.66
        cv.create_oval(rx - a + 1, mid - a + 1, rx + a - 1, mid + a - 1, fill=dot, outline=dot)

    def _draw_open(self, w, h, t):
        cv, c, s = self.cv, self.c, self.s
        k = min(1.0, (t - 0.35) / 0.65)
        left = int(GRIP * s)
        mid = h / 2.0
        grip = _mix(self.c["bg"], c["mute2"], k)
        for i in (-1, 0, 1):                                     # the drag handle
            y = mid + i * 4 * s
            cv.create_line(left * 0.42, y, left * 0.72, y, fill=grip, width=1)

        for name, x1, y1, x2, y2 in self._zones():
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            hot = self._zone == name
            if hot:
                round_rect(cv, x1 + 1, y1, x2 - 1, y2, (y2 - y1) / 2.0,
                           fill=_mix(c["bg2"], c["hair"], 0.6), outline="")
            if name == "shot":
                col = _mix(self.c["bg"], c["ink"] if hot else c["mute"], k)
                a = 6 * s
                for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    cv.create_line(cx + sx * a, cy + sy * a, cx + sx * a - sx * a * 0.55,
                                   cy + sy * a, fill=col, width=1.4)
                    cv.create_line(cx + sx * a, cy + sy * a, cx + sx * a,
                                   cy + sy * a - sy * a * 0.55, fill=col, width=1.4)
            else:
                # the record dot is the theme's record red, the one colour every palette shares
                col = _mix(self.c["bg"], c["record"], k if hot else k * 0.82)
                a = 5 * s
                cv.create_oval(cx - a, cy - a, cx + a, cy + a, fill=col, outline=col)

    def restyle(self, c):
        self.c = dict(c)
        self.draw()
