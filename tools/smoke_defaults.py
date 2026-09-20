"""First-run behaviour: the defaults that should apply once, and the registry that wins after."""
import os, sys, winreg
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import settings as S

ok = True
def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-34s %s %s" % (name, "ok  " if good else "FAIL", detail))

check("record_fps default", S.DEFAULTS["record_fps"] == 50, str(S.DEFAULTS["record_fps"]))
check("start_with_windows default", S.DEFAULTS["start_with_windows"] is True)
check("show_recording_frame default", S.DEFAULTS["show_recording_frame"] is True)
check("50 fps survives validate", S.validate(dict(S.DEFAULTS))["record_fps"] == 50)

# Snapshot the RAW registry value, not whether get_autostart() likes it. This test toggles a
# real user setting, and restoring it must not depend on how the app happens to interpret it -
# an earlier version of this file keyed the restore off get_autostart() and, once that became
# path-aware, silently deleted the user's entry instead of putting it back.
prev = None
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, S.RUN_KEY) as k:
        prev = winreg.QueryValueEx(k, S.APP_NAME)[0]
except FileNotFoundError:
    prev = None
try:
    S.set_autostart(False)
    check("autostart can be removed", not S.get_autostart())
    S.set_autostart(True)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, S.RUN_KEY) as k:
        cmd = winreg.QueryValueEx(k, S.APP_NAME)[0]
    check("autostart registered", S.get_autostart(), cmd)
    check("autostart starts hidden", "--hidden" in cmd)
finally:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, S.RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if prev is None:
            try:
                winreg.DeleteValue(k, S.APP_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, S.APP_NAME, 0, winreg.REG_SZ, prev)
    print("   (registry restored exactly: %s)" % (prev or "no entry"))
print("\nDEFAULTS", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
