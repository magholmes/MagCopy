"""Every plat.* call has to exist, with a matching signature, on the platform running it.

A two-platform layer fails in a particular way: mac.py grows an argument, the call site starts
passing it, and the Windows build raises TypeError somewhere the exception is caught and turned
into a polite message. That is exactly how "recording failed" and a region picker that returned
nothing both shipped at once - two signatures out of step, and no test that looked at the seam
rather than at behaviour.

This reads the call sites rather than running them, so it catches a mismatch on a code path
nobody exercised on this machine.
"""
import ast
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from magcopy import plat

SKIP = {"mac.py", "plat.py", "win.py", "tray_mac.py", "tray_win.py"}
problems = []
checked = 0

for fn in sorted(os.listdir(os.path.join(ROOT, "magcopy"))):
    if not fn.endswith(".py") or fn in SKIP:
        continue
    tree = ast.parse(open(os.path.join(ROOT, "magcopy", fn), encoding="utf-8").read())
    for node in ast.walk(tree):
        f = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name) and f.value.id == "plat"):
            continue
        checked += 1
        obj = getattr(plat, f.attr, None)
        if obj is None:
            problems.append("magcopy/%s:%d  plat.%s is missing here" % (fn, node.lineno, f.attr))
            continue
        try:
            params = inspect.signature(obj).parameters
        except (TypeError, ValueError):
            continue                      # a builtin or C callable: nothing to compare
        star_kw = any(p.kind == p.VAR_KEYWORD for p in params.values())
        star_pos = any(p.kind == p.VAR_POSITIONAL for p in params.values())
        for k in [kw.arg for kw in node.keywords if kw.arg]:
            if k not in params and not star_kw:
                problems.append("magcopy/%s:%d  plat.%s does not take %r here"
                                % (fn, node.lineno, f.attr, k))
        takes = sum(1 for p in params.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
        if not star_pos and len(node.args) > takes:
            problems.append("magcopy/%s:%d  plat.%s given %d positional args, takes %d"
                            % (fn, node.lineno, f.attr, len(node.args), takes))

print("%d plat.* call sites checked on %s" % (checked, "macOS" if plat.IS_MAC else "Windows"))
for line in problems:
    print("  FAIL", line)
print("\nPLATFORM API", "OK" if not problems else "PROBLEM")
sys.exit(1 if problems else 0)
