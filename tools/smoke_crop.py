"""Crop in the editor: the maths, and a real cropped GIF out the other end."""
import os, sys, tempfile, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import win, optimize
from magcopy.editor import preview_size
from magcopy.theme import register_fonts

ok = True
def check(n, good, d=""):
    global ok; ok = ok and good
    print("%-40s %s %s" % (n, "ok  " if good else "FAIL", d))

# preview sizing: big by default, and never taller than the screen allows
check("1280x720 on a 2560x1440 screen", preview_size(1280, 720, 2560, 1440, 1.0) == (1280, 720),
      str(preview_size(1280, 720, 2560, 1440, 1.0)))
pw, ph = preview_size(600, 1400, 2560, 1440, 1.0)
check("tall portrait fits the screen", ph <= 1440 - 330 - 80 + 2, "%dx%d" % (pw, ph))
pw, ph = preview_size(1280, 720, 1366, 768, 1.0)
check("small screen still gets a preview", pw >= 420 and ph > 0, "%dx%d" % (pw, ph))
check("bigger than the old fixed 460 px", preview_size(1280, 720, 2560, 1440, 1.0)[0] > 460)

win.set_dpi_aware(); register_fonts()
root = tk.Tk(); root.withdraw()
from magcopy.app import App
app = App(root)
tmp = tempfile.mkdtemp(prefix="magcopy-crop-")
app.settings["save_dir"] = tmp

vx, vy, _, _ = win.virtual_screen()
master = os.path.join(tmp, "m.mp4")
from magcopy.recorder import Recorder
rec = Recorder((vx + 100, vy + 100, 800, 600), master, fps=25, max_seconds=2, cursor=False)
rec.start(); rec.thread.join(30)
check("recorded a master", rec.error is None, str(rec.error))
print("   master:", optimize.probe(master))

out = os.path.join(tmp, "cropped.gif")
CROP = (100, 80, 400, 300)      # x, y, w, h in source pixels
res = optimize.optimize(master, out, crop=CROP, size_limit_bytes=10_000_000)
info = optimize.gif_info(out)
print("   result:", res)
check("gif has the cropped dimensions", (info["width"], info["height"]) == (CROP[2], CROP[3]),
      "%dx%d wanted %dx%d" % (info["width"], info["height"], CROP[2], CROP[3]))
check("still fits the limit", res.fits)

# and without a crop, the full frame comes through
out2 = os.path.join(tmp, "full.gif")
res2 = optimize.optimize(master, out2, size_limit_bytes=10_000_000)
i2 = optimize.gif_info(out2)
check("uncropped is the full frame", (i2["width"], i2["height"]) == (800, 600),
      "%dx%d" % (i2["width"], i2["height"]))
app.quit()
print("\nCROP", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
