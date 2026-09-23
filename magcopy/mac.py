"""macOS layer: screen capture, clipboard, global hotkeys, overlay windows, permissions.

This mirrors win.py function for function. Everything above this line - app, overlay, recorder,
editor - is written against that one interface, so the port lives here rather than being spread
through the program. Where win.py is 69 Win32 entry points through ctypes, this is PyObjC, with
one small ctypes island for Carbon's hotkey API, which has no Objective-C equivalent.

**Coordinates.** Windows puts the origin at the top-left of the desktop with y growing downward.
Cocoa puts it at the bottom-left of the primary screen with y growing upward. Every module above
this one is written to the Windows convention, so the flip happens here and nowhere else - see
`_to_cocoa` / `_from_cocoa`. ScreenCaptureKit is the exception that needs no flip: its sourceRect
is already top-left relative to the display.

**Handles.** `toplevel_hwnd` returns the real NSWindow behind a Tk toplevel, which is what every
window function here takes. Tk on macOS is itself a Cocoa application - NSApp is a TKApplication
and every Tk toplevel is a TKWindow in NSApp.windows() - so the window can be found and then
styled directly. `winfo_id()` and `wm frame` return Tk's own pointers, not NSWindows; casting
either to an Objective-C object segfaults, so they are not used.

**Retina.** macOS reports geometry in points and renders in pixels, usually at 2x. Everything
outside this module works in points, which is what Tk reports and what the overlay drags in.
Captures are the exception: `grab_once(..., retina=True)` asks for the real pixels, because a
screenshot of a 400x300 selection should be the 800x600 the screen actually has.
"""
import contextlib
import ctypes
import ctypes.util
import io
import os
import sys
import threading
import time

import numpy as np
from PIL import Image

from .settings import log_error, log_exc

IS_MAC = sys.platform == "darwin"

if IS_MAC:
    import objc
    import Quartz
    import CoreMedia
    import ScreenCaptureKit as SCK
    from AppKit import (NSApp, NSScreen, NSPasteboard, NSImage, NSColor, NSWorkspace,
                        NSPasteboardTypePNG, NSPasteboardTypeTIFF, NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorStationary, NSWindowCollectionBehaviorFullScreenAuxiliary,
                        NSScreenSaverWindowLevel, NSFloatingWindowLevel)
    from Foundation import NSObject, NSURL
    _carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))
    _appsvc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
else:
    objc = Quartz = CoreMedia = SCK = None


# ----------------------------------------------------------------------------- permissions
def has_screen_recording():
    """True when this app may see anything but its own windows.

    Worth checking explicitly, because a denial is silent: ScreenCaptureKit keeps working and
    hands back the desktop picture with every other application missing from it. A capture of
    nothing is indistinguishable from a capture of an empty desktop.
    """
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_screen_recording():
    """Ask once. macOS shows its own dialog, and only ever shows it once per app."""
    try:
        return bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        return False


def has_accessibility():
    """Not required for the shortcuts - Carbon hotkeys work without it - but reported anyway."""
    try:
        _appsvc.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(_appsvc.AXIsProcessTrusted())
    except Exception:
        return False


