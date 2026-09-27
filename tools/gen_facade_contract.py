#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Generate tests/test_facade_contract.py: every name callers reach through the facade.

`reviewer_lib` is being split into layered modules (LIB_SPLIT_PLAN.md) with reviewer_lib left
as a facade re-exporting everything. The contract test pins the names that must survive.

    python3 tools/gen_facade_contract.py

Run it after every split step. Exits non-zero, naming the names, if the facade has stopped
exporting something a caller still reaches for.

THE GOVERNING RULE: the contract is derived from CALLERS ALONE and is never filtered through
what the facade currently exports. Filtering makes a dropped re-export delete itself from the
contract on the next regeneration, so the check becomes structurally incapable of detecting the
one thing it exists for. Three defects have already produced a SHORT list here, and a short
list is worse than none -- the contract test still passes, so the split looks verified while an
unpinned name goes missing from a fail-closed commit gate. tests/test_facade_generator.py pins
each property below; see LIB_SPLIT_PLAN.md Section 4 for the history.

Three things a caller can do, all of which bind the facade:

  rl.name                              attribute access
  monkeypatch.setattr(rl, "name", x)   raises AttributeError if the name is gone, so it binds
                                       the facade just as hard, and no attribute scan sees it
  from reviewer_lib import name        direct import

References are resolved through the AST rather than by regex. That is what makes retention
safe: a textual matcher reads `"reviewer_lib.py"` as a use of a name `py`, and reads example
source inside a docstring as a real call site, and those phantom names would then be retained
forever and fail the contract test. Same defect class already fixed in
tools/check_split_layering.py, which matched names inside docstrings.
"""
import ast
import glob
import sys
from pathlib import Path

def _facade_aliases(tree, module):
    """Names this file binds the facade to -- `import reviewer_lib as rl` gives {"rl"}.

    Resolved per file from that file's OWN imports, so a module that never imports the facade
    contributes nothing however it happens to name its own locals.
    """
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _is_inherent(name):
    """`__file__`, `__doc__` and friends belong to every module; they are not re-exports."""
    return name.startswith("__") and name.endswith("__")


def _dynamic_access(node, aliases):
    """Classify `<f>(rl, "name", ...)` as a dependency, a probe, or not an access at all.

    The rule is whether the call RAISES when the name is absent. A raising accessor binds the
    facade exactly as `rl.name` does; a tolerant one does not.

        setattr / delattr          raise          -> dependency
        getattr(rl, x)             raises         -> dependency
        getattr(rl, x, default)    tolerant       -> probe
        hasattr(rl, x)             tolerant       -> probe

    Probes must stay out. tests/test_session_isolation.py:83 asserts
    `not hasattr(rl, "resume_argv")` and `not hasattr(rl, "run_codex_resume")`, keeping any
    session-less `--last` resume out of the library -- `--last` attaches to whichever Codex
    session ran most recently rather than to a named one. A first cut treated every
    `(alias, string)` call as a dependency and so demanded the facade export the exact two
    names that security test forbids.
    """
    if not isinstance(node, ast.Call) or len(node.args) < 2:
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if not (isinstance(node.args[0], ast.Name) and node.args[0].id in aliases):
        return None
    if name in ("setattr", "delattr"):
        return "requires"
    if name == "getattr":
        return "probe" if len(node.args) >= 3 else "requires"
    if name == "hasattr":
        return "probe"
    return None


def _const_strings(node):
    """The string constants of a literal tuple/list/set, or None if it is not all constants."""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    values = [e.value for e in node.elts
              if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return values if len(values) == len(node.elts) and values else None


def _loop_resolved(tree):
    """Map each dynamic-access Call to the names its loop variable can take.

    Covers `for name in ("A", "B"): getattr(rl, name)` -- the shape at
    tests/test_brief_composition.py:189-193, which sweeps the prompt constants for
    concatenation-seam typos and is the ONLY route by which the four `_CAPSULE_*` names reach
    the facade. A constant-only rule sees `Name`, not `Constant`, and drops every one.
    """
    resolved = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        values = _const_strings(node.iter)
        if not values:
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call) and len(inner.args) >= 2
                    and isinstance(inner.args[1], ast.Name)
                    and inner.args[1].id == node.target.id):
                resolved.setdefault(inner, set()).update(values)
    return resolved


def _scan(lib_path, paths):
    """(names reached on the facade, unresolvable dynamic accesses)."""
    lib_path = Path(lib_path).resolve()
    module = lib_path.stem
    found = set()
    unresolved = []
    for path in paths:
        path = Path(path)
        if path.resolve() == lib_path:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                found.update(a.name for a in node.names if a.name != "*")
        aliases = _facade_aliases(tree, module)
        if not aliases:
            continue
        by_loop = _loop_resolved(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                found.add(node.attr)
                continue
            kind = _dynamic_access(node, aliases)
            if kind != "requires":
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            elif node in by_loop:
                found.update(by_loop[node])
            else:
                # Never drop one silently -- that is how every earlier defect hid.
                unresolved.append("{}:{}".format(path.name, node.lineno))
    return {n for n in found if not _is_inherent(n)}, unresolved


def caller_names(lib_path, paths):
    """Every name reached on the facade, from callers alone. No export filtering."""
    return _scan(lib_path, paths)[0]


def unresolved_dynamic(lib_path, paths):
    """`file:line` for each runtime-computed facade name the contract cannot pin."""
    return _scan(lib_path, paths)[1]


def exported_names(lib_path):
    """Every top-level name the facade binds -- defined here OR re-exported.

    Import bindings count: extraction turns `def log` into `from reviewer_base import log`, and
    a check that only saw FunctionDef would call the name missing at exactly the step that
    moves it.
    """
    tree = ast.parse(Path(lib_path).read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_assigned(target))
        elif isinstance(node, ast.AnnAssign):
            names.update(_assigned(node.target))
    return names


def _assigned(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out = set()
        for elt in target.elts:
            out.update(_assigned(elt))
        return out
    return set()


def unexported(lib_path, names):
    """Contract names the facade no longer carries. Non-empty means a broken split step."""
    exported = exported_names(lib_path)
    return sorted(n for n in names if n not in exported)


def caller_paths(root):
    """Every file that might reach the facade: hooks, toggles and the whole suite."""
    root = Path(root)
    return [Path(p) for p in sorted(glob.glob(str(root / "*.py"))
                                    + glob.glob(str(root / "tests" / "*.py")))]


def facade_names(lib_path, paths):
    return sorted(caller_names(lib_path, paths))


_TEMPLATE = '''# SPDX-License-Identifier: MPL-2.0
"""The facade contract for the reviewer_lib split (LIB_SPLIT_PLAN.md).

