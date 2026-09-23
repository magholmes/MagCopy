"""Asking GitHub whether there is a newer MagCopy, and installing it.

Nothing here runs on its own. The window has a link that checks when it is clicked and a second
click that installs, because a screenshot tool quietly reaching the network and replacing itself
is not a thing anyone asked for.

Installing means handing off and getting out of the way. The installer stops the running copy
before it touches a file - it has to, since a tray app never answers a close request - so the
process that starts it cannot also be the one that waits for it and starts the new version. A
small detached helper does that instead, and deletes itself afterwards.

macOS has no installer to hand to, so its helper is the installer: it waits for this copy to
quit, swaps the .app bundle for the downloaded one, and starts it. The download is the zip the
one-line install script fetches, and like everything that script fetches it is never marked
quarantined, so the new copy opens without the Gatekeeper refusal a browser download gets.
"""
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

from . import plat
from .settings import APP_NAME, APP_VERSION, FROZEN, log_exc

REPO = "magholmes/MagCopy"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
PAGE = "https://github.com/%s/releases/latest" % REPO
# The same file a person downloads, rather than a second asset published only for this. What the
# updater installs is then provably what is on the releases page, and there is nothing extra there
# for someone to click by mistake.
WIN_ASSET = "MagCopy-windows.zip"
SETUP_NAME = "MagCopy-Setup.exe"
# The zip rather than the disk image: it is the file install-macos.sh downloads, so it is still
# the one people install from, and it unpacks with ditto instead of having to be mounted.
MAC_ASSET = "MagCopy-macos.zip"
MAC_APP = APP_NAME + ".app"
TIMEOUT = 20


def version_tuple(text):
    """'v1.10' -> (1, 10), so 1.10 sorts above 1.9 rather than below it."""
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums) or (0,)


def is_newer(remote, local=APP_VERSION):
    return version_tuple(remote) > version_tuple(local)


def fetch_latest(timeout=TIMEOUT):
    """(tag, asset_url_for_this_platform, page_url) for the newest release."""
    req = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "%s/%s" % (APP_NAME, APP_VERSION)})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    want = MAC_ASSET if plat.IS_MAC else WIN_ASSET
    url = None
    for asset in data.get("assets") or []:
        if asset.get("name") == want:
            url = asset.get("browser_download_url")
            break
    return (data.get("tag_name") or ""), url, (data.get("html_url") or PAGE)


def can_install():
    """True when this copy can replace itself without the user going to a browser.

    Running from source has no installer to hand. On macOS it depends where the app is: see
    _mac_can_install.
    """
    if plat.IS_MAC:
        return _mac_can_install()
    return FROZEN and os.name == "nt"


# ----------------------------------------------------------------------------- macOS
def app_bundle():
    """The .app this copy is running from, or None when it is not running from one."""
    if not (FROZEN and plat.IS_MAC):
        return None
    macos = os.path.dirname(os.path.realpath(sys.executable))     # .app/Contents/MacOS
    contents = os.path.dirname(macos)
    bundle = os.path.dirname(contents)
    if (os.path.basename(macos) == "MacOS" and os.path.basename(contents) == "Contents"
            and bundle.endswith(".app")):
        return bundle
    return None


def _mac_can_install():
    """Only a bundle this user can replace, where it actually lives.

    An app opened straight from a browser download runs from a randomised read-only copy macOS
    makes for it (App Translocation), and one opened from the disk image runs from the image.
    Replacing either would change nothing that is launched next time, so both open the download
    page instead - as does an installation this user has no write access to.
    """
    bundle = app_bundle()
    if not bundle or "/AppTranslocation/" in bundle or bundle.startswith("/Volumes/"):
        return False
    return os.access(os.path.dirname(bundle), os.W_OK) and os.access(bundle, os.W_OK)


