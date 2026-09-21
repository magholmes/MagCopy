"""Win32 layer: DPI, screen capture, clipboard, global hotkeys, frameless windows.

Everything here is ctypes against user32/gdi32/kernel32/shell32/dwmapi, so MagCopy needs no
compiled extension beyond numpy and Pillow. The one rule worth remembering: ctypes defaults
every argument and return value to c_int, which silently truncates 64-bit handles and pointers,
so every function used here declares argtypes/restype before it is called.
"""
import ctypes
import ctypes.wintypes as w
import io
import os
import threading

import numpy as np
from PIL import Image

IS_WIN = os.name == "nt"

if IS_WIN:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    g32 = ctypes.WinDLL("gdi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    s32 = ctypes.WinDLL("shell32", use_last_error=True)
else:                                                       # import-safe elsewhere; the app refuses to start
    u32 = g32 = k32 = s32 = None


# ----------------------------------------------------------------------------- prototypes
def _declare():
    g32.CreateDCW.restype = w.HDC
    g32.CreateDCW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p]
    g32.CreateCompatibleDC.restype = w.HDC
    g32.CreateCompatibleDC.argtypes = [w.HDC]
    g32.CreateCompatibleBitmap.restype = w.HBITMAP
    g32.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.restype = w.HGDIOBJ
    g32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    g32.DeleteObject.argtypes = [w.HGDIOBJ]
    g32.DeleteDC.argtypes = [w.HDC]
    g32.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
    g32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
    g32.GetDIBits.restype = ctypes.c_int
    g32.CreateSolidBrush.restype = w.HBRUSH
    g32.CreateSolidBrush.argtypes = [w.COLORREF]

    k32.GlobalAlloc.restype = w.HGLOBAL
    k32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [w.HGLOBAL]
    k32.GlobalUnlock.argtypes = [w.HGLOBAL]
    k32.GlobalFree.restype = w.HGLOBAL
    k32.GlobalFree.argtypes = [w.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalSize.argtypes = [w.HGLOBAL]
    k32.GetCurrentThreadId.restype = w.DWORD

    u32.SetClipboardData.restype = w.HANDLE
    u32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
    u32.GetClipboardData.restype = w.HANDLE
    u32.GetClipboardData.argtypes = [w.UINT]
    u32.RegisterClipboardFormatW.restype = w.UINT
    u32.OpenClipboard.argtypes = [w.HWND]
    u32.GetDesktopWindow.restype = w.HWND
    u32.GetParent.restype = w.HWND
    u32.GetParent.argtypes = [w.HWND]
    u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
    u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u32.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u32.SetClassLongPtrW.restype = ctypes.c_size_t
    u32.SetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, w.UINT]
    u32.GetWindowRect.argtypes = [w.HWND, ctypes.c_void_p]
    u32.SetLayeredWindowAttributes.argtypes = [w.HWND, w.COLORREF, ctypes.c_ubyte, w.DWORD]
    u32.SetLayeredWindowAttributes.restype = w.BOOL
    u32.AllowSetForegroundWindow.argtypes = [w.DWORD]
    u32.AllowSetForegroundWindow.restype = w.BOOL
    u32.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
    u32.UnregisterHotKey.argtypes = [w.HWND, ctypes.c_int]
    u32.GetMessageW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.UINT]
    u32.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, ctypes.c_void_p, ctypes.c_void_p]
    u32.DrawIconEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, w.HICON, ctypes.c_int,
                               ctypes.c_int, w.UINT, w.HBRUSH, w.UINT]
    u32.GetIconInfo.argtypes = [w.HICON, ctypes.c_void_p]
    u32.CopyIcon.restype = w.HICON
    u32.CopyIcon.argtypes = [w.HICON]
    u32.DestroyIcon.argtypes = [w.HICON]
    s32.DragQueryFileW.argtypes = [w.HANDLE, w.UINT, w.LPWSTR, w.UINT]
    s32.DragQueryFileW.restype = w.UINT


if IS_WIN:
    _declare()


