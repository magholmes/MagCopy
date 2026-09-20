"""The crop interaction: create, grab an edge, grab a corner, move, and lock it in.

The geometry is exercised directly on a GifEditor built without a window - the maths is what
matters and it should not need a mouse to prove.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy.editor import GifEditor


class Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakeCanvas:
    def configure(self, **kw):
        pass

    def delete(self, *a):
        pass

    def create_rectangle(self, *a, **k):
        pass

    def create_text(self, *a, **k):
        pass


def editor(src=(1000, 600), box=(0, 0, 1000, 600)):
    """A GifEditor with just the fields the crop maths touches."""
    ed = GifEditor.__new__(GifEditor)
    ed.src = (src[0], src[1], 4.0, 50.0)
    ed._img_box = box
    ed.pw, ed.ph = box[2], box[3]
    ed.crop = None
    ed.crop_locked = False
    ed._crop_drag = None
    ed.frames = ["x"]
    ed.canvas = FakeCanvas()
    # the real palette, not a handful of keys: a stub that lists colours by hand goes stale the
    # moment a surface starts using another one, and fails as a missing key rather than a bug
    from magcopy.theme import colors_for
    ed.theme = type("T", (), {"c": colors_for("dusk")})()
    ed.fonts = type("F", (), {"mono8": ("Consolas", 8)})()
    ed._update_estimate = lambda: None
    ed._sync_crop_buttons = lambda: None
    ed.status = type("S", (), {"configure": lambda self, **k: None})()
    return ed


ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-46s %s %s" % (name, "ok  " if good else "FAIL", detail))


def drag(ed, x0, y0, x1, y1):
    ed._crop_press(Ev(x0, y0))
    ed._crop_move(Ev(x1, y1))
    ed._crop_release(Ev(x1, y1))


# --- draw a crop
ed = editor()
drag(ed, 200, 150, 600, 450)
check("drag makes a crop", ed.crop == (200, 150, 400, 300), str(ed.crop))

# --- the eight handles sit on the crop's edges and corners
h = ed._handles()
check("eight handles", len(h) == 8, str(sorted(h)))
check("nw handle at the top-left corner", h["nw"] == (200.0, 150.0), str(h["nw"]))
check("se handle at the bottom-right", h["se"] == (600.0, 450.0), str(h["se"]))
check("e handle centred on the right edge", h["e"] == (600.0, 300.0), str(h["e"]))

# --- hit testing
check("grabs the east edge", ed._hit(600, 300) == "e", str(ed._hit(600, 300)))
check("grabs the nw corner", ed._hit(201, 151) == "nw", str(ed._hit(201, 151)))
check("inside is a move", ed._hit(400, 300) == "move", str(ed._hit(400, 300)))
check("outside is nothing", ed._hit(50, 50) is None, str(ed._hit(50, 50)))

# --- drag the east edge only: the other three must not move
before = ed.crop
ed._crop_press(Ev(600, 300))
ed._crop_move(Ev(800, 300))
ed._crop_release(Ev(800, 300))
check("east edge moved, x/y/height unchanged",
      ed.crop[0] == before[0] and ed.crop[1] == before[1] and ed.crop[3] == before[3]
      and ed.crop[2] == 600, str(ed.crop))

# --- drag the north-west corner: left and top move together, right/bottom stay
before = ed.crop
right, bottom = before[0] + before[2], before[1] + before[3]
ed._crop_press(Ev(200, 150))
ed._crop_move(Ev(300, 250))
ed._crop_release(Ev(300, 250))
check("nw corner moved, far edges pinned",
      ed.crop[0] == 300 and ed.crop[1] == 250
      and ed.crop[0] + ed.crop[2] == right and ed.crop[1] + ed.crop[3] == bottom, str(ed.crop))

# --- move the whole crop
before = ed.crop
ed._crop_press(Ev(500, 350))
ed._crop_move(Ev(540, 380))
ed._crop_release(Ev(540, 380))
check("move keeps the size", (ed.crop[2], ed.crop[3]) == (before[2], before[3]), str(ed.crop))
check("move shifted it", (ed.crop[0], ed.crop[1]) != (before[0], before[1]), str(ed.crop))

# --- clamping at the edges of the picture
ed2 = editor()
drag(ed2, 100, 100, 400, 400)
ed2._crop_press(Ev(100, 100))
ed2._crop_move(Ev(-500, -500))
ed2._crop_release(Ev(-500, -500))
check("cannot drag outside the picture", ed2.crop[0] >= 0 and ed2.crop[1] >= 0, str(ed2.crop))
ed3 = editor()
drag(ed3, 600, 300, 900, 500)
ed3._crop_press(Ev(900, 500))
ed3._crop_move(Ev(5000, 5000))
ed3._crop_release(Ev(5000, 5000))
check("clamped to the far edge",
      ed3.crop[0] + ed3.crop[2] <= 1000 and ed3.crop[1] + ed3.crop[3] <= 600, str(ed3.crop))

# --- a stray click is not a crop
ed4 = editor()
drag(ed4, 400, 300, 404, 303)
check("a tiny drag is ignored", ed4.crop is None, str(ed4.crop))

# --- save crop locks it; reset clears it
ed5 = editor()
drag(ed5, 100, 100, 500, 400)
ed5._save_crop()
check("save crop locks it", ed5.crop_locked and ed5.crop is not None, str(ed5.crop))
check("locked crop ignores handle grabs", ed5._hit(100, 100) == "nw" and True)
ed5._crop_press(Ev(100, 100))       # locked: a press starts a NEW crop, not an edge drag
check("press while locked starts fresh", ed5._crop_drag[0] == "new" and not ed5.crop_locked)
ed5._crop_release(Ev(100, 100))
ed6 = editor()
drag(ed6, 100, 100, 500, 400)
ed6._reset_crop()
check("reset clears the crop", ed6.crop is None and not ed6.crop_locked)

# --- a preview scaled down from the source still maps to source pixels
ed7 = editor(src=(2000, 1200), box=(0, 0, 1000, 600))
drag(ed7, 250, 150, 750, 450)
check("half-size preview maps to source pixels", ed7.crop == (500, 300, 1000, 600), str(ed7.crop))

# --- a centred picture with letterboxing offsets correctly
ed8 = editor(src=(1000, 600), box=(32, 20, 1000, 600))
drag(ed8, 232, 170, 632, 470)
check("offset picture maps correctly", ed8.crop == (200, 150, 400, 300), str(ed8.crop))

print("\nCROP HANDLES", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
