# SPDX-License-Identifier: MPL-2.0
"""The generator behind tests/test_facade_contract.py must not undercount the contract.

Every case here is a REGRESSION. A short contract is worse than no contract: the contract test
still passes, so the split looks verified while an unpinned name goes missing from the facade
and every commit in every repository blocks on the AttributeError.

The governing rule is that the contract is derived from CALLERS ALONE. It must never be
filtered through what the facade currently exports, because that makes a dropped re-export
delete itself from the contract on the next regeneration -- the check would be structurally
incapable of detecting the one thing it exists for.
"""
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GEN = _ROOT / "tools" / "gen_facade_contract.py"
_spec = importlib.util.spec_from_file_location("gen_facade_contract", _GEN)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _tree(tmp_path, lib_body, callers):
    lib = tmp_path / "mylib.py"
    lib.write_text(lib_body, encoding="utf-8")
    paths = [lib]
    for name, text in callers.items():
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return lib, paths


def test_a_caller_whose_filename_ends_with_the_lib_filename_is_still_scanned(tmp_path):
    """`test_mylib.py`.endswith(`mylib.py`) is True.

    The first generator skipped callers with `path.endswith(LIB_BASENAME)`, which swallowed
    tests/test_reviewer_lib.py -- the single largest consumer of the facade. Only the facade
    file ITSELF may be skipped.
    """
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"test_mylib.py": "import mylib as rl\n\n\ndef t():\n    rl.alpha()\n"},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]


def test_names_the_facade_re_exports_are_part_of_the_contract(tmp_path):
    """A re-exported name is exactly what the split creates, and exactly what must be pinned.

    The second generator collected only FunctionDef/ClassDef/Assign as "defined", so the moment
    step 1 moved `log`/`short`/`tail` into reviewer_base and imported them back, regenerating
    DROPPED them. The contract shrank in lockstep with the hollowing-out it exists to detect.
    """
    lib, paths = _tree(
        tmp_path,
        "from other import beta\n\nCONST = 1\n\n\ndef alpha():\n    pass\n",
        {"caller.py": "import mylib as rl\n\n\ndef t():\n    rl.alpha()\n    rl.beta()\n"
                      "    return rl.CONST\n"},
    )
    assert gen.facade_names(lib, paths) == ["CONST", "alpha", "beta"]
    assert gen.unexported(lib, gen.facade_names(lib, paths)) == []


def test_a_name_the_facade_stopped_exporting_stays_in_the_contract(tmp_path):
    """THE point of the contract, and the defect that made a previous generator worthless.

    A generator that intersects caller names with the CURRENT export surface silently forgets
    `beta` the moment `beta` stops being exported -- so regenerating after a bad split step
    launders the regression away. The name is retained, and `unexported` names it.
    """
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": "import mylib as rl\n\n\ndef t():\n    rl.alpha()\n    rl.beta()\n"},
    )
    names = gen.facade_names(lib, paths)
    assert names == ["alpha", "beta"], "a dropped re-export must not vanish from the contract"
    assert gen.unexported(lib, names) == ["beta"]


def test_setattr_by_string_is_a_facade_dependency(tmp_path):
    """`monkeypatch.setattr(rl, "name", ...)` raises if the attribute is gone, so it binds the
    facade exactly as `rl.name` does -- and no attribute-access scan can see it.

    This fixture is the guard; live caller evidence is not. Four real names reached the facade
    only this way, three of them lost that reach when split step 7 retargeted their patch sites
    onto reviewer_backends, and `_sleep` is scheduled to follow at step 8. When it does, no real
    caller will exercise the rule and this test will be its only coverage -- which is the point
    of pinning a capability synthetically rather than through whatever the tree happens to do.
    """
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
        {"caller.py": 'import mylib as rl\n\n\ndef t(monkeypatch):\n'
                      '    monkeypatch.setattr(rl, "beta", None)\n    rl.alpha()\n'},
    )
    assert gen.facade_names(lib, paths) == ["alpha", "beta"]


def test_getattr_without_a_default_is_a_facade_dependency(tmp_path):
    """Two-argument `getattr` raises when the name is gone, so it binds like `rl.name`."""
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n\n\nBETA = 1\n",
        {"caller.py": 'import mylib as rl\n\n\ndef t():\n'
                      '    rl.alpha()\n    return getattr(rl, "BETA")\n'},
    )
    assert gen.facade_names(lib, paths) == ["BETA", "alpha"]


