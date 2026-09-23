"""Live mode: the screen must NOT be frozen, and the selection must be undimmed."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, overlay
from magcopy.theme import Theme, Fonts, register_fonts

plat.set_dpi_aware(); register_fonts()
root = tk.Tk(); root.withdraw()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
vx, vy, vw, vh = plat.virtual_screen()

# a white reference panel that also CHANGES, so a frozen overlay would be caught out
back = tk.Toplevel(root); back.overrideredirect(True)
back.geometry("700x460+%d+%d" % (vx + 260, vy + 240))
back.configure(bg="#FFFFFF"); back.deiconify()
for _ in range(20): root.update(); time.sleep(0.01)

SEL = (300, 280, 520, 360)          # selector-local
res = {}
sel = overlay.RegionSelector(root, theme, fonts, live=True)

def drive():
    sel.start, sel.cur, sel.dragging = (SEL[0], SEL[1]), (SEL[0] + SEL[2], SEL[1] + SEL[3]), True
    sel._redraw(); sel.top.update_idletasks(); root.update()
    time.sleep(0.35); root.update()
    # inside the selection (hole) vs outside (dimmed)
    res["inside"] = plat.grab_once(vx + SEL[0] + 60, vy + SEL[1] + 60, 120, 80)[:, :, 2::-1].mean()
    res["outside"] = plat.grab_once(vx + 290, vy + 250, 120, 80)[:, :, 2::-1].mean()
    # now change what is behind: a frozen overlay would keep showing white
    back.configure(bg="#101010")
    for _ in range(10): root.update(); time.sleep(0.02)
    time.sleep(0.3); root.update()
    res["after_change"] = plat.grab_once(vx + SEL[0] + 60, vy + SEL[1] + 60, 120, 80)[:, :, 2::-1].mean()
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
rect = sel.run()
back.destroy(); root.update()

print("selection (hole)  mean brightness: %.1f" % res.get("inside", -1))
print("outside (dimmed)  mean brightness: %.1f" % res.get("outside", -1))
print("after the backdrop changed       : %.1f" % res.get("after_change", -1))
print("rect returned:", rect)
ok = True
if not (res.get("inside", 0) > res.get("outside", 999) + 25):
    print("FAIL: the selection is not brighter than the dimmed surround"); ok = False
if not (res.get("after_change", 999) < res.get("inside", 0) - 40):
    print("FAIL: the overlay is frozen - it did not follow the screen"); ok = False
if not rect: print("FAIL: no rect"); ok = False
print("\nLIVE OVERLAY", "OK" if ok else "PROBLEM")
root.destroy()
sys.exit(0 if ok else 1)
