"""The macOS failure that looks exactly like success: a denied Screen Recording permission.

ScreenCaptureKit does not raise when the permission is missing. It keeps working and hands back
the desktop with every other application's window absent - a picture of an empty desktop, which
is indistinguishable from a picture of an empty desktop. This is the single most likely way the
Mac build ships broken, so the app has to *ask* rather than infer it from a capture that looked
fine, and the self-test has to say which it got.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat

if not plat.IS_MAC:
    print("not macOS - nothing to check")
    sys.exit(0)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail))


granted = plat.has_screen_recording()
check("the permission is queried, not guessed", isinstance(granted, bool),
      "granted" if granted else "DENIED")
check("there is a way to ask for it", callable(plat.request_screen_recording))
check("and a way to send the user to the switch", callable(plat.open_privacy_pane))

# the app must consult the permission before it ever opens a picker, and say so in the self-test
import inspect
from magcopy import main as mainmod
src = inspect.getsource(mainmod.main)
check("startup checks the permission", "has_screen_recording" in src)
check("startup asks for it", "request_screen_recording" in src)
check("a refusal is explained rather than ignored", "_permission_panel" in src)
check("the self-test reports it", "screen recording" in inspect.getsource(mainmod.selftest))

if granted:
    # With the permission granted, a capture of a region holding another application's window
    # must contain something. An all-one-colour frame is what a denial looks like.
    import numpy as np
    vx, vy, vw, vh = plat.virtual_screen()
    a = plat.grab_once(vx, vy, min(900, vw), min(600, vh))
    distinct = len(np.unique(a[:, :, :3].reshape(-1, 3), axis=0))
    check("a granted capture has real content in it", distinct > 50,
          "%d distinct colours" % distinct)
else:
    print("%-52s %s %s" % ("a granted capture has real content in it", "skip",
                           "permission is denied on this machine - that is what is being tested"))

print("\nSCREEN RECORDING PERMISSION", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
