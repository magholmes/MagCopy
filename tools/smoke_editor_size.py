"""The editor window must fit comfortably on the monitor it opens on, at any recording shape."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import win
from magcopy.editor import SCREEN_FRACTION
from magcopy.theme import register_fonts
from magcopy.binaries import TOOLS, run
import tempfile

win.set_dpi_aware(); register_fonts()
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
from magcopy.editor import GifEditor
app = App(root); app.hide_window()
UI = float(os.environ.get("MAGCOPY_TEST_UI_SCALE", "1.0"))
if UI != app.ui_factor:
    app.set_ui_scale(UI); root.update()
print("ui scale:", app.ui_factor, "| effective scale:", round(app.scale, 2))
tmp = tempfile.mkdtemp(prefix="magcopy-size-")
ok = True

def check(n, good, d=""):
    global ok; ok = ok and good
    print("%-52s %s %s" % (n, "ok  " if good else "FAIL", d))

# check every monitor: a clip recorded on one must be measured against that one
MONS = [(m[1], m[2]) for m in win.monitors()]
print("monitors:", [m[0] for m in MONS])
SHAPES = [(1280, 720), (1920, 1080), (640, 480), (500, 1200), (2400, 700)]
CASES = [(work, sw, sh) for work, _ in MONS for sw, sh in SHAPES]
for work, sw, sh in CASES:
    m = os.path.join(tmp, "s%dx%d.mp4" % (sw, sh))
    run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=s=%dx%d:r=50:d=1" % (sw, sh), "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "20", "-pix_fmt", "yuv420p", m], timeout=200)
    # put the "recording" in the middle of this monitor so the editor targets it
    ed = GifEditor(app, m, rect=(work[0] + work[2] // 3, work[1] + work[3] // 3, 40, 40))
    ed.open()
    root.update(); root.update_idletasks()
    wx, wy, ww, wh = ed._target_work_area()
    w_, h_ = ed.top.winfo_width(), ed.top.winfo_height()
    frac_w, frac_h = w_ / ww, h_ / wh
    fits = w_ <= ww and h_ <= wh
    within = frac_w <= SCREEN_FRACTION + 0.02 and frac_h <= SCREEN_FRACTION + 0.02
    check("%4dx%-4d on a %dx%d monitor -> %4dx%-4d  (%.0f%% x %.0f%%)"
          % (sw, sh, ww, wh, w_, h_, frac_w * 100, frac_h * 100), fits and within)
    # and it must sit fully on that monitor
    x, y = ed.top.winfo_rootx(), ed.top.winfo_rooty()
    on = x >= wx - 2 and y >= wy - 2 and x + w_ <= wx + ww + 2 and y + h_ <= wy + wh + 2
    check("   sits fully on the monitor", on, "at (%d,%d) work=(%d,%d,%d,%d)" % (x, y, wx, wy, ww, wh))
    ed.close(); root.update()

app.quit()
print("\nEDITOR SIZE", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
