"""Render the window for the readme, with a neutral save path (no username in the shot)."""
import os, sys, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from magcopy import win
from magcopy.theme import register_fonts

win.set_dpi_aware(); register_fonts()
root = tk.Tk()
try: root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception: pass
from magcopy.app import App
app = App(root)
app.folder_label.configure(text=r"C:\Users\you\Pictures\MagCopy")
docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
os.makedirs(docs, exist_ok=True)

def shoot(name):
    root.update(); root.update_idletasks(); time.sleep(0.4); root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    a = win.grab_once(x, y, root.winfo_width(), root.winfo_height())
    Image.fromarray(a[:, :, 2::-1], "RGB").save(os.path.join(docs, name))
    print("wrote", name)

def go():
    shoot("window.png")
    for t in ("paper", "ember"):
        app.set_theme(t); app.folder_label.configure(text=r"C:\Users\you\Pictures\MagCopy")
        shoot("window-%s.png" % t)
    app.set_theme("dusk")
    app.quit(); root.quit()

root.after(600, go)
root.mainloop()
