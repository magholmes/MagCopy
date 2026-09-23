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
    # Windows: the one piece of chrome that can land inside the region is the control bar. A
    # recording of the whole screen left it nowhere else to go, and it was recorded - timer, stop
    # button and all - into the GIF. It is excluded from capture now; prove it on the real bar.
    from magcopy.recorder import RecordFrame
    from magcopy.theme import Fonts, Theme, register_fonts
    plat.set_dpi_aware()
    register_fonts()
    ok = True

    def check(name, good, detail=""):
        global ok
        ok = ok and good
        print("%-56s %s %s" % (name, "ok  " if good else "FAIL", detail))

    root = tk.Tk(); root.withdraw(); root.update()
    theme, fonts = Theme("dusk"), Fonts(root, 1.0)

    # --- placement, as geometry: where does the bar go when there is no room outside?
    vx, vy, vw, vh = plat.work_area_for((0, 0, 10, 10))
    whole = (vx, vy, vw, vh)
    rf = RecordFrame(root, theme, fonts, whole, lambda: None, lambda: None, 1.0)
    bw, bh = 250, rf.BAR_H
    small = RecordFrame(root, theme, fonts, (vx + 400, vy + 300, 500, 300),
                        lambda: None, lambda: None, 1.0)
    check("a normal region keeps the bar outside it",
          small._bar_spot(bw, bh, False)[2] == "outside", small._bar_spot(bw, bh, False)[2])
    check("a whole-screen region, excluded: over the recording",
          rf._bar_spot(bw, bh, True)[2] == "inside, excluded from capture",
          rf._bar_spot(bw, bh, True)[2])
    where = rf._bar_spot(bw, bh, False)[2]
    others = len(plat.monitors()) > 1
    check("a whole-screen region, not excludable: another monitor first",
          where == ("another monitor" if others else "inside - will be recorded"), where)

    # --- and the real thing: the actual bar, inside a region, recorded
    GREEN = "#00FF00"
    RECT = (vx + 300, vy + 260, 640, 360)
    back = tk.Toplevel(root); back.overrideredirect(True); back.attributes("-topmost", True)
    back.geometry("%dx%d+%d+%d" % (RECT[2] + 60, RECT[3] + 60, RECT[0] - 30, RECT[1] - 30))
    back.configure(bg=GREEN); back.deiconify()
    for _ in range(20):
        root.update(); time.sleep(0.01)
    frame = RecordFrame(root, theme, fonts, RECT, lambda: None, lambda: None, 1.0)
    frame.show()
    # put it squarely in the middle of what is about to be recorded
    bx, by = RECT[0] + (RECT[2] - 250) // 2, RECT[1] + (RECT[3] - frame.BAR_H) // 2
    frame.bar.geometry("+%d+%d" % (bx, by))
    for _ in range(30):
        root.update(); time.sleep(0.01)
    time.sleep(0.4); root.update()
    check("the bar is excluded from capture", frame.bar_excluded is True,
          "Windows 10 2004+ needed" if not frame.bar_excluded else "")
    # it is really on screen for the person recording: a normal window capture of the bar's
    # rectangle would show it, but ours cannot - so check it is mapped and where we put it
    check("and still on screen for the person recording",
          bool(frame.bar.winfo_viewable()) and abs(frame.bar.winfo_x() - bx) <= 1,
          "at %d,%d" % (frame.bar.winfo_x(), frame.bar.winfo_y()))

    out = os.path.join(tempfile.mkdtemp(prefix="magcopy-own-"), "r.mp4")
    rec = Recorder(RECT, out, fps=20, max_seconds=2, cursor=False)
    rec.start()
    deadline = time.time() + 12
    while rec.thread.is_alive() and time.time() < deadline:
        root.update(); time.sleep(0.02)
    rec.thread.join(5)
    frame.destroy(); back.destroy(); root.update()
    check("the recording was written", os.path.exists(out) and rec.frames > 5, "%d frames" % rec.frames)

    frames_dir = tempfile.mkdtemp(prefix="magcopy-own-frames-")
    run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", out,
         "-vf", "fps=5", os.path.join(frames_dir, "f%03d.png")], timeout=120)
    from PIL import Image
    worst, checked = 0, 0
    lx, ly = bx - RECT[0], by - RECT[1]
    for name in sorted(os.listdir(frames_dir)):
        if not name.endswith(".png"):
            continue
        a = np.asarray(Image.open(os.path.join(frames_dir, name)).convert("RGB")).astype(int)
        patch = a[ly:ly + frame.BAR_H, lx:lx + 250]
        # anything that is not the green backdrop where the bar sits is the bar being recorded
        not_green = int(((patch[:, :, 1] < 200) | (patch[:, :, 0] > 60) | (patch[:, :, 2] > 60)).sum())
        worst = max(worst, not_green)
        checked += 1
    check("frames were extracted to look at", checked > 0, "%d frames" % checked)
    check("no frame contains the bar - only the green behind it", worst < 40,
          "worst frame: %d non-green pixels where the bar is" % worst)
    root.destroy()
    print()
    print("OWN WINDOWS EXCLUDED", "OK" if ok else "PROBLEM")
    sys.exit(0 if ok else 1)

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
