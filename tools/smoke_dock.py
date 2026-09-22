"""The floating controller: off by default, draggable, opens on hover, and never self-captures.

The one that matters most is the last. The dock is topmost by construction, so if it is still on
screen when the picker freezes the display or a recording starts, it is in the picture - and the
person who clicked its own button is the one who gets it in their screenshot.
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat
from magcopy.settings import DEFAULTS, SETTINGS_FILE, validate

_saved = open(SETTINGS_FILE, "rb").read() if os.path.exists(SETTINGS_FILE) else None


def _restore():
    if _saved is None:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
    else:
        open(SETTINGS_FILE, "wb").write(_saved)


import atexit
atexit.register(_restore)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-56s %s %s" % (name, "ok  " if good else "FAIL", detail))


check("off by default", DEFAULTS["show_dock"] is False)
check("no remembered position by default",
      (DEFAULTS["dock_x"], DEFAULTS["dock_y"]) == (-1, -1))
v = validate(dict(DEFAULTS, show_dock="yes", dock_x="40", dock_y=None))
check("settings coerce to the right types",
      v["show_dock"] is True and v["dock_x"] == 40 and v["dock_y"] == -1,
      "%r %r %r" % (v["show_dock"], v["dock_x"], v["dock_y"]))

plat.set_dpi_aware()
from magcopy.theme import register_fonts

register_fonts()
root = tk.Tk()
try:
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception:
    pass
from magcopy.app import App

app = App(root)
app.hide_window()
root.update()

check("nothing floating until it is switched on", app.dock is None)

fired = []
app.start_screenshot = lambda: fired.append("shot")
app.start_gif = lambda: fired.append("gif")

app._set_dock(True)
for _ in range(20):
    root.update()
d = app.dock
check("switching it on puts one on screen", d is not None and d.top.winfo_viewable())
check("it is topmost", bool(d.top.attributes("-topmost")))
check("it does not take focus when clicked", root.focus_displayof() is not app.dock.top)

cw, ch = d._size()
check("it starts closed and small", d.open == 0.0 and cw < 70, "%dx%d" % (cw, ch))

# --- hover opens it
d._enter()
for _ in range(60):
    root.update()
    root.after(8)
    root.update()
    if d.open >= 1.0:
        break
ow, oh = d._size()
check("hovering opens it", d.open == 1.0 and ow > cw + 40, "%dx%d -> %dx%d" % (cw, ch, ow, oh))
check("open, it offers two buttons", [z[0] for z in d._zones()] == ["shot", "gif"],
      str([z[0] for z in d._zones()]))

# --- the buttons do the two things, and nothing else does
sx = int((d._zones()[0][1] + d._zones()[0][3]) / 2)
sy = int(oh / 2)
gx = int((d._zones()[1][1] + d._zones()[1][3]) / 2)


class Ev:
    def __init__(self, x, y, xr=0, yr=0):
        self.x, self.y, self.x_root, self.y_root = x, y, xr, yr


def pump(n=25):
    """The dock posts to the app's queue rather than calling straight through, so turn the loop."""
    for _ in range(n):
        root.update()
        root.after(12)
        root.update()


d._press(Ev(sx, sy, 500, 500)); d._release(Ev(sx, sy, 500, 500)); pump()
check("the frame button asks for a screenshot", fired == ["shot"], str(fired))
d._press(Ev(gx, sy, 500, 500)); d._release(Ev(gx, sy, 500, 500)); pump()
check("the red dot asks for a recording", fired == ["shot", "gif"], str(fired))
fired.clear()
d._press(Ev(4, sy, 500, 500)); d._release(Ev(4, sy, 500, 500)); pump()
check("the grip is not a button", not fired, str(fired))

# --- dragging moves it and is remembered, and a drag is never a click
x0, y0 = d.top.winfo_x(), d.top.winfo_y()
d._press(Ev(sx, sy, 800, 800))
d._move(Ev(sx, sy, 800 - 120, 800 - 90))
d._release(Ev(sx, sy, 800 - 120, 800 - 90))
pump(6)
x1, y1 = d.top.winfo_x(), d.top.winfo_y()
check("dragging moves it", (x1, y1) != (x0, y0), "%s -> %s" % ((x0, y0), (x1, y1)))
check("a drag does not press the button under it", not fired, str(fired))
check("where it sits is remembered",
      (app.settings["dock_x"], app.settings["dock_y"]) == (x1, y1),
      "%s vs %s" % ((app.settings["dock_x"], app.settings["dock_y"]), (x1, y1)))

# --- it stays on screen even when told to go somewhere absurd
app.settings["dock_x"], app.settings["dock_y"] = 99999, 99999
d._place(); root.update()
vx, vy, vw, vh = plat.virtual_screen()
wx, wy = d.top.winfo_x(), d.top.winfo_y()
check("a position off the end of the desk is clamped back",
      vx <= wx <= vx + vw and vy <= wy <= vy + vh, "at %d,%d" % (wx, wy))

# --- and the part that would otherwise end up in someone's screenshot
was = app._hide_for_capture()
root.update()
check("a capture takes it off screen", not d.top.winfo_viewable())
app._after_capture(was)
for _ in range(10):
    root.update()
check("it comes back afterwards", d.top.winfo_viewable())

# a recording hides it at the picker and restores only when the recording ends
app._hide_for_capture(); root.update()
check("it is down while a recording is being framed", not d.top.winfo_viewable())
app._record_done(type("R", (), {"cancelled": True, "error": None})())
for _ in range(10):
    root.update()
check("and back when the recording finishes", d.top.winfo_viewable())

# --- theme
app.theme.set("ember") if hasattr(app.theme, "set") else None
root.update()
check("it follows the theme", d in app.theme.listeners)

app._set_dock(False)
root.update()
check("switching it off removes it", app.dock is None)
check("and it is not left listening to the theme",
      not any(type(o).__name__ == "Dock" for o in app.theme.listeners))

app.quit()
print("\nDOCK", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
