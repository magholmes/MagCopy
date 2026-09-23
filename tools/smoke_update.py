"""The update link: compares versions properly, and never acts on its own.

The comparison is the part worth pinning down. String order says 1.10 is older than 1.9, which
would strand everyone on the release before a tenth one and look like the check is broken.
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from magcopy import plat, update
from magcopy.settings import APP_VERSION, SETTINGS_FILE

_saved = open(SETTINGS_FILE, "rb").read() if os.path.exists(SETTINGS_FILE) else None


def _restore():
    if _saved is None:
        if os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)
    else:
        open(SETTINGS_FILE, "wb").write(_saved)


import atexit
atexit.register(_restore)

ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-58s %s %s" % (name, "ok  " if good else "FAIL", detail))


# ---- version order
for remote, local, want in (("v1.8", "1.7", True), ("v1.10", "1.9", True), ("v1.7", "1.7", False),
                            ("v1.7", "1.7.1", False), ("v1.7.1", "1.7", True),
                            ("v2.0", "1.99", True), ("", "1.7", False), (None, "1.7", False)):
    got = update.is_newer(remote, local)
    check("%-8r newer than %-6r" % (remote, local), got is want, "got %s" % got)

# ---- it knows when it cannot install itself
check("running from source cannot self-install", update.can_install() is False)

# ---- the real release on github, since a wrong asset name is invisible until someone clicks
try:
    tag, url, page = update.fetch_latest()
    check("github answers with a tag", bool(tag), repr(tag))
    check("and an asset this platform can use", bool(url),
          (url or "").rsplit("/", 1)[-1] or "NONE")
    want = update.MAC_ASSET if plat.IS_MAC else update.WIN_ASSET
    check("the asset is the one the updater looks for", bool(url) and url.endswith(want),
          "expected %s" % want)
    check("the tag reads as a version", update.version_tuple(tag) > (0,),
          "%s parses to %s" % (tag, update.version_tuple(tag)))
    # and the installer really is inside the asset the updater will fetch, which is the thing
    # that silently stopped working when the release stopped publishing a bare Setup.exe
    if url and url.endswith(".zip") and not plat.IS_MAC:
        import urllib.request, zipfile, io as _io
        req = urllib.request.Request(url, headers={"User-Agent": "magcopy-test"})
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(_io.BytesIO(blob)) as zf:
            names = [n.rsplit("/", 1)[-1] for n in zf.namelist()]
        check("the download contains the installer", update.SETUP_NAME in names, str(names[:4]))
    elif url and plat.IS_MAC:
        # macOS: the app itself is what gets installed, so unpack the real download the way the
        # updater does - ditto, then the signature and architecture checks macOS would make.
        import shutil as _sh
        import tempfile as _tf
        path = update.download(url, timeout=120)
        keep = os.path.join(_tf.mkdtemp(prefix="magcopy-zip-"), os.path.basename(path))
        _sh.copy(path, keep)
        published = ""
        try:
            app_path = update.extract_app(path)
            published = update.bundle_version(app_path)
            check("the download unpacks to a signed app that runs here",
                  app_path.endswith(update.MAC_APP) and os.path.isfile(
                      os.path.join(app_path, "Contents", "MacOS", update.APP_NAME)),
                  "version %s" % published)
        except Exception as e:
            check("the download unpacks to a signed app that runs here", False,
                  "%s: %s" % (type(e).__name__, e))
        finally:
            _sh.rmtree(os.path.dirname(path), ignore_errors=True)

        # A Windows-only release carries the previous Mac download forward so the link keeps
        # working. The tag is newer; the app inside is not. Installing it would restart into the
        # same version and offer the same update forever, so the updater has to read the app's
        # own version and decline. The published build is run through the real install path with
        # only the final swap replaced, and it must install exactly when it is newer than this.
        states, swapped = [], []
        real_download, real_swap = update.download, update._mac_install_and_restart

        def fake_download(u, on_progress=None, timeout=update.TIMEOUT):
            d = _tf.mkdtemp(prefix="magcopy-update-")
            return _sh.copy(keep, os.path.join(d, os.path.basename(keep)))

        update.download = fake_download
        update._mac_install_and_restart = lambda new_app, **kw: swapped.append(new_app)
        try:
            u = update.Updater(lambda f: f(), lambda st, tx: states.append((st, tx)))
            u.latest = ("v99.0", url, update.PAGE)
            u.busy = True
            u._install_mac(url)
        finally:
            update.download, update._mac_install_and_restart = real_download, real_swap
            _sh.rmtree(os.path.dirname(keep), ignore_errors=True)
        newer = update.is_newer(published)
        check("a Mac build no newer than this one is not installed" if not newer
              else "a newer Mac build is installed",
              bool(swapped) == newer and states and states[-1][0] == ("installing" if newer else "current"),
              "published %s, this %s: %s" % (published, APP_VERSION, states[-1:]))
        if not newer:
            check("and that release stops being offered",
                  u.no_build_for == "v99.0" and u.latest is None, repr(u.no_build_for))
except Exception as e:
    check("github is reachable", False, "%s: %s" % (type(e).__name__, e))

# ---- macOS: the swap itself, run for real against stand-ins
# The helper waits for a process to exit, moves the old bundle aside, copies the new one in and
# starts it. Here the process is a short sleep, the bundles are two folders, and `open` is a stub
# that writes down what it was asked to start - so everything runs except launching an app.
if plat.IS_MAC:
    import shutil
    import subprocess
    import tempfile
    import time

    check("from source there is no bundle to replace", update.app_bundle() is None)

    def stand_in(where, marker):
        os.makedirs(os.path.join(where, "Contents", "MacOS"))
        with open(os.path.join(where, "Contents", "MacOS", "which"), "w") as fh:
            fh.write(marker)

    def which(bundle):
        try:
            with open(os.path.join(bundle, "Contents", "MacOS", "which")) as fh:
                return fh.read()
        except OSError:
            return None

    def swap(new_exists=True):
        apps, stub = tempfile.mkdtemp(prefix="magcopy-apps-"), tempfile.mkdtemp(prefix="magcopy-bin-")
        work = tempfile.mkdtemp(prefix="magcopy-update-")
        old_app, new_app = os.path.join(apps, "MagCopy.app"), os.path.join(work, "unpacked", "MagCopy.app")
        stand_in(old_app, "old")
        if new_exists:
            stand_in(new_app, "new")
        opened = os.path.join(stub, "opened")
        with open(os.path.join(stub, "open"), "w") as fh:
            fh.write('#!/bin/sh\necho "$@" > %s\n' % opened)
        os.chmod(os.path.join(stub, "open"), 0o755)
        sleeper = subprocess.Popen(["/bin/sleep", "1.2"])
        path_was = os.environ.get("PATH", "")
        os.environ["PATH"] = stub + os.pathsep + path_was
        try:
            update._mac_install_and_restart(new_app, pid=sleeper.pid, bundle=old_app)
        finally:
            os.environ["PATH"] = path_was
        early = which(old_app)
        deadline = time.time() + 20
        while not os.path.exists(opened) and time.time() < deadline:
            sleeper.poll()                     # reap it, or it lingers as a zombie kill -0 still sees
            time.sleep(0.05)
        time.sleep(0.2)
        got = {"early": early, "after": which(old_app), "opened": open(opened).read().strip()
               if os.path.exists(opened) else None,
               "leftovers": [n for n in os.listdir(apps) if n != "MagCopy.app"],
               "work_gone": not os.path.exists(work)}
        shutil.rmtree(apps, ignore_errors=True)
        shutil.rmtree(stub, ignore_errors=True)
        return got, old_app

    got, where = swap()
    check("the helper waits for the running copy to quit", got["early"] == "old", str(got["early"]))
    check("then the new app is in the old one's place", got["after"] == "new", str(got["after"]))
    check("and it is started hidden, from that same place",
          got["opened"] == "%s --args --hidden" % where, repr(got["opened"]))
    check("nothing is left beside it, and the download is cleaned up",
          not got["leftovers"] and got["work_gone"], str(got))
    got, _ = swap(new_exists=False)
    check("a copy that fails puts the old app back", got["after"] == "old" and not got["leftovers"],
          str(got))

# ---- the link does nothing until it is clicked, and checks before it installs
plat.set_dpi_aware()
from magcopy.theme import register_fonts

register_fonts()
root = tk.Tk()
try:
    root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
except Exception:
    pass
from magcopy.app import App

app = App(root)
app.hide_window()
root.update()

calls = []
app.updater.check = lambda: calls.append("check")
app.updater.install = lambda: calls.append("install")

check("nothing is found before anyone asks", app.updater.latest is None)
app._update_clicked()
check("the first click checks", calls == ["check"], str(calls))
app.updater.latest = ("v9.9", "https://example.invalid/MagCopy-Setup.exe", update.PAGE)
app._update_clicked()
check("the second click installs what was found", calls == ["check", "install"], str(calls))
app.updater.busy = True
app._update_clicked()
check("a click while it is working is ignored", calls == ["check", "install"], str(calls))
app.updater.busy = False

# ---- the states the link can show
seen = []
app.toast = lambda t, error=False: seen.append((t, error))
app._update_state("current", "1.7 is the newest")
check("up to date says so", app.update_link.cget("text") == "1.7 is the newest",
      repr(app.update_link.cget("text")))
app._update_state("available", "update to 9.9")
check("an available update is offered on the link",
      app.update_link.cget("text") == "update to 9.9", repr(app.update_link.cget("text")))
app._update_state("error", "could not reach github")
check("a failure is reported, not swallowed", seen and seen[-1][1] is True, str(seen[-1:]))

app.quit()
print("\nUPDATE", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
