"""Drive the region selector without a human: synthesise a drag and check the rect that comes back."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk
from magcopy import plat, overlay
from magcopy.theme import Theme, Fonts, register_fonts

plat.set_dpi_aware()
register_fonts()
root = tk.Tk(); root.withdraw()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
overlay.prewarm(root)

sel = overlay.RegionSelector(root, theme, fonts)
t0 = time.perf_counter()

def drive():
    print("overlay up in %.0f ms" % ((time.perf_counter() - t0) * 1000))
    sel.start, sel.cur, sel.dragging = (300, 200), (900, 620), True
    t = time.perf_counter()
    for i in range(30):                      # simulate a drag: 30 redraws
        sel.cur = (900 + i * 4, 620 + i * 3)
        sel._redraw()
        sel.top.update_idletasks()
    print("redraw during drag: %.2f ms/frame" % ((time.perf_counter() - t) / 30 * 1000))
    sel.shift = True; sel._redraw(); print("square rect:", sel._rect())
    sel.shift = False
    sel._nudge(-1, 0); sel._nudge(0, 2)
    sel._commit()

def _when_ready(sel, root, fn, tries=0):
    """Drive the picker once it is actually up, not after a guessed delay.

    Building the frozen still means grabbing the whole virtual desktop and resizing it, which on a
    loaded machine takes longer than any fixed timer anyone picked. Driving it early produced a
    picker that returned no rect and a screen sample of whatever was behind it - and it failed a
    different one of these tests on each run of the suite.
    """
    ready = False
    try:
        ready = bool(sel.top and sel.top.winfo_viewable() and getattr(sel, "draw_cv", None))
    except Exception:
        ready = False
    if ready or tries > 200:
        fn()
        return
    root.after(25, lambda: _when_ready(sel, root, fn, tries + 1))

_when_ready(sel, root, drive)
r = sel.run()
print("RESULT:", r)
vx, vy, vw, vh = plat.virtual_screen()
ok = r is not None and r[2] > 0 and r[3] > 0 and vx <= r[0] and vy <= r[1]
print("rect within virtual screen, non-empty:", ok)
root.destroy()
sys.exit(0 if ok else 1)
