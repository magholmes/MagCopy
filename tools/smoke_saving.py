"""Clicking save gif has to produce an answer immediately, and keep producing one.

The bug this covers: save() re-packed the progress bar with a plain pack(), which after a
pack_forget() appends to the END of the packing order - so the bar reappeared below the save
button in the window's bottom margin instead of above the status line. Nothing ever wrote
"saving" to the status either, so for the whole first minute the editor looked exactly as it
had before the click. Between them the program read as hung.
"""
import os
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat
from magcopy.theme import register_fonts

plat.set_dpi_aware()
register_fonts()
root = tk.Tk()
try:
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception:
    pass
from magcopy.app import App

app = App(root)
app.hide_window()
app.settings["save_dir"] = tempfile.mkdtemp(prefix="magcopy-saving-")
app.settings["open_folder_after_gif"] = True
revealed = []
app.reveal = lambda p: revealed.append((p, getattr(app.editor, "_done_win", None) is not None))

ok = True
seen = {"fracs": [], "texts": [], "btn": []}


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-54s %s %s" % (name, "ok  " if good else "FAIL", detail))


vx, vy, _, _ = plat.virtual_screen()


def step1():
    app._begin_recording((vx + 120, vy + 120, 480, 320))
    root.after(2500, app.stop_recording)
    root.after(4200, step2)


def step2():
    if app.editor is None:
        print("editor did not open")
        return root.quit()
    wait(0)


def wait(n):
    ed = app.editor
    if ed.frames:
        return go(ed)
    if n < 60:
        return root.after(250, lambda: wait(n + 1))
    print("no frames")
    root.quit()


def go(ed):
    before = ed.status.cget("text")
    seen["idle_text"] = before
    ed.timeline.set_range(0.2, 2.0)
    ed.save()
    root.update()
    # the click must have answered before a single slow thing has run
    seen["t0_text"] = ed.status.cget("text")
    seen["t0_frac"] = ed.prep.frac
    slaves = ed.prep.master.pack_slaves()
    seen["order"] = (slaves.index(ed.prep), slaves.index(ed.status))
    seen["btn0"] = ed.btn_save.text
    sample(0)


def sample(n):
    ed = app.editor
    if ed is None:
        return root.quit()
    seen["fracs"].append(ed.prep.frac)
    seen["texts"].append(ed.status.cget("text"))
    seen["btn"].append(ed.btn_save.text)
    if ed.saving and n < 200:
        return root.after(250, lambda: sample(n + 1))
    seen["final_btn"] = ed.btn_save.text
    seen["final_frac"] = ed.prep.frac
    root.after(700, root.quit)


root.after(600, step1)
root.mainloop()

# ---- what the click did
check("the click changes the status text at once",
      seen.get("t0_text") and seen["t0_text"] != seen.get("idle_text"), repr(seen.get("t0_text")))
check("and carries a clock, so a long silence still moves",
      seen.get("t0_text", "").rstrip().endswith("0s"), repr(seen.get("t0_text")))
check("the progress bar has a fraction immediately",
      seen.get("t0_frac") is not None and seen["t0_frac"] > 0, str(seen.get("t0_frac")))
check("the bar sits ABOVE the status line, not at the bottom",
      seen.get("order") and seen["order"][0] < seen["order"][1], str(seen.get("order")))
check("the save button says it is saving", seen.get("btn0") != "save gif", repr(seen.get("btn0")))

# ---- what it kept doing
fracs = [f for f in seen["fracs"] if f is not None]
check("the bar advances while it works", len(set(fracs)) > 1,
      "%d distinct of %d" % (len(set(fracs)), len(fracs)))
check("the bar never goes backwards", all(b >= a - 1e-9 for a, b in zip(fracs, fracs[1:])),
      str(fracs[:8]))
texts = [t for t in seen["texts"] if t]
check("the status keeps changing (a clock or a stage)", len(set(texts)) > 1,
      "%d distinct of %d" % (len(set(texts)), len(texts)))

# ---- and how it ended
check("the button gets its name back", seen.get("final_btn") == "save gif",
      repr(seen.get("final_btn")))
check("the bar finishes full", seen.get("final_frac") == 1.0, str(seen.get("final_frac")))
check("the folder was revealed without being asked", len(revealed) == 1, str(revealed))
check("and only after the done panel was up", bool(revealed) and revealed[0][1] is True,
      str(revealed[0][1]) if revealed else "never revealed")

print("\nSAVING FEEDBACK", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