def open_privacy_pane(which="ScreenCapture"):
    """Open the right pane of System Settings, so a missing permission is one click from fixed."""
    try:
        url = NSURL.URLWithString_(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_%s" % which)
        return bool(NSWorkspace.sharedWorkspace().openURL_(url))
    except Exception:
        return False


def set_dpi_aware():
    """Nothing to do: macOS is DPI-correct by construction. Kept so callers need no platform test."""
    return True


# ----------------------------------------------------------------------------- screens
def _primary_height():
    try:
        return float(NSScreen.screens()[0].frame().size.height)
    except Exception:
        return 0.0


def _from_cocoa(rect):
    """Cocoa (bottom-left, y up) -> the top-left, y-down rect everything else here speaks."""
    x, y, wd, ht = rect
    return (int(round(x)), int(round(_primary_height() - y - ht)), int(round(wd)), int(round(ht)))


def _to_cocoa(rect):
    x, y, wd, ht = rect
    return (float(x), float(_primary_height() - y - ht), float(wd), float(ht))


def _nsrect(o):
    return (o.origin.x, o.origin.y, o.size.width, o.size.height)


def monitors():
    """Each screen as (bounds, work_area, is_primary), top-left coordinates, in points.

    The work area excludes the menu bar and the Dock, which is what `visibleFrame` means.
    Placing chrome against the whole desktop instead puts it under the Dock or on the wrong screen.
    """
    out = []
    try:
        for i, s in enumerate(NSScreen.screens()):
            out.append((_from_cocoa(_nsrect(s.frame())),
                        _from_cocoa(_nsrect(s.visibleFrame())), i == 0))
    except Exception:
        pass
    return out or [((0, 0, 1440, 900), (0, 25, 1440, 875), True)]


def virtual_screen():
    """(x, y, w, h) of the whole desktop; x/y go negative for a screen left of or above the primary."""
    mons = monitors()
    x0 = min(m[0][0] for m in mons)
    y0 = min(m[0][1] for m in mons)
    x1 = max(m[0][0] + m[0][2] for m in mons)
    y1 = max(m[0][1] + m[0][3] for m in mons)
    return (x0, y0, x1 - x0, y1 - y0)


def work_area_for(rect):
    """Work area of the screen holding the biggest share of `rect`."""
    x, y, wd, ht = rect

    def overlap(m):
        mx, my, mw, mh = m
        return (max(0, min(x + wd, mx + mw) - max(x, mx))
                * max(0, min(y + ht, my + mh) - max(y, my)))

    best = max(monitors(), key=lambda m: overlap(m[0]), default=None)
    if best and overlap(best[0]) > 0:
        return best[1]
    return virtual_screen()


def scale_for(rect):
    """backingScaleFactor of the screen `rect` is mostly on - 2.0 on a Retina display."""
    x, y, wd, ht = rect
    try:
        best, area = None, 0
        for s in NSScreen.screens():
            mx, my, mw, mh = _from_cocoa(_nsrect(s.frame()))
            a = (max(0, min(x + wd, mx + mw) - max(x, mx))
                 * max(0, min(y + ht, my + mh) - max(y, my)))
            if a >= area:
                best, area = s, a
        return float(best.backingScaleFactor()) if best else 1.0
    except Exception:
        return 1.0


# ----------------------------------------------------------------------------- capture
BGRA = 1111970369                      # kCVPixelFormatType_32BGRA, 'BGRA'
_SHAREABLE = {"content": None, "at": 0.0}
_EXCLUDED = []                         # our own NSWindows, kept out of every capture


def exclude_window(nswindow):
    """Keep one of our own windows out of everything we capture.

    The recording border is eight strips placed strictly outside the captured rectangle on both
    platforms - being outside the rectangle is the belt to this brace - but a picker or a control
    bar that happens to overlap the region no longer risks appearing in a frame. Windows does the
    same for its control bar through SetWindowDisplayAffinity; see win.exclude_window. This
    applies to the recording stream only; see `_filter_for`.

    The cached display list is dropped at the same time: it carries the window list the capture
    filter is built from, and one taken a moment ago does not know about this window yet.

    Returns True once the window is on the list, as win.exclude_window does when Windows agrees
    to it. The recorder decides where its control bar may go from that answer: an excluded bar
    can sit over a whole-screen recording, and one that is not has to go somewhere else. This
    used to return nothing, so the bar was always treated as recordable - it was left out of every
    frame all the same, but sent to another monitor, or reported as being recorded, for no reason.
    """
    if nswindow is None:
        return False
    _prune_excluded()
    if nswindow not in _EXCLUDED:
        _EXCLUDED.append(nswindow)
        _SHAREABLE["content"] = None
    return True


def _prune_excluded():
    """Drop windows that have been destroyed, so a long session does not grow a stale list."""
    alive = []
    for w_ in _EXCLUDED:
        try:
            if int(w_.windowNumber()) > 0:
                alive.append(w_)
        except Exception:
            pass
    if len(alive) != len(_EXCLUDED):
        _EXCLUDED[:] = alive


def clear_excluded():
    del _EXCLUDED[:]
    return True


def _wait_for(event, timeout=8.0):
    """Wait for a Cocoa completion handler, turning the run loop over if we are the main thread.

    ScreenCaptureKit delivers these on a queue that is often the main one. Blocking the main
    thread on an Event is then a deadlock dressed up as a slow capture: the callback cannot run
    until the thread it is queued on stops waiting for it, and the thread will not stop waiting
    until the callback runs. It fails intermittently rather than always, because whether anything
    else happens to be pumping decides it - which is the worst way for it to fail.

    Off the main thread there is nothing to pump and an ordinary wait is correct.
    """
    if threading.current_thread() is not threading.main_thread():
        return event.wait(timeout)
    deadline = time.perf_counter() + timeout
    while not event.is_set() and time.perf_counter() < deadline:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.01, True)
    return event.is_set()


def _shareable(timeout=6.0, max_age=2.0):
    """SCShareableContent, cached briefly: asking for it costs an XPC round trip."""
    now = time.time()
    if _SHAREABLE["content"] is not None and now - _SHAREABLE["at"] < max_age:
        return _SHAREABLE["content"]
    box, done = {}, threading.Event()

    def got(content, err):                     # must return None: PyObjC rejects a value here
        box["c"], box["e"] = content, err
        done.set()

    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        False, False, got)
    if not _wait_for(done, timeout) or box.get("c") is None:
        raise RuntimeError("could not read the display list: %s" % box.get("e"))
    _SHAREABLE["content"] = box["c"]
    _SHAREABLE["at"] = now
    return box["c"]


def _display_for(x, y, wd, ht):
    """(SCDisplay, offset_x, offset_y) - sourceRect is relative to one display, not the desktop."""
    content = _shareable()
    best, area, off = None, -1, (0, 0)
    for d in content.displays():
        dx, dy, dw, dh = _from_cocoa(_nsrect(d.frame()))
        a = (max(0, min(x + wd, dx + dw) - max(x, dx))
             * max(0, min(y + ht, dy + dh) - max(y, dy)))
        if a > area:
            best, area, off = d, a, (dx, dy)
    if best is None:
        raise RuntimeError("no display found")
    return best, off[0], off[1]


def _excluded_windows(content):
    """Our own windows, as the SCWindows the filter wants."""
    if not _EXCLUDED:
        return []
    want = set()
    for w_ in _EXCLUDED:
        try:
            want.add(int(w_.windowNumber()))
        except Exception:
            pass
    return [w for w in content.windows() if int(w.windowID()) in want]


def _filter_for(display, exclude_own=False):
    """A capture filter for one display.

    `exclude_own` is for the recording stream, and only for it. Leaving MagCopy's own overlays out
    of a recording is the point of keeping that list; leaving them out of every capture would also
    hide them from anything that samples the screen to check what is on it - which is how several
    of the smoke tests work, and how two Windows bugs were found where a window was reported as
    mapped while compositing nothing. A capture that cannot see our own windows cannot check them.
    """
    content = _shareable()
    windows = _excluded_windows(content) if exclude_own else []
    return SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, windows)


def _config(sx, sy, sw, sh, out_w, out_h, cursor):
    cfg = SCK.SCStreamConfiguration.alloc().init()
    cfg.setSourceRect_(Quartz.CGRectMake(sx, sy, sw, sh))   # top-left, display-relative, points
    cfg.setWidth_(int(out_w))
    cfg.setHeight_(int(out_h))
    cfg.setPixelFormat_(BGRA)
    cfg.setShowsCursor_(bool(cursor))                       # free, and better than drawing it
    cfg.setScalesToFit_(False)
    return cfg


