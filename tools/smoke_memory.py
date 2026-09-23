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

if sys.platform == "darwin":
    # macOS: the same three questions, asked of the numbers Activity Monitor shows. The thread
    # cap in magcopy/__init__.py covers this platform too (VECLIB_MAXIMUM_THREADS is Accelerate's
    # spelling), and mac.trim_memory hands a picker's freed pages back the way win.trim_memory
    # does. Measured: 55 MB and 6 threads idle; 67 MB right after a picker, 54 MB once trimmed.
    import ctypes
    import gc

    import magcopy                                 # first, exactly as the launcher does

    IDLE_MB = 160            # the Windows limit; measured ~55
    THREAD_LIMIT = 16        # measured 6
    IDLE_SHOWN_MB = 80       # what Activity Monitor shows once trimmed; measured ~54

    _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")

    class RUsage2(ctypes.Structure):               # struct rusage_info_v2
        _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups",
            "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint",
            "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
            "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
            "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten")]

    class TaskInfo(ctypes.Structure):              # struct proc_taskinfo
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "pti_virtual_size", "pti_resident_size", "pti_total_user", "pti_total_system",
            "pti_threads_user", "pti_threads_system")] + [(n, ctypes.c_int32) for n in (
            "pti_policy", "pti_faults", "pti_pageins", "pti_cow_faults", "pti_messages_sent",
            "pti_messages_received", "pti_syscalls_mach", "pti_syscalls_unix", "pti_csw",
            "pti_threadnum", "pti_numrunning", "pti_priority")]

    def footprint():
        """The Memory column in Activity Monitor: this process's physical footprint, in MB."""
        gc.collect()
        ru = RUsage2()
        _libc.proc_pid_rusage(os.getpid(), 2, ctypes.byref(ru))          # RUSAGE_INFO_V2
        return ru.ri_phys_footprint / 1048576.0

    def thread_count():
        ti = TaskInfo()
        _libc.proc_pidinfo(os.getpid(), 4, 0, ctypes.byref(ti), ctypes.sizeof(ti))   # TASKINFO
        return int(ti.pti_threadnum)

    ok = True

    def check(name, good, detail=""):
        global ok
        ok = ok and good
        print("%-58s %s %s" % (name, "ok  " if good else "FAIL", detail))

    check("BLAS is held to one thread before numpy loads",
          os.environ.get("OPENBLAS_NUM_THREADS") == "1"
          and os.environ.get("VECLIB_MAXIMUM_THREADS") == "1",
          "%r %r" % (os.environ.get("OPENBLAS_NUM_THREADS"), os.environ.get("VECLIB_MAXIMUM_THREADS")))

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
    idle, threads = footprint(), thread_count()
    check("idle memory is small", idle < IDLE_MB, "%.0f MB (limit %d)" % (idle, IDLE_MB))
    check("and it is not running a thread per core", threads < THREAD_LIMIT,
          "%d threads (limit %d)" % (threads, THREAD_LIMIT))

    # a screenshot picker is the biggest thing the app ever builds: a Retina still of the desktop
    sel = overlay.selector_for(root, app.theme, app.fonts, app.settings)
    root.after(500, sel._cancel)
    sel.run()
    del sel
    app._after_capture(False)
    right_after = footprint()
    pump(3.4)                                      # the trim is debounced 2.5 s
    trimmed = footprint()
    # Tighter than the Windows margin, because a picker costs less here: ~12 MB that stays put
    # without the trim, and with it the footprint ends at or just under where it started.
    check("a picker's memory is given back once it closes", trimmed < idle + 5,
          "%.0f MB idle, %.0f MB right after, %.0f MB once trimmed" % (idle, right_after, trimmed))
    check("so Activity Monitor shows it back down too", trimmed < IDLE_SHOWN_MB,
          "%.0f MB (limit %d)" % (trimmed, IDLE_SHOWN_MB))

    app.quit()
    print("\nMEMORY", "OK" if ok else "PROBLEM")
    sys.exit(0 if ok else 1)

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