def extract_app(zip_path):
    """Unpack the downloaded zip and return the checked MagCopy.app inside it.

    ditto rather than zipfile: a bundle is symlinks, executable bits and a code signature sealed
    over all of it, and zipfile restores none of those - what came out would be refused at launch.
    It is then checked the way macOS will check it, and for this Mac's architecture, so a bad
    download fails here with a message rather than leaving a MagCopy that no longer opens.
    """
    out = os.path.join(os.path.dirname(zip_path), "unpacked")
    subprocess.run(["/usr/bin/ditto", "-x", "-k", zip_path, out], check=True,
                   capture_output=True, timeout=300)
    app = os.path.join(out, MAC_APP)
    exe = os.path.join(app, "Contents", "MacOS", APP_NAME)
    if not os.path.isfile(exe):
        raise IOError("%s is not in the download" % MAC_APP)
    sig = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app],
                         capture_output=True, timeout=180)
    if sig.returncode != 0:
        raise IOError("the downloaded app fails its signature check: %s"
                      % sig.stderr.decode("utf-8", "replace").strip())
    archs = subprocess.run(["/usr/bin/lipo", "-archs", exe], capture_output=True,
                           timeout=30).stdout.decode("utf-8", "replace").split()
    if archs and platform.machine() not in archs:
        raise IOError("the published build is %s and this Mac is %s"
                      % ("/".join(archs), platform.machine()))
    return app


