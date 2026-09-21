"""Open the screenshot picker and record exactly which input events reach it.

Run it, then drag a box the way you would to take a screenshot. It does this twice - once with
the Tk grab the picker normally takes, once without - and writes what it saw to
diagnose_picker.txt, so the difference between "no events at all" and "events, but not where the
picker is listening" is visible rather than guessed at.

It exists because the picker covers the screen and is driven entirely by the pointer, and there
is no way to synthesise a real click without the Accessibility permission the app deliberately
does not ask for. The only honest way to find out which events arrive is to have a person make
some and look.
"""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MAGCOPY_NO_AUTOSTART", "1")
os.environ.setdefault("MAGCOPY_INSTANCE_NAME", "MagCopy-diagnose")

root = tk.Tk(); root.withdraw(); root.update()
from magcopy import overlay, plat
from magcopy.settings import SETTINGS_DIR
from magcopy.theme import Theme, Fonts, register_fonts

register_fonts()
theme, fonts = Theme("dusk"), Fonts(root, 1.0)
overlay.prewarm(root)
report = []
SECONDS = 14


def run_round(label, take_grab):
    seen = {}

    def note(kind, e=None):
        seen[kind] = seen.get(kind, 0) + 1

    sel = overlay.RegionSelector(root, theme, fonts)
    if not take_grab:
        sel._claim_screen = _no_grab_claim(sel)

    def arm():
        cv, top = sel.cv, sel.top
        for widget, tag in ((cv, "canvas"), (top, "toplevel")):
            widget.bind("<Motion>", lambda e, t=tag: note(t + ":motion", e), add="+")
            widget.bind("<ButtonPress-1>", lambda e, t=tag: note(t + ":press", e), add="+")
            widget.bind("<B1-Motion>", lambda e, t=tag: note(t + ":drag", e), add="+")
            widget.bind("<ButtonRelease-1>", lambda e, t=tag: note(t + ":release", e), add="+")
        top.bind("<Key>", lambda e: note("key", e), add="+")
        t = sel._top_hwnd
        report.append("  window: level=%s opaque=%s alpha=%.2f ignoresMouse=%s "
                      "acceptsMouseMoved=%s isKey=%s grab=%r"
                      % (t.level(), t.isOpaque(), t.alphaValue(), t.ignoresMouseEvents(),
                         t.acceptsMouseMovedEvents(), t.isKeyWindow(),
                         str(root.call("grab", "current"))))

    root.after(300, arm)
    print("\n>>> %s - DRAG A BOX now (%ds)" % (label, SECONDS), flush=True)
    root.after(SECONDS * 1000, lambda: sel._cancel())
    rect = sel.run()
    report.append("%s -> picker returned %r" % (label, rect))
    for kind in ("canvas:motion", "canvas:press", "canvas:drag", "canvas:release",
                 "toplevel:motion", "toplevel:press", "toplevel:drag", "toplevel:release", "key"):
        report.append("    %-18s %s" % (kind, seen.get(kind, 0)))
    if not seen:
        report.append("    NOTHING AT ALL")
    report.append("")


def _no_grab_claim(sel):
    original = sel._claim_screen

    def claim():
        original()
        try:
            sel.top.grab_release()
        except Exception:
            pass
    return claim


print("Two rounds. Drag a box in each; the screen dims while it waits.")
run_round("ROUND 1: as the app does it (with the Tk grab)", True)
time.sleep(1.0)
run_round("ROUND 2: same picker, grab released", False)

text = "\n".join(report)
print("\n" + text)
try:
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    out = os.path.join(SETTINGS_DIR, "diagnose_picker.txt")
    open(out, "w").write(text + "\n")
    print("written to", out)
except Exception as e:
    print("could not write the report:", e)
root.destroy()
