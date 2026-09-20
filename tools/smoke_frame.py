"""Is the recording rectangle actually on screen? Sample the pixels where its strips should be."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from magcopy import win
from magcopy.theme import Theme, Fonts, register_fonts
from magcopy.recorder import RecordFrame

win.set_dpi_aware(); register_fonts()
root = tk.Tk(); root.withdraw()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
want = theme.c["record"]
wr, wg, wb = int(want[1:3], 16), int(want[3:5], 16), int(want[5:7], 16)
print("expecting the frame colour", want, (wr, wg, wb))

vx, vy, _, _ = win.virtual_screen()
rect = (vx + 300, vy + 300, 600, 400)
rf = RecordFrame(root, theme, fonts, rect, lambda: None, lambda: None, 1.0)
rf.show()
for _ in range(40):
    root.update(); time.sleep(0.02)
time.sleep(0.5); root.update()

x, y, w_, h_ = rect
t = 2
shot = win.grab_once(x - 6, y - 6, w_ + 12, h_ + 12)      # a little margin around the rect
rgb = shot[:, :, 2::-1].astype(int)

def near(px):
    """The corners are opaque; the continuous edge is drawn at low alpha, so it composites with
    whatever is behind it. Test the edge for a clear red shift rather than for an exact colour."""
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    if abs(r - wr) < 26 and abs(g - wg) < 26 and abs(b - wb) < 26:
        return True                                  # solid: a corner bracket
    return r > g + 24 and r > b + 24 and r > 55      # blended: the soft edge

samples = {
    "top edge":       rgb[5, 6 + w_ // 2],
    "bottom edge":    rgb[6 + h_ + 0, 6 + w_ // 2],
    "left edge":      rgb[6 + h_ // 2, 5],
    "right edge":     rgb[6 + h_ // 2, 6 + w_ + 0],
    "corner bracket": rgb[3, 22],
}
ok = True
for name, px in samples.items():
    hit = near(px)
    ok = ok and hit
    print("  %-12s rgb%-18s %s" % (name, tuple(int(v) for v in px), "FRAME VISIBLE" if hit else "not drawn"))
# The centre must be tested strictly, not with the loose red-shift rule the soft edge needs:
# ordinary warm screen content (pink, skin tones, a white window) trips that rule and would
# read as "the frame is covering the capture area". That nothing overlaps the capture rectangle
# is proved properly by the geometry check in smoke_realistic.py; here it is only a sanity look.
inside = rgb[6 + h_ // 2, 6 + w_ // 2]
strict = (abs(int(inside[0]) - wr) < 18 and abs(int(inside[1]) - wg) < 18
          and abs(int(inside[2]) - wb) < 18)
print("  %-12s rgb%-18s (must not be solid frame colour)" % ("centre", tuple(int(v) for v in inside)))
if strict:
    print("  FAIL: the frame is covering the capture area"); ok = False
rf.destroy(); root.update(); root.destroy()
print("\nRECORDING FRAME", "OK" if ok else "NOT VISIBLE")
sys.exit(0 if ok else 1)
