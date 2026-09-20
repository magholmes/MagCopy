"""Finding the three external encoders, and running them without flashing a console window.

MagCopy shells out to ffmpeg (capture -> master video, and frame extraction), gifski (the GIF
encoder) and gifsicle (lossless GIF optimisation). They are looked for in ./bin first, then on
PATH, so a checkout without the binaries still runs if they are installed system-wide.
"""
import os
import shutil
import subprocess
import sys

from .settings import APP_DIR, RES_DIR

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NAMES = ("ffmpeg", "gifski", "gifsicle")


def _exe(name):
    return name + (".exe" if os.name == "nt" else "")


def find(name):
    """Bundled copy first (so a release is self-contained), then whatever is on PATH."""
    for base in (os.path.join(RES_DIR, "bin"), os.path.join(APP_DIR, "bin")):
        p = os.path.join(base, _exe(name))
        if os.path.exists(p):
            return p
    return shutil.which(name)


class Tools:
    def __init__(self):
        self.paths = {n: find(n) for n in NAMES}

    @property
    def missing(self):
        return [n for n in NAMES if not self.paths.get(n)]

    def __getattr__(self, item):
        if item in NAMES:
            return self.paths.get(item)
        raise AttributeError(item)


TOOLS = Tools()


def run(args, timeout=None, capture=True, check=False):
    """Run a bundled tool with no console window. Returns CompletedProcess."""
    kw = dict(creationflags=CREATE_NO_WINDOW) if os.name == "nt" else {}
    return subprocess.run(args, timeout=timeout, check=check,
                          stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.PIPE if capture else subprocess.DEVNULL, **kw)


def popen(args, **kwargs):
    if os.name == "nt":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.Popen(args, **kwargs)


def probe_duration(path):
    """Seconds, parsed from ffmpeg's own stderr banner (keeps ffprobe out of the bundle)."""
    try:
        r = run([TOOLS.ffmpeg, "-hide_banner", "-i", path], timeout=30)
        text = (r.stderr or b"").decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                hms = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = hms.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0
