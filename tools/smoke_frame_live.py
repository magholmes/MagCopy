"""Is the rectangle on screen DURING a real recording, started the way the app starts one?"""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat
from magcopy.theme import register_fonts

plat.set_dpi_aware(); register_fonts()
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
app = App(root)
app.hide_window()
want = app.theme.c["record"]
wr, wg, wb = int(want[1:3], 16), int(want[3:5], 16), int(want[5:7], 16)
print("show_recording_frame setting:", app.settings["show_recording_frame"])
print("frame colour", want)

vx, vy, _, _ = plat.virtual_screen()
RECT = (vx + 350, vy + 350, 640, 400)
state = {"samples": [], "done": False}

def near(px):
    """The corners are opaque; the continuous edge is drawn at low alpha, so it composites with
    whatever is behind it. Test the edge for a clear red shift rather than for an exact colour."""
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    if abs(r - wr) < 26 and abs(g - wg) < 26 and abs(b - wb) < 26:
        return True                                  # solid: a corner bracket
    return r > g + 24 and r > b + 24 and r > 55      # blended: the soft edge

def sample(tag):
    x, y, w_, h_ = RECT
    shot = plat.grab_once(x - 6, y - 6, w_ + 12, h_ + 12)
    rgb = shot[:, :, 2::-1]
    # sample small patches rather than single pixels: the exact row a 3 px bracket lands on
    # shifts with scaling, and a one-pixel miss should not read as "the frame is not there"
    patches = {
        "top":       rgb[3:8, 6 + w_ // 2 - 2:6 + w_ // 2 + 2],
        "bottom":    rgb[6 + h_ - 1:6 + h_ + 5, 6 + w_ // 2 - 2:6 + w_ // 2 + 2],
        "left":      rgb[6 + h_ // 2 - 2:6 + h_ // 2 + 2, 3:8],
        "right":     rgb[6 + h_ // 2 - 2:6 + h_ // 2 + 2, 6 + w_ - 1:6 + w_ + 5],
        "corner tl": rgb[1:9, 14:30],
        "corner br": rgb[6 + h_ - 2:6 + h_ + 6, 6 + w_ - 30:6 + w_ - 14],
    }
    hits, seen = {}, {}
    for k, patch in patches.items():
        flat = patch.reshape(-1, 3)
        hits[k] = any(near(px) for px in flat)
        best = max(flat, key=lambda px: int(px[0]) - int(px[1]) - int(px[2]))
        seen[k] = tuple(int(c) for c in best)
    state["samples"].append((tag, hits, seen))
    print("  %-22s %s" % (tag, hits))

def go():
    app._begin_recording(RECT)
    root.after(400,  lambda: sample("0.4s into recording"))
    root.after(1200, lambda: sample("1.2s into recording"))
    root.after(2200, lambda: sample("2.2s into recording"))
    root.after(2600, app.stop_recording)
    root.after(4200, finish)

def finish():
    state["done"] = True
    root.quit()

root.after(600, go)
root.mainloop()

ok = bool(state["samples"]) and all(all(h.values()) for _, h, _ in state["samples"])
if not ok:
    print("\nFAIL - the rectangle was not on screen during the recording")
    for tag, hits, px in state["samples"]:
        print("   %s -> %s" % (tag, px))
print("\nLIVE FRAME", "OK" if ok else "MISSING")
try:
    app.quit()
except Exception:
    pass
sys.exit(0 if ok else 1)
