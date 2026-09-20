"""Look and feel: palettes, fonts and the widget set.

One colour is deliberately not from the palette: `record`, the frame drawn around a region while
it is being recorded. Every other colour is chosen to sit back; that one has to be legible against
whatever happens to be on screen, so it is a red in every theme - warmed off pure signal-red, and
carried mostly by the corner brackets rather than by weight.

The design language is the one from Opmize - Are.na's colour ladders, hairlines, Geist and
Geist Mono, lowercase mono labels, pill controls - with the palette made switchable so the
theme is a setting rather than a constant. Every widget registers with the Theme and gets a
`restyle(colours)` call, both on creation and whenever the palette changes.
"""
import os
import tkinter as tk
import tkinter.font as tkfont

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(APP_DIR, "fonts")


# ----------------------------------------------------------------------------- palettes
def _dark(ladder, focus, error, ok, record="#F2564B"):
    """Map an 8-step dark ladder (0 = background, 7 = foreground) onto the semantic names."""
    g = ladder
    return dict(bg=g[0], bg2=g[1], hair=g[2], hair_soft=g[1], ink=g[7], ink2=g[6],
                mute=g[5], mute2=g[4], paper_ink=g[0], focus=focus, error=error, ok=ok,
                record=record, shade=g[0], sel_fill=g[1])


def _light(ladder, focus, error, ok, record="#E0453B"):
    """Same ladder read the other way: 0 is the paper, 7 the ink."""
    g = ladder
    return dict(bg=g[0], bg2=g[1], hair=g[2], hair_soft=g[1], ink=g[7], ink2=g[6],
                mute=g[5], mute2=g[4], paper_ink=g[0], focus=focus, error=error, ok=ok,
                record=record, shade=g[7], sel_fill=g[1])


THEMES = {
    # are.na "Dusk" - the Opmize palette, purple-tinted grey
    "dusk": _dark(["#16171E", "#24222C", "#342E38", "#4F4756", "#81738C", "#A798B3", "#D5CADE", "#E7DBF0"],
                  focus="#5E6DEE", error="#CB76A9", ok="#98DC89"),
    # neutral charcoal
    "night": _dark(["#131314", "#1D1E20", "#2A2B2E", "#414348", "#6E7176", "#9A9DA3", "#CFD2D6", "#E8EAED"],
                   focus="#4C7DF0", error="#E06C75", ok="#7FBF6A"),
    # warm, paper-in-lamplight
    "ember": _dark(["#17140F", "#221D17", "#312921", "#4A3F33", "#7C6B57", "#A6927B", "#DCCCB6", "#F0E4D2"],
                   focus="#E08A3C", error="#D2694F", ok="#A8BF6A"),
    # cool blue-green
    "tide": _dark(["#0F1618", "#172123", "#223032", "#34484B", "#5B7A7E", "#87A7AB", "#C3DADC", "#DCEFF0"],
                  focus="#3FA9C9", error="#D4737B", ok="#7FC79B"),
    # light
    "paper": _light(["#FBFAF8", "#F1EFEA", "#DFDBD3", "#C3BDB2", "#8A8477", "#6A655B", "#3A3731", "#1C1A17"],
                    focus="#2F5FD0", error="#B3453A", ok="#3F7A3A"),
}
THEME_ORDER = [("dusk", "dusk"), ("night", "night"), ("ember", "ember"), ("tide", "tide"), ("paper", "paper")]
DEFAULT_THEME = "dusk"


def colors_for(name):
    return dict(THEMES.get(name, THEMES[DEFAULT_THEME]))


