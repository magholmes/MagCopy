"""Every plat.* call has to exist, with a matching signature, on the platform running it.

A two-platform layer fails in a particular way: mac.py grows an argument, the call site starts
passing it, and the Windows build raises TypeError somewhere the exception is caught and turned
into a polite message. That is exactly how "recording failed" and a region picker that returned
nothing both shipped at once - two signatures out of step, and no test that looked at the seam
rather than at behaviour.

This reads the call sites rather than running them, so it catches a mismatch on a code path
nobody exercised on this machine.

It then does the same against the platform that is *not* running, read from its source: win.py
cannot even be imported on a Mac, and mac.py needs PyObjC. That second half is the one that
matters when only one machine is to hand - an argument added on one side is caught before the
other side ever runs it.
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


def call_sites():
    """(file, call node) for every plat.something(...) in the shared modules."""
    for fn in sorted(os.listdir(os.path.join(ROOT, "magcopy"))):
        if not fn.endswith(".py") or fn in SKIP:
            continue
        tree = ast.parse(open(os.path.join(ROOT, "magcopy", fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            f = getattr(node, "func", None)
            if (isinstance(node, ast.Call) and isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name) and f.value.id == "plat"):
                yield fn, node


for fn, node in call_sites():
    f = node.func
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


# ----------------------------------------------------------------------------- the other one
def _shape(args, drop_self=False):
    """What a call may pass, from an ast.arguments: (positional count, keyword names, *a, **kw)."""
    pos = list(args.posonlyargs) + list(args.args)
    if drop_self and pos:
        pos = pos[1:]
    names = {a.arg for a in args.args} | {a.arg for a in args.kwonlyargs}
    return len(pos), names, args.vararg is not None, args.kwarg is not None


def _defs(body, into, drop=()):
    """name -> shape (None when it exists but its shape is unknown), from a module's top level.

    Walks into if/try blocks, since both platform modules define things conditionally.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            into[node.name] = _shape(node.args)
        elif isinstance(node, ast.ClassDef):
            init = next((n for n in node.body if isinstance(n, ast.FunctionDef)
                         and n.name == "__init__"), None)
            into[node.name] = _shape(init.args, drop_self=True) if init else (0, set(), False, False)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        into.setdefault(n.id, None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name != "*":
                    into.setdefault((a.asname or a.name).split(".")[0], None)
        elif isinstance(node, ast.If):
            _defs(node.body, into)
            _defs(node.orelse, into)
        elif isinstance(node, ast.Try):
            _defs(node.body, into)
            for h in node.handlers:
                _defs(h.body, into)
            _defs(node.orelse, into)
    return into


def _read(name):
    return ast.parse(open(os.path.join(ROOT, "magcopy", name), encoding="utf-8").read())


def other_platform_api(mac):
    """What plat offers on the platform that is not running: its module plus plat.py's own.

    plat.py is evaluated for the three kinds of block it has - `if IS_MAC:`/else around the star
    import, `if not IS_MAC:` for Windows-only additions, and `if not hasattr(impl, "x"):` for a
    stub that exists only where the module has no real one. Anything else is taken as applying,
    which can only let a problem through, never invent one.
    """
    impl = _defs(_read("mac.py" if mac else "win.py").body, {})
    api = dict(impl)
    for node in _read("plat.py").body:
        if not isinstance(node, ast.If):
            _defs([node], api)
            continue
        t = node.test
        src = ast.unparse(t)
        if src == "IS_MAC":
            continue                                  # the star-import switch itself
        if src == "not IS_MAC":
            if not mac:
                own = _defs(node.body, {})
                api.update(own)                       # these replace the module's own versions
            continue
        if (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
                and isinstance(t.operand, ast.Call) and getattr(t.operand.func, "id", "") == "hasattr"
                and len(t.operand.args) == 2 and isinstance(t.operand.args[1], ast.Constant)):
            if t.operand.args[1].value not in impl:
                _defs(node.body, api)
            continue
        _defs(node.body, api)
        _defs(node.orelse, api)
    return api


other = "Windows" if plat.IS_MAC else "macOS"
api = other_platform_api(mac=not plat.IS_MAC)
checked_other = 0
for fn, node in call_sites():
    name = node.func.attr
    checked_other += 1
    where = "magcopy/%s:%d" % (fn, node.lineno)
    if name not in api:
        problems.append("%s  plat.%s does not exist on %s" % (where, name, other))
        continue
    shape = api[name]
    if shape is None:
        continue
    takes, names, star_pos, star_kw = shape
    for k in [kw.arg for kw in node.keywords if kw.arg]:
        if k not in names and not star_kw:
            problems.append("%s  plat.%s does not take %r on %s" % (where, name, k, other))
    if not star_pos and len(node.args) > takes:
        problems.append("%s  plat.%s given %d positional args, takes %d on %s"
                        % (where, name, len(node.args), takes, other))

print("%d plat.* call sites checked against %s, from its source" % (checked_other, other))
for line in problems:
    print("  FAIL", line)
print("\nPLATFORM API", "OK" if not problems else "PROBLEM")
sys.exit(1 if problems else 0)