def _image_to_bgra(img, want_w, want_h):
    """CGImage -> a tightly packed (h, w, 4) BGRA array.

    CoreGraphics pads every row out to a 16-byte boundary, so a 400-pixel row is 1664 bytes
    rather than 1600, and the copied data can be longer again than bytesPerRow * height.
    Both have to be trimmed or the array comes out sheared.
    """
    w_ = Quartz.CGImageGetWidth(img)
    h_ = Quartz.CGImageGetHeight(img)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img)))
    arr = np.frombuffer(raw[:bpr * h_], dtype=np.uint8).reshape(h_, bpr // 4, 4)[:, :w_, :]
    if (w_, h_) != (want_w, want_h):
        arr = arr[:want_h, :want_w, :]
    return np.ascontiguousarray(arr)


def grab_once(x, y, width, height, cursor=False, retina=False, _tries=2):
    """One rectangle, as BGRA top-down - the same layout the Win32 build produces.

    `retina` asks for the display's real pixels instead of points, which is what a screenshot
    wants and what the overlay's frozen backdrop must not have: that one is drawn into a Tk
    canvas sized in points.

    It retries once. The capture goes through a system service, and a service under load can
    decline a request that succeeds a moment later - so a single refusal is not news, while a
    screenshot that silently does nothing very much is.
    """
    width, height = max(1, int(width)), max(1, int(height))
    scale = scale_for((x, y, width, height)) if retina else 1.0
    want_w, want_h = round(width * scale), round(height * scale)
    last = None
    for attempt in range(max(1, _tries)):
        if attempt:
            _SHAREABLE["content"] = None      # a stale display list is one way this fails
            time.sleep(0.15)
        try:
            display, ox, oy = _display_for(x, y, width, height)
            cfg = _config(x - ox, y - oy, width, height, want_w, want_h, cursor)
            box, done = {}, threading.Event()

            def shot(image, err):
                box["i"], box["e"] = image, err
                done.set()

            SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                _filter_for(display), cfg, shot)
            if not _wait_for(done, 8) or box.get("i") is None:
                raise RuntimeError("screen capture failed: %s" % box.get("e"))
            return _image_to_bgra(box["i"], want_w, want_h)
        except Exception as exc:
            last = exc
    raise last if last else RuntimeError("screen capture failed")


class _StreamOutput(NSObject):
    """Receives frames on a background dispatch queue and keeps only the newest."""

    def initWithOwner_(self, owner):
        self = objc.super(_StreamOutput, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, stype):
        if stype != 0:                                  # SCStreamOutputTypeScreen
            return
        try:
            pb = CoreMedia.CMSampleBufferGetImageBuffer(sbuf)
            if pb is None:
                return              # an idle tick: the screen did not change, so there is no image
            Quartz.CVPixelBufferLockBaseAddress(pb, 1)
            try:
                w_ = Quartz.CVPixelBufferGetWidth(pb)
                h_ = Quartz.CVPixelBufferGetHeight(pb)
                bpr = Quartz.CVPixelBufferGetBytesPerRow(pb)
                base = Quartz.CVPixelBufferGetBaseAddress(pb)
                arr = np.frombuffer(base.as_buffer(bpr * h_), dtype=np.uint8)
                arr = arr.reshape(h_, bpr // 4, 4)[:, :w_, :]
                self._owner._deliver(np.ascontiguousarray(arr))
            finally:
                Quartz.CVPixelBufferUnlockBaseAddress(pb, 1)
        except Exception:
            pass


class Grabber:
    """A reusable capture surface for one region size.

    The Win32 version keeps a device context and bitmap alive and re-blits into them, because
    creating them per frame is what makes a paced loop unaffordable. The equivalent here is an
    SCStream held open for the life of the recording: frames arrive on their own queue and
    `grab` takes the newest one. A one-shot capture costs about 40 ms, which cannot pace even
    20 fps, so this is not an optimisation - it is the difference between working and not.

    ScreenCaptureKit only sends a frame when something changed, so `grab` returning the same
    picture twice is the screen genuinely standing still. The recorder already accounts for
    that: it writes the previous frame again and counts the repeat.

    `width` and `height` are points, the way the picker dragged them. What comes back is
    `out_w` x `out_h` pixels, which on a Retina display is twice that in each direction - the
    caller has to be told, because the encoder it feeds needs the real size.
    """

    def __init__(self, width, height, fps=60, cursor=None, retina=True, origin=None):
        self.w, self.h = int(width), int(height)
        self.fps = max(1, min(120, int(fps)))
        # The region is given in points because that is what the picker dragged in, but the
        # recording is made of pixels and a Retina screen has four times as many of them. Capturing
        # at point resolution halves the linear resolution of every frame before the GIF pipeline
        # has seen it, and no amount of care downstream gets that back.
        scale = scale_for((origin or (0, 0)) + (self.w, self.h)) if retina else 1.0
        self.scale = scale
        self.out_w = max(2, int(round(self.w * scale)) & ~1)      # x264 wants even dimensions
        self.out_h = max(2, int(round(self.h * scale)) & ~1)
        self._stream = None
        self._output = None
        self._key = None                       # (x, y, cursor) the current stream is configured for
        self._frame = None
        self._blank = np.zeros((self.out_h, self.out_w, 4), dtype=np.uint8)
        self._lock = threading.Lock()

    # -- stream side
    def _deliver(self, arr):
        with self._lock:
            self._frame = arr

    def _start(self, x, y, cursor, _tries=2):
        """Open a stream for this region. Retries once, for the same reason grab_once does."""
        self._stop()
        for attempt in range(max(1, _tries)):
            try:
                return self._start_once(x, y, cursor)
            except Exception:
                if attempt + 1 >= _tries:
                    raise
                _SHAREABLE["content"] = None
                time.sleep(0.2)

    def _start_once(self, x, y, cursor):
        display, ox, oy = _display_for(x, y, self.w, self.h)
        cfg = _config(x - ox, y - oy, self.w, self.h, self.out_w, self.out_h, cursor)
        cfg.setQueueDepth_(8)
        cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, self.fps))
        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            _filter_for(display, exclude_own=True), cfg, None)
        out = _StreamOutput.alloc().initWithOwner_(self)
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(out, 0, None, None)
        if not ok:
            raise RuntimeError("could not attach to the capture stream: %s" % err)
        started, box = threading.Event(), {}

        def on_start(err_):
            box["e"] = err_
            started.set()

        stream.startCaptureWithCompletionHandler_(on_start)
        if not _wait_for(started, 8) or box.get("e") is not None:
            raise RuntimeError("could not start the capture stream: %s" % box.get("e"))
        self._stream, self._output, self._key = stream, out, (x, y, bool(cursor))
        # the first frame arrives a moment after the stream starts; without this the recording
        # opens on a blank frame, which reads as a dropped frame rather than a still screen
        deadline = time.perf_counter() + 0.5
        while time.perf_counter() < deadline:
            with self._lock:
                if self._frame is not None:
                    return
            time.sleep(0.005)

    def _stop(self):
        if self._stream is None:
            return
        stopped = threading.Event()

        def on_stop(err):
            stopped.set()

        try:
            self._stream.stopCaptureWithCompletionHandler_(on_stop)
            _wait_for(stopped, 3)
        except Exception:
            pass
        self._stream = self._output = self._key = None

    # -- caller side
    def grab(self, x, y, cursor=False):
        """Newest frame of the region at (x, y), as BGRA top-down."""
        key = (int(x), int(y), bool(cursor))
        if self._key != key:
            self._start(int(x), int(y), bool(cursor))
        with self._lock:
            frame = self._frame
        return frame if frame is not None else self._blank

    def close(self):
        self._stop()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def begin_precise_timing():
    """Ask for the scheduling this capture loop needs, and report what to undo afterwards.

    Windows needs `timeBeginPeriod(1)`, because its default 15.6 ms granularity cannot pace a
    25 fps loop at all. macOS has no such knob and does not need one: sleeps here are already
    accurate to well under a millisecond. What is worth asking for is the quality-of-service
    class, so an unrelated busy process cannot shear the capture - the same intent as the
    above-normal thread priority on Windows.
    """
    try:
        libc = ctypes.CDLL(None)
        QOS_CLASS_USER_INTERACTIVE = 0x21
        libc.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
        libc.pthread_set_qos_class_self_np.restype = ctypes.c_int
        libc.pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0)
        return True
    except Exception:
        return False


