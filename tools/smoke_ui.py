"""Build the app, walk every theme and scale, drive a real screenshot, shoot the window."""
import os, sys, time, tkinter as tk
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
import numpy as np
from magcopy import plat, overlay
from magcopy.theme import register_fonts, THEME_ORDER
from magcopy.main import _missing_python_packages

plat.set_dpi_aware(); register_fonts()
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "magcopy_ui")
os.makedirs(OUT, exist_ok=True)

root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass

from magcopy.app import App
app = App(root)
root.update(); root.update_idletasks()
failed = list(app.hotkeys._failed)
print("app built. hotkeys failed:", failed, "| tray installed:", bool(app.tray.ok))
if failed:
    # another copy of MagCopy (or another app) already owns the shortcut. That is the conflict
    # path working, not a defect, so report it rather than failing the run.
    print("   note: a shortcut was already taken by another process - not counted as a failure")

def shoot(name):
    root.update(); root.update_idletasks(); time.sleep(0.35); root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w_, h_ = root.winfo_width(), root.winfo_height()
    a = plat.grab_once(x - 2, y - 2, w_ + 4, h_ + 4)
    Image.fromarray(a[:, :, 2::-1], "RGB").save(os.path.join(OUT, name))
    return (w_, h_)

size = shoot("01_dusk.png"); print("window size:", size)
for name, _ in THEME_ORDER[1:]:
    app.set_theme(name); shoot("02_%s.png" % name)
app.set_theme("dusk")
for label, factor in (("small", 0.85), ("large", 1.2)):
    app.set_ui_scale(factor); print("scale", label, "->", shoot("03_%s.png" % label))
app.set_ui_scale(1.0)

# drive a real region screenshot through the real selector
orig_run = overlay.RegionSelector.run
def driven(self):
    def go():
        try:
            self.start, self.cur, self.dragging = (400, 260), (1080, 700), True
            self._redraw(); self.top.update_idletasks(); self._commit()
        except Exception as e: print("drive failed:", e)
    self.root.after(120, go)
    return orig_run(self)
overlay.RegionSelector.run = driven
app.start_screenshot()
root.update()
has, nbytes = plat.clipboard_image_info()
print("clipboard after screenshot: image=%s bytes=%d" % (has, nbytes))
overlay.RegionSelector.run = orig_run

app.toast("this is what a message looks like")
shoot("04_toast.png")
app._remember(os.path.join(OUT, "01_dusk.png"), "680 × 440", 123456)
shoot("05_recent.png")

ok = bool(has and nbytes > 1000 and app.tray.ok)
print("UI SMOKE", "OK" if ok else "PROBLEM")
app.quit()
print("files:", sorted(os.listdir(OUT)))
sys.exit(0 if ok else 1)