class Theme:
    """Holds the active palette and restyles every registered widget when it changes."""

    def __init__(self, name=DEFAULT_THEME):
        self.name = name if name in THEMES else DEFAULT_THEME
        self.c = colors_for(self.name)
        self.listeners = []
        self.on_change = None

    def add(self, obj):
        self.listeners.append(obj)
        try:
            obj.restyle(self.c)
        except Exception:
            pass
        return obj

    def set(self, name):
        if name not in THEMES or name == self.name:
            return
        self.name = name
        self.c = colors_for(name)
        for obj in list(self.listeners):
            try:
                obj.restyle(self.c)
            except tk.TclError:
                pass                                    # widget went away between theme changes
            except Exception:
                pass
        if self.on_change:
            self.on_change(self.c)

    def reset(self):
        self.listeners = []


# ----------------------------------------------------------------------------- fonts
def register_fonts():
    """Load the bundled Geist faces for this process so Tk can name them."""
    if os.name != "nt" or not os.path.isdir(FONT_DIR):
        return
    import ctypes
    FR_PRIVATE = 0x10
    for name in os.listdir(FONT_DIR):
        if name.lower().endswith((".ttf", ".otf")):
            try:
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(FONT_DIR, name), FR_PRIVATE, 0)
            except Exception:
                pass


class Fonts:
    def __init__(self, root, factor=1.0):
        fams = set(tkfont.families(root))

        def pick(cands):
            for f in cands:
                if f in fams:
                    return f
            return cands[-1]

        def pt(base):
            return max(6, int(round(base * factor)))

        self.sans = pick(["Geist", "Inter", "Helvetica Neue", "Segoe UI"])
        self.sans_med = pick(["Geist Medium", "Inter Medium", "Segoe UI Semibold", ""])
        self.mono = pick(["Geist Mono", "Menlo", "Cascadia Mono", "Consolas"])
        self.mono_med = pick(["Geist Mono Medium", "Cascadia Mono SemiBold", self.mono])
        med = (self.sans_med,) if self.sans_med else (self.sans,)
        med_w = () if self.sans_med else ("bold",)
        self.body = (self.sans, pt(10))
        self.body_med = med + (pt(10),) + med_w
        self.head = med + (pt(15),) + med_w
        self.big = med + (pt(22),) + med_w
        self.mono9 = (self.mono, pt(9))
        self.mono8 = (self.mono, pt(8))
        self.mono10 = (self.mono, pt(10))
        self.mono9m = (self.mono_med, pt(9))
        self.mono9u = tkfont.Font(family=self.mono, size=pt(9), underline=True)


# ----------------------------------------------------------------------------- primitives
def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """A rounded rectangle drawn as a smoothed polygon - Tk has no native rounded shape."""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r,
           x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class Hairline(tk.Frame):
    def __init__(self, parent, key="hair", **kw):
        super().__init__(parent, height=1, bd=0, highlightthickness=0, **kw)
        self.key = key

    def restyle(self, c):
        self.configure(bg=c[self.key])


class Label(tk.Label):
    """A tk.Label that follows the theme: role is one of ink, ink2, mute, mute2, error, ok."""

    def __init__(self, parent, role="ink", bgkey="bg", **kw):
        super().__init__(parent, bd=0, highlightthickness=0, **kw)
        self.role, self.bgkey = role, bgkey

    def set_role(self, role):
        self.role = role

    def restyle(self, c):
        self.configure(bg=c[self.bgkey], fg=c[self.role])


class Panel(tk.Frame):
    def __init__(self, parent, bgkey="bg", **kw):
        super().__init__(parent, bd=0, highlightthickness=0, **kw)
        self.bgkey = bgkey

    def restyle(self, c):
        self.configure(bg=c[self.bgkey])


class TextLink(tk.Label):
    """Mono text link: hover underlines it and lifts it to ink."""

    def __init__(self, parent, text, command, fonts, role="ink2", **kw):
        super().__init__(parent, text=text, bd=0, highlightthickness=0, cursor="hand2",
                         font=fonts.mono9, **kw)
        self.fonts, self.role, self.command, self.c = fonts, role, command, None
        self.enabled = True
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", lambda e: self.command() if self.enabled else None)

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        if self.c:
            self.restyle(self.c)

    def _hover(self, on):
        if self.c and self.enabled:
            self.configure(font=self.fonts.mono9u if on else self.fonts.mono9,
                           fg=self.c["ink"] if on else self.c[self.role])

    def restyle(self, c):
        self.c = c
        self.configure(bg=c["bg"], fg=c[self.role] if self.enabled else c["mute2"])


