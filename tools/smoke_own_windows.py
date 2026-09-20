"""A recording must not contain MagCopy's own recording frame.

The Windows build guarantees this by geometry: the border is eight strips placed strictly outside
the captured rectangle, so there is nowhere for it to land. macOS can do better and leaves our
windows out of the capture stream itself - which is worth checking rather than assuming, because
the filter is built from a window list that a moment's staleness would leave our windows out of.

The check is the blunt one: put a window of a colour nothing else on the desktop is *inside* the
region being recorded, record it, and look for that colour in the frames.
"""
import os, sys, tempfile, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from magcopy import plat, optimize
from magcopy.binaries import TOOLS, run
from magcopy.recorder import Recorder

if not plat.IS_MAC:
    print("not macOS - the border is kept out of frame by being outside it")
    sys.exit(0)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail))


MARK = "#FF00FF"                      # magenta: nothing on a desktop is this
RECT = (200, 200, 600, 400)
root = tk.Tk(); root.withdraw(); root.update()

# a marker window sitting squarely INSIDE the region about to be recorded
mark = tk.Toplevel(root); mark.overrideredirect(True)
mark.geometry("300x200+%d+%d" % (RECT[0] + 150, RECT[1] + 100))
mark.configure(bg=MARK); mark.attributes("-topmost", True); mark.deiconify()
root.update(); time.sleep(0.4); root.update()

hwnd = plat.toplevel_hwnd(mark)
check("the marker window was found", hwnd is not None)

# it is plainly there: an ordinary capture sees it
shot = plat.grab_once(*RECT)
seen = int(((shot[:, :, 2] > 200) & (shot[:, :, 1] < 80) & (shot[:, :, 0] > 200)).sum())
check("an ordinary capture sees it", seen > 1000, "%d magenta pixels" % seen)

# now mark it as ours, the way every overlay does, and record
plat.set_overlay_styles(hwnd)
out = os.path.join(tempfile.mkdtemp(prefix="magcopy-own-"), "r.mp4")
rec = Recorder(RECT, out, fps=20, max_seconds=2, cursor=False)
rec.start()
deadline = time.time() + 12
while rec.thread.is_alive() and time.time() < deadline:
    root.update(); time.sleep(0.02)
rec.thread.join(5)
check("the recording was written", os.path.exists(out) and rec.frames > 5,
      "%d frames" % rec.frames)

# pull a handful of frames back out and look for the marker colour
frames_dir = tempfile.mkdtemp(prefix="magcopy-own-frames-")
run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", out,
     "-vf", "fps=5", os.path.join(frames_dir, "f%03d.png")], timeout=120)
from PIL import Image
worst, checked = 0, 0
for name in sorted(os.listdir(frames_dir)):
    if not name.endswith(".png"):
        continue
    a = np.asarray(Image.open(os.path.join(frames_dir, name)).convert("RGB"))
    hits = int(((a[:, :, 0] > 200) & (a[:, :, 1] < 80) & (a[:, :, 2] > 200)).sum())
    worst = max(worst, hits)
    checked += 1
check("frames were extracted to look at", checked > 0, "%d frames" % checked)
check("no frame contains our own window", worst == 0, "worst frame: %d magenta pixels" % worst)

mark.destroy(); root.destroy()
print("\nOWN WINDOWS EXCLUDED", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
