"""Entry point: check the environment, then open the window (or run a one-shot from the CLI)."""
import os
import sys
import tkinter as tk
from tkinter import messagebox

from .binaries import TOOLS
from .settings import APP_NAME, APP_VERSION, log_exc, setup_crash_logging
from .theme import register_fonts
from . import plat

_CRASH_FH = None


def _missing_python_packages():
    missing = []
    for mod, pkg in (("numpy", "numpy"), ("PIL", "Pillow")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    return missing


def selftest(argv=None):
    """Check an installation without opening a window: `MagCopy --selftest`.

    A frozen build resolves paths differently from a checkout (bundled files land in a temporary
    _MEIPASS folder, settings move to %APPDATA%), so this is also how the release .exe is verified.
    """
    import tkinter as tk
    from .settings import APP_DIR, RES_DIR, SETTINGS_DIR, FROZEN, save_dir, DEFAULTS
    from .binaries import TOOLS, run
    from . import plat as W

    lines, ok = [], True

    def check(name, good, detail=""):
        nonlocal ok
        ok = ok and good
        lines.append("%-22s %s %s" % (name, "ok  " if good else "FAIL", detail))

    check("frozen build", True, "yes" if FROZEN else "no (running from source)")
    check("app dir", os.path.isdir(APP_DIR), APP_DIR)
    check("bundled resources", os.path.isdir(RES_DIR), RES_DIR)
    for tool in ("ffmpeg", "gifski", "gifsicle"):
        path = TOOLS.paths.get(tool)
        works = False
        if path and os.path.exists(path):
            r = run([path, "-version" if tool == "ffmpeg" else "--version"], timeout=30)
            works = r.returncode == 0
        check(tool, works, path or "not found")
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        probe = os.path.join(SETTINGS_DIR, ".writetest")
        open(probe, "w").close()
        os.remove(probe)
        check("settings dir", True, SETTINGS_DIR)
    except Exception as e:
        check("settings dir", False, "%s (%s)" % (SETTINGS_DIR, e))
    try:
        check("captures dir", os.path.isdir(save_dir(dict(DEFAULTS))), save_dir(dict(DEFAULTS)))
    except Exception as e:
        check("captures dir", False, str(e))

    W.set_dpi_aware()
    root = _new_root()                       # before any AppKit call - see _new_root
    try:
        vx, vy, vw, vh = W.virtual_screen()
        arr = W.grab_once(vx, vy, min(320, vw), min(200, vh))
        check("screen capture", arr.shape[0] > 0 and arr.shape[1] > 0, "%dx%d desktop" % (vw, vh))
        check("clipboard", W.set_clipboard_image(arr), "wrote a test image")
    except Exception as e:
        check("screen capture", False, str(e))
    try:
        from .theme import Fonts, register_fonts
        register_fonts()
        f = Fonts(root, 1.0)
        check("fonts", True, "%s / %s" % (f.sans, f.mono))
    except Exception as e:
        check("tk + fonts", False, str(e))
    parsed = W.parse_hotkey(DEFAULTS["hotkey_shot"])
    check("hotkey parsing", parsed is not None, DEFAULTS["hotkey_shot"])
    if sys.platform == "darwin":
        # these two are the difference between "works" and "silently captures nothing"
        check("screen recording", W.has_screen_recording(),
              "granted" if W.has_screen_recording() else
              "DENIED - System Settings > Privacy & Security > Screen Recording")
        lines.append("%-22s %s %s" % ("accessibility", "ok  ",
                                      "granted" if W.has_accessibility() else
                                      "not granted (not needed: the shortcuts use Carbon)"))

    report = os.linesep.join([
        "%s %s self-test" % (APP_NAME, APP_VERSION),
        "-" * 52,
    ] + lines + [
        "",
        "Everything checks out." if ok else "Something is wrong - see the FAIL lines above.",
    ])
    print(report)
    try:                             # always leave it on disk: a windowed build has no console
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(os.path.join(SETTINGS_DIR, "selftest.txt"), "w", encoding="utf-8") as fh:
            fh.write(report + os.linesep)
    except Exception:
        pass
    if FROZEN and "--quiet" not in (argv or []):
        try:
            import tkinter.messagebox as mb
            mb.showinfo("%s self-test" % APP_NAME, report)
        except Exception:
            pass
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if ok else 1


def _new_root():
    """Create the Tk root, and do it before anything else touches AppKit.

    Tk 9 on macOS installs its own NSApplication subclass, TKApplication, and then calls methods
    that exist only on it - `macOSVersion` is the first, reached while resolving a system colour.
    NSApplication is a singleton, so whoever creates it first wins: if any AppKit or Core Graphics
    call has already brought a plain NSApplication into being, Tk gets that one instead and dies
    on its first colour lookup with "unrecognized selector sent to instance".

    It costs nothing to create the root first and everything to get it wrong, and it is invisible
    from a source checkout - where the permission is already granted, so the call that would have
    tripped it never runs. It only appears in a fresh bundle, on someone else's Mac.
    """
    root = tk.Tk()
    root.withdraw()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    return root


def _permission_panel(seen_before=False):
    """Name the permission, say what it is for, and open the pane that grants it.

    Without this the first run of a denied build is a screenshot of an empty desktop and no
    error anywhere - the single most likely way a working macOS build looks broken.

    `seen_before` is the case that reads as a bug and is not one. macOS remembers the permission
    against the application's code signature, and without a Developer ID certificate every build
    is signed afresh, so an updated MagCopy is a different application as far as that list is
    concerned. The old entry stays, still switched on, and the new one is refused - which looks
    exactly like the setting being ignored. Telling someone to switch on a thing that is visibly
    already on is not help, so say what actually has to happen instead.
    """
    try:
        from tkinter import messagebox
        if seen_before:
            body = ("%s is in the Screen Recording list and switched on, and is still being "
                    "refused.\n\n"
                    "That is expected after an update, and it is not something you did. macOS "
                    "remembers the permission against the app's signature, and an updated %s is "
                    "signed afresh - so the entry you can see belongs to the previous version.\n\n"
                    "In System Settings -> Privacy & Security -> Screen Recording:\n"
                    "   1. select %s in the list\n"
                    "   2. click the - button to remove it\n"
                    "   3. quit and reopen %s, and allow it when asked\n\n"
                    "Open that pane now?" % (APP_NAME, APP_NAME, APP_NAME, APP_NAME))
        else:
            body = ("%s cannot see the screen yet.\n\n"
                    "System Settings -> Privacy & Security -> Screen Recording, and switch %s on. "
                    "macOS only asks once, so the switch is the way back if the prompt has gone.\n\n"
                    "Screenshots and recordings come out blank until it is on.\n\n"
                    "Open that pane now?" % (APP_NAME, APP_NAME))
        go = messagebox.askretrycancel("%s needs permission" % APP_NAME, body)
        if go:
            plat.open_privacy_pane("ScreenCapture")
    except Exception:
        log_exc("permission panel")


def main(argv=None):
    global _CRASH_FH
    argv = list(sys.argv[1:] if argv is None else argv)
    _CRASH_FH = setup_crash_logging()

    if os.name != "nt" and sys.platform != "darwin":
        print("%s runs on Windows and macOS: the capture, clipboard and hotkeys are native to "
              "each." % APP_NAME)
        return 2

    plat.set_dpi_aware()
    register_fonts()
    root = _new_root()                       # before any AppKit call - see _new_root

    if sys.platform == "darwin" and not plat.has_screen_recording():
        # A denied Screen Recording permission is the one failure that looks like a working app:
        # ScreenCaptureKit keeps answering and returns the desktop with every other window
        # missing. Ask before anything else, and say where the switch is if the ask is refused.
        # Ask first. If macOS does not even show its own dialog, it has asked before and this
        # build is being refused against a remembered answer - which is the updated-app case.
        asked = plat.request_screen_recording()
        if not plat.has_screen_recording() and "--no-permission-prompt" not in argv:
            _permission_panel(seen_before=not asked)

    if "--selftest" in argv:
        return selftest(argv)

    from .tray import already_running, wake_running_instance
    if already_running() and "--allow-multiple" not in argv:
        wake_running_instance()                  # bring the existing copy forward instead
        try:
            root.destroy()
        except Exception:
            pass
        return 0

    missing = _missing_python_packages()
    if missing:
        messagebox.showerror(APP_NAME, "Missing Python packages: %s\n\nInstall with:\n"
                                       "  python -m pip install %s"
                             % (", ".join(missing), " ".join(missing)))
        return 1

    from .settings import RES_DIR, APP_DIR
    if os.name == "nt":
        for base in (RES_DIR, APP_DIR):
            ico = os.path.join(base, "icon.ico")
            if os.path.exists(ico):
                try:
                    root.iconbitmap(default=ico)
                    break
                except Exception:
                    pass

    from .app import App
    app = App(root)

    if TOOLS.missing:
        root.after(400, lambda: app.toast(
            "missing: %s — screenshots work, gifs need them (see the readme)"
            % ", ".join(TOOLS.missing), error=True))
    if "--hidden" in argv or "--tray" in argv:
        root.after(0, app.hide_window)
    if "--shot" in argv:
        root.after(200, app.start_screenshot)
    if "--gif" in argv:
        root.after(200, app.start_gif)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
