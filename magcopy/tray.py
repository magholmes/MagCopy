"""Whichever menu bar or notification area this machine has.

Both sides expose the same three things - `Tray`, `already_running` and `wake_running_instance` -
so app.py and main.py do not know which one they got.
"""
import sys

if sys.platform == "darwin":
    from .tray_mac import Tray, already_running, wake_running_instance   # noqa: F401
else:
    from .tray_win import Tray, already_running, wake_running_instance   # noqa: F401

__all__ = ["Tray", "already_running", "wake_running_instance"]
