"""Build icon.ico from the source artwork.

  python tools/make_icon.py path/to/image.jpg [out.ico] [--crop L,T,R,B]

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


def square(im):
    """Centre-crop to a square: icons are square, and letterboxing wastes the small sizes."""
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return im.crop((left, top, left + side, top + side))


def render(im, size):
    out = im.resize((size, size), Image.LANCZOS)
    if size <= 48:
        # Downscaling always costs local contrast; at tray size that is the difference between
        # a recognisable shape and a smudge. Unsharp first, then a little saturation back.
        radius = 0.6 if size <= 24 else 0.8
        out = out.filter(ImageFilter.UnsharpMask(radius=radius, percent=110, threshold=2))
        out = ImageEnhance.Color(out).enhance(1.12)
    return out


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


def build(src_path, out_path, crop=None):
    im = Image.open(src_path).convert("RGB")
    if crop:
        im = im.crop(crop)
    im = square(im)
    return write_ico([render(im, s) for s in SIZES], out_path)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    src = argv[0]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rest = [a for a in argv[1:] if not a.startswith("--")]
    out = rest[0] if rest else os.path.join(root, "icon.ico")
    crop = None
    for a in argv[1:]:
        if a.startswith("--crop="):
            crop = tuple(int(v) for v in a.split("=", 1)[1].split(","))
        elif a == "--crop":
            crop = tuple(int(v) for v in argv[argv.index(a) + 1].split(","))
    build(src, out, crop)
    print("wrote %s (%.0f KB) with sizes %s"
          % (out, os.path.getsize(out) / 1024, ", ".join(str(s) for s in SIZES)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
