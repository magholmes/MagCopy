"""The region picker must always be escapable, even with no keyboard focus at all.

This is the test for the worst bug this app has had. The picker covers every screen, and on macOS
a borderless window cannot become the key window - AppKit refuses - so a Tk binding on <Escape>
can never fire. Combined with a global grab, that turned the picker into a locked session with no
way back: the reporter had to restart the machine.

So the escape hatch is checked here the way it is actually reached - a global hotkey delivered
while the picker's own nested event loop is running - and the dead-man timer is checked separately.
Both are proved by opening a real picker and getting out of it, not by reading the code.

The whole file runs under a watchdog, because a regression here means a covered screen.
"""
import os, signal, sys, threading, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The root comes first, before anything touches Carbon or AppKit - see main.py's _new_root.
root = tk.Tk(); root.withdraw(); root.update()

from magcopy import overlay, plat
from magcopy.theme import Theme, Fonts, register_fonts


def watchdog():
    time.sleep(40)
    print("\nWATCHDOG: the picker never closed - killing, and that is a FAIL", flush=True)
    os.kill(os.getpid(), signal.SIGKILL)


threading.Thread(target=watchdog, daemon=True).start()

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail), flush=True)


def post_hotkey(entry):
    """The event Carbon would post for a real keypress, put straight on the main queue."""
    import ctypes, ctypes.util
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


register_fonts()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
overlay.prewarm(root)

if not plat.IS_MAC:
    print("not macOS - the picker takes focus normally and the grab is what makes it modal")
    sys.exit(0)

# ---------------------------------------------------------------- 1. the global Escape hotkey
sel = overlay.RegionSelector(root, theme, fonts)
state = {}


def drive():
    state["grab"] = root.call("grab", "current") or ""
    state["hotkey"] = sel._escape_hotkey is not None and sel._escape_hotkey.ok
    hw = sel._top_hwnd
    state["level"] = hw.level() if hw else None
    if sel._escape_hotkey is not None:
        state["posted"] = post_hotkey(sel._escape_hotkey._entry)


root.after(400, drive)
t0 = time.perf_counter()
rect = sel.run()                     # blocks in wait_window until something cancels it
elapsed = time.perf_counter() - t0

check("the picker does not take a global grab", "global" not in str(state.get("grab", "")),
      "grab was %r" % state.get("grab"))
check("it sits above everything (real window level)", (state.get("level") or 0) >= 1000,
      "level %s" % state.get("level"))
check("a global Escape hotkey is held while it is up", state.get("hotkey") is True)
check("the hotkey event was accepted", state.get("posted") is True)
check("...and it closed the picker", rect is None, "%.1fs" % elapsed)
check("Escape was handed back afterwards", sel._escape_hotkey is None)
again = plat.TransientHotkey("esc", lambda: None)
check("so the rest of the machine can have it", again.start() is True)
again.stop()

# ---------------------------------------------------------------- 2. the dead-man timer
sel2 = overlay.RegionSelector(root, theme, fonts)
sel2.IDLE_LIMIT_MS = 1200            # the real one is 45s; no point waiting that long here
if sel2._escape_hotkey is None:
    pass
t0 = time.perf_counter()
rect2 = sel2.run()
idle_elapsed = time.perf_counter() - t0
check("with no activity at all it cancels itself", rect2 is None, "%.1fs" % idle_elapsed)
check("and it does so promptly", idle_elapsed < 6.0, "%.1fs" % idle_elapsed)

root.destroy()
print("\nESCAPE HATCH", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