def end_precise_timing(token):
    """Nothing to give back: the QoS class dies with the thread that asked for it."""
    return True


_LIBSYSTEM = []


def trim_memory():
    """Give freed memory back to macOS once a capture is over. See win.trim_memory.

    The picker holds a Retina still of the whole desktop and frees it when it closes, but the
    allocator keeps freed pages cached for reuse, so what Activity Monitor shows stays at the
    peak. malloc_zone_pressure_relief is what the system itself calls under memory pressure: it
    returns those cached pages. Measured on a 1512x982 point display: 55 MB idle, 67 MB after a
    picker, 54 MB once trimmed.

    The larger fix on Windows - numpy's OpenBLAS starting a thread per core - is not in here,
    because it is in magcopy/__init__.py and already covers this platform: 6 threads idle.
    """
    try:
        if not _LIBSYSTEM:
            lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            lib.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            lib.malloc_zone_pressure_relief.restype = ctypes.c_size_t
            _LIBSYSTEM.append(lib)
        _LIBSYSTEM[0].malloc_zone_pressure_relief(None, 0)      # every zone, as much as it can
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------- clipboard
def set_clipboard_image(bgra):
    """Put a BGRA array on the pasteboard as a real image.

    Windows needs a packed DIB built by hand and a second CF_HTML flavour to survive a paste
    into a browser. NSPasteboard takes PNG and TIFF and every application understands both.
    """
    try:
        arr = np.asarray(bgra)
        rgb = arr[:, :, 2::-1] if arr.shape[2] >= 3 else arr
        img = Image.fromarray(np.ascontiguousarray(rgb), "RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        ns = NSImage.alloc().initWithData_(_nsdata(data))
        ok = bool(pb.writeObjects_([ns])) if ns else False
        if not ok:                                   # writeObjects_ can refuse a malformed image
            ok = bool(pb.setData_forType_(_nsdata(data), NSPasteboardTypePNG))
        return ok
    except Exception:
        return False


def _nsdata(b):
    from Foundation import NSData
    return NSData.dataWithBytes_length_(b, len(b))


def set_clipboard_files(paths):
    """Put files on the pasteboard as file references, so a paste attaches rather than pastes a path."""
    try:
        urls = [NSURL.fileURLWithPath_(os.path.abspath(p)) for p in paths if os.path.exists(p)]
        if not urls:
            return False
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        return bool(pb.writeObjects_(urls))
    except Exception:
        return False


def clipboard_image_info():
    """(has_image, bytes) - only used by the self-test."""
    try:
        pb = NSPasteboard.generalPasteboard()
        for t in (NSPasteboardTypePNG, NSPasteboardTypeTIFF):
            d = pb.dataForType_(t)
            if d is not None:
                return (True, int(d.length()))
        return (False, 0)
    except Exception:
        return (False, 0)


# ----------------------------------------------------------------------------- global hotkeys
# Carbon rather than an NSEvent global monitor, for two reasons. RegisterEventHotKey consumes the
# keystroke, so Escape stopping a recording does not also reach the application being recorded;
# and it needs no Accessibility permission, which leaves MagCopy asking for one permission instead
# of two. The API is older than Cocoa and has no Objective-C face, so this part is ctypes.
_CMD, _SHIFT, _OPT, _CTRL = 0x0100, 0x0200, 0x0800, 0x1000
_EVENT_CLASS_KEYBOARD = 0x6B657962          # 'keyb'
_EVENT_HOTKEY_PRESSED = 5
# Two different four-character codes, and using one for the other is silent: GetEventParameter
# returns eventParameterNotFoundErr, the id stays 0, no route matches that, and the keystroke is
# swallowed. Registration still succeeds and the handler still fires, so everything looks right
# from the outside - which is how this shipped.
_PARAM_DIRECT_OBJECT = 0x2D2D2D2D           # '----', kEventParamDirectObject: the parameter's NAME
_TYPE_HOTKEY_ID = 0x686B6964                # 'hkid', typeEventHotKeyID: the parameter's TYPE
_SIGNATURE = 0x4D616743                     # 'MagC'


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

if IS_MAC:
    _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    _carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
                                            ctypes.c_void_p, ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_void_p)]
    _carbon.RegisterEventHotKey.restype = ctypes.c_int32
    _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    _carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    _carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_uint32,
                                            ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
    _carbon.InstallEventHandler.restype = ctypes.c_int32
    _carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.c_void_p]
    _carbon.GetEventParameter.restype = ctypes.c_int32

