"""Run every smoke test in order and summarise."""
import os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["smoke_gifinfo.py", "smoke_defaults.py", "smoke_frame.py", "smoke_overlay.py",
         "smoke_frame_live.py", "smoke_live_overlay.py", "smoke_flow.py",
         "smoke_pacing.py", "smoke_instance.py", "smoke_theme_colours.py", "smoke_ratio.py", "smoke_crop.py", "smoke_crop_handles.py", "smoke_editor_size.py", "smoke_done.py", "smoke_ui.py", "smoke_editor.py",
         "smoke_pipeline.py", "smoke_realistic.py"]

# constructing App() on a machine with no settings file would register "start with Windows";
# the tests must not touch the real registry
ENV = dict(os.environ, MAGCOPY_NO_AUTOSTART="1",
           MAGCOPY_INSTANCE_NAME="MagCopy-suite-%d" % os.getpid())
rows = []
for t in TESTS:
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], env=ENV,
                       cwd=os.path.dirname(HERE), capture_output=True, text=True)
    rows.append((t, r.returncode, time.time() - t0))
    tail = [l for l in (r.stdout or "").strip().splitlines() if l.strip()][-1:] or [""]
    print("%-22s %-5s %6.1fs  %s" % (t, "PASS" if r.returncode == 0 else "FAIL",
                                     time.time() - t0, tail[0][:70]))
    if r.returncode != 0:
        print("--- stdout ---\n" + (r.stdout or "")[-1500:])
        print("--- stderr ---\n" + (r.stderr or "")[-1500:])
bad = [t for t, rc, _ in rows if rc != 0]
print("\n%d/%d passed in %.0fs" % (len(rows) - len(bad), len(rows), sum(d for _, _, d in rows)))
sys.exit(1 if bad else 0)