`reviewer_lib` is being split into eight layered modules, with reviewer_lib itself left as a
facade that re-exports everything. Four hooks, five toggles and 20 test files all do
`import reviewer_lib as rl` and reach names through it.

This pins EVERY such name, so a re-export dropped during the split fails HERE, in one obvious
place naming the missing names, rather than as an AttributeError inside a fail-closed commit
gate whose symptom is "every commit in every repository is blocked". The check runs when
pytest runs it; it is not an import-time assertion.

GENERATED by tools/gen_facade_contract.py from the real call sites. Regenerate, do not
hand-edit, when a name is legitimately added or removed -- tests/test_facade_generator.py
fails if this file goes stale, and pins the generator against undercounting.
"""
import pytest

import reviewer_lib as rl

pytestmark = pytest.mark.smoke  # install-time subset (tests/test_smoke_subset.py)

FACADE_NAMES = (
{names}
)


def test_the_facade_exports_every_name_its_callers_reach_for():
    missing = [n for n in FACADE_NAMES if not hasattr(rl, n)]
    assert not missing, (
        "reviewer_lib no longer exports {{}} name(s) that hooks, toggles or tests use: {{}}"
        .format(len(missing), ", ".join(sorted(missing))))


def test_the_contract_list_is_not_silently_emptied():
    # A regeneration bug that produced a short or empty tuple would make the test above
    # vacuous while still passing. tests/test_facade_generator.py pins the generator itself.
    assert len(FACADE_NAMES) >= {floor}
'''


def render(names):
    rows = ["    " + " ".join('"{}",'.format(n) for n in names[i:i + 3])
            for i in range(0, len(names), 3)]
    return _TEMPLATE.format(names="\n".join(rows), floor=len(names))


def main():
    root = Path(__file__).resolve().parent.parent
    lib = root / "reviewer_lib.py"
    paths = caller_paths(root)
    names, unresolved = _scan(lib, paths)
    names = sorted(names)
    out = root / "tests" / "test_facade_contract.py"
    out.write_text(render(names), encoding="utf-8")
    sys.stdout.write("{} names -> {}\n".format(len(names), out))

    status = 0
    missing = unexported(lib, names)
    if missing:
        sys.stderr.write(
            "FAIL: {} name(s) are reached by callers but no longer exported by {}:\n  {}\n"
            .format(len(missing), lib.name, "\n  ".join(missing)))
        status = 1
    if unresolved:
        sys.stderr.write(
            "FAIL: {} facade access(es) name an attribute computed at runtime, so the contract "
            "cannot pin them:\n  {}\n".format(len(unresolved), "\n  ".join(unresolved)))
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
