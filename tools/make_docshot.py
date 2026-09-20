"""Render the window for the readme, with a neutral save path (no username in the shot).

On macOS the files are named -macos so they sit alongside the Windows ones rather than replacing
them: the two builds look the same by design, and the readme shows one of each to say so.
"""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MAGCOPY_NO_AUTOSTART", "1")
os.environ.setdefault("MAGCOPY_INSTANCE_NAME", "MagCopy-docshot")
from PIL import Image
from magcopy import plat
from magcopy.theme import register_fonts

MAC = plat.IS_MAC
SUFFIX = "-macos" if MAC else ""
FOLDER = "~/Pictures/MagCopy" if MAC else r"C:\Users\you\Pictures\MagCopy"

plat.set_dpi_aware(); register_fonts()
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
app = App(root)
app.folder_label.configure(text=FOLDER)
docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
os.makedirs(docs, exist_ok=True)


def shoot(name):
    # The window has to be in front of whatever else is open, or the capture is of that instead.
    # Windows gets this for free because the shot is taken from the process that owns the window;
    # an accessory app on macOS has to ask.
    root.attributes("-topmost", True)
    root.lift()
    if MAC:
        try:
            from AppKit import NSApp
            NSApp().activateIgnoringOtherApps_(True)
        except Exception:
            pass
    root.update(); root.update_idletasks(); time.sleep(0.5); root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    # retina on macOS: the readme is read on screens that can show it
    a = plat.grab_once(x, y, root.winfo_width(), root.winfo_height(), retina=MAC)
    Image.fromarray(a[:, :, 2::-1], "RGB").save(os.path.join(docs, name))
    root.attributes("-topmost", False)
    print("wrote", name, "%dx%d" % (a.shape[1], a.shape[0]))


def go():
    shoot("window%s.png" % SUFFIX)
    for t in ("paper", "ember"):
        app.set_theme(t); app.folder_label.configure(text=FOLDER)
        shoot("window-%s%s.png" % (t, SUFFIX))
    app.set_theme("dusk")
    app.quit(); root.quit()


root.after(700, go)
root.mainloop()
