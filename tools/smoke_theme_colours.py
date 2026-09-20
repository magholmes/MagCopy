"""Picking a theme must change the accents - and must not change what red means."""
import os, sys, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy.theme import THEMES, THEME_ORDER, Theme

ok = True
def check(n, good, d=""):
    global ok; ok = ok and good
    print("%-50s %s %s" % (n, "ok  " if good else "FAIL", d))

names = [k for k, _ in THEME_ORDER]

# the accent used by the selection outline, crosshair and crop handles
focus = {n: THEMES[n]["focus"] for n in names}
check("every theme has its own accent", len(set(focus.values())) == len(names), str(focus))

# the dim behind a selection, and behind a crop
shade = {n: THEMES[n]["shade"] for n in names}
check("every theme has its own dim", len(set(shade.values())) == len(names), str(shade))

# recording is red, the same red, everywhere
rec = {n: THEMES[n]["record"] for n in names}
check("recording is one colour in every theme", len(set(rec.values())) == 1, str(set(rec.values())))
def is_red(h):
    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    return r > 180 and r > g + 70 and r > b + 70
check("and that colour is red", all(is_red(v) for v in rec.values()), list(set(rec.values()))[0])

# every palette must define every key the surfaces ask for, or a theme switch throws
NEEDED = ["bg", "bg2", "hair", "hair_soft", "ink", "ink2", "mute", "mute2", "paper_ink",
          "focus", "error", "ok", "record", "shade", "sel_fill"]
for n in names:
    missing = [k for k in NEEDED if k not in THEMES[n]]
    check("%s defines every colour used" % n, not missing, ",".join(missing))

# accents must be visible against their own background
def lum(h):
    r, g, b = (int(h[i:i+2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
for n in names:
    c = THEMES[n]
    check("%s accent reads on its background" % n, contrast(c["focus"], c["bg"]) >= 2.4,
          "%.1f:1" % contrast(c["focus"], c["bg"]))
    check("%s red reads on its background" % n, contrast(c["record"], c["bg"]) >= 2.4,
          "%.1f:1" % contrast(c["record"], c["bg"]))

# switching a live Theme really does hand out the new colours
root = tk.Tk(); root.withdraw()
t = Theme("dusk")
seen = []
class Probe:
    def restyle(self, c): seen.append(c["focus"])
t.add(Probe())
for n in names[1:]:
    t.set(n)
check("a live theme switch pushes new colours", seen == [THEMES[n]["focus"] for n in names],
      "%d updates" % len(seen))
root.destroy()

print("\nTHEME COLOURS", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
