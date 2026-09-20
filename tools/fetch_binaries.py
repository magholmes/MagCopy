"""Download ffmpeg, gifski and gifsicle into ./bin.

They are not committed: they are large, and they are separately licensed programs that MagCopy
runs as subprocesses rather than links against. This fetches them from their published releases.
"""
import io
import os
import sys
import zipfile
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")

SOURCES = {
    "ffmpeg.exe": dict(
        url="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        member_endswith="bin/ffmpeg.exe", kind="zip",
        note="ffmpeg, GPL (gyan.dev essentials build)"),
    "gifski.exe": dict(
        url="https://github.com/ImageOptim/gifski/releases/latest/download/gifski.zip",
        member_endswith="win/gifski.exe", kind="zip", note="gifski, AGPL-3.0"),
    "gifsicle.exe": dict(
        url="https://www.lcdf.org/gifsicle/gifsicle-1.95-win64.zip",
        member_endswith="gifsicle.exe", kind="zip", note="gifsicle, GPL-2.0"),
}


def fetch(name, spec):
    out = os.path.join(BIN, name)
    if os.path.exists(out):
        print("  %-14s already here" % name)
        return True
    print("  %-14s downloading (%s)…" % (name, spec["note"]))
    try:
        with urllib.request.urlopen(spec["url"], timeout=180) as r:
            blob = r.read()
        zf = zipfile.ZipFile(io.BytesIO(blob))
        want = [n for n in zf.namelist() if n.replace("\\", "/").endswith(spec["member_endswith"])]
        if not want:
            want = [n for n in zf.namelist() if n.replace("\\", "/").endswith("/" + name)
                    or n == name]
        if not want:
            print("      could not find %s inside the archive" % spec["member_endswith"])
            return False
        with zf.open(want[0]) as src, open(out, "wb") as dst:
            dst.write(src.read())
        print("      %.1f MB" % (os.path.getsize(out) / 1e6))
        return True
    except Exception as e:
        print("      failed: %s" % e)
        print("      put %s in %s by hand, or install it on PATH" % (name, BIN))
        return False


def main():
    if os.name != "nt":
        print("These are the Windows builds; MagCopy is Windows-only.")
        return 2
    os.makedirs(BIN, exist_ok=True)
    print("Fetching encoders into %s" % BIN)
    ok = all([fetch(n, s) for n, s in SOURCES.items()])
    print("\nDone." if ok else "\nSome downloads failed - see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
