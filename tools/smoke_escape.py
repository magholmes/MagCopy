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

# The root comes first, before anything touches Carbon or AppKit, for the same reason main.py
# creates it first: Tk installs its own NSApplication subclass, NSApplication is a singleton, and
# Carbon's GetApplicationEventTarget will bring a plain one into being if it gets there first.
# Tk then dies on its first colour lookup. Creating it here also matches how the app is ordered.
import tkinter as tk
_ROOT = tk.Tk()
_ROOT.withdraw()
_ROOT.update()


def _pump_once():
    """Turn the main run loop over once, so a Carbon hotkey handler gets a chance to fire."""
    _ROOT.update()
    time.sleep(0.02)


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-50s %s %s" % (name, "ok  " if good else "FAIL", detail))


def deliver_hotkey(entry):
    """Put a real hotkey event through the app's own dispatch path and see if it comes out.

    Registering a hotkey and *receiving* one are different claims, and only the first can be
    checked without pressing a key. This checks the second by building the event Carbon would
    have built - class 'keyb', kind kEventHotKeyPressed, carrying an EventHotKeyID as its direct
    object - and posting it to the main queue, which Tk drains. It needs no Accessibility, so it
    runs everywhere rather than skipping itself on the machines that matter.

    The bug it exists for: the dispatcher read the parameter under its *type* code rather than
    kEventParamDirectObject. GetEventParameter then failed, the id came back 0, no route matched,
    and every shortcut was silently swallowed - while registration still reported success.
    """
    import ctypes.util
    carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))

    class EventHotKeyID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    carbon.CreateEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                   ctypes.c_double, ctypes.c_uint32,
                                   ctypes.POINTER(ctypes.c_void_p)]
    carbon.CreateEvent.restype = ctypes.c_int32
    carbon.SetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_uint32, ctypes.c_void_p]
    carbon.SetEventParameter.restype = ctypes.c_int32
    carbon.PostEventToQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint16]
    carbon.PostEventToQueue.restype = ctypes.c_int32
    carbon.GetMainEventQueue.restype = ctypes.c_void_p

    ev = ctypes.c_void_p()
    if carbon.CreateEvent(None, 0x6B657962, 5, 0.0, 0, ctypes.byref(ev)) != 0:
        return False
    ident = EventHotKeyID(0x4D616743, entry[1])
    if carbon.SetEventParameter(ev, 0x2D2D2D2D, 0x686B6964,
                                ctypes.sizeof(ident), ctypes.byref(ident)) != 0:
        return False
    return carbon.PostEventToQueue(carbon.GetMainEventQueue(), ev, 1) == 0


def tap_escape():
    """Synthesise a real Escape press; a registered hotkey only sees input at this level."""
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)      # KEYEVENTF_KEYUP


fired = threading.Event()
hk = plat.TransientHotkey("esc", fired.set)
check("escape registers as a global hotkey", hk.start() is True)

if plat.IS_MAC and hk._entry:
    posted = deliver_hotkey(hk._entry)
    check("a hotkey event can be put through the dispatcher", posted)
    deadline = time.time() + 3            # Tk's loop is what drains the Carbon queue
    while not fired.is_set() and time.time() < deadline:
        _pump_once()
    check("...and the callback actually runs", fired.is_set(),
          "" if fired.is_set() else "registered but never delivered - shortcuts would do nothing")
elif not plat.IS_MAC:
    tap_escape()
    check("a real escape press fires it", fired.wait(2))

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
