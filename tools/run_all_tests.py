"""Run every smoke test in order and summarise."""
import os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
# smoke_permission.py and smoke_own_windows.py are macOS-only and no-op elsewhere: they cover the
# two ways a Mac build fails while looking like it works - a denied Screen Recording permission,
# which captures an empty desktop rather than raising, and our own overlays landing in a recording.
TESTS = ["smoke_platform_api.py", "smoke_gifinfo.py", "smoke_defaults.py", "smoke_memory.py", "smoke_permission.py", "smoke_own_windows.py", "smoke_escape_hatch.py", "smoke_hotkey_flow.py", "smoke_feedback.py", "smoke_dock.py", "smoke_update.py", "smoke_frame.py", "smoke_overlay.py",
         "smoke_frame_live.py", "smoke_live_overlay.py", "smoke_frozen.py", "smoke_drag.py", "smoke_escape.py", "smoke_rebind.py", "smoke_rebind_realkeys.py", "smoke_flow.py",
         "smoke_pacing.py", "smoke_instance.py", "smoke_theme_colours.py", "smoke_ratio.py", "smoke_crop.py", "smoke_crop_handles.py", "smoke_editor_size.py", "smoke_done.py", "smoke_saving.py", "smoke_ui.py", "smoke_editor.py",
         "smoke_pipeline.py", "smoke_realistic.py"]

# constructing App() on a machine with no settings file would register "start with Windows";
# the tests must not touch the real registry
ENV = dict(os.environ, MAGCOPY_NO_AUTOSTART="1",
           MAGCOPY_INSTANCE_NAME="MagCopy-suite-%d" % os.getpid())


def run(t):
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], env=ENV,
                       cwd=os.path.dirname(HERE), capture_output=True, text=True)
    return r, time.time() - t0


rows, retried = [], []
for t in TESTS:
    # Several of these sample the screen or need the keyboard, and a process that has just exited
    # can still have windows on screen for a moment. Without a beat between them the next test
    # measures the last one's leftovers.
    time.sleep(0.5)
    r, took = run(t)
    first = r
    if r.returncode != 0:
        # These run against the real desktop - real keystrokes, real screen samples - usually while
        # someone is using the machine. A failure gets one more go after a longer pause, and the
        # summary says so: a flake stays visible without turning the run red, and a real
        # regression still fails twice.
        time.sleep(2.0)
        r, took2 = run(t)
        took += took2
        if r.returncode == 0:
            retried.append(t)
    rows.append((t, r.returncode, took))
    tail = [l for l in (r.stdout or "").strip().splitlines() if l.strip()][-1:] or [""]
    mark = "PASS*" if t in retried else ("PASS" if r.returncode == 0 else "FAIL")
    print("%-22s %-5s %6.1fs  %s" % (t, mark, took, tail[0][:70]))
    if t in retried:
        print("--- stdout of the failed first run ---\n" + (first.stdout or "")[-1500:])
    elif r.returncode != 0:
        print("--- stdout ---\n" + (r.stdout or "")[-1500:])
        print("--- stderr ---\n" + (r.stderr or "")[-1500:])
bad = [t for t, rc, _ in rows if rc != 0]
print("\n%d/%d passed in %.0fs" % (len(rows) - len(bad), len(rows), sum(d for _, _, d in rows)))
if retried:
    print("* passed on a second run after failing the first: %s" % ", ".join(retried))
sys.exit(1 if bad else 0)
