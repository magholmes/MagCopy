"""The whole path the shortcut takes: start_gif -> region selector -> recording frame on screen."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import win, overlay
from magcopy.theme import register_fonts

win.set_dpi_aware(); register_fonts()
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
app = App(root)
app.hide_window()
want = app.theme.c["record"]
wr, wg, wb = int(want[1:3], 16), int(want[3:5], 16), int(want[5:7], 16)

vx, vy, _, _ = win.virtual_screen()
SEL = ((360, 300), (980, 700))                       # drag in selector-local coords
state = {"rect": None, "samples": []}

orig_run = overlay.RegionSelector.run
def driven(self):
    def go():
        try:
            self.start, self.cur, self.dragging = SEL[0], SEL[1], True
            self._redraw(); self.top.update_idletasks(); self._commit()
        except Exception as e:
            print("drive failed:", e)
    self.root.after(150, go)
    return orig_run(self)
overlay.RegionSelector.run = driven

def near(px):
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    if abs(r - wr) < 26 and abs(g - wg) < 26 and abs(b - wb) < 26: return True
    return r > g + 24 and r > b + 24 and r > 55

def sample(tag):
    rf = app.record_frame
    if rf is None:
        state["samples"].append((tag, None)); print("  %-20s NO RecordFrame object" % tag); return
    x, y, w_, h_ = rf.rect
    rgb = win.grab_once(x - 8, y - 8, w_ + 16, h_ + 16)[:, :, 2::-1]
    patches = {"top": rgb[2:9, 8 + w_ // 2 - 2:8 + w_ // 2 + 2],
               "left": rgb[8 + h_ // 2 - 2:8 + h_ // 2 + 2, 2:9],
               "corner tl": rgb[1:10, 14:32]}
    hits = {k: any(near(px) for px in p.reshape(-1, 3)) for k, p in patches.items()}
    state["samples"].append((tag, hits))
    print("  %-20s %s  (windows alive: %d)" % (tag, hits, len(rf.windows)))

def after_start():
    state["rect"] = app.record_frame.rect if app.record_frame else None
    print("recorder running:", app.recorder is not None, "| frame rect:", state["rect"])
    print("show_recording_frame:", app.settings["show_recording_frame"])
    root.after(500,  lambda: sample("0.5s in"))
    root.after(1500, lambda: sample("1.5s in"))
    root.after(2000, app.stop_recording)
    root.after(3600, root.quit)

root.after(400, lambda: (app.start_gif(), root.after(700, after_start)))
root.mainloop()

good = [s for s in state["samples"] if s[1]]
ok = bool(good) and all(all(h.values()) for _, h in good) and len(good) == len(state["samples"])
print("\nFULL FLOW", "OK" if ok else "FRAME MISSING IN THE REAL FLOW")
try: app.quit()
except Exception: pass
sys.exit(0 if ok else 1)