class IconButton(tk.Canvas):
    """Window control: a small glyph that gets a hairline ring on hover."""

    def __init__(self, parent, kind, command, scale=1.0, **kw):
        d = int(26 * scale)
        super().__init__(parent, width=d, height=d, bd=0, highlightthickness=0, cursor="hand2", **kw)
        self.kind, self.command, self.s, self.d = kind, command, scale, d
        self.c, self.hover = None, False
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<Button-1>", lambda e: self.command())

    def _hov(self, on):
        self.hover = on
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c, d, s = self.c, self.d, self.s
        self.configure(bg=c["bg"])
        fg = c["ink"] if self.hover else c["mute"]
        if self.hover:
            self.create_oval(1, 1, d - 2, d - 2, outline=c["hair"], fill=c["bg2"])
        m, g = d / 2, 4 * s
        if self.kind == "close":
            self.create_line(m - g, m - g, m + g, m + g, fill=fg, width=1.2)
            self.create_line(m + g, m - g, m - g, m + g, fill=fg, width=1.2)
        else:
            self.create_line(m - g, m + 1, m + g, m + 1, fill=fg, width=1.2)

    def restyle(self, c):
        self.c = c
        self.draw()


class Pills(tk.Canvas):
    """Segmented mono pills. The active pill is ink on background."""

    def __init__(self, parent, fonts, options, value, command, scale=1.0, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(28 * scale), **kw)
        self.fonts, self.options, self.value, self.command, self.s = fonts, options, value, command, scale
        self.c, self.hover, self.boxes = None, None, []
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._click)
        self.configure(cursor="hand2")
        self._layout()

    def _layout(self):
        f = tkfont.Font(font=self.fonts.mono9)
        x, h = 0, int(24 * self.s)
        self.boxes = []
        for key, text in self.options:
            wd = f.measure(text) + int(22 * self.s)
            self.boxes.append((key, text, x, 2, x + wd, 2 + h))
            x += wd + int(6 * self.s)
        self.configure(width=max(x, 1))
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        for key, text, x1, y1, x2, y2 in self.boxes:
            active, hov = key == self.value, key == self.hover
            if active:
                round_rect(self, x1, y1, x2, y2, (y2 - y1) / 2, fill=c["ink"], outline=c["ink"])
                fg = c["paper_ink"]
            else:
                round_rect(self, x1, y1, x2, y2, (y2 - y1) / 2,
                           fill=c["bg2"] if hov else c["bg"], outline=c["mute"] if hov else c["hair"])
                fg = c["ink"] if hov else c["ink2"]
            self.create_text((x1 + x2) / 2, (y1 + y2) / 2 + 1, text=text, font=self.fonts.mono9, fill=fg)

    def _at(self, x, y):
        for key, text, x1, y1, x2, y2 in self.boxes:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _motion(self, e):
        self._set_hover(self._at(e.x, e.y))

    def _set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self.draw()

    def _click(self, e):
        key = self._at(e.x, e.y)
        if key is not None and key != self.value:
            self.value = key
            self.draw()
            self.command(key)

    def set(self, key):
        self.value = key
        self.draw()

    def restyle(self, c):
        self.c = c
        self.draw()


