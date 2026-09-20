"""Frozen mode: the picker must NOT follow the screen, and the selection must be undimmed.

The exact inverse of smoke_live_overlay. Freezing is the feature here - a menu or a tooltip stays
put while you frame it - so the test changes the screen behind the picker and insists that what
shows through the selection does not change with it. It samples the screen rather than asking the
code what it drew, because the two have disagreed before.
"""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, overlay
from magcopy.theme import Theme, Fonts, register_fonts

plat.set_dpi_aware(); register_fonts()
root = tk.Tk(); root.withdraw()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
vx, vy, vw, vh = plat.virtual_screen()

back = tk.Toplevel(root); back.overrideredirect(True)
back.geometry("700x460+%d+%d" % (vx + 260, vy + 240))
back.configure(bg="#FFFFFF"); back.attributes("-topmost", True); back.deiconify()
for _ in range(25): root.update(); time.sleep(0.01)
time.sleep(0.3); root.update()

SEL = (300, 280, 520, 360)          # selector-local, well inside the white panel
res = {}
sel = overlay.RegionSelector(root, theme, fonts)      # frozen: live=False

def drive():
    sel.start, sel.cur, sel.dragging = (SEL[0], SEL[1]), (SEL[0] + SEL[2], SEL[1] + SEL[3]), True
    sel._redraw(); sel.top.update_idletasks(); root.update()
    time.sleep(0.35); root.update()
    # sample well inside the selection, and a patch of dim that the selection does not reach
    res["inside"] = plat.grab_once(vx + SEL[0] + 80, vy + SEL[1] + 80, 120, 80)[:, :, 2::-1].mean()
    res["outside"] = plat.grab_once(vx + 280, vy + 245, 120, 25)[:, :, 2::-1].mean()
    back.configure(bg="#101010")                      # change the world behind the picker
    for _ in range(12): root.update(); time.sleep(0.02)
    time.sleep(0.35); root.update()
    res["after_change"] = plat.grab_once(vx + SEL[0] + 80, vy + SEL[1] + 80, 120, 80)[:, :, 2::-1].mean()
    sel._commit()

root.after(150, drive)
rect = sel.run()
back.destroy(); root.update()

print("selection          mean brightness: %.1f" % res.get("inside", -1))
print("dimmed surround    mean brightness: %.1f" % res.get("outside", -1))
print("after the backdrop changed        : %.1f" % res.get("after_change", -1))
print("rect returned:", rect)
ok = True
if not (res.get("inside", 0) > res.get("outside", 999) + 25):
    print("FAIL: the selection is not brighter than the dimmed surround"); ok = False
if abs(res.get("after_change", -999) - res.get("inside", 0)) > 12:
    print("FAIL: the picker followed the screen - it is not frozen"); ok = False
if not rect:
    print("FAIL: no rect"); ok = False
print("\nFROZEN PICKER", "OK" if ok else "PROBLEM")
root.destroy()
sys.exit(0 if ok else 1)
