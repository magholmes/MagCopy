"""Record, open the editor, build the preview, trim, save - driven on a real Tk main loop."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from magcopy import win, optimize
from magcopy.theme import register_fonts

win.set_dpi_aware(); register_fonts()
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(SHOTS, exist_ok=True)
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
app = App(root)
app.settings["save_dir"] = os.path.join(os.environ["TEMP"], "magcopy_editor_test")
os.makedirs(app.settings["save_dir"], exist_ok=True)
app.hide_window()

state = {"res": None, "fail": None, "t0": time.time()}
orig = app.gif_saved
def saved(res):
    orig(res); state["res"] = res
app.gif_saved = res_hook = saved

def fail(msg):
    state["fail"] = msg; root.quit()

vx, vy, _, _ = win.virtual_screen()
def step1():
    app._begin_recording((vx + 80, vy + 80, 760, 480))
    root.after(3000, app.stop_recording)
    root.after(4200, step2)

def step2():
    ed = app.editor
    if ed is None: return fail("editor did not open")
    print("editor open; source =", ed.src)
    wait_preview(ed, 0)

def wait_preview(ed, n):
    if ed.frames:
        print("preview frames:", len(ed.frames), "at", ed.preview_fps, "fps")
        return root.after(400, lambda: step3(ed))
    if n > 240: return fail("preview never built")
    root.after(250, lambda: wait_preview(ed, n + 1))

def step3(ed):
    try:
        x, y = ed.top.winfo_rootx(), ed.top.winfo_rooty()
        a = win.grab_once(x - 2, y - 2, ed.top.winfo_width() + 4, ed.top.winfo_height() + 4)
        Image.fromarray(a[:, :, 2::-1], "RGB").save(os.path.join(SHOTS, "06_editor.png"))
    except Exception as e:
        print("editor screenshot failed:", e)
    ed.timeline.set_range(0.4, 2.4); ed._update_estimate(); ed.show_frame(1.0)
    ed.save()
    wait_save(0)

def wait_save(n):
    if state["res"]: return root.after(200, root.quit)
    if n > 1200: return fail("save never finished")
    root.after(250, lambda: wait_save(n + 1))

root.after(300, step1)
root.mainloop()

if state["fail"]:
    print("FAIL:", state["fail"]); app.quit(); sys.exit(1)
res = state["res"]
info = optimize.gif_info(res.path)
print("saved:", res)
print("gif: %dx%d frames=%d dur=%.2fs loop=%s %.2fMB delays=%s"
      % (info["width"], info["height"], info["frames"], info["duration"], info["loops"],
         info["bytes"] / 1e6, info["delays"]))
ok = True
if abs(info["duration"] - 2.0) > 0.3:
    print("FAIL: trim did not apply (%.2fs, wanted 2.0s)" % info["duration"]); ok = False
if not (res.bytes <= 10_000_000 and res.fits): print("FAIL: over budget"); ok = False
if not info["loops"]: print("FAIL: no loop block"); ok = False
if info["width"] != 760: print("note: width is %d (ladder stepped down)" % info["width"])
print("EDITOR SMOKE", "OK" if ok else "PROBLEM")
app.quit()
sys.exit(0 if ok else 1)
