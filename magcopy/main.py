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


def main(argv=None):
    global _CRASH_FH
    argv = list(sys.argv[1:] if argv is None else argv)
    _CRASH_FH = setup_crash_logging()

    if os.name != "nt":
        print("%s needs Windows: it uses Win32 capture, clipboard and hotkeys." % APP_NAME)
        return 2

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