def set_dpi_aware():
    """Per-monitor-v2 so captures are in real pixels and overlays line up on mixed-DPI setups."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            u32.SetProcessDPIAware()
        except Exception:
            pass


# ----------------------------------------------------------------------------- structs
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hCursor", w.HANDLE), ("ptScreenPos", w.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                ("hbmMask", w.HBITMAP), ("hbmColor", w.HBITMAP)]


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
CURSOR_SHOWING, DI_NORMAL = 0x0001, 0x0003


def virtual_screen():
    """(x, y, w, h) of the whole desktop; x/y are negative when a monitor sits left of or above the primary."""
    return (u32.GetSystemMetrics(SM_XVIRTUALSCREEN), u32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            u32.GetSystemMetrics(SM_CXVIRTUALSCREEN), u32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


# ----------------------------------------------------------------------------- capture
class Grabber:
    """A reusable capture surface for one region size.

    The DC and bitmap are created once; each grab is a BitBlt plus a GetDIBits into the same
    buffer, which is what makes a 30 fps recording loop affordable. Returns BGRA, top-down.
    """

    def __init__(self, width, height, fps=None, cursor=None, retina=True, origin=None):
        # fps, cursor, retina and origin exist for the macOS grabber, which has to open a stream
        # at a frame rate and work out a Retina scale factor from where the region sits. None of
        # that applies here: the process is DPI-aware, so a point is a pixel and a BitBlt needs no
        # warning about how fast it will be called. They are accepted and ignored so that
        # recorder.py can call one constructor on both platforms.
        del fps, cursor, retina, origin
        self.w, self.h = int(width), int(height)
        self.out_w, self.out_h = self.w, self.h      # what the encoder is fed; no scaling here
        self.scale = 1.0
        self.src = g32.CreateDCW("DISPLAY", None, None, None)
        self.mem = g32.CreateCompatibleDC(self.src)
        self.bmp = g32.CreateCompatibleBitmap(self.src, self.w, self.h)
        self.old = g32.SelectObject(self.mem, self.bmp)
        self.bi = BITMAPINFO()
        self.bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self.bi.bmiHeader.biWidth = self.w
        self.bi.bmiHeader.biHeight = -self.h            # negative height = top-down rows
        self.bi.bmiHeader.biPlanes = 1
        self.bi.bmiHeader.biBitCount = 32
        self.bi.bmiHeader.biCompression = 0
        self.buf = ctypes.create_string_buffer(self.w * self.h * 4)
        self._closed = False

    def grab(self, x, y, cursor=False):
        g32.BitBlt(self.mem, 0, 0, self.w, self.h, self.src, int(x), int(y), SRCCOPY | CAPTUREBLT)
        if cursor:
            self._draw_cursor(x, y)
        g32.GetDIBits(self.mem, self.bmp, 0, self.h, self.buf, ctypes.byref(self.bi), 0)
        return np.frombuffer(self.buf, dtype=np.uint8).reshape(self.h, self.w, 4)

    def _draw_cursor(self, ox, oy):
        """Composite the live cursor into the frame, offset by its own hotspot."""
        try:
            ci = CURSORINFO()
            ci.cbSize = ctypes.sizeof(CURSORINFO)
            if not u32.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & CURSOR_SHOWING) or not ci.hCursor:
                return
            hicon = u32.CopyIcon(ci.hCursor)
            if not hicon:
                return
            try:
                ii = ICONINFO()
                if u32.GetIconInfo(hicon, ctypes.byref(ii)):
                    hx, hy = ii.xHotspot, ii.yHotspot
                    if ii.hbmMask:
                        g32.DeleteObject(ii.hbmMask)
                    if ii.hbmColor:
                        g32.DeleteObject(ii.hbmColor)
                else:
                    hx = hy = 0
                x = ci.ptScreenPos.x - ox - hx
                y = ci.ptScreenPos.y - oy - hy
                u32.DrawIconEx(self.mem, int(x), int(y), hicon, 0, 0, 0, None, DI_NORMAL)
            finally:
                u32.DestroyIcon(hicon)
        except Exception:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            g32.SelectObject(self.mem, self.old)
            g32.DeleteObject(self.bmp)
            g32.DeleteDC(self.mem)
            g32.DeleteDC(self.src)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def grab_once(x, y, width, height, cursor=False, retina=True):
    # retina is meaningful only on macOS, where a point is not a pixel. This process is
    # DPI-aware, so a BitBlt already reads the screen's real pixels; the argument is accepted so
    # that one call site works on both platforms.
    del retina
    with Grabber(width, height) as gr:
        return gr.grab(x, y, cursor).copy()


# ----------------------------------------------------------------------------- clipboard
CF_DIB, CF_HDROP = 8, 15
GMEM_MOVEABLE_ZERO = 0x0042


def _mem_handle(payload):
    gm = k32.GlobalAlloc(GMEM_MOVEABLE_ZERO, len(payload))
    if not gm:
        raise MemoryError("GlobalAlloc failed")
    p = k32.GlobalLock(gm)
    if not p:
        k32.GlobalFree(gm)
        raise MemoryError("GlobalLock failed")
    ctypes.memmove(p, payload, len(payload))
    k32.GlobalUnlock(gm)
    return gm


def _open_clipboard(retries=10, delay=0.02):
    """Another process can hold the clipboard for a moment; a few retries beats failing outright."""
    import time
    for _ in range(retries):
        if u32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def set_clipboard_image(bgra):
    """Put a screenshot on the clipboard as CF_DIB (what every app understands) plus PNG.

    Alpha is dropped: a screen grab is opaque, and a 32-bit DIB with a zero alpha channel is
    what makes pasted screenshots come out black in some apps.
    """
    h, wd = bgra.shape[:2]
    rgb = np.ascontiguousarray(bgra[:, :, :3])
    flipped = np.ascontiguousarray(rgb[::-1])                     # DIBs are bottom-up
    stride = (wd * 3 + 3) & ~3                                    # rows are DWORD-aligned
    if stride != wd * 3:
        padded = np.zeros((h, stride), dtype=np.uint8)
        padded[:, :wd * 3] = flipped.reshape(h, wd * 3)
        body = padded.tobytes()
    else:
        body = flipped.tobytes()
    hdr = BITMAPINFOHEADER()
    hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    hdr.biWidth, hdr.biHeight, hdr.biPlanes, hdr.biBitCount = wd, h, 1, 24
    hdr.biCompression = 0
    hdr.biSizeImage = len(body)
    dib = bytes(hdr) + body

    png = io.BytesIO()
    Image.fromarray(rgb[:, :, ::-1], "RGB").save(png, "PNG", compress_level=1)
    png = png.getvalue()

    if not _open_clipboard():
        return False
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_DIB, _mem_handle(dib))
        cf_png = u32.RegisterClipboardFormatW("PNG")
        if cf_png:
            u32.SetClipboardData(cf_png, _mem_handle(png))
        return True
    except Exception:
        return False
    finally:
        u32.CloseClipboard()


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", w.DWORD), ("pt", w.POINT), ("fNC", w.BOOL), ("fWide", w.BOOL)]


def set_clipboard_files(paths):
    """CF_HDROP, so Ctrl+V in Discord/Explorer pastes the file itself (this is how a GIF gets sent)."""
    paths = [os.path.abspath(p) for p in paths]
    df = DROPFILES()
    df.pFiles = ctypes.sizeof(DROPFILES)
    df.fWide = True
    body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    if not _open_clipboard():
        return False
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(CF_HDROP, _mem_handle(bytes(df) + body))
        return True
    except Exception:
        return False
    finally:
        u32.CloseClipboard()


def clipboard_image_info():
    """(has_dib, bytes) - only used by the self-test."""
    if not _open_clipboard():
        return (False, 0)
    try:
        if not u32.IsClipboardFormatAvailable(CF_DIB):
            return (False, 0)
        h = u32.GetClipboardData(CF_DIB)
        return (True, int(k32.GlobalSize(h)) if h else 0)
    finally:
        u32.CloseClipboard()


# ----------------------------------------------------------------------------- global hotkeys
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_NULL = 0x0312, 0x0000

VK_NAMES = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "space": 0x20, "tab": 0x09, "enter": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "printscreen": 0x2C, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "backspace": 0x08, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC, "`": 0xC0,
}
MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
             "win": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN}


def parse_hotkey(text):
    """'ctrl+shift+a' -> (mods, vk). Returns None when the string names no usable key."""
    if not text:
        return None
    mods, vk = 0, None
    for part in [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]:
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
        elif part in VK_NAMES:
            vk = VK_NAMES[part]
        elif len(part) == 1 and (part.isalpha() or part.isdigit()):
            vk = ord(part.upper())
        else:
            return None
    if vk is None:
        return None
    return (mods, vk)


def format_hotkey(mods, vk):
    parts = []
    for name, bit in (("ctrl", MOD_CONTROL), ("shift", MOD_SHIFT), ("alt", MOD_ALT), ("win", MOD_WIN)):
        if mods & bit:
            parts.append(name)
    rev = {v: k for k, v in VK_NAMES.items()}
    if vk in rev:
        parts.append(rev[vk])
    elif 0x30 <= vk <= 0x5A:
        parts.append(chr(vk).lower())
    else:
        parts.append("0x%02X" % vk)
    return "+".join(parts)


class HotkeyManager:
    """Owns a thread with its own message queue; RegisterHotKey delivers WM_HOTKEY there.

    Callbacks run on that thread, so anything touching Tk must hand back with `after`.
    Bindings are re-applied wholesale by `rebind`, which is how the settings UI changes a shortcut.
    """

    def __init__(self, on_error=None):
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending = None                # list of (id, mods, vk, callback) to (re)register
        self._callbacks = {}
        self._failed = []                   # names that Windows refused (usually already taken)
        self.on_error = on_error

    @property
    def started(self):
        """True once the hotkey thread exists, so a caller knows to rebind rather than start."""
        return self._thread is not None

    def start(self, bindings):
        """bindings: list of (name, hotkey_string, callback)."""
        self._pending = bindings
        self._thread = threading.Thread(target=self._run, name="magcopy-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self._failed

    def rebind(self, bindings):
        """Swap the whole set. Returns the names Windows refused."""
        with self._lock:
            self._pending = bindings
            self._apply_done = threading.Event()
        if self._tid:
            u32.PostThreadMessageW(self._tid, WM_NULL, 0, 0)
            self._apply_done.wait(2)
        return list(self._failed)

    def stop(self):
        self._stop.set()
        if self._tid:
            u32.PostThreadMessageW(self._tid, WM_NULL, 0, 0)
        if self._thread:
            self._thread.join(2)

    # -- thread side
    def _apply(self):
        for hk_id in list(self._callbacks):
            u32.UnregisterHotKey(None, hk_id)
        self._callbacks.clear()
        self._failed = []
        for i, (name, combo, cb) in enumerate(self._pending or [], start=1):
            parsed = parse_hotkey(combo)
            if not parsed:
                self._failed.append(name)
                continue
            mods, vk = parsed
            if u32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                self._callbacks[i] = (name, cb)
            else:
                self._failed.append(name)
        if self._failed and self.on_error:
            try:
                self.on_error(list(self._failed))
            except Exception:
                pass

    def _run(self):
        self._tid = k32.GetCurrentThreadId()
        msg = w.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)       # force this thread to own a queue
        self._apply()
        self._ready.set()
        while not self._stop.is_set():
            r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                entry = self._callbacks.get(int(msg.wParam))
                if entry:
                    try:
                        entry[1]()
                    except Exception:
                        pass
            with self._lock:
                pending, done = self._pending, getattr(self, "_apply_done", None)
                if pending is not None and done is not None and not done.is_set():
                    self._apply()
                    done.set()
        for hk_id in list(self._callbacks):
            u32.UnregisterHotKey(None, hk_id)


class TransientHotkey:
    """One global hotkey, held only while something is happening, on a thread of its own.

    This is deliberately not part of HotkeyManager. That one owns the user's shortcuts and
    re-registers the whole set whenever it changes; borrowing it for a key that lives for a few
    seconds would mean tearing down working shortcuts and hoping Windows gives them back.

    Escape is the case this exists for. While a recording runs the foreground window belongs to
    whatever is being recorded, so a Tk binding never sees the key - it has to be a real global
    hotkey. That does mean Escape is taken from every other application for the length of the
    recording, which is why it is released the moment the recording ends.
    """

    def __init__(self, combo, callback):
        self.combo, self.callback = combo, callback
        self._thread = None
        self._tid = None
        self._stop = threading.Event()
        self.ok = False

    def start(self):
        parsed = parse_hotkey(self.combo)
        if not parsed:
            return False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(parsed, ready),
                                        name="magcopy-transient-hotkey", daemon=True)
        self._thread.start()
        ready.wait(2)
        return self.ok

    def stop(self):
        self._stop.set()
        if self._tid:
            u32.PostThreadMessageW(self._tid, WM_NULL, 0, 0)
        if self._thread:
            self._thread.join(2)
        self._thread = None

    def _run(self, parsed, ready):
        mods, vk = parsed
        self._tid = k32.GetCurrentThreadId()
        msg = w.MSG()
        u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)       # force this thread to own a queue
        self.ok = bool(u32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        ready.set()
        if not self.ok:
            return
        try:
            while not self._stop.is_set():
                r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1):
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            u32.UnregisterHotKey(None, 1)


# ----------------------------------------------------------------------------- frameless window
_W32 = {}
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00020000, 0x00080000


def toplevel_hwnd(tk_widget):
    """Tk's own HWND is a child of the real top-level created by the Tk runtime; walk up to it."""
    try:
        hwnd = u32.GetParent(tk_widget.winfo_id())
        return hwnd or tk_widget.winfo_id()
    except Exception:
        return None


