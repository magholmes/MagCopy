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

def _front_to_back(sel):
    """Which of the picker's two layers is in front, as the window server sees it."""
    import Quartz
    ours = {}
    if sel._top_hwnd is not None:
        ours[int(sel._top_hwnd.windowNumber())] = "dim"
    if sel._under_hwnd is not None:
        ours[int(sel._under_hwnd.windowNumber())] = "still"
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
        name = ours.get(int(w.get("kCGWindowNumber", 0)))
        if name:
            out.append(name)
    return out


SEL = (300, 280, 520, 360)          # selector-local, well inside the white panel
res = {}
sel = overlay.RegionSelector(root, theme, fonts)      # frozen: live=False

def drive():
    sel.start, sel.cur, sel.dragging = (SEL[0], SEL[1]), (SEL[0] + SEL[2], SEL[1] + SEL[3]), True
    if plat.IS_MAC:
        res["level_before"] = sel._top_hwnd.level()
        # Every layer must cover the screen exactly. Tk maps a full-screen window 38 points low,
        # clear of the menu bar, and a layer left there is wrong in two visible ways: the still
        # seen through the selection is offset by the height of the menu bar - which reads as the
        # top bar duplicating - and anything drawn on it, the crosshair above all, sits that far
        # from the pointer it is marking.
        res["frames"] = {name: tuple(int(v) for v in (h.frame().origin.x, h.frame().origin.y,
                                                      h.frame().size.width, h.frame().size.height))
                         for name, h in (("dim", sel._top_hwnd), ("still", sel._under_hwnd),
                                         ("chrome", sel._chrome_hwnd)) if h is not None}
    sel._redraw(); sel.top.update_idletasks(); root.update()
    if plat.IS_MAC:
        # The dim layer and the undimmed still beneath it are stacked by level, so the dim layer
        # losing its level puts it *behind* the still - the screen stops looking dimmed and the
        # rectangle, ticks and readout all vanish, from the first redraw onward. Everything else
        # goes on working, which makes it invisible to any test that only checks the rect.
        res["level_after"] = sel._top_hwnd.level()
        res["under_level"] = sel._under_hwnd.level() if sel._under_hwnd else None
        res["order"] = _front_to_back(sel)
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
if plat.IS_MAC:
    if res.get("level_after") != res.get("level_before"):
        print("FAIL: the dim layer lost its window level when the hole was punched (%s -> %s)"
              % (res.get("level_before"), res.get("level_after"))); ok = False
    want = (0, 0, sel.vw, sel.vh)
    for name, frame in (res.get("frames") or {}).items():
        if frame != want:
            print("FAIL: the %s layer is at %s, not covering the screen at %s"
                  % (name, frame, want)); ok = False
    if res.get("order") != ["dim", "still"]:
        print("FAIL: the dim layer is not in front of the still - order was %r"
              % (res.get("order"),)); ok = False
    else:
        print("layer order during the drag   : %s (dim in front, as it must be)"
              % " then ".join(res["order"]))
print("\nFROZEN PICKER", "OK" if ok else "PROBLEM")
root.destroy()
sys.exit(0 if ok else 1)
