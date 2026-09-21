"""A capture that worked must say so, even with no window open.

MagCopy spends most of its life in the menu bar with the window closed, and a screenshot has no
preview by design - so a successful capture leaves nothing on screen at all. The picker flashes,
the image lands on the clipboard, and the only way to find out whether it worked is to paste
somewhere and look.

That is how a working build got reported as broken three times over: it was copying exactly what
was asked for, silently. The confirmation used to fire only for errors, which is precisely
backwards - an error at least leaves the shortcut visibly doing nothing, while success leaves no
trace whatever.
"""
import os, sys, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MAGCOPY_NO_AUTOSTART", "1")
os.environ.setdefault("MAGCOPY_INSTANCE_NAME", "MagCopy-feedback")

root = tk.Tk(); root.withdraw(); root.update()
from magcopy.theme import register_fonts
register_fonts()
from magcopy.app import App

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-54s %s %s" % (name, "ok  " if good else "FAIL", detail))


app = App(root)
root.update()

said = {"flash": [], "notify": []}
app.tray.flash = lambda text, seconds=2.2: said["flash"].append(text)
app.tray.notify = lambda title, text: said["notify"].append(text)

check("the tray can be asked to confirm something", hasattr(type(app.tray), "flash"))

# with the window open, the toast in the window is the confirmation and the menu bar stays quiet
app.show_window(); root.update()
app.toast("400 × 300 copied")
root.update()
check("with the window open it stays out of the menu bar",
      not said["flash"], "flashed %r" % said["flash"])

# with the window closed there is nowhere else for it to go
app.hide_window(); root.update()
said["flash"].clear(); said["notify"].clear()
app.toast("400 × 300 copied")
root.update()
check("with the window closed a success is confirmed", said["flash"] == ["400 × 300 copied"],
      "flashed %r" % said["flash"])

said["flash"].clear()
app.toast("could not reach the clipboard", error=True)
root.update()
check("and so is a failure", said["flash"] == ["could not reach the clipboard"],
      "flashed %r" % said["flash"])

import inspect
src = inspect.getsource(App.start_screenshot)
check("the screenshot path reports what it copied", "self.toast(" in src)
check("and leaves a line saying what it did", "log_error" in src)

app.quit()                    # already tears the root down
print("\nCAPTURE FEEDBACK", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
