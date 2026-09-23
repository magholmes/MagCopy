"""MagCopy has to stay small while it sits in the tray.

The failure this guards against was invisible in use and enormous in Task Manager: numpy's
bundled OpenBLAS started a worker per CPU core on import and committed a buffer for each - 19
threads and ~611 MB on a 20-core machine - for matrix maths MagCopy never does. The fix is two
environment variables set in magcopy/__init__.py before numpy loads, and it breaks silently if
anything ever imports numpy first, or a numpy release changes which variable it reads.

So this measures the process rather than checking the variables: committed memory, threads, and
what Task Manager would show after a screenshot has come and gone.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.name != "nt":
    print("not windows - the counters read here are Win32\nMEMORY OK")
    sys.exit(0)

import ctypes
import ctypes.wintypes as wt
import gc

import magcopy                                     # first, exactly as the launcher does

IDLE_COMMIT_MB = 160      # measured ~65; the old build was ~680
THREAD_LIMIT = 16         # measured ~10; the old build was ~29 on 20 cores
IDLE_TASKMGR_MB = 80      # measured ~21 after a trim; the old build showed 186


class PMC2(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t), ("PrivateWorkingSetSize", ctypes.c_size_t),
                ("SharedCommitUsage", ctypes.c_ulonglong)]


k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
k32.GetCurrentProcess.restype = wt.HANDLE
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC2), wt.DWORD]


def mem():
    gc.collect()
    m = PMC2()
    m.cb = ctypes.sizeof(PMC2)
    psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
    return m.PrivateUsage / 1048576.0, m.PrivateWorkingSetSize / 1048576.0


def thread_count():
    class TE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                    ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", ctypes.c_long),
                    ("tpDeltaPri", ctypes.c_long), ("dwFlags", wt.DWORD)]
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x4, 0)               # TH32CS_SNAPTHREAD
    te = TE32()
    te.dwSize = ctypes.sizeof(TE32)
    pid, n = os.getpid(), 0
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return n


ok = True


def check(name, good, detail=""):
    global ok
    ok = ok and good
    print("%-58s %s %s" % (name, "ok  " if good else "FAIL", detail))


check("BLAS is held to one thread before numpy loads",
      os.environ.get("OPENBLAS_NUM_THREADS") == "1", repr(os.environ.get("OPENBLAS_NUM_THREADS")))

import tkinter as tk
from magcopy import overlay, plat
plat.set_dpi_aware()
from magcopy.theme import register_fonts
register_fonts()
root = tk.Tk()
root.withdraw()
from magcopy.app import App

app = App(root)
app.hide_window()


def pump(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        time.sleep(0.01)


pump(0.6)
commit, _ = mem()
threads = thread_count()
check("idle committed memory is small", commit < IDLE_COMMIT_MB,
      "%.0f MB (limit %d)" % (commit, IDLE_COMMIT_MB))
check("and it is not running a thread per core", threads < THREAD_LIMIT,
      "%d threads (limit %d)" % (threads, THREAD_LIMIT))

# a screenshot picker is the biggest thing the app ever builds - the whole desktop, twice
sel = overlay.selector_for(root, app.theme, app.fonts, app.settings)
root.after(500, sel._cancel)
sel.run()
del sel
app._after_capture(False)
_, resident_after = mem()
pump(3.4)                                          # the trim is debounced 2.5 s
commit2, resident = mem()
check("a picker's memory is released when it closes", commit2 < commit + 20,
      "%.0f MB -> %.0f MB committed" % (commit, commit2))
check("and handed back, so Task Manager shows it too", resident < IDLE_TASKMGR_MB,
      "%.0f MB right after, %.0f MB once trimmed (limit %d)"
      % (resident_after, resident, IDLE_TASKMGR_MB))

app.quit()
print("\nMEMORY", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
