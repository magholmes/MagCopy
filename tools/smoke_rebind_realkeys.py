"""Binding a shortcut with REAL keystrokes must not run the action - and must arm afterwards.

smoke_rebind drives the field with Tk's own synthetic events, which never reach RegisterHotKey,
so it cannot see the race this guards against. This one injects real keystrokes through the OS,
auto-repeat included, at the real settings window with the real hotkey manager underneath.
Windows only; it needs the window to genuinely hold the keyboard.
"""
import ctypes
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.name != "nt":
    print("not windows - nothing to inject\nREBIND REALKEYS OK")
    sys.exit(0)

from magcopy import plat, win
from magcopy.settings import SETTINGS_FILE

_saved = open(SETTINGS_FILE, "rb").read() if os.path.exists(SETTINGS_FILE) else None


def _restore():
    if _saved is None:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
    else:
        open(SETTINGS_FILE, "wb").write(_saved)


import atexit
atexit.register(_restore)

u32 = ctypes.windll.user32
VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "j": 0x4A}
KEYUP = 2
COMBO = "ctrl+alt+j"


def down(k): u32.keybd_event(VK[k], 0, 0, 0)
def up(k):   u32.keybd_event(VK[k], 0, KEYUP, 0)


ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-58s %s %s" % (name, "ok  " if good else "FAIL", detail))


win.set_dpi_aware()
from magcopy.theme import register_fonts
register_fonts()
root = tk.Tk()
try:
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception:
    pass
from magcopy.app import App

app = App(root)
# a running MagCopy owns ctrl+shift+a; that is fine, we never press it here
app.show_window()
root.update()
ran = []
app.start_screenshot = lambda: ran.append("shot")
app.start_gif = lambda: ran.append("gif")

f = app.field_shot
hwnd = plat.toplevel_hwnd(root)
have_focus = False
for _ in range(15):                  # a window from the previous test may still be going away
    win.allow_foreground()
    u32.SetForegroundWindow(hwnd)
    root.lift(); root.focus_force(); f.focus_force()
    for _ in range(10):
        root.update(); time.sleep(0.02)
    have_focus = u32.GetForegroundWindow() == hwnd
    if have_focus:
        break
if not have_focus:
    # This types a real combination at whatever is in front, so running it without the keyboard
    # is worse than not running it at all. Skipped out loud rather than failed or forced through.
    print("SKIPPED: could not take the keyboard, so no keys were injected")
    print("\nREBIND REALKEYS OK (skipped)")
    app.quit()
    sys.exit(0)
check("the test window holds the keyboard", have_focus)

before = app.settings["hotkey_shot"]
f.start_capture(); root.update()
check("hotkeys are off while capturing", app._hotkeys_held and not app.hotkeys._callbacks,
      "%d registered" % len(app.hotkeys._callbacks))


def pump(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update(); time.sleep(0.01)


# --- press the new combination for real, and hold it the way a person does
down("ctrl"); down("alt"); pump(0.05)
down("j")
for _ in range(15):                      # auto-repeat while held
    pump(0.03); down("j")
pump(0.35)                               # still held: nothing may fire in here
check("the field captured it", f.value == COMBO, repr(f.value))
check("still held: nothing armed yet", app._hotkeys_held is True and not app.hotkeys._callbacks,
      "%d registered" % len(app.hotkeys._callbacks))
check("still held: nothing ran", not ran, str(ran))
up("j"); up("alt"); up("ctrl")
pump(0.6)                                # let go; the deferred arm should run now
check("released: the new shortcut is armed", app._hotkeys_held is False and len(app.hotkeys._callbacks) >= 1,
      "%d registered, refused %s" % (len(app.hotkeys._callbacks), app.hotkeys._failed))
check("released: still nothing ran", not ran, str(ran))
check("the setting took", app.settings["hotkey_shot"] == COMBO, repr(app.settings["hotkey_shot"]))

# --- and a fresh press of it, now, must fire (otherwise the suspend simply never came back)
down("ctrl"); down("alt"); down("j"); pump(0.05); up("j"); up("alt"); up("ctrl")
pump(0.8)
check("a fresh press after arming runs the action", ran == ["shot"], str(ran))

app.quit()
print("\nREBIND REALKEYS", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
