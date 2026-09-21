"""The menu bar item, and making sure only one copy runs.

Where the Windows build registers a window class, runs a WNDPROC on a private message loop and
feeds Shell_NotifyIcon from it, macOS has NSStatusItem: an object, an image and a menu. There is
no thread here at all. Tk on macOS is already a Cocoa application - NSApp is a TKApplication - so
the status item joins the run loop Tk is pumping, and menu commands arrive on the main thread.
That is the opposite of the Windows build, where every callback lands on the tray thread and has
to be handed back; `post` in app.py is still correct here, just no longer load-bearing.

Single instance is a lock file rather than a named mutex, and the second copy asks the first to
show itself through a distributed notification rather than posting a window message. Same shape,
different plumbing.
"""
import os
import subprocess
import sys

import objc
from AppKit import (NSApp, NSApplicationActivationPolicyAccessory, NSImage, NSImageLeft,
                    NSImageOnly, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
from Foundation import NSObject, NSDistributedNotificationCenter, NSSize, NSTimer

from .settings import APP_NAME, SETTINGS_DIR, log_exc

MENU_ICON_POINTS = 18.0         # the menu bar is 22pt tall; 18 leaves the usual breathing room


def _instance_name():
    # MAGCOPY_INSTANCE_NAME lets a test run its own isolated instance without colliding with a
    # real MagCopy the user has open - killing theirs to run a test is not an acceptable trade.
    return os.environ.get("MAGCOPY_INSTANCE_NAME") or "MagCopy-single-instance"


_WAKE_NOTIFICATION = "com.magholmes.magcopy.show-window"


class _Target(NSObject):
    """Menu action target. Cocoa sends every click here, and the index says which item it was."""

    def initWithTray_(self, tray):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._tray = tray
        return self

    def itemChosen_(self, sender):
        try:
            self._tray._chose(int(sender.tag()))
        except Exception:
            log_exc("menu bar command")

    def statusClicked_(self, sender):
        try:
            self._tray.on_activate()
        except Exception:
            log_exc("menu bar activate")

    def wake_(self, note):
        """A second copy was launched and asked us to show ourselves."""
        try:
            self._tray.on_activate()
        except Exception:
            log_exc("wake")


class Tray:
    """A menu bar item. `items` is a list of (label, key); key None = separator."""

    def __init__(self, icon_path, tip, items, on_command, on_activate):
        self.icon_path = icon_path
        self.tip = tip
        self.items = items
        self.on_command = on_command
        self.on_activate = on_activate
        self.item = None
        self._target = None
        self._menu = None
        self._flash_job = None

    @property
    def ok(self):
        """True once the item is really in the menu bar."""
        return self.item is not None

    # ---- lifecycle
    def start(self):
        try:
            # an accessory app has no Dock icon and never appears in the app switcher, which is
            # what "lives in the menu bar" means here. It can still show and focus windows.
            NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:
            pass
        try:
            self._target = _Target.alloc().initWithTray_(self)
            bar = NSStatusBar.systemStatusBar()
            self.item = bar.statusItemWithLength_(NSVariableStatusItemLength)
            image = self._image()
            button = self.item.button()
            if button is not None:
                if image is not None:
                    button.setImage_(image)
                else:
                    button.setTitle_("◉")
                button.setToolTip_(self.tip)
            self._menu = self._build_menu()
            self.item.setMenu_(self._menu)
            NSDistributedNotificationCenter.defaultCenter(
            ).addObserver_selector_name_object_(
                self._target, b"wake:", _WAKE_NOTIFICATION + ":" + _instance_name(), None)
            return True
        except Exception:
            log_exc("menu bar start")
            return False

    def stop(self):
        try:
            NSDistributedNotificationCenter.defaultCenter().removeObserver_(self._target)
        except Exception:
            pass
        try:
            if self.item is not None:
                NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
        except Exception:
            pass
        self.item = None

    def flash(self, text, seconds=2.2):
        """Say it in the menu bar itself, briefly.

        A notification is the obvious way and cannot be relied on: posted through osascript it is
        attributed to another application, and it is silently dropped if the user has notifications
        turned off for that one. The menu bar item is ours and is already on screen, so putting the
        message beside it always works.
        """
        button = self.item.button() if self.item is not None else None
        if button is None:
            return False
        try:
            # A status item button shows its image *or* its title depending on imagePosition, and
            # the default with an image set is image-only - so setting a title alone changes
            # nothing visible. It has to be told to make room for both.
            button.setImagePosition_(NSImageLeft)
            button.setTitle_(" " + str(text))
            if self._flash_job is not None:
                self._flash_job.invalidate()
            self._flash_job = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                float(seconds), False, lambda t: self._unflash())
            return True
        except Exception:
            log_exc("menu bar flash")
            return False

    def _unflash(self):
        self._flash_job = None
        try:
            if self.item is not None and self.item.button() is not None:
                self.item.button().setTitle_("")
                self.item.button().setImagePosition_(NSImageOnly)
        except Exception:
            pass

    def notify(self, title, text):
        """A notification, for things that happen with no window open.

        UNUserNotificationCenter is the modern way and needs a signed bundle with its own
        identifier to post anything at all, which a checkout run from source does not have.
        osascript works either way, so that is what this uses.
        """
        try:
            script = ('display notification %s with title %s'
                      % (_as_applescript(text[:250]), _as_applescript(title[:80])))
            subprocess.Popen(["osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    # ---- internals
    def _image(self):
        """The menu bar icon, as a template image so macOS tints it for light and dark bars."""
        path = self.icon_path
        if not path or not os.path.exists(path):
            return None
        try:
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is None:
                return None
            img.setSize_(NSSize(MENU_ICON_POINTS, MENU_ICON_POINTS))
            # not a template: the icon is a photographic cut-out, and rendering it as a
            # silhouette would turn it into a black blob
            img.setTemplate_(False)
            return img
        except Exception:
            return None

    def _build_menu(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for i, (label, key) in enumerate(self.items):
            if key is None:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, b"itemChosen:", "")
            it.setTarget_(self._target)
            it.setTag_(i)
            it.setEnabled_(True)
            menu.addItem_(it)
        return menu

    def _chose(self, index):
        if 0 <= index < len(self.items):
            key = self.items[index][1]
            if key:
                self.on_command(key)


def _as_applescript(text):
    """AppleScript string literal: only backslash and double quote need escaping."""
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


# ----------------------------------------------------------------------------- single instance
_LOCK = []


def already_running():
    """True if another MagCopy holds the instance lock.

    Two copies cannot both hold the global shortcuts - the second one silently loses them - and
    with "start at login" on by default that is easy to arrange by accident. flock is held by the
    open file descriptor, so the kernel drops it even if the process is killed outright, which is
    the property the Windows named mutex has and a plain pid file does not.
    """
    import fcntl
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        path = os.path.join(SETTINGS_DIR, "%s.lock" % _instance_name())
        fh = open(path, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return True                       # somebody else holds it
        _LOCK.append(fh)                      # keep it open for the life of the process
        try:
            fh.write(str(os.getpid()))
            fh.flush()
        except Exception:
            pass
        return False
    except Exception:
        log_exc("instance lock")
        return False


def wake_running_instance():
    """Ask the copy that is already running to show its window."""
    try:
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_(
            _WAKE_NOTIFICATION + ":" + _instance_name(), None)
        return True
    except Exception:
        log_exc("wake running instance")
        return False