class DotToggle(tk.Canvas):
    """A dot (filled when on) and a lowercase mono word inside a hairline pill."""

    def __init__(self, parent, fonts, text, value, command, scale=1.0, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(28 * scale), **kw)
        self.fonts, self.text, self.value, self.command, self.s = fonts, text, value, command, scale
        self.c, self.hover = None, False
        f = tkfont.Font(font=self.fonts.mono9)
        self.w = f.measure(text) + int(38 * scale)
        self.configure(width=self.w, cursor="hand2")
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<Button-1>", self._click)

    def _hov(self, on):
        self.hover = on
        self.draw()

    def _click(self, e):
        self.value = not self.value
        self.draw()
        self.command(self.value)

    def set(self, value):
        self.value = bool(value)
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        h = int(24 * self.s)
        fg = c["ink"] if self.hover else c["ink2"]
        round_rect(self, 0, 2, self.w - 1, 2 + h, h / 2,
                   fill=c["bg2"] if self.hover else c["bg"], outline=c["mute"] if self.hover else c["hair"])
        d = int(8 * self.s)
        cx, cy = int(15 * self.s), 2 + h / 2
        if self.value:
            self.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2, fill=fg, outline=fg)
        else:
            self.create_oval(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2, fill="", outline=fg, width=1.5)
        self.create_text(cx + d / 2 + int(7 * self.s), cy + 1, text=self.text, anchor="w",
                         font=self.fonts.mono9, fill=fg)

    def restyle(self, c):
        self.c = c
        self.draw()


class Button(tk.Canvas):
    """A pill button. kind 'primary' fills with ink; 'ghost' is a hairline outline."""

    def __init__(self, parent, fonts, text, command, kind="ghost", scale=1.0, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(32 * scale), **kw)
        self.fonts, self.text, self.command, self.kind, self.s = fonts, text, command, kind, scale
        self.c, self.hover, self.enabled = None, False, True
        f = tkfont.Font(font=fonts.mono9)
        self.w = f.measure(text) + int(34 * scale)
        self.configure(width=self.w, cursor="hand2")
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<Button-1>", lambda e: self.command() if self.enabled else None)

    def set_text(self, text):
        self.text = text
        f = tkfont.Font(font=self.fonts.mono9)
        self.w = f.measure(text) + int(34 * self.s)
        self.configure(width=self.w)
        self.draw()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self.draw()

    def _hov(self, on):
        self.hover = on and self.enabled
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        h = int(28 * self.s)
        if not self.enabled:
            round_rect(self, 0, 2, self.w - 1, 2 + h, h / 2, fill=c["bg"], outline=c["hair"])
            fg = c["mute2"]
        elif self.kind == "primary":
            fill = c["ink2"] if self.hover else c["ink"]
            round_rect(self, 0, 2, self.w - 1, 2 + h, h / 2, fill=fill, outline=fill)
            fg = c["paper_ink"]
        else:
            round_rect(self, 0, 2, self.w - 1, 2 + h, h / 2,
                       fill=c["bg2"] if self.hover else c["bg"], outline=c["mute"] if self.hover else c["hair"])
            fg = c["ink"] if self.hover else c["ink2"]
        self.create_text(self.w / 2, 2 + h / 2 + 1, text=self.text, font=self.fonts.mono9, fill=fg)

    def restyle(self, c):
        self.c = c
        self.draw()