# macOS virtual key codes. Nothing like the Windows VK numbers, but the settings file stores the
# shortcut as text ("ctrl+shift+a"), so only parse/format have to agree with each other.
VK_NAMES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31,
    "u": 32, "[": 33, "i": 34, "p": 35, "enter": 36, "return": 36, "l": 37, "j": 38, "'": 39,
    "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48,
    "space": 49, "`": 50, "backspace": 51, "esc": 53, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "home": 115, "pageup": 116, "delete": 117,
    "end": 119, "pagedown": 121, "left": 123, "right": 124, "down": 125, "up": 126,
}
MOD_CONTROL, MOD_SHIFT, MOD_ALT, MOD_WIN = _CTRL, _SHIFT, _OPT, _CMD
MOD_NAMES = {"ctrl": _CTRL, "control": _CTRL, "alt": _OPT, "option": _OPT, "shift": _SHIFT,
             "cmd": _CMD, "command": _CMD, "win": _CMD, "super": _CMD}
# how each modifier is spelled back to the user: the symbols every other Mac application uses
_MOD_GLYPH = [("ctrl", _CTRL), ("shift", _SHIFT), ("alt", _OPT), ("cmd", _CMD)]


def parse_hotkey(text):
    """'ctrl+shift+a' -> (mods, keycode). None when the string names no usable key."""
    if not text:
        return None
    mods, vk = 0, None
    for part in [p.strip().lower() for p in str(text).replace(" ", "").split("+") if p.strip()]:
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
        elif part in VK_NAMES:
            vk = VK_NAMES[part]
        else:
            return None
    if vk is None:
        return None
    return (mods, vk)


def format_hotkey(mods, vk):
    parts = [name for name, bit in _MOD_GLYPH if mods & bit]
    rev = {}
    for k, v in VK_NAMES.items():
        rev.setdefault(v, k)
    parts.append(rev.get(vk, "0x%02X" % vk))
    return "+".join(parts)


# The modifier keys themselves, left and right: command, shift, option, control. The same set
# win.MODIFIER_VKS names, in this platform's key codes.
MODIFIER_KEYCODES = (55, 54, 56, 60, 58, 61, 59, 62)


def combo_down(combo=None):
    """True while any modifier - or `combo`'s own key - is still physically held.

    The same question win.combo_down answers, for the same reason: a shortcut armed while the
    keys that were just pressed to set it are still down can go off straight away, and the
    settings field waits for a clear keyboard before arming one. Asked of the HID state, which
    needs no permission - it says whether a key is down, not what anyone typed - and in the
    combined session state, so a key held by a posted event reads as held too, as it does from
    GetAsyncKeyState.
    """
    codes = list(MODIFIER_KEYCODES)
    parsed = parse_hotkey(combo) if combo else None
    if parsed:
        codes.append(parsed[1])
    try:
        state = Quartz.kCGEventSourceStateCombinedSessionState
        return any(bool(Quartz.CGEventSourceKeyState(state, int(c))) for c in codes)
    except Exception:
        return False


_HOTKEY_HANDLER = {"installed": False, "ref": None, "cb": None}
_HOTKEY_ROUTES = {}                 # carbon id -> callback
_HOTKEY_LOCK = threading.Lock()
_NEXT_HOTKEY_ID = [1]


def _hotkey_dispatch(call_ref, event, user_data):
    hk = _EventHotKeyID()
    st = _carbon.GetEventParameter(event, _PARAM_DIRECT_OBJECT, _TYPE_HOTKEY_ID, None,
                                   ctypes.sizeof(hk), None, ctypes.byref(hk))
    if st != 0:
        log_error("hotkey dispatch", "GetEventParameter returned %d" % st)
        return 0
    with _HOTKEY_LOCK:
        cb = _HOTKEY_ROUTES.get(int(hk.id))
    if cb is None:
        log_error("hotkey dispatch", "no route for hotkey id %d" % int(hk.id))
        return 0
    try:
        cb()
    except Exception:
        log_exc("hotkey callback")
    return 0


def _ensure_handler():
    """One Carbon handler for the whole process, on the main event target.

    Tk's own run loop is what pumps it, so callbacks arrive on the main thread - the opposite of
    the Win32 build, where they come off a private message-loop thread and have to be handed back
    to Tk. Calling root.after from here is still correct, just no longer necessary.
    """
    if _HOTKEY_HANDLER["installed"]:
        return True
    cb = _HANDLER(_hotkey_dispatch)
    spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
    ref = ctypes.c_void_p()
    st = _carbon.InstallEventHandler(_carbon.GetApplicationEventTarget(), cb, 1,
                                     ctypes.byref(spec), None, ctypes.byref(ref))
    if st != 0:
        return False
    _HOTKEY_HANDLER.update(installed=True, ref=ref, cb=cb)   # cb must outlive the handler
    return True


