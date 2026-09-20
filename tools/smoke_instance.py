"""A second launch must hand off to the first rather than starting a rival with no shortcuts."""
import os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Deliberately NOT importing already_running() here: it *acquires* the mutex as a side effect,
# so checking from this process would make the child believe a copy is already running.
env = dict(os.environ, MAGCOPY_NO_AUTOSTART="1",
           MAGCOPY_INSTANCE_NAME="MagCopy-test-%d" % os.getpid())
held = subprocess.run([sys.executable, "-c",
                       "import sys; sys.path.insert(0, r'%s');"
                       "from magcopy.tray import already_running; print(already_running())" % ROOT],
                      env=env, capture_output=True, text=True).stdout.strip()
print("another copy already running before we start:", held)

first = subprocess.Popen([sys.executable, os.path.join(ROOT, "magcopy.pyw"), "--hidden"], env=env)
time.sleep(6)
if first.poll() is not None:
    print("FAIL: the first instance exited immediately"); sys.exit(1)
print("first instance is running (pid %d)" % first.pid)

t0 = time.time()
second = subprocess.run([sys.executable, os.path.join(ROOT, "magcopy.pyw")], env=env, timeout=60)
took = time.time() - t0
print("second launch exited with %d after %.1fs" % (second.returncode, took))

ok = second.returncode == 0 and took < 30 and first.poll() is None
if not ok:
    print("FAIL: the second launch should exit cleanly and leave the first running")
first.terminate()
try:
    first.wait(10)
except Exception:
    first.kill()
print("SINGLE INSTANCE", "OK" if ok else "PROBLEM")
sys.exit(0 if ok else 1)