class HotkeyField(tk.Canvas):
    """Click it, then press a combination; it shows what it captured and hands back the string.

    Capture is done on the Tk widget (not a global hook), so it only ever reads keys while the
    field has focus, and Escape leaves the old value alone.
    """

    MODS = {"Control_L": "ctrl", "Control_R": "ctrl", "Shift_L": "shift", "Shift_R": "shift",
            "Alt_L": "alt", "Alt_R": "alt", "Super_L": "win", "Super_R": "win"}
    NAMED = {"space": "space", "Tab": "tab", "Return": "enter", "Delete": "delete", "Insert": "insert",
             "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown", "BackSpace": "backspace",
             "Up": "up", "Down": "down", "Left": "left", "Right": "right", "Print": "printscreen",
             "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]", "semicolon": ";",
             "apostrophe": "'", "comma": ",", "period": ".", "slash": "/", "backslash": "\\", "grave": "`"}

    def __init__(self, parent, fonts, value, command, scale=1.0, width=170, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(28 * scale),
                         width=int(width * scale), **kw)
        self.fonts, self.value, self.command, self.s = fonts, value, command, scale
        self.c, self.hover, self.capturing = None, False, False
        self._mods = set()
        self.configure(cursor="hand2")
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<Button-1>", lambda e: self.start_capture())
        self.bind("<KeyPress>", self._key_down)
        self.bind("<KeyRelease>", self._key_up)
        self.bind("<FocusOut>", lambda e: self.cancel())

    def start_capture(self):
        self.capturing = True
        self._mods = set()
        self.focus_set()
        self.draw()

    def cancel(self):
        if self.capturing:
            self.capturing = False
            self._mods = set()
            self.draw()

    def set(self, value):
        self.value = value
        self.draw()

    def _hov(self, on):
        self.hover = on
        self.draw()

    def _key_down(self, e):
        if not self.capturing:
            return
        name = e.keysym
        if name == "Escape":
            self.cancel()
            return "break"
        if name in self.MODS:
            self._mods.add(self.MODS[name])
            self.draw()
            return "break"
        key = self.NAMED.get(name)
        if key is None:
            if len(name) == 1 and (name.isalpha() or name.isdigit()):
                key = name.lower()
            elif name.lower().startswith("f") and name[1:].isdigit():
                key = name.lower()
            else:
                return "break"
        if not self._mods:                      # a bare key would swallow that key system-wide
            self.draw(warn="needs ctrl, alt or shift")
            return "break"
        order = [m for m in ("ctrl", "shift", "alt", "win") if m in self._mods]
        combo = "+".join(order + [key])
        self.capturing = False
        self._mods = set()
        self.value = combo
        self.draw()
        self.command(combo)
        return "break"

    def _key_up(self, e):
        if self.capturing and e.keysym in self.MODS:
            self._mods.discard(self.MODS[e.keysym])
            self.draw()
        return "break" if self.capturing else None

    def draw(self, warn=None):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        wd, h = self.winfo_reqwidth(), int(24 * self.s)
        if self.capturing:
            outline, fill = c["focus"], c["bg2"]
            live = "+".join([m for m in ("ctrl", "shift", "alt", "win") if m in self._mods])
            text = (live + "+…") if live else "press keys…"
            fg = c["ink"]
        else:
            outline = c["mute"] if self.hover else c["hair"]
            fill = c["bg2"] if self.hover else c["bg"]
            text = self.value or "unset"
            fg = c["ink"] if self.hover else c["ink2"]
        if warn:
            outline, text, fg = c["error"], warn, c["error"]
        round_rect(self, 0, 2, wd - 1, 2 + h, h / 2, fill=fill, outline=outline)
        self.create_text(wd / 2, 2 + h / 2 + 1, text=text, font=self.fonts.mono9, fill=fg)

    def restyle(self, c):
        self.c = c
        self.draw()


class ProgressLine(tk.Canvas):
    """Hairline track with an ink bar and a little diamond playhead."""

    def __init__(self, parent, scale=1.0, **kw):
        super().__init__(parent, bd=0, highlightthickness=0, height=int(14 * scale), **kw)
        self.s, self.c, self.frac = scale, None, None
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, frac):
        self.frac = frac
        self.draw()

    def draw(self):
        self.delete("all")
        if not self.c:
            return
        c = self.c
        self.configure(bg=c["bg"])
        wd, h = self.winfo_width(), self.winfo_height()
        y = h / 2
        self.create_line(0, y, wd, y, fill=c["hair"], width=1)
        if self.frac is None:
            return
        x = max(0, min(wd, wd * self.frac))
        self.create_line(0, y, x, y, fill=c["ink"], width=2)
        d = int(4 * self.s)
        self.create_polygon(x, y - d, x + d, y, x, y + d, x - d, y, fill=c["ink"], outline=c["ink"])

    def restyle(self, c):
        self.c = c
        self.draw()
