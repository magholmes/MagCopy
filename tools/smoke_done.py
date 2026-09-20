"""After saving: the editor stays open, the panel appears, and both answers do the right thing."""
import os
import sys
import tempfile
import time
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
tmp = tempfile.mkdtemp(prefix="magcopy-done-")
app.settings["save_dir"] = tmp
app.settings["open_folder_after_gif"] = True         # the behaviour under test...
revealed = []
app.reveal = lambda p: revealed.append(p)           # ...with Explorer stubbed out

ok = True
state = {}


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-52s %s %s" % (name, "ok  " if good else "FAIL", detail))


vx, vy, _, _ = plat.virtual_screen()


def step1():
    app._begin_recording((vx + 120, vy + 120, 560, 360))
    root.after(1800, app.stop_recording)
    root.after(3400, step2)


def step2():
    ed = app.editor
    if ed is None:
        state["fail"] = "editor did not open"
        return root.quit()
    state["ed"] = ed
    wait_preview(ed, 0)


def wait_preview(ed, n):
    if ed.frames:
        ed.timeline.set_range(0.2, 1.2)
        ed.save()
        return wait_save(0)
    if n > 240:
        state["fail"] = "preview never built"
        return root.quit()
    root.after(250, lambda: wait_preview(ed, n + 1))


def wait_save(n):
    ed = state["ed"]
    if ed.result is not None and getattr(ed, "_done_win", None) is not None:
        return root.after(400, step3)
    if n > 1200:
        state["fail"] = "save never finished (result=%s)" % (ed.result,)
        return root.quit()
    root.after(250, lambda: wait_save(n + 1))


def step3():
    ed = state["ed"]
    check("the gif was written", ed.result is not None and os.path.exists(ed.result.path),
          os.path.basename(ed.result.path))
    check("the editor is still open", bool(ed.top.winfo_exists()))
    check("a done panel appeared", ed._done_win is not None and ed._done_win.winfo_exists())
    check("save and play are usable again", ed.btn_save.enabled and ed.btn_play.enabled)
    check("the file was revealed", len(revealed) == 1, os.path.basename(revealed[0]) if revealed else "-")
    check("revealed the file just written", bool(revealed) and revealed[0] == ed.result.path)

    # "keep editing" dismisses the panel and leaves a working editor
    ed._dismiss_done()
    root.update()
    check("keep editing closes the panel", ed._done_win is None)
    check("editor survives keep-editing", bool(ed.top.winfo_exists()))
    ed.show_frame(0.5)
    check("the preview still works afterwards", ed._photo is not None)

    # saving again writes a second file and raises a fresh panel
    before = {f for f in os.listdir(tmp) if f.endswith(".gif")}
    state["before"] = before
    time.sleep(1.1)                       # the name carries a second-resolution timestamp
    ed.save()
    wait_second(0)


def wait_second(n):
    ed = state["ed"]
    now = {f for f in os.listdir(tmp) if f.endswith(".gif")}
    if len(now) > len(state["before"]) and ed._done_win is not None:
        check("saving again writes another file", True, "%d gifs" % len(now))
        check("and raises the panel again", ed._done_win.winfo_exists())
        # "done" closes the editor for good
        ed._dismiss_done()
        ed.close()
        root.update()
        check("done closes the editor", not ed.top.winfo_exists())
        check("the app let go of it", app.editor is None)
        return root.quit()
    if n > 1200:
        state["fail"] = "second save never finished"
        return root.quit()
    root.after(250, lambda: wait_second(n + 1))


root.after(400, step1)
root.mainloop()

if state.get("fail"):
    print("FAIL:", state["fail"])
    ok = False
print("\nSAVE-AND-ASK", "OK" if ok else "PROBLEM")
try:
    app.quit()
except Exception:
    pass
sys.exit(0 if ok else 1)

