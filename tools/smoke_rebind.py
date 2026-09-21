"""Binding a new shortcut must not run the thing you are binding it to.

The bug: the field captured Ctrl+Space, the app registered it immediately, and the user was still
holding both keys. RegisterHotKey matches a key going down, a held key goes down again on every
auto-repeat, so about a quarter of a second later the screenshot ran - which hid the window, which
took focus off the field, which cancelled the capture. The shortcut could never be set.

So: the live shortcuts come off while a field is capturing, and nothing is armed again until the
keyboard is clear.
"""
import ctypes
import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, win
from magcopy.settings import SETTINGS_FILE

# This drives the real settings UI, which saves. Keep the file exactly as it was found - an
# earlier test in this suite once left the user's own autostart entry deleted, and a shortcut is
# the same kind of thing: something they chose, that this has no business changing.
_saved = None
if os.path.exists(SETTINGS_FILE):
    with open(SETTINGS_FILE, "rb") as fh:
        _saved = fh.read()


def _restore():
    if _saved is None:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
        return
    with open(SETTINGS_FILE, "wb") as fh:
        fh.write(_saved)


import atexit

atexit.register(_restore)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-56s %s %s" % (name, "ok  " if good else "FAIL", detail))


# ---- the mechanism itself, at the Win32 level
if os.name == "nt":
    u32 = ctypes.windll.user32
    VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "f9": 0x78}
    COMBO = "ctrl+alt+shift+f9"          # obscure enough to be free, and types nothing

    fired = threading.Event()
    hk = win.TransientHotkey(COMBO, fired.set)
    for k in ("ctrl", "alt", "shift"):
        u32.keybd_event(VK[k], 0, 0, 0)
    u32.keybd_event(VK["f9"], 0, 0, 0)
    time.sleep(0.05)
    registered = hk.start()
    check("a shortcut still held reads as held", plat.combo_down(COMBO) is True)
    for _ in range(10):                  # what a real keyboard does while a key stays down
        u32.keybd_event(VK["f9"], 0, 0, 0)
        time.sleep(0.03)
    repeat_fired = fired.is_set()
    u32.keybd_event(VK["f9"], 0, 2, 0)
    for k in ("shift", "alt", "ctrl"):
        u32.keybd_event(VK[k], 0, 2, 0)
    hk.stop()
    # Not an assertion. This demonstrates the OS behaviour being guarded against, and whether a
    # synthetic repeat wins the race is a coin toss - on real hardware, holding the keys for a
    # quarter of a second does it every time. What must hold is the two checks below: that a held
    # combination reads as held, and that the app refuses to arm while it does.
    print("%-56s %s" % ("   (mid-hold arming fired on repeat this run)",
                        "yes" if registered and repeat_fired else "not this time"))
    # The release is posted, not applied, so give it a moment rather than sampling once.
    for _ in range(40):
        if not plat.combo_down(COMBO):
            break
        time.sleep(0.05)
    check("released keys read as clear", plat.combo_down(COMBO) is False)
else:
    check("combo_down answers without raising", plat.combo_down("ctrl+space") in (True, False))

# ---- and the app does not do that
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
app.show_window()
root.update()

ran = []
app.start_screenshot = lambda: ran.append("shot")
app.start_gif = lambda: ran.append("gif")

f = app.field_shot
before = app.settings["hotkey_shot"]
registered_during = {}

f.start_capture()
root.update()
check("capture takes the live shortcuts off", app._hotkeys_held is True)
registered_during["count"] = len(app.hotkeys._callbacks)
check("nothing is registered while capturing", registered_during["count"] == 0,
      "%d still registered" % registered_during["count"])

for ev in ("<KeyPress-Control_L>", "<KeyPress-Alt_L>", "<KeyPress-j>"):
    f.event_generate(ev)
    root.update()

check("the field captured the combination", f.value == "ctrl+alt+j", repr(f.value))
check("nothing ran while it was being set", not ran, str(ran))
# let the deferred arm run; the synthetic keys are not really down, so it should be quick
for _ in range(40):
    root.update()
    root.after(25)
    root.update()
    if not app._hotkeys_held:
        break
check("it arms once the keyboard is clear", app._hotkeys_held is False)
check("still nothing ran", not ran, str(ran))

settled = app.settings["hotkey_shot"]
check("the setting took, or rolled back cleanly",
      settled in ("ctrl+alt+j", before), repr(settled))
check("exactly the shortcuts are registered", len(app.hotkeys._callbacks) + len(app.hotkeys._failed) == 2,
      "%d registered, %d refused" % (len(app.hotkeys._callbacks), len(app.hotkeys._failed)))

# ---- cancelling has to put them back too
f.start_capture()
root.update()
check("cancelling also suspends", app._hotkeys_held is True)
f.cancel()
for _ in range(40):
    root.update()
    root.after(25)
    root.update()
    if not app._hotkeys_held:
        break
check("and restores after a cancel", app._hotkeys_held is False)

app.quit()
print("\nREBIND", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
