"""Build the application icons from the source artwork.

  python tools/make_icon.py path/to/image.jpg [out.ico] [--crop L,T,R,B]
  python tools/make_icon.py path/to/image.jpg --icns        # icon.icns + menubar.png, for macOS

`--crop` picks the part of the photograph the icon is made from, before the square crop. The
subject usually wants to fill the frame: a wide shot that reads fine at 256 px is a smudge of
background at 16, where the icon actually earns its keep.

An .ico is not one picture, it is a set. Windows picks a size per context - 16 px in the tray and
title bar, 32 in Explorer's list, 256 on the desktop - and if the file only holds a big one it
gets scaled down on the fly, which turns detail to mush at tray size. So every standard size is
rendered separately here, and the small ones are sharpened after the downscale to put back the
edge contrast that any good resampling filter necessarily removes.
"""
import io
import os
import struct
import sys

from PIL import Image, ImageEnhance, ImageFilter

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

# An .icns wants specific names at specific sizes, in 1x and 2x pairs. macOS picks per context
# the same way Windows does, and the same argument applies: render each one rather than letting
# something else scale a big one down.
#
# It stops at 512 rather than the 1024 a full set allows. The source artwork is a 300 px cut-out,
# so a 1024 px slice is three times its real resolution - invented detail, blurry where Finder
# shows it largest, and a megabyte and a half of file for the privilege.
ICNS_SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
              (256, 1), (256, 2), (512, 1)]
MENUBAR_PX = 44                  # 22pt at 2x - the menu bar is 22pt tall


def square(im):
    """Make it square.

    An opaque picture is centre-cropped - letterboxing an icon wastes the small sizes on bars.
    A cut-out with transparency is padded instead: cropping it would slice the subject, and the
    padding costs nothing because it is invisible.
    """
    w, h = im.size
    if w == h:
        return im
    if im.mode == "RGBA" and im.getextrema()[3][0] < 255:
        side = max(w, h)
        out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        out.paste(im, ((side - w) // 2, (side - h) // 2), im)
        return out
    side = min(w, h)
    return im.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))


def render(im, size):
    out = im.resize((size, size), Image.LANCZOS)
    if size > 48:
        return out
    # Downscaling always costs local contrast; at tray size that is the difference between a
    # recognisable shape and a smudge. Sharpen the colour only - running an unsharp mask over the
    # alpha channel puts a bright rim around a cut-out, which is exactly the halo the cut avoided.
    radius = 0.6 if size <= 24 else 0.8
    alpha = out.split()[-1] if out.mode == "RGBA" else None
    rgb = out.convert("RGB").filter(ImageFilter.UnsharpMask(radius=radius, percent=110, threshold=2))
    rgb = ImageEnhance.Color(rgb).enhance(1.12)
    if alpha is None:
        return rgb
    rgb = rgb.convert("RGBA")
    rgb.putalpha(alpha)
    return rgb


def write_ico(frames, out_path):
    """Write the ICO container directly, one PNG per size.

    Pillow's ICO writer takes a single image and resizes it itself, so the per-size sharpening
    above would be thrown away. The format is small enough to emit honestly: a 6-byte header, a
    16-byte directory entry per image, then the encoded images. Windows has accepted PNG-encoded
    entries at every size since Vista.
    """
    blobs = []
    for im in frames:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())
    n = len(blobs)
    offset = 6 + 16 * n
    out = [struct.pack("<HHH", 0, 1, n)]                       # reserved, type=icon, count
    for im, blob in zip(frames, blobs):
        w, h = im.size
        out.append(struct.pack("<BBBBHHII",
                               0 if w >= 256 else w,           # 0 means 256 in this field
                               0 if h >= 256 else h,
                               0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    with open(out_path, "wb") as fh:
        fh.write(b"".join(out) + b"".join(blobs))
    return out_path


def build_icns(src_path, out_dir, crop=None):
    """icon.icns via iconutil, plus the small PNG the menu bar item uses.

    iconutil is the only supported way to write an .icns, and it takes a directory of PNGs named
    exactly as below. The menu bar image is separate and deliberately small: NSImage scales it to
    18pt, and handing it a 512px photograph to shrink every time the bar redraws is wasteful and
    softer than doing it once here.
    """
    import shutil
    import subprocess
    im = _load(src_path, crop)
    iconset = os.path.join(out_dir, "MagCopy.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    for base, mult in ICNS_SIZES:
        px = base * mult
        name = "icon_%dx%d%s.png" % (base, base, "@2x" if mult == 2 else "")
        render(im, px).save(os.path.join(iconset, name), format="PNG", optimize=True)
    icns = os.path.join(out_dir, "icon.icns")
    r = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    shutil.rmtree(iconset, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError("iconutil failed: %s" % r.stdout.decode("utf-8", "replace"))
    menubar = os.path.join(out_dir, "menubar.png")
    render(im, MENUBAR_PX).save(menubar, format="PNG", optimize=True)
    return icns, menubar


def _load(src_path, crop=None):
    im = Image.open(src_path)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    if crop:
        im = im.crop(crop)
    return square(im)


def build(src_path, out_path, crop=None):
    im = _load(src_path, crop)
    return write_ico([render(im, s) for s in SIZES], out_path)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    src = argv[0]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rest = [a for a in argv[1:] if not a.startswith("--")]
    crop = None
    for a in argv[1:]:
        if a.startswith("--crop="):
            crop = tuple(int(v) for v in a.split("=", 1)[1].split(","))
        elif a == "--crop":
            crop = tuple(int(v) for v in argv[argv.index(a) + 1].split(","))
    if "--icns" in argv:
        icns, menubar = build_icns(src, rest[0] if rest else root, crop)
        print("wrote %s (%.0f KB) with sizes %s"
              % (icns, os.path.getsize(icns) / 1024,
                 ", ".join("%d%s" % (b, "@2x" if m == 2 else "") for b, m in ICNS_SIZES)))
        print("wrote %s (%.0f KB) at %dpx" % (menubar, os.path.getsize(menubar) / 1024, MENUBAR_PX))
        return 0
    out = rest[0] if rest else os.path.join(root, "icon.ico")
    build(src, out, crop)
    print("wrote %s (%.0f KB) with sizes %s"
          % (out, os.path.getsize(out) / 1024, ", ".join(str(s) for s in SIZES)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
