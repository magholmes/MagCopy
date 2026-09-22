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
    if url and url.endswith(".zip"):
        import urllib.request, zipfile, io as _io
        req = urllib.request.Request(url, headers={"User-Agent": "magcopy-test"})
        with urllib.request.urlopen(req, timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(_io.BytesIO(blob)) as zf:
            names = [n.rsplit("/", 1)[-1] for n in zf.namelist()]
        check("the download contains the installer", update.SETUP_NAME in names, str(names[:4]))
except Exception as e:
    check("github is reachable", False, "%s: %s" % (type(e).__name__, e))

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
