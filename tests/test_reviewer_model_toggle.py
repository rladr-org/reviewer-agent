# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "reviewer_model_toggle.py")


def run_toggle(args, home, env_extra=None):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env.pop("REVIEWER_CODEX_MODEL", None)
    env.pop("REVIEWER_CODEX_EFFORT", None)
    env.pop("REVIEWER_COMMIT_TIMEOUT_SECS", None)  # ambient value must not leak into tests
    env.pop("REVIEWER_CONFIG_FILE", None)
    env.pop("REVIEWER_PI_MODEL", None)   # highest-precedence pi source; same leak risk
    env.pop("REVIEWER_PI_EFFORT", None)  # ...and its effort twin, which outranks the default
    env.pop("REVIEWER_BACKEND", None)    # a shell-level backend must not decide these tests
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, TOGGLE] + args, capture_output=True, text=True, env=env
    )


def _effective(home, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    return rl.codex_model()


def test_set_persists_model_and_echoes(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(["gpt-5.5-codex"], home)
    assert p.returncode == 0, p.stderr
    assert "gpt-5.5-codex" in p.stdout
    assert _effective(home, monkeypatch) == "gpt-5.5-codex"


def test_show_never_attributes_the_model_to_a_skipped_env(tmp_path):
    """Same defect class as the timeout source, one function away.

    `codex_model()` re-validates every candidate and SKIPS an invalid one, so an unusable
    REVIEWER_CODEX_MODEL falls through to the state file -- but the display called any non-empty
    env var the source. Reported that way, an operator would unset an env var that was already
    being ignored and wonder why the model did not change.
    """
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle([], home, {"REVIEWER_CODEX_MODEL": 'bad"name'})
    assert p.returncode == 0
    assert "m1" in p.stdout, p.stdout
    assert "REVIEWER_CODEX_MODEL" not in p.stdout, p.stdout


def test_show_reports_the_env_model_when_it_is_valid(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle([], home, {"REVIEWER_CODEX_MODEL": "env-model"})
    assert p.returncode == 0
    assert "env-model" in p.stdout
    assert "REVIEWER_CODEX_MODEL" in p.stdout


def test_show_reports_override_and_default(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle([], home)
    assert p.returncode == 0
    assert "m1" in p.stdout
    run_toggle(["clear"], home)
    p2 = run_toggle([], home)
    assert p2.returncode == 0
    assert "default" in p2.stdout.lower()


def test_clear_removes_override(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle(["clear"], home)
    assert p.returncode == 0
    assert _effective(home, monkeypatch) is None


def test_invalid_model_name_rejected(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(['bad"name'], home)
    assert p.returncode == 2
    assert "ERROR" in (p.stdout + p.stderr)
    assert _effective(home, monkeypatch) is None


def test_extra_arguments_rejected(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(["two", "tokens"], home)
    assert p.returncode == 2
    assert _effective(home, monkeypatch) is None


# --------------------------------------------------------------------------
# Optional timeout parameter. A 5700-line staged diff timed out the commit audit at 600s;
# the gate is fail-CLOSED, so a timeout blocks the commit. The default rises to 900s and the
# operator can raise it further per machine without a code change.
# --------------------------------------------------------------------------
def _effective_timeout(home, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.delenv("REVIEWER_COMMIT_TIMEOUT_SECS", raising=False)
    return rl.commit_review_timeout()


def test_model_and_timeout_can_be_set_together(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(["gpt-5.5-codex", "1800"], home)
    assert p.returncode == 0, p.stderr
    assert "1800" in p.stdout
    assert _effective(home, monkeypatch) == "gpt-5.5-codex"
    assert _effective_timeout(home, monkeypatch) == 1800


def test_timeout_subcommand_leaves_the_model_alone(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle(["timeout", "1200"], home)
    assert p.returncode == 0, p.stderr
    assert _effective(home, monkeypatch) == "m1"
    assert _effective_timeout(home, monkeypatch) == 1200


def test_timeout_clear_reverts_to_the_default(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["timeout", "1200"], home)
    p = run_toggle(["timeout", "clear"], home)
    assert p.returncode == 0, p.stderr
    assert _effective_timeout(home, monkeypatch) == 900


def test_model_clear_does_not_touch_the_timeout(tmp_path, monkeypatch):
    # `clear` has always meant "clear the model override"; it must keep that meaning.
    home = str(tmp_path / "rh")
    run_toggle(["m1", "1500"], home)
    p = run_toggle(["clear"], home)
    assert p.returncode == 0
    assert _effective(home, monkeypatch) is None
    assert _effective_timeout(home, monkeypatch) == 1500


# --------------------------------------------------------------------------
# Optional reasoning-effort parameter. The audit inherited whatever the global codex config
# said, so buying the reviewer more thinking meant slowing the operator's own interactive
# codex too. `effort` is a subcommand rather than a third positional because
# `<model> <seconds>` already consumes both slots and a bare `/reviewerModel max` would be
# ambiguous against a model name of the same shape.
# --------------------------------------------------------------------------
def _effective_effort(home, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)
    return rl.codex_effort()


def test_effort_subcommand_persists_and_echoes(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(["effort", "max"], home)
    assert p.returncode == 0, p.stderr
    assert "max" in p.stdout
    assert _effective_effort(home, monkeypatch) == "max"


def test_effort_subcommand_leaves_the_model_and_timeout_alone(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["m1", "1500"], home)
    p = run_toggle(["effort", "xhigh"], home)
    assert p.returncode == 0, p.stderr
    assert _effective(home, monkeypatch) == "m1"
    assert _effective_timeout(home, monkeypatch) == 1500
    assert _effective_effort(home, monkeypatch) == "xhigh"


def test_effort_clear_reverts_to_inheriting_the_codex_default(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["effort", "max"], home)
    p = run_toggle(["effort", "clear"], home)
    assert p.returncode == 0, p.stderr
    assert _effective_effort(home, monkeypatch) is None


def test_an_invalid_level_is_rejected_and_NOTHING_is_written(tmp_path, monkeypatch):
    """A well-formed typo is the dangerous case: codex returns HTTP 400 on an
    unrecognised level, and the commit gate is fail-CLOSED, so accepting one here
    would block every commit until someone traced it back to this file."""
    home = str(tmp_path / "rh")
    p = run_toggle(["effort", "maximum"], home)
    assert p.returncode == 2
    assert "ERROR" in (p.stdout + p.stderr)
    assert _effective_effort(home, monkeypatch) is None


def test_the_rejection_message_lists_the_levels(tmp_path):
    """An operator who guessed wrong should not have to read the source to find
    the real name."""
    p = run_toggle(["effort", "maximum"], str(tmp_path / "rh"))
    for level in ("low", "high", "xhigh", "max"):
        assert level in (p.stdout + p.stderr)


def test_effort_with_no_value_is_rejected(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    p = run_toggle(["effort"], home)
    assert p.returncode == 2
    assert _effective_effort(home, monkeypatch) is None


def test_an_invalid_level_does_not_apply_a_model_given_alongside_it(tmp_path, monkeypatch):
    """Validate EVERYTHING before writing ANYTHING -- the rule the model/timeout
    pair already follows. `effort` takes the whole command, so a bad level must
    leave the previous model in place rather than half-applying."""
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    p = run_toggle(["effort", 'max" injected=evil'], home)
    assert p.returncode == 2
    assert _effective(home, monkeypatch) == "m1"
    assert _effective_effort(home, monkeypatch) is None


def test_model_clear_does_not_touch_the_effort(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    run_toggle(["m1"], home)
    run_toggle(["effort", "high"], home)
    p = run_toggle(["clear"], home)
    assert p.returncode == 0
    assert _effective(home, monkeypatch) is None
    assert _effective_effort(home, monkeypatch) == "high"


def test_show_reports_the_effort_and_its_source(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["effort", "max"], home)
    p = run_toggle([], home)
    assert p.returncode == 0, p.stderr
    assert "max" in p.stdout


def test_show_says_inherited_when_no_effort_override_is_set(tmp_path):
    p = run_toggle([], str(tmp_path / "rh"))
    assert p.returncode == 0, p.stderr
    assert "effort" in p.stdout.lower()


def test_show_never_attributes_the_effort_to_a_skipped_env(tmp_path):
    """Same defect class the model and timeout displays each shipped once:
    resolution SKIPS an invalid env var, so calling it the source would send an
    operator to unset a variable that was already being ignored."""
    home = str(tmp_path / "rh")
    run_toggle(["effort", "high"], home)
    p = run_toggle([], home, env_extra={"REVIEWER_CODEX_EFFORT": "banana"})
    assert p.returncode == 0, p.stderr
    assert "high" in p.stdout
    assert "REVIEWER_CODEX_EFFORT" not in p.stdout


def test_the_shipped_hook_timeout_exceeds_the_review_budget():
    """REGRESSION. `hooks.json` and `DEFAULT_COMMIT_TIMEOUT` live in different files with no link
    between them, and they drifted: the manifest carried 660 (the old 600s budget + 10%) while the
    budget was raised to 900 nineteen hours later, so Claude Code would cancel the hook 240s before
    the reviewer's own timeout could produce a verdict.

    That is not a lost message, it is a FAIL-OPEN. Per the Claude Code hook docs, exit 2 blocks a
    tool call and "any other exit code is a non-blocking error" -- a cancelled hook is neither, so
    the commit PROCEEDS unreviewed. The manifest must always outlast the review it is waiting on.

    Scope is deliberately the SHIPPED MANIFEST and nothing else. Two different invariants
    were briefly conflated here: this repo-content one, and the deployment one (the
    operator's live `~/.claude/settings.json` wiring must also outlast the budget). The
    second cannot be asserted from a repo unit test without reading personal config from
    outside the repository -- a hostile settings.json then fails a suite whose content is
    perfectly correct. It is checked where it belongs, next to the deployment, by
    `~/.claude/hooks/test_bash_gate.py::test_the_deadlines_are_ordered_innermost_first`.

    The live install carries no `hooks/` directory at all (it is wired from settings.json),
    so this skips rather than errors there.
    """
    import json

    manifest_path = _BASE / "hooks" / "hooks.json"
    if not manifest_path.exists():
        pytest.skip("no plugin manifest in this tree (live install is wired from "
                    "settings.json; test_bash_gate.py covers that side)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    timeouts = [h.get("timeout") for entry in manifest["hooks"]["PreToolUse"]
                if entry.get("matcher") == "Bash" for h in entry["hooks"]]
    assert timeouts, "no PreToolUse/Bash hook in the manifest"
    for t in timeouts:
        assert t is not None, "an unset timeout defaults to 600s, below the review budget"
        assert t > rl.DEFAULT_COMMIT_TIMEOUT, (
            "hooks.json timeout {}s must exceed the {}s review budget, or Claude Code "
            "cancels the gate mid-review and the commit proceeds unreviewed".format(
                t, rl.DEFAULT_COMMIT_TIMEOUT))


def test_the_manifest_check_ignores_the_operator_s_personal_hook_config(tmp_path, monkeypatch):
    """REGRESSION, reproducing the reviewer's own counter-example.

    The test above briefly also asserted on `_hook_timeout_ceiling()`, which PREFERS
    `Path.home()/.claude/settings.json` over the repo manifest. That made a repo test
    depend on unrelated personal config: pointing HOME at a `.claude/settings.json` whose
    gate hook carries a 1-second timeout failed the suite while the staged repo content
    was untouched. A repo suite must not be breakable from outside the repo."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 /x/commit_hook.py", "timeout": 1}]}]}}),
        encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    assert Path.home() == home, "HOME monkeypatch did not take effect"
    test_the_shipped_hook_timeout_exceeds_the_review_budget()


def test_a_timeout_above_the_shipped_hook_ceiling_is_refused(tmp_path):
    """The knob has to be honest. Raising the review budget past the hook's own timeout does
    nothing -- Claude Code cancels the process first -- and worse, a cancelled hook is a
    non-blocking error, so the commit it was gating proceeds. Silently accepting the value would
    leave the operator believing in a budget that converts the gate into a no-op."""
    home = str(tmp_path / "rh")
    p = run_toggle(["timeout", "100000"], home)
    assert p.returncode != 0
    assert "hook" in (p.stderr or "").lower()
    assert rl.commit_review_timeout_detail  # value was NOT persisted
    monkey_home = Path(home)
    assert not any(monkey_home.rglob("*commit_timeout*")), "the rejected value was written anyway"


def test_the_ceiling_is_found_in_the_shipped_manifest_without_any_user_settings(
    tmp_path, monkeypatch
):
    """CAUGHT BY CI, 2026-08-22, on the first run against the public repository.

    `test_a_timeout_above_the_shipped_hook_ceiling_is_refused` passed on the author's machine and
    failed on every CI platform, which is the signature of a test resting on personal state: the
    ceiling was being found in `~/.claude/settings.json`, whose legacy entry spells the hook as a
    single command STRING. The shipped manifest spells it in EXEC form -- `"command": "python3"`
    with the script in `args` -- so the resolver, which searched only `command`, found nothing
    there and returned "no ceiling".

    That is a product defect, not a test defect. On any plugin install (no legacy settings entry)
    the ceiling check silently did nothing, so `/reviewerModel timeout 100000` was accepted --
    and a budget above the hook's own timeout means Claude Code cancels the gate, which is a
    NON-BLOCKING error, so the commit it was gating proceeds UNREVIEWED. The guard against
    turning the fail-closed gate fail-open was itself inoperative.

    This test pins the manifest path specifically, with HOME pointed at an empty directory so no
    user settings can rescue it.
    """
    fake_home = tmp_path / "empty-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    state = str(tmp_path / "rh")

    p = run_toggle(["timeout", "100000"], state, {"HOME": str(fake_home)})

    assert p.returncode != 0, (
        "with no user settings the shipped manifest must still supply a ceiling:\n"
        + p.stdout + p.stderr)
    combined = (p.stdout + p.stderr).lower()
    assert "hook" in combined
    assert "3600" in combined, "the refusal should quote the manifest's own hook timeout"


def test_the_ceiling_reads_the_exec_form_the_manifest_actually_uses(tmp_path, monkeypatch):
    """The resolver must look where the script name IS, not where it used to be.

    Both spellings are real: `hooks.json` ships exec form, and an older standalone install wires
    a single command string in settings.json. Reading only one of them is how this went unnoticed
    -- the author had the other."""
    import reviewer_model_toggle as t

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3",
             "args": ["/somewhere/commit_hook.py"], "timeout": 1234}]}]}}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(t.Path, "home", classmethod(lambda _cls: fake_home))

    ceiling, where = t._hook_timeout_ceiling()

    assert ceiling == 1234, "the exec-form hook was not recognised at all"
    assert "settings.json" in where


def test_timeout_below_the_floor_is_rejected(tmp_path, monkeypatch):
    # A too-small value would turn the fail-closed gate into "every commit blocked".
    home = str(tmp_path / "rh")
    p = run_toggle(["timeout", "30"], home)
    assert p.returncode == 2
    assert "ERROR" in (p.stdout + p.stderr)
    assert _effective_timeout(home, monkeypatch) == 900


def test_non_integer_timeout_is_rejected_without_applying_the_model(tmp_path, monkeypatch):
    # Both arguments are validated BEFORE either is written -- a rejected command must not
    # leave the model changed and the timeout not, which would be worse than doing nothing.
    home = str(tmp_path / "rh")
    p = run_toggle(["m1", "not-a-number"], home)
    assert p.returncode == 2
    assert _effective(home, monkeypatch) is None
    assert _effective_timeout(home, monkeypatch) == 900


def test_show_reports_the_effective_timeout(tmp_path):
    home = str(tmp_path / "rh")
    p = run_toggle([], home)
    assert p.returncode == 0
    assert "900" in p.stdout
    run_toggle(["timeout", "1500"], home)
    p2 = run_toggle([], home)
    assert "1500" in p2.stdout


def test_show_reports_the_env_as_the_source_when_the_env_is_valid(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["timeout", "1500"], home)
    p = run_toggle([], home, {"REVIEWER_COMMIT_TIMEOUT_SECS": "2400"})
    assert p.returncode == 0
    assert "2400" in p.stdout
    assert "REVIEWER_COMMIT_TIMEOUT_SECS" in p.stdout


def test_show_never_attributes_the_value_to_a_skipped_env(tmp_path):
    """The displayed SOURCE must obey the same validity rules as the resolved value.

    `commit_review_timeout()` deliberately SKIPS an invalid env value and falls through, so
    reporting "from env" merely because the variable is set describes a resolution that did not
    happen -- and the operator would go on to edit the wrong place.
    """
    home = str(tmp_path / "rh")
    run_toggle(["timeout", "1500"], home)
    p = run_toggle([], home, {"REVIEWER_COMMIT_TIMEOUT_SECS": "abc"})
    assert p.returncode == 0
    assert "1500" in p.stdout, p.stdout
    assert "REVIEWER_COMMIT_TIMEOUT_SECS" not in p.stdout, p.stdout
    assert "commit_timeout" in p.stdout, p.stdout  # the state file it really came from


def test_show_says_default_when_an_invalid_env_is_the_only_setting(tmp_path):
    home = str(tmp_path / "rh")
    p = run_toggle([], home, {"REVIEWER_COMMIT_TIMEOUT_SECS": "-5"})
    assert p.returncode == 0
    assert "900" in p.stdout
    assert "default" in p.stdout
    assert "REVIEWER_COMMIT_TIMEOUT_SECS" not in p.stdout


# --------------------------------------------------------------------------
# Backend awareness. /reviewerModel wrote and read the CODEX slot unconditionally, so on a pi
# project it reported settings that were not in force and accepted a model nothing would read.
# Plan: docs/plans/reviewer_model_pi.md
# --------------------------------------------------------------------------
PI = {"REVIEWER_BACKEND": "pi"}


def _slot(home, name):
    """Raw contents of a state slot, or None. Read from disk rather than through the resolver,
    so a test asserting WHICH slot was written cannot be satisfied by precedence."""
    f = Path(home) / "state" / name
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def test_setting_a_model_on_a_pi_project_writes_the_pi_slot_not_the_codex_slot(tmp_path):
    home = str(tmp_path / "rh")
    p = run_toggle(["glm-5.2"], home, PI)
    assert p.returncode == 0, p.stderr
    assert _slot(home, "pi_model") == "glm-5.2"
    assert _slot(home, "codex_model") is None, "wrote the codex slot on a pi project"


def test_setting_a_model_on_a_codex_project_still_writes_the_codex_slot(tmp_path):
    """REGRESSION guard for the branch: the codex path must not move."""
    home = str(tmp_path / "rh")
    p = run_toggle(["gpt-5.5-codex"], home, {"REVIEWER_BACKEND": "codex"})
    assert p.returncode == 0, p.stderr
    assert _slot(home, "codex_model") == "gpt-5.5-codex"
    assert _slot(home, "pi_model") is None


def test_show_on_a_pi_project_reports_the_pi_model_and_names_the_backend(tmp_path):
    """The original bug was not that pi was unsupported -- it was that `/reviewerModel` with no
    arguments printed the CODEX model on a pi project, i.e. a setting with no effect on the
    reviewer that would actually run."""
    home = str(tmp_path / "rh")
    run_toggle(["gpt-5.5-codex"], home, {"REVIEWER_BACKEND": "codex"})
    run_toggle(["glm-5.2"], home, PI)
    # Written directly, so this test cannot be satisfied by a broken setter that put "glm-5.2"
    # into the codex slot -- which is exactly how it passed vacuously against the old code.
    rl.set_backend_model  # noqa: B018  (facade presence is asserted elsewhere)
    p = run_toggle([], home, PI)
    assert p.returncode == 0, p.stderr
    assert _slot(home, "pi_model") == "glm-5.2", "setter did not write the pi slot"
    assert _slot(home, "codex_model") == "gpt-5.5-codex", "setter clobbered the codex slot"
    assert "glm-5.2" in p.stdout
    assert "gpt-5.5-codex" not in p.stdout, "reported the codex model on a pi project"
    # "backend pi", not a bare "pi": the substring `pi` occurs in ordinary words and in paths,
    # so a loose check passes against output that names no backend at all.
    assert "backend pi" in p.stdout, p.stdout


def test_clear_on_a_pi_project_clears_only_the_pi_slot(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["gpt-5.5-codex"], home, {"REVIEWER_BACKEND": "codex"})
    run_toggle(["glm-5.2"], home, PI)
    p = run_toggle(["clear"], home, PI)
    assert p.returncode == 0, p.stderr
    assert _slot(home, "pi_model") is None
    assert _slot(home, "codex_model") == "gpt-5.5-codex", "clearing pi touched the codex slot"


def test_setting_effort_on_a_pi_project_writes_the_pi_slot_not_the_codex_slot(tmp_path):
    """pi accepts `--thinking <level>` (off..max), so effort is a real control on both backends
    and gets the same per-backend slot the model does. An earlier draft REFUSED effort on pi on
    the belief that pi had no such control; `pi --help` disproves it."""
    home = str(tmp_path / "rh")
    p = run_toggle(["effort", "high"], home, PI)
    assert p.returncode == 0, p.stderr
    assert _slot(home, "pi_effort") == "high"
    assert _slot(home, "codex_effort") is None, "wrote codex effort from a pi project"


def test_effort_clear_on_a_pi_project_clears_only_the_pi_slot(tmp_path):
    home = str(tmp_path / "rh")
    run_toggle(["effort", "high"], home, {"REVIEWER_BACKEND": "codex"})
    run_toggle(["effort", "low"], home, PI)
    p = run_toggle(["effort", "clear"], home, PI)
    assert p.returncode == 0, p.stderr
    assert _slot(home, "pi_effort") is None
    assert _slot(home, "codex_effort") == "high", "clearing pi touched the codex slot"


def test_the_two_backends_do_not_share_a_level_set(tmp_path):
    """`ultra` is codex-only and `off` is pi-only. Validating either against the other's list
    would let an unknown level reach the CLI, which pi REJECTS -- and on a fail-closed gate a
    rejected review blocks every commit until someone traces it back here."""
    home = str(tmp_path / "rh")
    assert run_toggle(["effort", "ultra"], home, PI).returncode != 0
    assert _slot(home, "pi_effort") is None
    assert run_toggle(["effort", "off"], home, PI).returncode == 0
    assert _slot(home, "pi_effort") == "off"

    assert run_toggle(["effort", "off"], home, {"REVIEWER_BACKEND": "codex"}).returncode != 0
    assert _slot(home, "codex_effort") is None
    assert run_toggle(["effort", "ultra"], home, {"REVIEWER_BACKEND": "codex"}).returncode == 0
    assert _slot(home, "codex_effort") == "ultra"


def test_an_invalid_pi_effort_is_skipped_at_resolve_time_not_just_on_set(tmp_path, monkeypatch):
    """The toggle validates what IT writes, but the env var and a hand-edited state file bypass
    it completely. Both must be re-validated on the way out, or `ultra` reaches pi and every
    commit in the project is blocked."""
    home = tmp_path / "rh"
    monkeypatch.setenv("REVIEWER_HOME", str(home))
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)
    rl.set_backend_effort("pi", "high")

    monkeypatch.setenv("REVIEWER_PI_EFFORT", "ultra")     # codex-only level
    effort, source = rl.reviewer_effort_detail("pi")
    assert effort == "high", "an invalid env level reached the resolver"
    assert "REVIEWER_PI_EFFORT" not in source

    monkeypatch.delenv("REVIEWER_PI_EFFORT", raising=False)
    rl.pi_effort_file().write_text("none\n", encoding="utf-8")   # hand-edited, codex-only
    effort, source = rl.reviewer_effort_detail("pi")
    assert effort == "medium", "an invalid state file reached the resolver"
    assert source == "default"


def test_unset_pi_effort_resolves_to_the_effective_medium_not_to_nothing(tmp_path, monkeypatch):
    """The hook sends `medium` when nothing is set, so an unset override is not "no setting" --
    reporting it as one would describe a level that is definitely being sent as absent."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_PI_EFFORT", raising=False)
    assert rl.reviewer_effort_detail("pi") == ("medium", "default")


def test_show_on_a_pi_project_reports_the_effective_effort(tmp_path):
    home = str(tmp_path / "rh")
    p = run_toggle([], home, PI)
    assert p.returncode == 0, p.stderr
    assert "medium" in p.stdout, p.stdout


def test_an_ambient_pi_effort_does_not_leak_into_the_tests(tmp_path, monkeypatch):
    """REGRESSION. `run_toggle` scrubbed REVIEWER_CODEX_EFFORT but not REVIEWER_PI_EFFORT, so a
    maintainer who had exported a pi effort in their own shell would see the default-resolution
    tests above fail -- or worse, pass for the wrong reason on a value that happened to match.

    The asymmetry is the whole bug: every OTHER highest-precedence source was scrubbed, and the
    pi effort variable was added later without being added here. Assert through the helper
    rather than by inspecting it, so the test fails if the scrub is ever dropped again."""
    monkeypatch.setenv("REVIEWER_PI_EFFORT", "xhigh")
    home = str(tmp_path / "rh")
    p = run_toggle([], home, PI)
    assert p.returncode == 0, p.stderr
    assert "medium" in p.stdout, p.stdout
    assert "xhigh" not in p.stdout, p.stdout


def test_the_backend_effort_api_raises_rather_than_falling_back_to_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    with pytest.raises(ValueError):
        rl.set_backend_effort("nonsense", "low")
    with pytest.raises(ValueError):
        rl.reviewer_effort_detail("nonsense")
    with pytest.raises(ValueError):
        rl.set_backend_effort("pi", "ultra")
    with pytest.raises(ValueError):
        rl.set_backend_effort("codex", "off")


def test_reviewer_efforts_differ_per_backend():
    assert "ultra" in rl.reviewer_efforts("codex")
    assert "ultra" not in rl.reviewer_efforts("pi")
    assert "off" in rl.reviewer_efforts("pi")
    assert "off" not in rl.reviewer_efforts("codex")


def test_timeout_is_shared_and_still_settable_from_a_pi_project(tmp_path):
    """The timeout genuinely DOES apply to pi (commit_hook passes commit_review_timeout to
    run_pi_review), so it must NOT be refused alongside model and effort."""
    home = str(tmp_path / "rh")
    p = run_toggle(["timeout", "1200"], home, PI)
    assert p.returncode == 0, p.stderr
    assert "1200" in p.stdout


def test_reviewer_model_detail_reports_the_source_per_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    monkeypatch.delenv("REVIEWER_PI_MODEL", raising=False)
    rl.set_backend_model("pi", "glm-5.2")
    model, source = rl.reviewer_model_detail("pi")
    assert model == "glm-5.2"
    assert "pi_model" in source

    monkeypatch.setenv("REVIEWER_PI_MODEL", "env-model")
    model, source = rl.reviewer_model_detail("pi")
    assert model == "env-model"
    assert "REVIEWER_PI_MODEL" in source


def test_an_invalid_pi_model_is_skipped_rather_than_reported(tmp_path, monkeypatch):
    """Mirrors the codex resolver's documented behaviour: an invalid candidate is SKIPPED, and
    the source must name where the value really came from -- otherwise an operator unsets a
    variable that was already being ignored."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    rl.set_backend_model("pi", "glm-5.2")
    monkeypatch.setenv("REVIEWER_PI_MODEL", "not a valid token")
    model, source = rl.reviewer_model_detail("pi")
    assert model == "glm-5.2"
    assert "REVIEWER_PI_MODEL" not in source


def test_reviewer_model_detail_for_codex_agrees_with_codex_model_detail(tmp_path, monkeypatch):
    """Delegation, not reimplementation: the codex chain includes a config.json candidate that a
    hand-rolled copy would drop."""
    home = tmp_path / "rh"
    monkeypatch.setenv("REVIEWER_HOME", str(home))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    (home).mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"model": "from-config"}), encoding="utf-8")
    assert rl.reviewer_model_detail("codex") == rl.codex_model_detail()
    assert rl.reviewer_model_detail("codex")[0] == "from-config"


def test_reviewer_model_detail_rejects_an_unknown_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    with pytest.raises(ValueError):
        rl.reviewer_model_detail("nonsense")