def test_getattr_over_a_loop_of_constant_names_is_a_facade_dependency(tmp_path):
    """The real shape at tests/test_brief_composition.py:189-193, which sweeps the prompt
    constants for concatenation-seam typos:

        for name in ("COMMIT_PROMPT", "_CAPSULE_UNIVERSAL", ...):
            text = getattr(rl, name)

    A constant-only rule sees `Name`, not `Constant`, and drops all of them. Four names --
    `_CAPSULE_UNIVERSAL`, `_CAPSULE_COMMIT_HEAD`, `_CAPSULE_COMMIT_REPEAT_HEAD`,
    `_CAPSULE_COMMIT_REPEAT_TAIL` -- reach the facade ONLY this way.
    """
    lib, paths = _tree(
        tmp_path,
        "GAMMA = 'g'\nDELTA = 'd'\n\n\ndef alpha():\n    pass\n",
        {"caller.py": 'import mylib as rl\n\n\ndef t():\n'
                      '    rl.alpha()\n'
                      '    for name in ("GAMMA", "DELTA"):\n'
                      '        print(getattr(rl, name))\n'},
    )
    assert gen.facade_names(lib, paths) == ["DELTA", "GAMMA", "alpha"]


def test_getattr_with_a_default_is_a_probe_not_a_dependency(tmp_path):
    """Three-argument `getattr` never raises, so it tolerates the name being absent."""
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": 'import mylib as rl\n\n\ndef t():\n'
                      '    rl.alpha()\n    return getattr(rl, "maybe_gone", None)\n'},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]


def test_an_unresolvable_dynamic_access_is_reported_rather_than_dropped(tmp_path):
    """The lesson that produced every earlier defect: silence is the dangerous outcome.

    A name computed at runtime cannot be pinned, but it must not vanish without a word --
    otherwise the contract quietly stops covering a real call site.
    """
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": "import mylib as rl\n\n\ndef t(which):\n"
                      "    rl.alpha()\n    return getattr(rl, which.upper())\n"},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]
    reported = gen.unresolved_dynamic(lib, paths)
    assert len(reported) == 1 and "caller.py:6" in reported[0], reported


def test_the_real_tree_has_no_unresolvable_dynamic_facade_access():
    assert gen.unresolved_dynamic(_ROOT / "reviewer_lib.py", gen.caller_paths(_ROOT)) == []


def test_names_inside_string_literals_are_not_mistaken_for_call_sites(tmp_path):
    """What makes retention safe: references are resolved through the AST, not by regex.

    A textual matcher reads `"mylib.py"` as a use of a name `py`, and reads fixture source
    embedded in a docstring as real call sites. Those false names would then be retained
    forever and fail the contract test. Same defect class already fixed in
    tools/check_split_layering.py, which matched names inside docstrings.
    """
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": 'import mylib as rl\n\n\ndef t():\n'
                      '    """Docs mentioning rl.ghost and the file mylib.py."""\n'
                      '    sample = "import mylib as rl; rl.phantom()"\n'
                      '    rl.alpha()\n    return sample\n'},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]


def test_inherent_module_attributes_are_not_contract_names(tmp_path):
    """`rl.__file__` is a property of being a module, not a name the facade re-exports."""
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": "import mylib as rl\n\n\ndef t():\n    rl.alpha()\n    return rl.__file__\n"},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]


def test_an_unrelated_local_named_rl_is_not_read_as_the_facade(tmp_path):
    """The alias is resolved from the file's OWN imports; a file that never imports the facade
    contributes nothing, however it happens to name its variables."""
    lib, paths = _tree(
        tmp_path,
        "def alpha():\n    pass\n",
        {"caller.py": "import mylib as rl\n\n\ndef t():\n    rl.alpha()\n",
         "other.py": "class Thing:\n    pass\n\n\ndef t():\n    rl = Thing()\n"
                     "    return rl.unrelated_attribute\n"},
    )
    assert gen.facade_names(lib, paths) == ["alpha"]


