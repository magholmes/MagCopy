"""A drag must register wherever the press lands.

The live region picker stacks two full-screen windows: a dim layer that takes the mouse, and a
chrome layer above it carrying the crosshair and readout. The chrome is supposed to be
click-through. When that failed it was only click-through where its pixels matched its colour
key, so a press landing on something drawn - the crosshair sits under the pointer, so most of
them - went to a window with no handlers and the drag never started. It worked about one try in
five. This drives the events at both layers and insists on a selection either way.
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import overlay, win
from magcopy.theme import Fonts, Theme, register_fonts

win.set_dpi_aware()
register_fonts()
root = tk.Tk()
root.withdraw()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail))


def drag_on(which, live=True):
    """Drive a real press/drag/release at one of the two layers; return the rect that comes back."""
    sel = overlay.RegionSelector(root, theme, fonts, live=live)
    out = {}

    def go():
        cv = sel.cv if which == "dim" else getattr(sel, "chrome_cv", sel.cv)
        out["passthrough"] = getattr(sel, "_chrome_passthrough", None)
        # event_generate only queues; the handlers do not run until the loop turns. Flush after
        # each one, or the checks below race the events they just posted.
        try:
            for ev, x, y in (("<Motion>", 420, 360), ("<ButtonPress-1>", 420, 360),
                             ("<B1-Motion>", 900, 700), ("<ButtonRelease-1>", 900, 700)):
                if not sel.top.winfo_exists():
                    break
                cv.event_generate(ev, x=x, y=y)
                sel.top.update()
        except Exception as e:
            out["err"] = str(e)
        try:
            if sel.top.winfo_exists():        # release did not commit: do not hang the test
                sel._cancel()
        except Exception:
            pass

    sel.root.after(90, go)
    out["rect"] = sel.run()
    return out


r = drag_on("dim")
check("live overlay: a drag registers", bool(r["rect"]) and r["rect"][2] > 300, str(r["rect"]))
check("the chrome layer is click-through", r.get("passthrough") is True, str(r.get("passthrough")))

r = drag_on("dim", live=False)
check("frozen overlay: a drag registers", bool(r["rect"]) and r["rect"][2] > 300, str(r["rect"]))

# ten in a row, because the bug this covers only showed up some of the time
runs = [drag_on("dim") for _ in range(10)]
good = [x for x in runs if x["rect"] and x["rect"][2] > 300]
check("ten drags in a row all register", len(good) == 10, "%d of 10" % len(good))
check("and the chrome layer was click-through every time",
      all(x.get("passthrough") is True for x in runs),
      "%d of 10" % sum(1 for x in runs if x.get("passthrough") is True))

print("\nDRAG", "OK" if ok else "PROBLEM")
root.destroy()
sys.exit(0 if ok else 1)
