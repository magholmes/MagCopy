"""Download ffmpeg, gifski and gifsicle into ./bin.

They are not committed to the repository: they are large, and they are separately licensed
programs that MagCopy runs as subprocesses rather than links against. This fetches them from the
projects' own published builds and then checks that each one actually runs.

Windows
  ffmpeg    gyan.dev "essentials" Windows build  (GPL)
  gifski    the win/gifski.exe inside the GitHub release archive  (AGPL-3.0)
  gifsicle  eternallybored.org, the Windows build linked from the gifsicle homepage  (GPL-2.0)

macOS
  ffmpeg    osxexperts.net static build - arm64 or Intel, chosen for this machine  (GPL)
  gifski    the mac/gifski inside the same GitHub release archive  (AGPL-3.0)
  gifsicle  built from the author's own source tarball, because nobody ships a
            self-contained macOS binary. It is a few seconds of ./configure && make and
            produces something with no Homebrew dylibs hanging off it, which matters
            because this gets bundled into a .app.  (GPL-2.0)
"""
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
UA = {"User-Agent": "MagCopy-fetch/1.0 (+https://github.com/magholmes/MagCopy)"}
GIFSKI_FALLBACK = "1.34.0"
GIFSICLE_VERSION = "1.96"
IS_MAC = sys.platform == "darwin"
ARM = platform.machine() in ("arm64", "aarch64")


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
    if os.name != "nt":
        os.chmod(out, 0o755)


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


def build_gifsicle(out):
    """./configure && make, into `out`. Needs the Xcode command line tools."""
    if not shutil.which("cc") or not shutil.which("make"):
        raise RuntimeError("no C compiler - run: xcode-select --install")
    url = "https://www.lcdf.org/gifsicle/gifsicle-%s.tar.gz" % GIFSICLE_VERSION
    blob = _get(url)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
            tf.extractall(tmp)
        src = os.path.join(tmp, "gifsicle-%s" % GIFSICLE_VERSION)
        env = dict(os.environ)
        # -mmacosx-version-min keeps the binary runnable on older macOS than the build host
        env["CFLAGS"] = env.get("CFLAGS", "") + " -O2 -mmacosx-version-min=11.0"
        log = os.path.join(tmp, "build.log")
        with open(log, "w") as fh:
            for cmd in (["./configure", "--disable-gifview", "--disable-gifdiff"],
                        ["make", "-j%d" % (os.cpu_count() or 4)]):
                r = subprocess.run(cmd, cwd=src, stdout=fh, stderr=subprocess.STDOUT, env=env)
                if r.returncode != 0:
                    raise RuntimeError("%s failed - see %s" % (cmd[0], log))
        built = os.path.join(src, "src", "gifsicle")
        if not os.path.exists(built):
            built = os.path.join(src, "gifsicle")
        shutil.copy2(built, out)
        os.chmod(out, 0o755)


WINDOWS_SOURCES = {
    "ffmpeg.exe": dict(
        url=lambda: "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        suffix="bin/ffmpeg.exe", note="ffmpeg, GPL (gyan.dev essentials build)"),
    "gifski.exe": dict(
        url=gifski_url, suffix="win/gifski.exe", note="gifski, AGPL-3.0"),
    "gifsicle.exe": dict(
        url=lambda: "https://eternallybored.org/misc/gifsicle/releases/gifsicle-1.95-win64.zip",
        suffix="gifsicle.exe", note="gifsicle, GPL-2.0"),
}

MAC_SOURCES = {
    "ffmpeg": dict(
        url=lambda: ("https://www.osxexperts.net/ffmpeg9arm.zip" if ARM
                     else "https://www.osxexperts.net/ffmpeg80intel.zip"),
        suffix="ffmpeg",
        note="ffmpeg, GPL (osxexperts static %s build)" % ("arm64" if ARM else "Intel")),
    "gifski": dict(
        url=gifski_url, suffix="mac/gifski", note="gifski, AGPL-3.0"),
    "gifsicle": dict(
        build=build_gifsicle, note="gifsicle, GPL-2.0 (built from source)"),
}

SOURCES = MAC_SOURCES if IS_MAC else WINDOWS_SOURCES


def works(path):
    try:
        r = subprocess.run([path, "-version"] if "ffmpeg" in os.path.basename(path)
                           else [path, "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.returncode == 0
    except Exception:
        return False


def arch_of(path):
    """What `lipo` says this binary is - so a wrong-architecture download is obvious."""
    if not IS_MAC:
        return ""
    try:
        r = subprocess.run(["lipo", "-archs", path], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=20)
        return " (%s)" % r.stdout.decode().strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def fetch(name, spec):
    out = os.path.join(BIN, name)
    if os.path.exists(out) and works(out):
        print("  %-13s already here%s" % (name, arch_of(out)))
        return True
    if "build" in spec:
        print("  %-13s building     (%s)" % (name, spec["note"]))
        try:
            spec["build"](out)
        except Exception as e:
            print("      failed: %s" % e)
            print("      or install it yourself: brew install gifsicle")
            return False
    else:
        print("  %-13s downloading  (%s)" % (name, spec["note"]))
        try:
            blob = _get(spec["url"]())
            _extract(blob, name, spec["suffix"], out)
        except Exception as e:
            print("      failed: %s" % e)
            print("      put %s into %s by hand, or install it on PATH" % (name, BIN))
            return False
    if IS_MAC:
        # a binary pulled out of a zip carries the quarantine flag of the download, and macOS
        # refuses to execute it until that is cleared - silently, as "killed: 9"
        subprocess.run(["xattr", "-d", "com.apple.quarantine", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not works(out):
        print("      downloaded but it will not run - delete %s and try again" % out)
        return False
    print("      %.1f MB, runs%s" % (os.path.getsize(out) / 1e6, arch_of(out)))
    return True


def main():
    if not IS_MAC and os.name != "nt":
        print("No prebuilt encoders for this platform - install ffmpeg, gifski and gifsicle on PATH.")
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
