"""The platform layer: whichever of win.py or mac.py this machine needs.

Everything that touches the operating system - capture, clipboard, global hotkeys, overlay
windows - lives behind this one name, so the rest of MagCopy is written once. `win.py` is Win32
through ctypes; `mac.py` is PyObjC. They expose the same functions, and a caller that only uses
what is listed here never needs to know which one it got.

A few capabilities exist on one platform and not the other. Rather than making every caller test
for them, the missing side gets a stub that does the harmless thing: `reveal_in_finder` falls
back to Explorer's own select, `set_window_shape` reports that it could not, the permission
checks say yes on a system that has no such permission to ask for. Real per-platform behaviour
belongs in the module that owns it, not in an `if` at the call site.
"""
import os
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"

if IS_MAC:
    from .mac import *          # noqa: F401,F403
    from . import mac as impl   # noqa: F401
else:
    from .win import *          # noqa: F401,F403
    from . import win as impl   # noqa: F401


# ----------------------------------------------------------------------------- fallbacks
if not hasattr(impl, "trim_memory"):
    def trim_memory():
        """Nothing to ask for here; the OS manages residency on its own."""
        return False


if not hasattr(impl, "combo_down"):
    def combo_down(combo=None):
        """No way to ask here, so say the keyboard is clear and arm immediately."""
        return False


if not IS_MAC:
    def has_screen_recording():
        """Windows needs no permission to read the screen."""
        return True

    def request_screen_recording():
        return True

    def has_accessibility():
        return True

    def open_privacy_pane(which="ScreenCapture"):
        return False

    def scale_for(rect):
        """The Win32 layer already works in physical pixels, so there is nothing to scale."""
        return 1.0

    def exclude_window(handle):
        """No equivalent: the recording border is kept out of frame by being outside it."""
        return False

    def clear_excluded():
        return False

    def set_window_shape(hwnd, width, height, rects):
        """No equivalent. The Windows chrome layer uses a colour key instead."""
        return False

    def clear_window_shape(hwnd):
        return set_window_hole(hwnd, 1, 1, None)       # noqa: F405

    def begin_precise_timing():
        """1 ms scheduling and above-normal priority, for the length of a capture."""
        raised = False
        try:
            import ctypes as _c
            _c.windll.winmm.timeBeginPeriod(1)          # 15.6 ms -> 1 ms scheduling
            raised = True
        except Exception:
            pass
        try:
            import ctypes as _c
            _c.windll.kernel32.SetThreadPriority(
                _c.windll.kernel32.GetCurrentThread(), 1)        # ABOVE_NORMAL
        except Exception:
            pass
        return raised

    def end_precise_timing(token):
        if token:
            try:
                import ctypes as _c
                _c.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        return True

    def place_overlay(hwnd, x, y, width, height, click_through=False, alpha=1.0):
        """Win32 shows a window where it is told, so styling and moving it is enough."""
        set_overlay_styles(hwnd)                                    # noqa: F405
        if click_through:
            set_click_through(hwnd, True, int(alpha * 255))         # noqa: F405
        move_window(hwnd, x, y)                                     # noqa: F405
        raise_topmost(hwnd)                                         # noqa: F405
        return True

    def set_window_frame(hwnd, x, y, width, height):
        """Win32 places an overrideredirect window where it is told, so this is move_window."""
        return move_window(hwnd, x, y)                      # noqa: F405

    def order_below(hwnd, other):
        """No equivalent needed: the Win32 picker moves a canvas rather than stacking windows."""
        return False

    def reveal_in_finder(path):
        """Show a file in Explorer, selected."""
        try:
            allow_foreground()                          # noqa: F405
            subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
            return True
        except Exception:
            return False
