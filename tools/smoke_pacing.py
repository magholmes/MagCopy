"""Does the capture loop actually hit its frame interval, and is the master the right length?"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, optimize
from magcopy.recorder import Recorder

plat.set_dpi_aware()
tmp = tempfile.mkdtemp(prefix="magcopy-pace-")
vx, vy, _, _ = plat.virtual_screen()
ok = True
for fps, secs in ((25, 4), (50, 3)):
    out = os.path.join(tmp, "p%d.mp4" % fps)
    rec = Recorder((vx + 60, vy + 60, 800, 500), out, fps=fps, max_seconds=secs, cursor=True)
    t0 = time.perf_counter()
    rec.start(); rec.thread.join(secs + 25)
    wall = time.perf_counter() - t0
    wd, ht, dur, vfps = optimize.probe(out)
    expected = fps * secs
    drift = abs(dur - secs)
    print("%2d fps: wall %.2fs | master %.2fs %dx%d @%.4g | frames %d/%d (repeated %d) | drift %.3fs"
          % (fps, wall, dur, wd, ht, vfps, rec.frames, expected, rec.repeated, drift))
    if abs(rec.frames - expected) > expected * 0.06:
        print("   FAIL: frame count off by more than 6%"); ok = False
    if drift > 0.25:
        print("   FAIL: master duration drifted from real time"); ok = False
print("PACING OK" if ok else "PACING PROBLEM")
sys.exit(0 if ok else 1)
