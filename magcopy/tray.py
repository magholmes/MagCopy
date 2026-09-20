"""The notification-area icon.

MagCopy spends most of its life with no window open, waiting on two shortcuts, so it needs a
place to live that is not the taskbar. Shell_NotifyIcon wants a real window on the same thread
as the message loop that serves it, so this creates a message-only window with a Python WNDPROC
and runs its own loop - the same shape as the hotkey thread.

Menu commands are dispatched back to Tk by the caller (`on_command` is invoked on this thread,
so the app hands it straight to `root.after`).
"""
import ctypes
import ctypes.wintypes as w
import os
import threading

from .settings import APP_NAME, log_exc

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
s32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_DESTROY, WM_COMMAND, WM_APP = 0x0002, 0x0111, 0x8000
WM_TRAY = WM_APP + 1
WM_SHOW_WINDOW = WM_APP + 2      # posted by a second launch to surface the instance already running
TRAY_CLASS = "MagCopyTrayWindow"
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD)]


def _declare():
    u32.CreateWindowExW.restype = w.HWND
    u32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU,
                                    w.HINSTANCE, w.LPVOID]
    u32.DefWindowProcW.restype = ctypes.c_ssize_t
    u32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    u32.RegisterClassW.restype = w.ATOM
    u32.LoadImageW.restype = w.HANDLE
    u32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
    u32.LoadIconW.restype = w.HICON
    u32.CreatePopupMenu.restype = w.HMENU
    u32.TrackPopupMenu.restype = ctypes.c_int
    u32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HWND, ctypes.c_void_p]
    u32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
    u32.DestroyWindow.argtypes = [w.HWND]
    k32.GetModuleHandleW.restype = w.HMODULE
    s32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.c_void_p]


_declare()


class Tray:
    """Adds an icon to the notification area. `items` is a list of (label, key); key None = separator."""

    def __init__(self, icon_path, tip, items, on_command, on_activate):
        self.icon_path = icon_path
        self.tip = tip[:127]
        self.items = items
        self.on_command = on_command
        self.on_activate = on_activate
        self.hwnd = None
        self.hicon = None
        self._nid = None
        self._thread = None
        self._ready = threading.Event()
        self._tid = None
        self._proc = None                 # keep the WNDPROC alive or Windows calls freed memory

    def start(self):
        self._thread = threading.Thread(target=self._run, name="magcopy-tray", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.hwnd is not None

    def stop(self):
        if self.hwnd:
            try:
                u32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(2)

    def notify(self, title, text):
        """Balloon tip - used for 'saved' and for errors that happen with no window open."""
        if not self._nid:
            return
        try:
            nid = self._nid
            nid.uFlags = NIF_INFO = 0x10
            nid.szInfoTitle = title[:63]
            nid.szInfo = text[:255]
            nid.dwInfoFlags = 0
            s32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        except Exception:
            pass

    # ---- thread side
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                low = lparam & 0xFFFF
                if low in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_activate()
                elif low == WM_RBUTTONUP:
                    self._menu(hwnd)
                return 0
            if msg == WM_SHOW_WINDOW:
                self.on_activate()
                return 0
            if msg == WM_COMMAND:
                idx = wparam & 0xFFFF
                real = [it for it in self.items if it[1]]
                if 1 <= idx <= len(real):
                    self.on_command(real[idx - 1][1])
                return 0
            if msg == WM_DESTROY:
                self._remove()
                u32.PostQuitMessage(0)
                return 0
        except Exception:
            log_exc("tray wndproc")
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self, hwnd):
        menu = u32.CreatePopupMenu()
        idx = 0
        for label, key in self.items:
            if key is None:
                u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                idx += 1
                u32.AppendMenuW(menu, MF_STRING, idx, label)
        pt = w.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(hwnd)                    # so the menu closes when focus leaves
        cmd = u32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None)
        u32.DestroyMenu(menu)
        if cmd:
            real = [it for it in self.items if it[1]]
            if 1 <= cmd <= len(real):
                self.on_command(real[cmd - 1][1])

    def _remove(self):
        if self._nid:
            try:
                s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
            self._nid = None

    def _run(self):
        try:
            self._tid = k32.GetCurrentThreadId()
            hinst = k32.GetModuleHandleW(None)
            self._proc = WNDPROC(self._wndproc)
            cls = WNDCLASS()
            cls.lpfnWndProc = self._proc
            cls.hInstance = hinst
            cls.lpszClassName = TRAY_CLASS
            u32.RegisterClassW(ctypes.byref(cls))
            self.hwnd = u32.CreateWindowExW(0, TRAY_CLASS, APP_NAME, 0, 0, 0, 0, 0,
                                            None, None, hinst, None)
            if not self.hwnd:
                self._ready.set()
                return
            self.hicon = None
            if self.icon_path and os.path.exists(self.icon_path):
                self.hicon = u32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                            LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not self.hicon:
                self.hicon = u32.LoadIconW(None, ctypes.c_wchar_p(32512))    # IDI_APPLICATION
            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = self.hicon
            nid.szTip = self.tip
            s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self._nid = nid
        except Exception:
            log_exc("tray start")
        finally:
            self._ready.set()
        msg = w.MSG()
        while True:
            r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        self._remove()


# ----------------------------------------------------------------------------- single instance
def already_running():
    """True if another MagCopy owns the instance mutex.

    Two copies cannot both hold the global shortcuts - the second one silently loses them - and
    with "start with Windows" on by default that is easy to arrange by accident. The named mutex
    is held for the life of the process, so it is released even on a hard kill.
    """
    ERROR_ALREADY_EXISTS = 183
    k32.CreateMutexW.restype = w.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    handle = k32.CreateMutexW(None, False, r"Local\MagCopy-single-instance")
    if not handle:
        return False
    _MUTEX.append(handle)                       # keep it alive for the process lifetime
    return ctypes.get_last_error() == ERROR_ALREADY_EXISTS


_MUTEX = []


def wake_running_instance():
    """Ask the copy that is already running to show its window."""
    try:
        u32.FindWindowW.restype = w.HWND
        u32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
        hwnd = u32.FindWindowW(TRAY_CLASS, None)
        if hwnd:
            u32.PostMessageW(hwnd, WM_SHOW_WINDOW, 0, 0)
            return True
    except Exception:
        log_exc("wake running instance")
    return False