def _register(combo, callback):
    """(carbon_ref, id) or None. None means the combination is unusable or already taken."""
    parsed = parse_hotkey(combo)
    if not parsed or not _ensure_handler():
        return None
    mods, vk = parsed
    with _HOTKEY_LOCK:
        hk_id = _NEXT_HOTKEY_ID[0]
        _NEXT_HOTKEY_ID[0] += 1
    ref = ctypes.c_void_p()
    st = _carbon.RegisterEventHotKey(vk, mods, _EventHotKeyID(_SIGNATURE, hk_id),
                                     _carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
    if st != 0 or not ref:
        return None                       # -9878 is "another application already owns this one"
    with _HOTKEY_LOCK:
        _HOTKEY_ROUTES[hk_id] = callback
    return (ref, hk_id)


def _unregister(entry):
    ref, hk_id = entry
    try:
        _carbon.UnregisterEventHotKey(ref)
    except Exception:
        pass
    with _HOTKEY_LOCK:
        _HOTKEY_ROUTES.pop(hk_id, None)


class HotkeyManager:
    """The user's shortcuts. `rebind` swaps the whole set, which is how the settings UI changes one.

    **A callback must not touch Tk.** Carbon delivers these through a C callback that Tk's own
    event loop invokes, so it runs re-entrantly inside Tcl - and calling back into Tcl from there
    deadlocks rather than failing: `after` never returns from Tkinter._register and the whole
    program stops. Put the work on a queue and let the Tk side pick it up, which is what app.py's
    `post` does and why every binding there is `lambda: self.post(...)`.
    """

    def __init__(self, on_error=None):
        self._entries = []
        self._failed = []
        self._started = False
        self.on_error = on_error

    @property
    def started(self):
        """True once a set has been applied, so a caller knows to rebind rather than start."""
        return self._started

    @property
    def _callbacks(self):
        """What win.HotkeyManager keeps under this name: every live registration, by id.

        The rebinding test counts these to prove nothing stays armed while a shortcut is being
        typed, and it is the same test on both platforms.
        """
        with _HOTKEY_LOCK:
            return {hk_id: _HOTKEY_ROUTES.get(hk_id) for _ref, hk_id in self._entries}

    def start(self, bindings):
        """bindings: list of (name, hotkey_string, callback). Returns the names that failed."""
        return self._apply(bindings)

    def rebind(self, bindings):
        return self._apply(bindings)

    def stop(self):
        for e in self._entries:
            _unregister(e)
        self._entries = []

    def _apply(self, bindings):
        self.stop()
        self._started = True
        self._failed = []
        for name, combo, cb in (bindings or []):
            entry = _register(combo, cb)
            if entry:
                self._entries.append(entry)
            else:
                self._failed.append(name)
        if self._failed and self.on_error:
            try:
                self.on_error(list(self._failed))
            except Exception:
                pass
        return list(self._failed)


class TransientHotkey:
    """One global hotkey, held only while something is happening.

    Escape is the case this exists for. While a recording runs the focused window belongs to
    whatever is being recorded, so a Tk binding never sees the key - it has to be a real global
    hotkey. Carbon consumes it, which means Escape is taken from every other application for the
    length of the recording; that is why it is released the instant the recording ends.
    """

    def __init__(self, combo, callback):
        self.combo, self.callback = combo, callback
        self._entry = None
        self.ok = False

    def start(self):
        self._entry = _register(self.combo, self.callback)
        self.ok = self._entry is not None
        return self.ok

    def stop(self):
        if self._entry:
            _unregister(self._entry)
            self._entry = None
        self.ok = False


# ----------------------------------------------------------------------------- windows
_NSWIN_CACHE = {}


def _all_windows():
    try:
        return list(NSApp().windows()) if NSApp() else []
    except Exception:
        return []


def toplevel_hwnd(tk_widget):
    """The NSWindow behind a Tk toplevel.

    Tk on macOS is a Cocoa application, so every toplevel really is an NSWindow sitting in
    NSApp.windows(); the only difficulty is saying which. Geometry is enough almost always and
    costs nothing. When it is ambiguous - two windows the same size in the same place, which the
    recording border manages - the window is named for a moment and found by that instead.
    """
    if not IS_MAC or tk_widget is None:
        return None
    try:
        key = str(tk_widget)
        cached = _NSWIN_CACHE.get(key)
        if cached is not None:
            try:
                if cached.isVisible() or cached.frame().size.width >= 0:
                    return cached
            except Exception:
                _NSWIN_CACHE.pop(key, None)
        tk_widget.update_idletasks()
        hits = []
        # A toplevel that has not been shown yet is 1x1 at the origin as far as Tk is concerned,
        # which says nothing about which window it is - and whatever it matched would be cached
        # for the life of the widget. The recorder asks at exactly that moment, to exclude its
        # control bar before placing it, so go straight to the name for an unmapped one.
        if tk_widget.winfo_ismapped():
            want = (tk_widget.winfo_rootx(), tk_widget.winfo_rooty(),
                    tk_widget.winfo_width(), tk_widget.winfo_height())
            for w_ in _all_windows():
                fx, fy, fw, fh = _from_cocoa(_nsrect(w_.frame()))
                if abs(fx - want[0]) <= 2 and abs(fy - want[1]) <= 2 and \
                   abs(fw - want[2]) <= 4 and abs(fh - want[3]) <= 4:
                    hits.append(w_)
            if len(hits) == 1:
                _NSWIN_CACHE[key] = hits[0]
                return hits[0]
        marker = "__magcopy_%d__" % id(tk_widget)          # ambiguous: name it and look again
        old = tk_widget.title()
        tk_widget.title(marker)
        tk_widget.update_idletasks()
        found = next((w_ for w_ in _all_windows() if w_.title() == marker), None)
        tk_widget.title(old)
        if found is not None:
            _NSWIN_CACHE[key] = found
        return found or (hits[0] if hits else None)
    except Exception:
        return None


def luminance(hex_color):
    """Rough perceptual brightness of a #rrggbb colour, used to pick the window border mode."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def make_frameless(root, border_hex, bg_hex, dark=None):
    """Drop the title bar, keep a real window.

    Windows does this by clearing WS_CAPTION and WS_THICKFRAME and then asking DWM to round the
    corners. The macOS equivalent is not to remove the title bar but to let the content run
    underneath it: NSWindowStyleMaskFullSizeContentView, a transparent titlebar with the title
    hidden, and the three standard buttons hidden. Rounded corners and the hairline come free,
    because the window is still a titled window as far as the compositor is concerned.

    Setting the style mask to borderless instead is the obvious reading and is worse: the corners
    go square, and a borderless NSWindow is not meant to become the key window - keyboard
    shortcuts in the editor would be a coin toss.
    """
    if not IS_MAC:
        return None
    try:
        nswin = toplevel_hwnd(root)
        if nswin is None:
            return None
        NS_FULL_SIZE_CONTENT_VIEW = 1 << 15
        NS_TITLE_HIDDEN = 1
        nswin.setStyleMask_(nswin.styleMask() | NS_FULL_SIZE_CONTENT_VIEW)
        nswin.setTitlebarAppearsTransparent_(True)
        nswin.setTitleVisibility_(NS_TITLE_HIDDEN)
        for button in (0, 1, 2):            # close, minimise, zoom
            b = nswin.standardWindowButton_(button)
            if b is not None:
                b.setHidden_(True)
        # Not movableByWindowBackground: every Tk widget lives in one NSView, so macOS cannot
        # tell a background from a button and the whole window would drag from anywhere,
        # swallowing clicks. app.py drags it explicitly instead.
        try:
            from AppKit import NSAppearance
            is_dark = luminance(bg_hex) < 0.5 if dark is None else bool(dark)
            name = "NSAppearanceNameDarkAqua" if is_dark else "NSAppearanceNameAqua"
            nswin.setAppearance_(NSAppearance.appearanceNamed_(name))
        except Exception:
            pass
        return nswin
    except Exception:
        return None


def set_overlay_styles(hwnd):
    """Mark a window as transient chrome: above everything, on every Space, never taking focus.

    Same intent as the Win32 WS_EX_NOACTIVATE / WS_EX_TOOLWINDOW pair. The Spaces part has no
    Windows equivalent and is needed here: without it an overlay belongs to the desktop it was
    created on and vanishes when the user switches away mid-recording.
    """
    if hwnd is None:
        return False
    try:
        hwnd.setLevel_(NSScreenSaverWindowLevel)
        hwnd.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                    | NSWindowCollectionBehaviorStationary
                                    | NSWindowCollectionBehaviorFullScreenAuxiliary)
        hwnd.setHidesOnDeactivate_(False)
        # Off by default, and the picker is built entirely around the pointer: without it there
        # are no mouse-moved events, so the crosshair cannot follow and a drag gets no feedback.
        hwnd.setAcceptsMouseMovedEvents_(True)
        try:
            hwnd.setHasShadow_(False)
        except Exception:
            pass
        exclude_window(hwnd)          # and keep it out of anything we capture
        return True
    except Exception:
        return False


def allow_foreground():
    """No-op. macOS has no foreground lock to hand over - see NSWorkspace in reveal_in_finder."""
    return True


def raise_topmost(hwnd):
    """Re-assert a window's place above everything else."""
    if hwnd is None:
        return False
    try:
        hwnd.setLevel_(NSScreenSaverWindowLevel)
        hwnd.orderFrontRegardless()
        return True
    except Exception:
        return False


def move_window(hwnd, x, y):
    if hwnd is None:
        return False
    try:
        fr = hwnd.frame()
        cx, cy, _, _ = _to_cocoa((x, y, fr.size.width, fr.size.height))
        hwnd.setFrameOrigin_(Quartz.CGPointMake(cx, cy))
        return True
    except Exception:
        return False


def set_click_through(hwnd, on=True, alpha=255, keep_layer=False):
    """Let clicks fall through to whatever is underneath, and set the window's opacity.

    The Win32 version has a long comment about WS_EX_LAYERED drawing nothing until its alpha is
    set explicitly. There is no such trap here: ignoresMouseEvents and alphaValue are independent,
    and neither one blanks the window. `keep_layer` means "do not touch the opacity", which is
    what the colour-keyed chrome layer needs on Windows; here it simply skips the alpha.
    """
    if hwnd is None:
        return False
    try:
        hwnd.setIgnoresMouseEvents_(bool(on))
        if not keep_layer:
            hwnd.setAlphaValue_(max(0.0, min(1.0, alpha / 255.0)))
        return True
    except Exception:
        return False


def set_window_hole(hwnd, width, height, hole=None):
    """Shape a window to everything except `hole` (x, y, w, h), in window-local top-left pixels.

    This is how the region picker shows the selection live and undimmed: rather than painting a
    lighter rectangle - impossible on a uniformly translucent window - the dim layer stops
    existing where the selection is, so the real screen shows through at full brightness.

    Windows does this with SetWindowRgn. The equivalent here is an even-odd CAShapeLayer used as
    the content view's mask, which cuts through the Tk drawing as well as the window background.
    Passing hole=None restores the whole window.
    """
    if hwnd is None:
        return False
    try:
        view = hwnd.contentView()
        view.setWantsLayer_(True)
        layer = view.layer()
        if layer is None:
            return False
        with _keeping_level(hwnd):
            hwnd.setOpaque_(False)
            hwnd.setBackgroundColor_(NSColor.clearColor())
            return _mask_layer(layer, width, height, [(0, 0, width, height)],
                               hole if (hole and hole[2] > 0 and hole[3] > 0) else None)
    except Exception:
        return False


def set_window_shape(hwnd, width, height, rects):
    """Show only `rects` (top-left, window-local) and cut everything else away.

    The live picker's crosshair, outline, corner ticks and readout have to sit above the dim
    layer without being dimmed with it. Windows gives that layer a colour key and paints the
    background in it; Tk on macOS has no -transparentcolor at all, so the window is masked down
    to exactly the shapes that were drawn on it instead. Same result, and rather more exact.
    """
    if hwnd is None:
        return False
    try:
        view = hwnd.contentView()
        view.setWantsLayer_(True)
        layer = view.layer()
        if layer is None:
            return False
        with _keeping_level(hwnd):
            hwnd.setOpaque_(False)
            hwnd.setBackgroundColor_(NSColor.clearColor())
            return _mask_layer(layer, width, height, list(rects or []), None)
    except Exception:
        return False


@contextlib.contextmanager
def _keeping_level(hwnd):
    """Put the window back at the level it was at, whatever the body does to it.

    Insurance, not a fix for anything currently known: a window whose level is load-bearing - and
    the picker's layers are stacked against each other by level - should not be left at the mercy
    of a call that quietly changes it. Tk does exactly that elsewhere, demoting a grabbed window
    to the modal-panel level, and the symptom was hard enough to read once.
    """
    try:
        before = hwnd.level()
    except Exception:
        before = None
    try:
        yield
    finally:
        try:
            if before is not None and hwnd.level() != before:
                hwnd.setLevel_(before)
        except Exception:
            pass


def _mask_layer(layer, width, height, rects, hole):
    """Mask `layer` to `rects`, optionally minus `hole`. All top-left, window-local.

    The fill rule is not a detail. Even-odd is what subtracts the hole from the full rectangle,
    and it is exactly wrong for a set of shapes that may overlap: two overlapping rectangles
    cancel where they meet, so a text label inside its own background box punches a hole through
    the middle of that box. Non-zero unions them, which is what a set of drawn shapes means.
    """
    try:
        path = Quartz.CGPathCreateMutable()
        for (rx, ry, rw, rh) in rects:
            if rw <= 0 or rh <= 0:
                continue
            Quartz.CGPathAddRect(path, None,
                                 Quartz.CGRectMake(rx, height - ry - rh, rw, rh))
        if hole:
            hx, hy, hw, hh = (int(v) for v in hole)
            Quartz.CGPathAddRect(path, None,
                                 Quartz.CGRectMake(hx, height - hy - hh, hw, hh))
        shape = Quartz.CAShapeLayer.layer()
        shape.setFrame_(Quartz.CGRectMake(0, 0, width, height))
        shape.setPath_(path)
        shape.setFillRule_("even-odd" if hole else "non-zero")
        layer.setMask_(shape)
        return True
    except Exception:
        return False


def clear_window_shape(hwnd):
    if hwnd is None:
        return False
    try:
        layer = hwnd.contentView().layer()
        if layer is not None:
            layer.setMask_(None)
        return True
    except Exception:
        return False


def place_overlay(hwnd, x, y, width, height, click_through=False, alpha=1.0):
    """Style, position and reveal an overlay window without it being seen in the wrong place.

    Tk maps a window before anything can be done to it, and it maps a full-screen one 38 points
    low - clear of the menu bar, which is right for an ordinary window and wrong for this. Moving
    it afterwards works, but the frame it was first shown at has already been presented: the still
    appears shifted down by the height of the menu bar for one frame, which reads as the top bar
    briefly duplicating and the whole screen jumping.

    So the window is made fully transparent before it is positioned and only turned up once it is
    where it belongs. One extra step, and nothing is ever shown in the wrong place.
    """
    if hwnd is None:
        return False
    try:
        hwnd.setAlphaValue_(0.0)
        set_overlay_styles(hwnd)
        if click_through:
            hwnd.setIgnoresMouseEvents_(True)
        set_window_frame(hwnd, x, y, width, height)
        hwnd.orderFrontRegardless()
        hwnd.setAlphaValue_(max(0.0, min(1.0, float(alpha))))
        return True
    except Exception:
        return False


def set_window_frame(hwnd, x, y, width, height):
    """Place a window exactly where asked, in top-left coordinates.

    Tk cannot be relied on for this. Ask for a full-screen window at +0+0 and it comes back 38
    points lower, pushed clear of the menu bar - sensible for an ordinary window and wrong for one
    whose whole job is to cover the screen. The picker's undimmed layer landed there, so the still
    seen through the selection was offset by exactly the height of the menu bar, which reads as a
    second copy of it. Setting the frame on the window itself is not subject to that.
    """
    if hwnd is None:
        return False
    try:
        cx, cy, cw, ch = _to_cocoa((x, y, width, height))
        hwnd.setFrame_display_(Quartz.CGRectMake(cx, cy, cw, ch), True)
        return True
    except Exception:
        return False


def order_below(hwnd, other):
    """Put `hwnd` immediately below `other` in the window order, both being overlays.

    The frozen picker needs it: the undimmed still has to sit under the dimmed one rather than
    merely at the same level, where the order between two screen-saver-level windows is undefined.
    """
    if hwnd is None or other is None:
        return False
    try:
        hwnd.setLevel_(other.level())
        hwnd.orderWindow_relativeTo_(-1, other.windowNumber())      # NSWindowBelow
        return True
    except Exception:
        return False


def foreground_window_rect():
    """Rect of the frontmost window of the frontmost application."""
    try:
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
        for w_ in wins or []:
            if int(w_.get("kCGWindowLayer", 1)) != 0:
                continue
            b = w_.get("kCGWindowBounds")
            if not b:
                continue
            return (int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))
    except Exception:
        pass
    return None


