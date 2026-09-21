"""Crop in the editor: the maths, and a real cropped GIF out the other end."""
import os, sys, tempfile, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, optimize
from magcopy.editor import fit_preview, SCREEN_FRACTION
from magcopy.theme import register_fonts

ok = True
def check(n, good, d=""):
    global ok; ok = ok and good
    print("%-40s %s %s" % (n, "ok  " if good else "FAIL", d))

# preview fitting: fills the space it is given, keeps shape, never upscales past the source
pw, ph = fit_preview(1280, 720, 1600, 900)
check("fits a wide source in a big space", (pw, ph) == (1280, 720), "%dx%d" % (pw, ph))
pw, ph = fit_preview(1920, 1080, 1200, 900)
check("limited by width, aspect kept", pw == 1200 and abs(ph - 1200 * 1080 / 1920) <= 2,
      "%dx%d" % (pw, ph))
pw, ph = fit_preview(600, 1400, 1600, 700)
check("tall source limited by height", ph <= 700 and pw < 600, "%dx%d" % (pw, ph))
check("never upscaled past the source", fit_preview(400, 300, 4000, 4000)[0] == 400,
      str(fit_preview(400, 300, 4000, 4000)))
check("aspect ratio preserved", abs((lambda w, h: w / h)(*fit_preview(1920, 1080, 900, 900))
                                   - 1920 / 1080) < 0.02)

plat.set_dpi_aware(); register_fonts()
root = tk.Tk(); root.withdraw()
from magcopy.app import App
app = App(root)
tmp = tempfile.mkdtemp(prefix="magcopy-crop-")
app.settings["save_dir"] = tmp

vx, vy, _, _ = plat.virtual_screen()
master = os.path.join(tmp, "m.mp4")
from magcopy.recorder import Recorder
rec = Recorder((vx + 100, vy + 100, 800, 600), master, fps=25, max_seconds=2, cursor=False)
rec.start(); rec.thread.join(30)
check("recorded a master", rec.error is None, str(rec.error))
mw, mh, _, _ = optimize.probe(master)
# The region is dragged in points and recorded in pixels, which on a Retina display is twice
# that in each direction - so the master's own size is the thing to measure against, not the
# number that was asked for. Hardcoding 800x600 here quietly asserted the old, halved capture.
print("   master: %dx%d from an 800x600 point region" % (mw, mh))

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
check("uncropped is the full frame", (i2["width"], i2["height"]) == (mw, mh),
      "%dx%d, master is %dx%d" % (i2["width"], i2["height"], mw, mh))
check("the master is captured at the screen's real pixels",
      mw >= 800 * plat.scale_for((vx + 100, vy + 100, 800, 600)) - 2,
      "%dx%d for an 800x600 point region at %.0fx"
      % (mw, mh, plat.scale_for((vx + 100, vy + 100, 800, 600))))
app.quit()
print("\nCROP", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