def luminance(hex_color):
    """Rough perceptual brightness of a #rrggbb colour, used to pick the window border mode."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _colorref(hex_color):
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return (b << 16) | (g << 8) | r


def make_frameless(root, border_hex, bg_hex, dark=None):
    """Drop the title bar and resize frame, keep a real minimizable window, round it on Windows 11."""
    if not IS_WIN:
        return None
    try:
        hwnd = toplevel_hwnd(root)
        if not hwnd:
            return None
        style = u32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        style = (style & ~WS_CAPTION & ~WS_THICKFRAME) | WS_MINIMIZEBOX | WS_SYSMENU
        u32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
        try:                                                   # paint the frame in the theme colour, never black
            brush = g32.CreateSolidBrush(_colorref(bg_hex))
            if brush:
                old = _W32.get("brush")
                _W32["brush"] = brush
                u32.SetClassLongPtrW(hwnd, -10, brush)         # GCLP_HBRBACKGROUND
                u32.SetClassLongPtrW(root.winfo_id(), -10, brush)
                if old:
                    g32.DeleteObject(old)
        except Exception:
            pass
        try:
            dwm = ctypes.windll.dwmapi
            pref = ctypes.c_int(2)                             # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            col = ctypes.c_uint(_colorref(border_hex))
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), ctypes.sizeof(col))
            is_dark = luminance(bg_hex) < 0.5 if dark is None else bool(dark)
            flag = ctypes.c_int(1 if is_dark else 0)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(flag), ctypes.sizeof(flag))
        except Exception:
            pass
        return hwnd
    except Exception:
        return None


HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040


WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x08000000, 0x00000080


def set_overlay_styles(hwnd):
    """Mark a window as transient chrome: never takes focus, never appears in Alt+Tab.

    Every window MagCopy paints over the screen - the crosshair, the recording frame, the bar -
    is decoration for something else. WS_EX_NOACTIVATE keeps it from stealing focus from the app
    being recorded, and WS_EX_TOOLWINDOW keeps a dozen one-pixel strips out of the task switcher.
    Clicks still work, so the bar's stop and cancel keep responding.
    """
    GWL_EXSTYLE = -20
    try:
        ex = u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        return True
    except Exception:
        return False


def allow_foreground():
    """Let the next process we start put itself in front of us.

    Windows will not normally let a background process steal the foreground, which is usually a
    kindness: it is what stops installers jumping in front of what you are typing. It also means
    that launching Explorer to show a freshly saved file lands it *behind* our own window, where
    it only blinks in the taskbar. ASFW_ANY hands our own foreground rights to whatever starts
    next, which is exactly the case the call was designed for.
    """
    try:
        return bool(u32.AllowSetForegroundWindow(w.DWORD(0xFFFFFFFF)))      # ASFW_ANY
    except Exception:
        return False


def raise_topmost(hwnd):
    """Re-assert a window's place in the topmost band.

    Setting WS_EX_TOPMOST once is not a promise: another app going topmost, or full screen, can
    end up over it, and the recording frame is useless the moment something covers it. Cheap
    enough to simply repeat while a recording runs.
    """
    try:
        u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        return True
    except Exception:
        return False


def move_window(hwnd, x, y):
    u32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010)   # NOSIZE|NOZORDER|NOACTIVATE


def set_click_through(hwnd, on=True, alpha=255, keep_layer=False):
    """WS_EX_TRANSPARENT so the recording frame never eats clicks meant for the app underneath.

    WS_EX_LAYERED has to come with it - transparency hit-testing is only reliable on a layered
    window - but a layered window draws nothing at all until its layer attributes are set. Adding
    the style and stopping there is what made the recording rectangle invisible; the window was
    there the whole time, composited at zero opacity. So set the alpha explicitly, every time.
    """
    GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED, LWA_ALPHA = -20, 0x00000020, 0x00080000, 0x02
    try:
        if keep_layer:
            # The caller already configured this window's transparency (Tk's -transparentcolor
            # sets LWA_COLORKEY). Setting LWA_ALPHA here would replace that and make the whole
            # window opaque, so only add the hit-testing flag and leave the layer alone.
            #
            # Read it back: Tk re-applies window styles of its own while a window is being mapped,
            # and can drop this one again. Without WS_EX_TRANSPARENT the window is only
            # click-through where its pixels match the colour key - so anything drawn on it, a
            # crosshair under the pointer above all, silently eats the click.
            for _ in range(3):
                ex = u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                want = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
                u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want)
                u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
                if bool(u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT) == bool(on):
                    return True
            return False
        ex = u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if on:
            ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            ex &= ~WS_EX_TRANSPARENT
        u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
        if ex & WS_EX_LAYERED:
            u32.SetLayeredWindowAttributes(hwnd, 0, int(max(0, min(255, alpha))), LWA_ALPHA)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------- monitors
class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, ctypes.POINTER(w.RECT), w.LPARAM)


def monitors():
    """Each monitor as (bounds, work_area, is_primary), in physical virtual-screen pixels.

    The work area excludes the taskbar. Placing chrome against the virtual screen instead puts it
    on whichever monitor happens to be furthest left, or underneath the taskbar.
    """
    found = []

    def cb(hmon, hdc, lprect, data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            b, k = mi.rcMonitor, mi.rcWork
            found.append(((b.left, b.top, b.right - b.left, b.bottom - b.top),
                          (k.left, k.top, k.right - k.left, k.bottom - k.top),
                          bool(mi.dwFlags & 1)))
        return True

    try:
        u32.EnumDisplayMonitors.argtypes = [w.HDC, ctypes.c_void_p, _MONITORENUMPROC, w.LPARAM]
        u32.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.c_void_p]
        u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(cb), 0)
    except Exception:
        pass
    if not found:
        vx, vy, vw, vh = virtual_screen()
        found = [((vx, vy, vw, vh), (vx, vy, vw, vh), True)]
    return found


def work_area_for(rect):
    """Work area of the monitor holding the biggest share of `rect`."""
    x, y, wd, ht = rect

    def overlap(m):
        mx, my, mw, mh = m
        return (max(0, min(x + wd, mx + mw) - max(x, mx))
                * max(0, min(y + ht, my + mh) - max(y, my)))

    best = max(monitors(), key=lambda m: overlap(m[0]), default=None)
    if best and overlap(best[0]) > 0:
        return best[1]
    return virtual_screen()


# ----------------------------------------------------------------------------- window regions
RGN_DIFF = 4


def set_window_hole(hwnd, width, height, hole=None):
    """Shape a window to everything except `hole` (x, y, w, h), in window-local pixels.

    This is how the region picker shows the selection live and undimmed: rather than painting a
    lighter rectangle - impossible on a uniformly translucent window - the dim layer simply stops
    existing where the selection is, so what shows through is the real screen at full brightness.
    Passing hole=None restores the whole window.
    """
    try:
        g32.CreateRectRgn.restype = w.HRGN
        g32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        g32.CombineRgn.argtypes = [w.HRGN, w.HRGN, w.HRGN, ctypes.c_int]
        u32.SetWindowRgn.argtypes = [w.HWND, w.HRGN, w.BOOL]
        full = g32.CreateRectRgn(0, 0, int(width), int(height))
        if hole and hole[2] > 0 and hole[3] > 0:
            x, y, hw, hh = (int(v) for v in hole)
            cut = g32.CreateRectRgn(x, y, x + hw, y + hh)
            g32.CombineRgn(full, full, cut, RGN_DIFF)
            g32.DeleteObject(cut)
        u32.SetWindowRgn(hwnd, full, True)       # the window owns the region now; do not delete it
        return True
    except Exception:
        return False


def foreground_window_rect():
    """Rect of whatever window is in front - used by the overlay's 'snap to window' helper."""
    try:
        u32.GetForegroundWindow.restype = w.HWND
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return None
        r = w.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


def flash_taskbar(root):
    try:
        u32.FlashWindow(toplevel_hwnd(root), True)
    except Exception:
        pass
