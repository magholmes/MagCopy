"""1) the record border must never overlap the capture rect
   2) a realistic 20s screen recording must come out under 10 MB and still be readable"""
import os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import optimize
from magcopy.binaries import TOOLS, run

ok = True

# ---- 1. border geometry (pure logic: the strips must sit strictly outside the rect)
def strips(rect, t):
    x, y, wd, ht = rect
    return [(x - t, y - t, wd + 2 * t, t), (x - t, y + ht, wd + 2 * t, t),
            (x - t, y, t, ht), (x + wd, y, t, ht)]

def overlaps(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

for rect in ((100, 100, 760, 480), (0, 0, 300, 200), (-1400, -500, 640, 360), (5, 5, 33, 17)):
    cap = (rect[0], rect[1], int(rect[2]) & ~1, int(rect[3]) & ~1)   # what Recorder actually grabs
    for t in (2, 3, 4):
        for s in strips(rect, t):
            if overlaps(s, cap):
                print("FAIL: border strip %s overlaps capture %s (t=%d)" % (s, cap, t)); ok = False
print("border geometry:", "clear of the capture" if ok else "OVERLAPPING")

# ---- 2. realistic screen content: a real desktop grab, panned, with a moving block over it
from magcopy import win
from PIL import Image
win.set_dpi_aware()
tmp = tempfile.mkdtemp(prefix="magcopy-real-")
vx, vy, vw, vh = win.virtual_screen()
shot = os.path.join(tmp, "desk.png")
a = win.grab_once(vx, vy, min(vw, 2200), min(vh, 1400))
Image.fromarray(a[:, :, 2::-1], "RGB").save(shot)
print("source desktop grab:", Image.open(shot).size)

clip = os.path.join(tmp, "screen.mp4")
# pan a 1280x720 window across the grab, and slide a solid block over it: real UI, real motion
vf = ("crop=1280:720:x='min(iw-1280,mod(t*70,iw))':y='min(ih-720,mod(t*40,ih))',"
      "drawbox=x='mod(t*260,1100)+40':y='240+130*sin(t*2)':w=160:h=96:color=0x5E6DEE@0.85:t=fill,"
      "drawbox=x='mod(t*180,1000)+60':y='120+90*cos(t*1.5)':w=220:h=3:color=0xE0A45C:t=fill")
r = run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-loop", "1", "-framerate", "25", "-t", "20", "-i", shot,
         "-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12",
         "-pix_fmt", "yuv444p", clip], timeout=900)
if r.returncode != 0:
    print("ffmpeg failed:", (r.stderr or b"").decode("utf-8", "replace")[:500]); sys.exit(1)
print("clip: %.1f MB %s" % (os.path.getsize(clip) / 1e6, optimize.probe(clip)))

out = os.path.join(tmp, "screen.gif")
t0 = time.perf_counter()
res = optimize.optimize(clip, out, size_limit_bytes=10_000_000,
                        on_progress=lambda t, f=None: print("   ", t))
el = time.perf_counter() - t0
info = optimize.gif_info(out)
print("\nRESULT %s in %.1fs" % (res, el))
print("gif: %dx%d frames=%d dur=%.2fs loop=%s delays=%s"
      % (info["width"], info["height"], info["frames"], info["duration"], info["loops"], info["delays"]))
if res.bytes > 10_000_000: print("FAIL: over 10 MB"); ok = False
if not res.fits: print("FAIL: reported as not fitting"); ok = False
if abs(info["duration"] - 20.0) > 0.6: print("FAIL: duration drifted (%.2fs)" % info["duration"]); ok = False
# a long final delay is correct, not a defect: when the picture stops changing gifski merges the
# duplicate frames and extends the delay, which is exactly what the recording did.
if len(info["delays"]) and max(info["delays"]) > info["duration"] * 100 * 0.5:
    print("FAIL: one frame holds for more than half the clip"); ok = False
# keep a frame so the result can be eyeballed
run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", out,
     "-vf", "select=eq(n\,8)", "-frames:v", "1",
     os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots", "07_gif_frame.png")], timeout=120)
print("REALISTIC", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
