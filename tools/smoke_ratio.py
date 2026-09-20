"""A locked aspect ratio must survive every drag direction, and the edges of the desktop."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy.overlay import RegionSelector
from magcopy.settings import ASPECT_RATIOS, aspect_value

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail))


def sel(ratio=None, shift=False, vw=2000, vh=1200):
    s = RegionSelector.__new__(RegionSelector)
    s.ratio, s.shift = ratio, shift
    s.vw, s.vh = vw, vh
    s.start = s.cur = None
    return s


def rect(ratio, a, b, shift=False, vw=2000, vh=1200):
    s = sel(ratio, shift, vw, vh)
    s.start, s.cur = a, b
    return s._rect()


def is_ratio(r, want, tol=0.02):
    return r and r[3] > 0 and abs(r[2] / r[3] - want) <= tol


# --- every option parses and produces the shape it names
for name, _ in ASPECT_RATIOS:
    v = aspect_value(name)
    if name == "free":
        check("'free' means no constraint", v is None)
        continue
    r = rect(v, (300, 300), (900, 700))
    check("%-5s drag comes out %s" % (name, name), is_ratio(r, v), "%dx%d" % (r[2], r[3]))

# --- all four drag directions
V = aspect_value("4:5")
for label, a, b in (("down-right", (400, 300), (900, 900)),
                    ("down-left", (900, 300), (400, 900)),
                    ("up-right", (400, 900), (900, 300)),
                    ("up-left", (900, 900), (400, 300))):
    r = rect(V, a, b)
    check("4:5 holds dragging %s" % label, is_ratio(r, V), "%dx%d at (%d,%d)" % (r[2], r[3], r[0], r[1]))

# --- a wide drag and a tall drag both end up 4:5
wide = rect(V, (200, 400), (1800, 500))
tall = rect(V, (200, 100), (300, 1100))
check("a wide drag is pulled to 4:5", is_ratio(wide, V), "%dx%d" % (wide[2], wide[3]))
check("a tall drag is pulled to 4:5", is_ratio(tall, V), "%dx%d" % (tall[2], tall[3]))

# --- clamping at the desktop edge must not bend the shape
edge = rect(V, (1900, 1100), (2600, 1900))
check("still 4:5 when clamped at the corner", is_ratio(edge, V),
      "%dx%d at (%d,%d)" % (edge[2], edge[3], edge[0], edge[1]))
check("clamped box stays on the desktop",
      edge[0] + edge[2] <= 2000 and edge[1] + edge[3] <= 1200, str(edge))
top = rect(V, (100, 50), (900, 1400))
check("still 4:5 when clamped at the bottom", is_ratio(top, V), "%dx%d" % (top[2], top[3]))

# --- shift is the escape hatch from a lock, and means square without one
free = rect(V, (300, 300), (1200, 500), shift=True)
check("shift frees a locked ratio", not is_ratio(free, V) and free[2] == 900 and free[3] == 200,
      "%dx%d" % (free[2], free[3]))
sq = rect(None, (300, 300), (900, 500), shift=True)
check("shift alone still means square", sq[2] == sq[3], "%dx%d" % (sq[2], sq[3]))
fr = rect(None, (300, 300), (900, 500))
check("no ratio, no shift: free", fr == (300, 300, 600, 200), str(fr))

# --- 16:9 on a wide drag, and 1:1
r = rect(aspect_value("16:9"), (100, 100), (1700, 300))
check("16:9 from a wide drag", is_ratio(r, 16 / 9), "%dx%d" % (r[2], r[3]))
r = rect(1.0, (500, 500), (1300, 700))
check("1:1 is square", r[2] == r[3], "%dx%d" % (r[2], r[3]))

print("\nASPECT RATIO", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