# --------------------------------------------------------------------------
# end-to-end against the real tree
# --------------------------------------------------------------------------
def test_every_contract_name_is_actually_exported_today():
    """The whole inventory, not a hand-picked sample: if any caller reaches a name the facade
    no longer carries, generation is supposed to say so."""
    names = gen.facade_names(_ROOT / "reviewer_lib.py", gen.caller_paths(_ROOT))
    assert gen.unexported(_ROOT / "reviewer_lib.py", names) == []


def test_the_checked_in_contract_is_not_stale():
    """Regeneration is required after every split step, and this is what enforces it rather
    than trusting it to be remembered. A stale contract pins the PREVIOUS step's surface."""
    current = (_ROOT / "tests" / "test_facade_contract.py").read_text(encoding="utf-8")
    names = gen.facade_names(_ROOT / "reviewer_lib.py", gen.caller_paths(_ROOT))
    assert current == gen.render(names), (
        "tests/test_facade_contract.py is stale -- run python3 tools/gen_facade_contract.py")


@pytest.mark.parametrize("name", [
    # dropped by the endswith defect (reached only from tests/test_reviewer_lib.py)
    "git_head", "brief_argv", "run_codex_brief", "session_attribution", "render_manifest",
    # dropped by the re-export defect (moved into reviewer_base by split step 1)
    "log", "short", "tail", "reviewer_home", "reviewer_state_home", "_sanitize",
    # NOTE: the "invisible to attribute scanning" group is now EMPTY, and that is finished, not
    # broken. It held `pi_argv`, `parse_pi_final_text` and `parse_pi_tool_flags` until step 7
    # retargeted their patch sites onto reviewer_backends, and `_sleep` until step 8 did the same
    # onto reviewer_stopcontinue. No caller reaches the facade by setattr any more, so no live row
    # can exist. The generator CAPABILITY those rows evidenced is pinned synthetically by
    # test_setattr_by_string_is_a_facade_dependency, which needs no live caller; the four names
    # are pinned as exports by the test below.
    # reached only through `getattr(rl, name)` over a loop of constants
    "_CAPSULE_UNIVERSAL", "_CAPSULE_COMMIT_HEAD", "_CAPSULE_COMMIT_REPEAT_HEAD",
    "_CAPSULE_COMMIT_REPEAT_TAIL",
])
def test_names_each_generator_defect_lost_are_in_the_contract(name):
    names = gen.facade_names(_ROOT / "reviewer_lib.py", gen.caller_paths(_ROOT))
    assert name in names


@pytest.mark.parametrize(
    "name", ["pi_argv", "parse_pi_final_text", "parse_pi_tool_flags", "_sleep"])
def test_a_name_no_caller_reaches_any_more_is_still_exported(name):
    """Why the contract legitimately SHRANK at split steps 7 and 8, and what replaced it.

    These left the contract because the `monkeypatch.setattr(rl, ...)` sites that were their only
    reach into the facade now patch the layer that owns them -- reviewer_backends for the first
    three, reviewer_stopcontinue for `_sleep` -- where the callers live. No caller asks the facade
    for them, so a caller-derived contract correctly stops listing them.

    That is also the exact shape of the regression this whole file exists to catch, so the
    distinction cannot rest on a commit message. The facade's obligation is to re-export every
    public name (LIB_SPLIT_PLAN.md 4) -- not merely the ones the test suite happens to touch,
    since commit_hook and any out-of-tree caller reach it too. So they are pinned here directly
    against the facade instead: dropping one from its `from reviewer_<layer> import` block fails
    this test rather than silently regenerating a shorter contract.
    """
    import reviewer_lib as rl
    assert hasattr(rl, name)


@pytest.mark.parametrize("name", ["resume_argv", "run_codex_resume"])
def test_a_name_a_security_test_forbids_is_never_pulled_into_the_contract(name):
    """`tests/test_session_isolation.py:83` asserts `not hasattr(rl, "resume_argv")` -- no
    helper may resume the globally-most-recent Codex session, since `--last` attaches to
    whatever ran last rather than to a named session.

    A first cut of the setattr rule matched ANY two-argument call taking (alias, string), so it
    read that existence PROBE as a dependency and demanded the facade export the exact two
    names the security test forbids. Probes are not dependencies.
    """
    names = gen.facade_names(_ROOT / "reviewer_lib.py", gen.caller_paths(_ROOT))
    assert name not in names
