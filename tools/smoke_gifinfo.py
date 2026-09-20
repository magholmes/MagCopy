"""Check the GIF parser against ffmpeg's own frame count on real files."""
import os, sys, glob, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import optimize
from magcopy.binaries import TOOLS, run

tmp = tempfile.mkdtemp(prefix="magcopy-gifinfo-")
cases = []
# a) synthetic 20-frame gif at a known 5 cs delay
g = os.path.join(tmp, "a.gif")
run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
     "-i", "testsrc=s=160x120:r=20:d=1", "-vf", "fps=20", "-loop", "0", g], timeout=120)
cases.append((g, 20, 5))
# b) 8 frames at 12.5 fps -> 8 cs
g2 = os.path.join(tmp, "b.gif")
run([TOOLS.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
     "-i", "testsrc=s=200x100:r=12.5:d=0.64", "-vf", "fps=12.5", "-loop", "0", g2], timeout=120)
cases.append((g2, 8, 8))

ok = True
for path, want_frames, want_cs in cases:
    info = optimize.gif_info(path)
    # ffmpeg's own count, as an independent check
    r = run([TOOLS.ffmpeg, "-hide_banner", "-i", path, "-f", "null", "-"], timeout=120)
    txt = (r.stderr or b"").decode("utf-8", "replace")
    ff = 0
    for line in txt.splitlines():
        if "frame=" in line:
            try: ff = int(line.split("frame=")[1].split()[0])
            except Exception: pass
    match = info["frames"] == ff
    print("%s: parser frames=%d ffmpeg frames=%d delays=%s dur=%.2fs loop=%s  %s" % (
        os.path.basename(path), info["frames"], ff, info["delays"], info["duration"],
        info["loops"], "OK" if match else "MISMATCH"))
    ok = ok and match and info["frames"] > 0
    if set(info["delays"]) - {want_cs}:
        print("   note: delays present:", info["delays"])
print("PARSER OK" if ok else "PARSER MISMATCH")
sys.exit(0 if ok else 1)
