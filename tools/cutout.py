"""Cut a flat background out of an image, leaving transparency.

  python tools/cutout.py in.jpg out.png [--tol 38] [--feather 1.2]

The background is found by flooding inwards from the edges rather than by thresholding the whole
picture. A threshold removes every pale pixel wherever it is - including highlights inside the
subject, which is how cut-outs end up full of holes. Flooding only removes pale pixels that are
actually connected to the border, so an enclosed highlight survives.

The alpha is then blurred by about a pixel. A hard 0/255 edge on an irregular subject reads as
jagged at every size an icon is drawn at; a slight feather is what makes it look cut rather than
cropped.
"""
import os
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageFilter


def background_mask(rgb, tol=38):
    """True where a pixel is background: near the border colour AND reachable from the edge."""
    h, w = rgb.shape[:2]
    corners = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]]).astype(np.int16)
    base = np.median(corners, axis=0)                       # median, so a stray dark edge pixel
    close = (np.abs(rgb.astype(np.int16) - base).max(axis=2) <= tol)   # cannot drag it off

    seen = np.zeros((h, w), dtype=bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if close[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if close[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and close[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    return seen


def cut_out(src, tol=38, feather=1.2):
    im = Image.open(src).convert("RGB")
    rgb = np.asarray(im)
    bg = background_mask(rgb, tol)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    a = Image.fromarray(alpha, "L")
    if feather > 0:
        a = a.filter(ImageFilter.GaussianBlur(feather))
        # pull the edge back in slightly: blurring alone leaves a pale halo of background colour
        a = a.point(lambda v: 0 if v < 110 else min(255, int((v - 110) * 255 / 145)))
    out = im.convert("RGBA")
    out.putalpha(a)
    return out, bg


def trim(im, pad=2):
    """Crop to what is left, with a little breathing room."""
    box = im.split()[-1].getbbox()
    if not box:
        return im
    x0, y0, x1, y1 = box
    return im.crop((max(0, x0 - pad), max(0, y0 - pad),
                    min(im.width, x1 + pad), min(im.height, y1 + pad)))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    tol = 38
    feather = 1.2
    for i, a in enumerate(argv):
        if a == "--tol":
            tol = int(argv[i + 1])
        elif a == "--feather":
            feather = float(argv[i + 1])
    im, bg = cut_out(argv[0], tol, feather)
    im = trim(im)
    im.save(argv[1])
    kept = 100.0 * (1 - bg.mean())
    print("wrote %s  %dx%d  %.0f%% of the picture kept" % (argv[1], im.width, im.height, kept))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
