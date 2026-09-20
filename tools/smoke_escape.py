"""Escape must stop a running recording, from anywhere.

While recording, the focused window belongs to whatever is being recorded, so this has to be a
real global hotkey rather than a Tk binding. The test registers it the way the app does and
delivers the key through the OS, which is the only way to prove the claim.
"""
import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat

ok = True
_ROOT = []


def _pump_once():
    """Turn the main run loop over once, so a Carbon hotkey handler gets a chance to fire."""
    import tkinter as tk
    if not _ROOT:
        r = tk.Tk()
        r.withdraw()
        _ROOT.append(r)
    _ROOT[0].update()
    time.sleep(0.02)


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-50s %s %s" % (name, "ok  " if good else "FAIL", detail))


def can_synthesise():
    """Posting a key event into the system needs Accessibility; registering a hotkey does not.

    Worth being exact about, because it is the one place the two come apart: MagCopy itself
    never needs Accessibility - Carbon hotkeys work without it - but a test that delivers a
    keystroke on the user's behalf does.
    """
    if not plat.IS_MAC:
        return True
    return plat.has_accessibility()


def tap_escape():
    """Synthesise a real Escape press; a registered hotkey only sees input at this level."""
    if plat.IS_MAC:
        import Quartz
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(src, 53, down)     # 53 = kVK_Escape
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        return
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)      # KEYEVENTF_KEYUP


fired = threading.Event()
hk = plat.TransientHotkey("esc", fired.set)
check("escape registers as a global hotkey", hk.start() is True)

if can_synthesise():
    tap_escape()
    if plat.IS_MAC:
        # the Carbon handler is dispatched by the main run loop, which nothing is pumping here
        deadline = time.time() + 2
        while not fired.is_set() and time.time() < deadline:
            _pump_once()
    check("a real escape press fires it", fired.wait(2))
else:
    print("%-50s %s %s" % ("a real escape press fires it", "skip",
                           "needs Accessibility, which MagCopy itself does not"))

hk.stop()

# and it must let go afterwards, or escape stays broken everywhere until the app exits
again = plat.TransientHotkey("esc", lambda: None)
check("it releases the key when it stops", again.start() is True)
again.stop()

# a second one cannot register while the first holds it - that is the proof it was really held
held = plat.TransientHotkey("esc", lambda: None)
held.start()
clash = plat.TransientHotkey("esc", lambda: None)
check("while held, the key is genuinely taken", clash.start() is False)
held.stop()
clash.stop()

# nonsense never leaves a thread behind
bad = plat.TransientHotkey("not-a-key", lambda: None)
check("an unparseable combo just fails", bad.start() is False)
bad.stop()

# --- the app wires it to the recording and drops it afterwards
import inspect

from magcopy import app as appmod

src = inspect.getsource(appmod.App._begin_recording)
check("recording starts the escape hotkey",
      "TransientHotkey" in src and "stop_recording" in src)
check("it is posted to the main loop, not called on the hotkey thread", "self.post" in src)
done = inspect.getsource(appmod.App._record_done)
check("finishing a recording releases it", "_release_record_esc" in done)
check("quitting releases it", "_release_record_esc" in inspect.getsource(appmod.App.quit))

print("\nESCAPE", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
