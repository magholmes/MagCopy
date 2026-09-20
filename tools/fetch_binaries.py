"""Download ffmpeg, gifski and gifsicle into ./bin.

They are not committed to the repository: they are large, and they are separately licensed
programs that MagCopy runs as subprocesses rather than links against. This fetches them from the
projects' own published builds and then checks that each one actually runs.

  ffmpeg    gyan.dev "essentials" Windows build  (GPL)
  gifski    the win/gifski.exe inside the GitHub release archive  (AGPL-3.0)
  gifsicle  eternallybored.org, the Windows build linked from the gifsicle homepage  (GPL-2.0)
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
UA = {"User-Agent": "MagCopy-fetch/1.0 (+https://github.com/magholmes/MagCopy)"}
GIFSKI_FALLBACK = "1.34.0"


def _get(url, timeout=300):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def _extract(blob, name, wanted_suffix, out):
    """Pull one member out of a .zip or .tar.xz and write it to `out`."""
    if blob[:2] == b"PK":
        ar = zipfile.ZipFile(io.BytesIO(blob))
        members = ar.namelist()
        opener = ar.open
    else:
        ar = tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
        members = ar.getnames()
        opener = ar.extractfile
    hits = [m for m in members if m.replace("\\", "/").endswith(wanted_suffix)]
    if not hits:
        hits = [m for m in members if m.replace("\\", "/").endswith("/" + name) or m == name]
    if not hits:
        raise RuntimeError("no %s inside the archive" % wanted_suffix)
    src = opener(hits[0])
    if src is None:
        raise RuntimeError("could not read %s" % hits[0])
    with src, open(out, "wb") as dst:
        dst.write(src.read())


def gifski_url():
    """Ask GitHub for the latest release, and fall back to a known-good tag if that fails."""
    try:
        data = json.loads(_get("https://api.github.com/repos/ImageOptim/gifski/releases/latest", 60))
        for a in data.get("assets", []):
            if a["name"].endswith((".tar.xz", ".zip")):
                return a["browser_download_url"]
    except Exception:
        pass
    return ("https://github.com/ImageOptim/gifski/releases/download/%s/gifski-%s.tar.xz"
            % (GIFSKI_FALLBACK, GIFSKI_FALLBACK))


SOURCES = {
    "ffmpeg.exe": dict(
        url=lambda: "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        suffix="bin/ffmpeg.exe", note="ffmpeg, GPL (gyan.dev essentials build)"),
    "gifski.exe": dict(
        url=gifski_url, suffix="win/gifski.exe", note="gifski, AGPL-3.0"),
    "gifsicle.exe": dict(
        url=lambda: "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip",
        suffix="gifsicle.exe", note="gifsicle, GPL-2.0"),
}


def works(path):
    try:
        r = subprocess.run([path, "-version"] if "ffmpeg" in path else [path, "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode == 0
    except Exception:
        return False


def fetch(name, spec):
    out = os.path.join(BIN, name)
    if os.path.exists(out) and works(out):
        print("  %-13s already here" % name)
        return True
    print("  %-13s downloading  (%s)" % (name, spec["note"]))
    try:
        blob = _get(spec["url"]())
        _extract(blob, name, spec["suffix"], out)
    except Exception as e:
        print("      failed: %s" % e)
        print("      put %s into %s by hand, or install it on PATH" % (name, BIN))
        return False
    if not works(out):
        print("      downloaded but it will not run - delete %s and try again" % out)
        return False
    print("      %.1f MB, runs" % (os.path.getsize(out) / 1e6))
    return True


def main():
    if os.name != "nt":
        print("These are the Windows builds; MagCopy is Windows-only.")
        return 2
    os.makedirs(BIN, exist_ok=True)
    print("Fetching encoders into %s\n" % BIN)
    results = [fetch(n, s) for n, s in SOURCES.items()]
    if all(results):
        print("\nDone - MagCopy is ready to run.")
        return 0
    print("\nSome downloads failed. MagCopy will still take screenshots; GIFs need all three.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
