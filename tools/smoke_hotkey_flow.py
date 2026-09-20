"""A shortcut must reach the thing it is a shortcut for - in the real app, not in a fixture.

Every earlier test stopped one step short. Registration was checked (it succeeded), and the
picker was driven by calling its methods directly, so nothing ever followed a keystroke all the
way through. The gap hid a one-word bug - the dispatcher read the hotkey id under the wrong
parameter name - that left every shortcut silently doing nothing while reporting success.

So this builds the actual App, takes the hotkeys it actually registered, posts the event Carbon
would have posted, and checks that the action at the far end ran. No Accessibility needed, which
matters: the check that needs a permission is the check that gets skipped.
"""
import ctypes, ctypes.util, os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MAGCOPY_NO_AUTOSTART", "1")
os.environ.setdefault("MAGCOPY_INSTANCE_NAME", "MagCopy-hotkey-flow")

root = tk.Tk(); root.withdraw(); root.update()          # before Carbon - see main.py's _new_root
from magcopy import plat
from magcopy.theme import register_fonts

if not plat.IS_MAC:
    print("not macOS - RegisterHotKey delivers through its own message loop there")
    sys.exit(0)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail), flush=True)


def post_hotkey(hk_id):
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
    ident = EventHotKeyID(0x4D616743, hk_id)
    if carbon.SetEventParameter(ev, 0x2D2D2D2D, 0x686B6964,
                                ctypes.sizeof(ident), ctypes.byref(ident)) != 0:
        return False
    return carbon.PostEventToQueue(carbon.GetMainEventQueue(), ev, 1) == 0


register_fonts()
from magcopy.app import App
app = App(root)

check("both shortcuts registered", len(app.hotkeys._entries) == 2,
      "%d of 2, failed: %s" % (len(app.hotkeys._entries), app.hotkeys._failed))
check("the stored shortcuts are usable ones",
      all("+" in app.settings[k] for k in ("hotkey_shot", "hotkey_gif")),
      "%s / %s" % (app.settings["hotkey_shot"], app.settings["hotkey_gif"]))

# Stand in for the two actions: the point is that the keystroke arrives, not what it then opens.
ran = []
app.start_screenshot = lambda: ran.append("shot")
app.start_gif = lambda: ran.append("gif")

for label, index, expect in (("screenshot", 0, "shot"), ("gif", 1, "gif")):
    ran.clear()
    entry = app.hotkeys._entries[index]
    check("%s: the event is accepted" % label, post_hotkey(entry[1]))
    deadline = time.time() + 3
    while not ran and time.time() < deadline:
        root.update(); time.sleep(0.02)      # Tk's loop drains Carbon, then _pump drains the queue
    check("%s: ...and the action actually runs" % label, ran == [expect],
          "" if ran == [expect] else "registered but never delivered - the shortcut would do nothing")

app.hotkeys.stop()
app.tray.stop()
root.destroy()
print("\nHOTKEY FLOW", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