def _mac_install_and_restart(new_app, pid=None, bundle=None):
    """Swap the running bundle for `new_app`, from a helper that waits for this copy to quit.

    Nothing outside stops a running copy here the way the Windows installer does, so the app
    quits itself once this returns (see App._update_state) and the helper waits for that. It
    moves the old bundle aside rather than deleting it, copies the new one into its place, and
    only then removes the old one - or puts it back, so a failed copy leaves the old version
    rather than none. Then it starts the new copy hidden, as a login start does, and cleans up.

    `pid` and `bundle` default to this process and the bundle it runs from; smoke_update passes
    a stand-in for each, so the swap itself is tested without replacing anything real.
    """
    bundle = bundle or app_bundle()
    if not bundle:
        raise RuntimeError("not running from an app bundle")
    work = os.path.dirname(os.path.dirname(new_app))       # the temp folder the zip went into
    helper = os.path.join(work, "magcopy-update.sh")
    script = "\n".join([
        "#!/bin/sh",
        "PID=%d" % (os.getpid() if pid is None else pid),
        "APP=%s" % shlex.quote(bundle),
        "NEW=%s" % shlex.quote(new_app),
        'OLD="$APP.old-$$"',
        "i=0",
        'while kill -0 "$PID" 2>/dev/null; do',        # asked to quit; allow it 30 s, then insist
        "  i=$((i + 1))",
        '  [ "$i" -eq 150 ] && kill "$PID" 2>/dev/null',
        '  [ "$i" -ge 200 ] && kill -9 "$PID" 2>/dev/null',
        "  sleep 0.2",
        "done",
        'if mv "$APP" "$OLD"; then',
        '  if /usr/bin/ditto "$NEW" "$APP"; then rm -rf "$OLD"; else rm -rf "$APP"; mv "$OLD" "$APP"; fi',
        "fi",
        'xattr -dr com.apple.quarantine "$APP" 2>/dev/null',
        'open "$APP" --args --hidden',
        "rm -rf %s" % shlex.quote(work),
        ""])
    with open(helper, "w", encoding="utf-8") as fh:
        fh.write(script)
    os.chmod(helper, 0o755)
    # its own session, so it outlives this process rather than going down with it
    subprocess.Popen(["/bin/sh", helper], start_new_session=True, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return helper


def download(url, on_progress=None, timeout=TIMEOUT):
    """Fetch to a temp file, reporting 0..1 as it goes. Returns the path."""
    out = os.path.join(tempfile.mkdtemp(prefix="magcopy-update-"), os.path.basename(url) or "setup")
    req = urllib.request.Request(url, headers={"User-Agent": "%s/%s" % (APP_NAME, APP_VERSION)})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(out, "wb") as fh:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(min(1.0, done / float(total)))
    if total and done < total:
        raise IOError("the download stopped early (%d of %d bytes)" % (done, total))
    return out


def extract_setup(zip_path):
    """Pull the installer out of the downloaded zip."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == SETUP_NAME]
        if not names:
            raise IOError("%s is not in the download" % SETUP_NAME)
        out = os.path.join(os.path.dirname(zip_path), SETUP_NAME)
        with zf.open(names[0]) as src, open(out, "wb") as dst:
            while True:
                chunk = src.read(262144)
                if not chunk:
                    break
                dst.write(chunk)
    return out


def install_and_restart(setup_path):
    """Start the installer from a helper that outlives us, then let it stop this copy.

    /SILENT shows a progress window and asks nothing. The installer's own "start MagCopy" step is
    skipped in silent mode by design, so the helper does it - and only after the install returns,
    or it would race the files being written.
    """
    if os.name != "nt":
        raise RuntimeError("no silent installer on this platform")
    target = sys.executable
    helper = os.path.join(tempfile.gettempdir(), "magcopy-update.cmd")
    lines = [
        "@echo off",
        'start "" /wait "%s" /SILENT /NORESTART' % setup_path,
        'start "" "%s" --hidden' % target,
        'del "%s" >nul 2>&1' % setup_path,
        'del "%%~f0" >nul 2>&1',
    ]
    with open(helper, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")
    from .binaries import CREATE_NO_WINDOW
    # DETACHED_PROCESS as well: the helper has to survive the installer killing this process
    subprocess.Popen(["cmd", "/c", helper], creationflags=CREATE_NO_WINDOW | 0x00000008,
                     close_fds=True)
    return helper


class Updater:
    """The window's side of it: check, then install, both off the Tk thread."""

    def __init__(self, post, on_state):
        self.post, self.on_state = post, on_state
        self.latest = None                  # (tag, url, page) once a check has found something
        self.busy = False

    def check(self):
        if self.busy:
            return
        self.busy = True
        self.on_state("checking", "checking…")
        threading.Thread(target=self._check, name="magcopy-update-check", daemon=True).start()

    def _check(self):
        try:
            tag, url, page = fetch_latest()
        except Exception:
            log_exc("update check")
            self.post(lambda: self._done("error", "could not reach github"))
            return
        if not is_newer(tag):
            self.post(lambda: self._done("current", "%s is the newest" % APP_VERSION))
            return
        self.latest = (tag, url, page)
        label = "update to %s" % tag.lstrip("v")
        self.post(lambda: self._done("available", label))

    def install(self):
        if self.busy or not self.latest:
            return
        tag, url, page = self.latest
        if not (can_install() and url):
            self.post(lambda: self.on_state("open", page))       # the window opens it instead
            return
        self.busy = True
        self.on_state("downloading", "downloading %s…" % tag.lstrip("v"))
        threading.Thread(target=self._install, args=(url,), name="magcopy-update",
                         daemon=True).start()

    def _install(self, url):
        if plat.IS_MAC:
            return self._install_mac(url)
        try:
            path = download(url, on_progress=lambda f: self.post(
                lambda: self.on_state("downloading", "downloading… %d%%" % int(f * 100))))
            if path.lower().endswith(".zip"):
                self.post(lambda: self.on_state("downloading", "unpacking…"))
                path = extract_setup(path)
            install_and_restart(path)
        except Exception:
            log_exc("update install")
            self.post(lambda: self._done("error", "the update failed"))
            return
        self.post(lambda: self._done("installing", "installing - MagCopy will restart"))

    def _install_mac(self, url):
        try:
            path = download(url, on_progress=lambda f: self.post(
                lambda: self.on_state("downloading", "downloading… %d%%" % int(f * 100))))
            self.post(lambda: self.on_state("downloading", "unpacking…"))
            _mac_install_and_restart(extract_app(path))
        except Exception:
            log_exc("update install")
            self.post(lambda: self._done("error", "the update failed"))
            return
        self.post(lambda: self._done("installing", "installing - MagCopy will restart"))

    def _done(self, state, text):
        self.busy = False
        self.on_state(state, text)
