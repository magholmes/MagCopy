"""Settings file, app paths, start-at-login and the crash/error log.

Settings live next to the script when running from source, and in the platform's own place when
frozen - %APPDATA%\\MagCopy on Windows, ~/Library/Application Support/MagCopy on macOS - so a
packaged copy never tries to write inside Program Files or an app bundle.
"""
import datetime
import json
import os
import sys
import traceback

IS_MAC = sys.platform == "darwin"
APP_NAME = "MagCopy"
APP_VERSION = "1.3.2"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN
           else os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)


def _settings_dir():
    if not FROZEN:
        return APP_DIR
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", APP_DIR), APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP_NAME)
    return os.path.join(os.path.expanduser("~/.config"), APP_NAME.lower())


SETTINGS_DIR = _settings_dir()
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def _known_pictures():
    """Ask Windows where Pictures actually is.

    Guessing %USERPROFILE%\\Pictures is wrong on any machine where OneDrive has redirected the
    folder, which is most of them - captures then land in the user's root instead.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        FOLDERID_Pictures = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"
        guid = ctypes.create_unicode_buffer(FOLDERID_Pictures)
        iid = (ctypes.c_byte * 16)()
        if ctypes.windll.ole32.IIDFromString(guid, ctypes.byref(iid)) != 0:
            return None
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(iid), 0, None,
                                                      ctypes.byref(out)) != 0:
            return None
        path = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path if path and os.path.isdir(path) else None
    except Exception:
        return None


def default_save_dir():
    if IS_MAC:
        # ~/Pictures is a fixed location on macOS - there is no redirection to ask about, the way
        # OneDrive moves Pictures on Windows - so this needs no equivalent of SHGetKnownFolderPath
        pics = os.path.expanduser("~/Pictures")
        if os.path.isdir(pics):
            return os.path.join(pics, APP_NAME)
        return os.path.join(os.path.expanduser("~"), APP_NAME)
    pics = _known_pictures()
    if pics:
        return os.path.join(pics, APP_NAME)
    for env in ("USERPROFILE", "HOME"):
        base = os.environ.get(env)
        if base:
            for cand in (os.path.join(base, "OneDrive", "Pictures"), os.path.join(base, "Pictures")):
                if os.path.isdir(cand):
                    return os.path.join(cand, APP_NAME)
            return os.path.join(base, APP_NAME)
    return os.path.join(APP_DIR, "captures")


DEFAULTS = dict(
    hotkey_shot="ctrl+shift+a",          # region screenshot -> clipboard, no preview
    hotkey_gif="ctrl+shift+s",           # region recording -> editor -> optimised gif
    theme="dusk",
    ui_scale=1.0,
    save_dir="",                          # "" means default_save_dir()
    save_screenshots=False,               # the screenshot always goes to the clipboard; this also writes a file
    save_gifs=True,
    copy_gif_to_clipboard=True,           # put the finished gif on the clipboard as a file
    open_folder_after_gif=True,           # show the finished gif in its folder
    max_seconds=20,
    record_fps=50,                        # 50 and 25 divide into exact gif delays; 30 does not
    capture_cursor=True,
    aspect_ratio="free",                  # "free", or "w:h" - constrains the selection while dragging
    size_limit_mb=10.0,                   # discord's free-tier ceiling
    target_headroom=0.985,                # aim just under the limit, never at it
    play_sound=False,
    show_recording_frame=True,
    start_with_windows=True,              # applied on first run only; after that the system wins
                                          # (the Run key on Windows, a LaunchAgent on macOS - the
                                          # settings key keeps its name so existing files still load)
)


def log_error(where, text):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(os.path.join(SETTINGS_DIR, "errors.log"), "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n%s\n\n" % (datetime.datetime.now().isoformat(timespec="seconds"), where, text))
    except Exception:
        pass


def log_exc(where):
    log_error(where, traceback.format_exc())


def setup_crash_logging():
    """The window has no console, so an unhandled crash has to leave a trace on disk."""
    try:
        import faulthandler
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        fh = open(os.path.join(SETTINGS_DIR, "crash.log"), "a", encoding="utf-8")
        faulthandler.enable(fh)
        return fh                                  # kept open for the process lifetime on purpose
    except Exception:
        return None


def is_first_run():
    """True when no settings file exists yet, so first-run defaults may still be applied."""
    return not os.path.exists(SETTINGS_FILE)


def load():
    s = dict(DEFAULTS)
    raw = {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            raw = {k: v for k, v in json.load(fh).items() if k in DEFAULTS}
    except Exception:
        pass
    s.update(raw)
    out = validate(s)
    # A repaired value has to be written back, or the file and the running app disagree for as
    # long as the file exists: the window would show the shortcut it is really using while
    # settings.json went on claiming the unusable one it was corrected from.
    if raw and any(raw.get(k) != out.get(k) for k in raw):
        save(out)
    return out


ASPECT_RATIOS = [("free", "free"), ("1:1", "1:1"), ("4:5", "4:5"), ("5:4", "5:4"),
                 ("4:3", "4:3"), ("16:9", "16:9")]


def aspect_value(name):
    """"4:5" -> 0.8; "free" or anything unparseable -> None."""
    try:
        w, h = (float(v) for v in str(name).split(":"))
        return w / h if w > 0 and h > 0 else None
    except Exception:
        return None


def usable_hotkey(text):
    """A global shortcut needs ctrl, alt or cmd/win - shift on its own is not enough.

    The rule exists for the same reason a bare key is refused: the combination is taken from every
    other application for as long as MagCopy runs. "shift+a" does not read as drastic, and it means
    capital A stops working everywhere, which is a hard thing to connect back to a screenshot tool.
    """
    from . import plat
    if not plat.parse_hotkey(text):
        return False
    parts = {p.strip().lower() for p in str(text).split("+")}
    return bool(parts & {"ctrl", "control", "alt", "option", "cmd", "command", "win", "super"})


def validate(s):
    from .theme import THEMES
    for key in ("hotkey_shot", "hotkey_gif"):
        if not usable_hotkey(s.get(key)):
            s[key] = DEFAULTS[key]
    if s.get("aspect_ratio") not in [k for k, _ in ASPECT_RATIOS]:
        s["aspect_ratio"] = "free"
    if s.get("theme") not in THEMES:
        s["theme"] = DEFAULTS["theme"]
    try:
        s["ui_scale"] = float(s.get("ui_scale", 1.0))
    except Exception:
        s["ui_scale"] = 1.0
    if s["ui_scale"] not in (0.85, 1.0, 1.2, 1.45):
        s["ui_scale"] = 1.0
    if s.get("max_seconds") not in (10, 20, 30, 60):
        s["max_seconds"] = max(1, min(60, int(s.get("max_seconds", 20) or 20)))
    # snap to a rate that divides 100 exactly, so gif delays stay true to real time
    try:
        want = int(s.get("record_fps", 25) or 25)
    except Exception:
        want = 25
    s["record_fps"] = min((50, 25, 20), key=lambda f: abs(f - want))
    try:
        s["size_limit_mb"] = max(0.5, min(500.0, float(s.get("size_limit_mb", 10.0))))
    except Exception:
        s["size_limit_mb"] = 10.0
    for key in ("save_screenshots", "save_gifs", "copy_gif_to_clipboard", "open_folder_after_gif",
                "capture_cursor", "play_sound", "show_recording_frame", "start_with_windows"):
        s[key] = bool(s.get(key, DEFAULTS[key]))
    return s


def save(s):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({k: s[k] for k in DEFAULTS if k in s}, fh, indent=1)
    except Exception:
        log_exc("save settings")


def save_dir(s):
    d = (s.get("save_dir") or "").strip() or default_save_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = default_save_dir()
        os.makedirs(d, exist_ok=True)
    return d


def capture_path(directory, ext):
    """A short, date-named file in `directory`: 2026-09-20.gif, then -2, -3 and so on.

    The name people actually read is the date; a full timestamp made every capture a wall of
    digits that all looked alike. The counter only appears when it has to.
    """
    base = datetime.date.today().isoformat()
    path = os.path.join(directory, "%s.%s" % (base, ext))
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, "%s-%d.%s" % (base, n, ext))
        n += 1
    return path


# ----------------------------------------------------------------------------- run at startup
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_LABEL = "com.magholmes.magcopy"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LAUNCH_LABEL)


def _mac_launch_args():
    """The command a LaunchAgent should run to start this copy, hidden."""
    if FROZEN:
        # inside a bundle sys.executable is Contents/MacOS/MagCopy, which is the thing to launch
        return [sys.executable, "--hidden"]
    return [sys.executable, os.path.join(APP_DIR, "magcopy.pyw"), "--hidden"]


def _mac_agent_args():
    """The ProgramArguments currently in the LaunchAgent, or None if there is no agent."""
    if not os.path.exists(LAUNCH_AGENT):
        return None
    try:
        import plistlib
        with open(LAUNCH_AGENT, "rb") as fh:
            return list(plistlib.load(fh).get("ProgramArguments") or [])
    except Exception:
        return None


def _mac_get_autostart():
    args = _mac_agent_args()
    return bool(args) and os.path.normcase(args[0]) == os.path.normcase(_mac_launch_args()[0])


def _mac_set_autostart(on):
    """Write or remove the LaunchAgent, and tell launchd about it either way.

    SMAppService.mainApp.register() is the modern call and is bundle-only: it raises outright when
    the process is a loose python running a script, which is exactly how MagCopy runs from source.
    A LaunchAgent works in both shapes, and is a file the user can read and delete.
    """
    import plistlib
    import subprocess
    try:
        if on:
            os.makedirs(os.path.dirname(LAUNCH_AGENT), exist_ok=True)
            with open(LAUNCH_AGENT, "wb") as fh:
                plistlib.dump({
                    "Label": LAUNCH_LABEL,
                    "ProgramArguments": _mac_launch_args(),
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                }, fh)
        elif os.path.exists(LAUNCH_AGENT):
            os.remove(LAUNCH_AGENT)
        uid = os.getuid()
        # bootout first even when enabling: launchd keeps the old definition otherwise, so a
        # moved or rebuilt copy would go on starting the one that is no longer there
        subprocess.run(["launchctl", "bootout", "gui/%d/%s" % (uid, LAUNCH_LABEL)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if on:
            subprocess.run(["launchctl", "bootstrap", "gui/%d" % uid, LAUNCH_AGENT],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        log_exc("set_autostart")
        return False


def autostart_target():
    """The command the startup entry should hold for *this* copy."""
    if IS_MAC:
        return " ".join(_mac_launch_args())
    if FROZEN:
        return '"%s" --hidden' % sys.executable
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.join(APP_DIR, "magcopy.pyw")
    return '"%s" "%s" --hidden' % (pyw if os.path.exists(pyw) else sys.executable, script)


def _exe_in(command):
    """The executable path out of a Run-key command line."""
    command = (command or "").strip()
    if command.startswith('"'):
        return command[1:command.find('"', 1)] if command.find('"', 1) > 0 else command
    return command.split(" ")[0]


def get_autostart():
    """True only when the system's startup entry points at THIS copy.

    Checking merely that the value exists is how a moved or replaced .exe ends up reporting
    "starts with Windows" while the entry still launches a copy that is no longer there - or
    worse, an older one that grabs the shortcuts first.
    """
    if IS_MAC:
        return _mac_get_autostart()
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current = winreg.QueryValueEx(key, APP_NAME)[0]
    except Exception:
        return False
    return os.path.normcase(_exe_in(current)) == os.path.normcase(_exe_in(autostart_target()))


def autostart_points_elsewhere():
    """A startup entry exists but launches a different copy - worth telling the user about."""
    if IS_MAC:
        args = _mac_agent_args()
        if args and os.path.normcase(args[0]) != os.path.normcase(_mac_launch_args()[0]):
            return args[0]
        return None
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current = winreg.QueryValueEx(key, APP_NAME)[0]
    except Exception:
        return None
    if os.path.normcase(_exe_in(current)) != os.path.normcase(_exe_in(autostart_target())):
        return _exe_in(current)
    return None


def set_autostart(on, target=None):
    """Point the system's startup entry at this copy, or take it away."""
    if IS_MAC:
        return _mac_set_autostart(on)
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                if target is None:
                    target = autostart_target()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, target)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        log_exc("set_autostart")
        return False