def frontmost_app():
    """The application in front of everything, unless that is MagCopy itself."""
    try:
        from AppKit import NSRunningApplication
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        me = NSRunningApplication.currentApplication()
        if app is None or me is None or app.processIdentifier() == me.processIdentifier():
            return None
        return app
    except Exception:
        return None


def activate_app(app):
    """Hand focus back to `app`, as returned by frontmost_app.

    Clicking a window activates the application it belongs to, and there is no window-level
    switch for that on macOS the way WS_EX_NOACTIVATE is on Windows - only an NSPanel can decline,
    and a Tk toplevel is not one. So the controller gives focus back instead of never taking it:
    the app that was in front when the pointer arrived is made active again before the capture
    starts, which puts the screen back in the state it was in and matches what a shortcut does,
    since a shortcut never activates MagCopy in the first place.
    """
    if app is None:
        return False
    try:
        return bool(app.activateWithOptions_(0))
    except Exception:
        return False


def flash_taskbar(root):
    """Bounce the Dock icon once, the way a background app asks for attention here."""
    try:
        NSApp().requestUserAttention_(10)        # NSInformationalRequest
    except Exception:
        pass


def reveal_in_finder(path):
    """Show a file in Finder, selected, and bring Finder forward. One call, unlike explorer /select."""
    try:
        url = NSURL.fileURLWithPath_(os.path.abspath(path))
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([url])
        return True
    except Exception:
        return False
