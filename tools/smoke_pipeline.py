"""Record a real region, then optimise it; then force the ladder with a synthetic hard clip."""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk
from magcopy import win, optimize
from magcopy.binaries import TOOLS, run
from magcopy.recorder import Recorder

win.set_dpi_aware()
tmp = tempfile.mkdtemp(prefix="magcopy-test-")
print("tools:", {k: bool(v) for k, v in TOOLS.paths.items()})

# ---- 1. record a real region
vx, vy, vw, vh = win.virtual_screen()
rect = (vx + 100, vy + 100, 900, 560)
master = os.path.join(tmp, "master.mp4")
rec = Recorder(rect, master, fps=25, max_seconds=3, cursor=True)
t0 = time.perf_counter()
rec.start()
rec.thread.join(30)
print("recorded %d frames, dropped %d, %.2fs wall, err=%s" % (rec.frames, rec.dropped,
      time.perf_counter() - t0, rec.error))
print("master:", os.path.getsize(master), "bytes  probe:", optimize.probe(master))
assert rec.error is None and rec.frames > 20, "recorder failed"

# ---- 2. optimise it
out = os.path.join(tmp, "real.gif")
t0 = time.perf_counter()
res = optimize.optimize(master, out, size_limit_bytes=10_000_000,
                        on_progress=lambda t, f=None: print("   ", t))
print("REAL -> %s in %.1fs" % (res, time.perf_counter() - t0))
assert res.bytes <= 10_000_000 and res.fits

# ---- 3. force the ladder: 12s of 1280x720 noise at 25fps is far too big for 10 MB as a GIF
hard = os.path.join(tmp, "hard.mp4")
run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
     "-i", "nullsrc=s=1280x720:r=25:d=6", "-vf",
     "geq=random(1)*255:128:128,noise=alls=40:allf=t+u,format=yuv444p",
     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", hard], timeout=600)
print("hard clip:", os.path.getsize(hard), "bytes  probe:", optimize.probe(hard))
out2 = os.path.join(tmp, "hard.gif")
t0 = time.perf_counter()
res2 = optimize.optimize(hard, out2, size_limit_bytes=10_000_000,
                         on_progress=lambda t, f=None: print("   ", t))
print("HARD -> %s in %.1fs" % (res2, time.perf_counter() - t0))
print("attempts:", res2.attempts, "fits:", res2.fits)
assert res2.bytes <= 10_000_000 and res2.fits, 'ladder failed to get under budget'

# ---- 4. check the emitted gif really has integer centisecond delays
import struct
for label, path in (("real", out), ("hard", out2)):
    d = open(path, "rb").read()
    delays = {}
    i = 0
    while True:
        i = d.find(b"\x21\xf9\x04", i)
        if i < 0: break
        v = struct.unpack("<H", d[i+4:i+6])[0]; delays[v] = delays.get(v, 0) + 1; i += 1
    canvas = struct.unpack("<HH", d[6:10])
    print("%s: %s canvas=%s delays=%s loop=%s" % (label, d[:6].decode(), canvas, delays,
          b"NETSCAPE2.0" in d))
print("\nALL PIPELINE TESTS PASSED")
