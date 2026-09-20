"""Entry point: check the environment, then open the window (or run a one-shot from the CLI)."""
import os
import sys
import tkinter as tk
from tkinter import messagebox

from .binaries import TOOLS
from .settings import APP_NAME, APP_VERSION, log_exc, setup_crash_logging
from .theme import register_fonts
from . import win

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
    """Check an installation without opening a window: `MagCopy.exe --selftest`.

    A frozen build resolves paths differently from a checkout (bundled files land in a temporary
    _MEIPASS folder, settings move to %APPDATA%), so this is also how the release .exe is verified.
    """
    import tkinter as tk
    from .settings import APP_DIR, RES_DIR, SETTINGS_DIR, FROZEN, save_dir, DEFAULTS
    from .binaries import TOOLS, run
    from . import win as W

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
    try:
        vx, vy, vw, vh = W.virtual_screen()
        arr = W.grab_once(vx, vy, min(320, vw), min(200, vh))
        check("screen capture", arr.shape[0] > 0 and arr.shape[1] > 0, "%dx%d desktop" % (vw, vh))
        check("clipboard", W.set_clipboard_image(arr), "wrote a test image")
    except Exception as e:
        check("screen capture", False, str(e))
    try:
        root = tk.Tk()
        root.withdraw()
        from .theme import Fonts, register_fonts
        register_fonts()
        f = Fonts(root, 1.0)
        check("fonts", True, "%s / %s" % (f.sans, f.mono))
        root.destroy()
    except Exception as e:
        check("tk + fonts", False, str(e))
    parsed = W.parse_hotkey(DEFAULTS["hotkey_shot"])
    check("hotkey parsing", parsed is not None, DEFAULTS["hotkey_shot"])

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
            r = tk.Tk()
            r.withdraw()
            mb.showinfo("%s self-test" % APP_NAME, report)
            r.destroy()
        except Exception:
            pass
    return 0 if ok else 1


def main(argv=None):
    global _CRASH_FH
    argv = list(sys.argv[1:] if argv is None else argv)
    _CRASH_FH = setup_crash_logging()

    if os.name != "nt":
        print("%s needs Windows: it uses Win32 capture, clipboard and hotkeys." % APP_NAME)
        return 2

    if "--selftest" in argv:
        return selftest(argv)

    from .tray import already_running, wake_running_instance
    if already_running() and "--allow-multiple" not in argv:
        wake_running_instance()                  # bring the existing copy forward instead
        return 0

    missing = _missing_python_packages()
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, "Missing Python packages: %s\n\nInstall with:\n"
                                       "  python -m pip install %s"
                             % (", ".join(missing), " ".join(missing)))
        return 1

    win.set_dpi_aware()
    register_fonts()
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    from .settings import RES_DIR, APP_DIR
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
