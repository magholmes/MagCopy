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
from magcopy import win

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-50s %s %s" % (name, "ok  " if good else "FAIL", detail))


def tap_escape():
    """Synthesise a real Escape press; RegisterHotKey only sees input at this level."""
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)      # KEYEVENTF_KEYUP


fired = threading.Event()
hk = win.TransientHotkey("esc", fired.set)
check("escape registers as a global hotkey", hk.start() is True)

tap_escape()
check("a real escape press fires it", fired.wait(2))

hk.stop()

# and it must let go afterwards, or escape stays broken everywhere until the app exits
again = win.TransientHotkey("esc", lambda: None)
check("it releases the key when it stops", again.start() is True)
again.stop()

# a second one cannot register while the first holds it - that is the proof it was really held
held = win.TransientHotkey("esc", lambda: None)
held.start()
clash = win.TransientHotkey("esc", lambda: None)
check("while held, the key is genuinely taken", clash.start() is False)
held.stop()
clash.stop()

# nonsense never leaves a thread behind
bad = win.TransientHotkey("not-a-key", lambda: None)
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
