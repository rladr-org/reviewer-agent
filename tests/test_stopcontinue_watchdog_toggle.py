# SPDX-License-Identifier: MPL-2.0
"""`/stopContinueWatchdog on [interval]|off|status` toggle.

The watchdog REQUIRES stopContinue active (its tick relies on the Stop hook, inert otherwise),
so `on` errors until stopContinue is enabled. The flag file's content is the interval; `on N`
after `on M` re-writes it. `status` is for the operator, labelled like every other watchdog command;
the tick checks the flag files with Read, not `status`.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "stopcontinue_watchdog_toggle.py")
STOPCONTINUE_TOGGLE = str(ROOT / "stopcontinue_toggle.py")
SESSION_START = str(ROOT / "session_start_hook.py")

# The watchdog `on` now bakes a SESSION-scoped marker path, so the toggle resolves this Claude
# session id from CLAUDE_CODE_SESSION_ID and the tick/arm helpers take it as an argument.
SID = "claude-toggle-sess"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """REVIEWER_STATE_DIR overrides REVIEWER_HOME; an inherited CLAUDE session id would leak the
    real session into subprocess arms. Clear them so the tests provide their own via SID."""
    for var in ("REVIEWER_STATE_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def run(action, proj, home):
    env = dict(os.environ)
    env.pop("REVIEWER_STATE_DIR", None)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    env["CLAUDE_CODE_SESSION_ID"] = SID          # the toggle `on` fails closed without this
    args = [action] if isinstance(action, str) else list(action)
    return subprocess.run(
        [sys.executable, TOGGLE, *args], capture_output=True, text=True, env=env
    )


def make_repo(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return str(proj)


def enable_stopcontinue(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    p = subprocess.run(
        [sys.executable, STOPCONTINUE_TOGGLE, "on", "acceptRisk"],
        capture_output=True, text=True, env=env,
    )
    assert p.returncode == 0, p.stderr


def disable_stopcontinue(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    p = subprocess.run(
        [sys.executable, STOPCONTINUE_TOGGLE, "off"],
        capture_output=True, text=True, env=env,
    )
    assert p.returncode == 0, p.stderr


def wd_active(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.is_stopcontinue_watchdog_active(proj)


def wd_interval(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.watchdog_interval(proj)


def test_on_requires_stopcontinue_active(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run("on", proj, home)
    assert p.returncode != 0
    assert not wd_active(home, proj, monkeypatch)
    assert "stopContinue is not active" in (p.stdout + p.stderr)
    assert "/stopContinue on acceptRisk" in (p.stdout + p.stderr)


def test_on_sets_flag_with_default_interval_when_stopcontinue_active(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    p = run("on", proj, home)
    assert p.returncode == 0, p.stderr
    assert wd_active(home, proj, monkeypatch)
    assert wd_interval(home, proj, monkeypatch) == rl.WATCHDOG_DEFAULT_INTERVAL
    assert "ON" in p.stdout


def test_on_with_interval_sets_it(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    p = run(["on", "5"], proj, home)
    assert p.returncode == 0, p.stderr
    assert wd_interval(home, proj, monkeypatch) == 5


def test_interval_change_rewrites_the_stored_interval(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "3"], proj, home)
    assert wd_interval(home, proj, monkeypatch) == 3
    run(["on", "5"], proj, home)
    assert wd_interval(home, proj, monkeypatch) == 5


def test_on_rejects_out_of_range_and_nonint_intervals(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    for bad in ("0", "60", "90", "abc"):
        p = run(["on", bad], proj, home)
        assert p.returncode != 0, "interval {!r} should be rejected".format(bad)
        assert not wd_active(home, proj, monkeypatch)
        assert "interval must be an integer" in (p.stdout + p.stderr)


def test_off_clears_flag(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    assert wd_active(home, proj, monkeypatch)
    p = run("off", proj, home)
    assert p.returncode == 0
    assert not wd_active(home, proj, monkeypatch)
    assert "OFF" in p.stdout


def test_status_speaks_the_skill_contract(tmp_path, monkeypatch):
    # #20: the skill relays one line quoted verbatim, `stopContinueWatchdog: ON ...` or
    # `... OFF ...`; a bare ACTIVE/INACTIVE left the agent nothing it could quote.
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    assert run("status", proj, home).stdout.strip().startswith("stopContinueWatchdog: OFF for ")
    run(["on", "5"], proj, home)
    out = run("status", proj, home).stdout.strip()
    assert out.startswith("stopContinueWatchdog: ON for ") and "(interval 5 min)" in out


def test_on_fails_when_not_a_git_repo(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = tmp_path / "plain"
    proj.mkdir()
    p = run("on", str(proj), home)
    assert p.returncode != 0
    assert "ERROR" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------
# The `on`/`off` output carries the CronList-reconcile instruction the model acts on.
# --------------------------------------------------------------------------
def test_on_output_carries_the_arm_instruction(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    out = run(["on", "3"], proj, home).stdout
    assert "[stopContinueWatchdog]" in out
    assert rl.WATCHDOG_MARKER in out
    assert "*/3 * * * *" in out          # the target cron schedule for the interval
    assert "CronList" in out and "CronCreate" in out and "CronDelete" in out
    assert "stopContinueWatchdog tick" in out  # the tick prompt is included verbatim


def test_off_output_carries_the_cancel_instruction(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    out = run("off", proj, home).stdout
    assert rl.WATCHDOG_MARKER in out
    assert "CronDelete" in out


# --------------------------------------------------------------------------
# SessionStart backstop: re-arm context is injected iff the flag is on.
# --------------------------------------------------------------------------
def run_session_start(proj, home):
    env = dict(os.environ)
    env.pop("REVIEWER_STATE_DIR", None)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    return subprocess.run(
        [sys.executable, SESSION_START],
        input=json.dumps({"cwd": proj, "session_id": SID}),
        capture_output=True, text=True, env=env,
    )


def test_sessionstart_injects_rearm_context_when_flag_on(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "5"], proj, home)
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert rl.WATCHDOG_MARKER in ctx
    assert "*/5 * * * *" in ctx  # re-arms at the STORED interval


def test_sessionstart_injects_nothing_when_flag_off(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)  # stopContinue on, but watchdog OFF
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""  # no additionalContext emitted


# --------------------------------------------------------------------------
# Regression: `/stopContinue off` clears only the stopContinue flag, leaving the watchdog flag
# set. Because the tick relies on the (now inert) Stop hook, the watchdog must go dormant:
# `status` reports it OFF (dormant), the tick self-cancels on the missing stopContinue flag, and
# SessionStart stops re-arming.
# --------------------------------------------------------------------------
def test_status_inactive_when_stopcontinue_turned_off_after_arming(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    assert run("status", proj, home).stdout.strip().startswith("stopContinueWatchdog: ON")
    disable_stopcontinue(proj, home)
    assert wd_active(home, proj, monkeypatch)  # the watchdog flag itself is still set...
    out = run("status", proj, home).stdout.strip()  # ...but it is not effective
    assert out.startswith("stopContinueWatchdog: OFF")
    assert "dormant because /stopContinue is off" in out


def test_sessionstart_injects_nothing_when_stopcontinue_off_even_if_flag_on(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "5"], proj, home)
    disable_stopcontinue(proj, home)
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""  # no re-arm context despite the flag being set


# --------------------------------------------------------------------------
# Tick-prompt visibility reduction — see docs/plans/watchdog_tick_visibility.md.
# Tick prompt is now per-project (names the two absolute flag-file paths so the model
# checks them with Read, not by shelling into stopcontinue_watchdog_toggle.py status). The
# recovery branch ("if unfinished work, resume it") is preserved verbatim; the healthy path
# contracts to reply `ok`.
# --------------------------------------------------------------------------
def test_watchdog_tick_prompt_is_per_project(tmp_path, monkeypatch):
    """A new helper watchdog_tick_prompt(proj) returns a string that embeds the two absolute
    flag-file paths for that project."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj, SID)
    # Both flag files must appear by absolute path so the tick can Read them.
    assert str(rl.stopcontinue_flag_file(proj)) in tick
    assert str(rl.stopcontinue_watchdog_flag_file(proj)) in tick


def test_watchdog_tick_prompt_uses_read_not_bash(tmp_path, monkeypatch):
    """The new tick prompt directs the model to Read the flag files instead of shelling into
    stopcontinue_watchdog_toggle.py status — the whole point of the visibility change."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj, SID)
    # No shell-out to a Python script.
    assert "python3" not in tick
    assert "stopcontinue_watchdog_toggle.py" not in tick
    # And the Read direction is present.
    assert "Read" in tick or "read" in tick.lower()


def test_watchdog_tick_prompt_conditions_cancel_on_missing_flag(tmp_path, monkeypatch):
    """CronDelete must be CONDITIONED on a missing flag, not unconditional. Additionally the
    condition must scope over BOTH flags (an impl checking only one would not self-cancel
    when the OTHER was cleared, leaving a stale cron), and there must be EXACTLY ONE
    CronDelete direction (an extra unconditional CronDelete alongside the conditional one
    would cancel on every tick and defeat the watchdog).

    Four independent assertions:
    - Exactly one CronDelete in the prompt (no bonus unconditional cancel).
    - Both flag paths appear BEFORE the CronDelete — they are the subject of the check.
    - The CronDelete is inside a real 'if/when ... missing ... CronDelete' clause (no
      intervening '.' or ';' — a real conditional structure, not proximity).
    - A set-alternation word (either/any/both/or) sits between the 'if/when' and 'missing',
      proving the predicate ranges over the set of both flags, not just one."""
    import re
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj, SID)
    a_path = str(rl.stopcontinue_flag_file(proj))
    b_path = str(rl.stopcontinue_watchdog_flag_file(proj))

    # (a) Exactly one CronDelete direction.
    n_delete = tick.count("CronDelete")
    assert n_delete == 1, (
        "expected exactly one CronDelete direction, got %d: %r" % (n_delete, tick)
    )
    cd_at = tick.find("CronDelete")

    # (b) Both flag paths appear before the CronDelete. An impl that names only one flag
    # in the check clause (or names both only after the cancel) fails here.
    a_at = tick.find(a_path)
    b_at = tick.find(b_path)
    assert 0 <= a_at < cd_at, (
        "stopContinue flag path %r not present before CronDelete: %r" % (a_path, tick)
    )
    assert 0 <= b_at < cd_at, (
        "watchdog flag path %r not present before CronDelete: %r" % (b_path, tick)
    )

    # (c) The CronDelete is inside a real conditional clause.
    conditional = re.compile(
        r"\b(if|when)\b[^.;]*?\bmissing\b[^.;]*?\bCronDelete\b",
        re.IGNORECASE,
    )
    assert conditional.search(tick), (
        "CronDelete is not gated by a real 'if/when ... missing ... CronDelete' clause "
        "in the tick prompt (proximity alone is insufficient): %r" % tick
    )

    # (d) The conditional's 'missing' predicate scopes over BOTH flags via a
    # set-alternation word ('either'/'any'/'both'/'or') between the 'if'/'when' and
    # 'missing'. This forbids a single-flag conditional from passing.
    scope = re.compile(
        r"\b(if|when)\b[^.;]*?\b(either|any|both|or)\b[^.;]*?\bmissing\b",
        re.IGNORECASE,
    )
    assert scope.search(tick), (
        "cancel-conditional's 'missing' predicate does not scope over BOTH flags "
        "(needs a set-alternation word like 'either'/'any'/'both'/'or' between "
        "'if'/'when' and 'missing'): %r" % tick
    )


def test_watchdog_tick_prompt_conditions_resume_on_unfinished_work(tmp_path, monkeypatch):
    """The rescue path ('resume unfinished work') must be conditional on there being
    unfinished work — an unconditional 'resume' (as in 'always resume unfinished work...')
    would fire on every healthy tick and defeat the visibility reduction.

    Same shape as the cancel-branch test: require a conditional marker ('if'/'when') to
    precede 'unfinished work', which must precede 'resume', all within one clause."""
    import re
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj, SID)
    pattern = re.compile(
        r"\b(if|when)\b[^.;]*?\bunfinished\s+work\b[^.;]*?\bresume\b",
        re.IGNORECASE,
    )
    assert pattern.search(tick), (
        "resume is not gated by a conditional 'if/when ... unfinished work ... resume' "
        "clause in the tick prompt: %r" % tick
    )


def test_watchdog_tick_prompt_healthy_reply_is_ok_only_when_no_unfinished_work(
    tmp_path, monkeypatch
):
    """The healthy-effective-ACTIVE-and-nothing-to-resume path contracts to a two-character
    `ok` reply so the transcript stays quiet. But `ok` must be the ELSE of the
    unfinished-work branch, not the unconditional response — otherwise the tick would answer
    `ok` even when it should be resuming stranded work, defeating recovery.

    Assert the HEALTHY `ok` (the one that ends the resume branch) comes AFTER 'resume' with an
    'otherwise'/'else' between them in one clause. Note the tick also carries a fail-safe `ok`
    ("if you cannot read those files, do NOT delete anything -- reply `ok`") that precedes
    'resume'; the healthy `ok` is specifically the else of the unfinished-work branch, so this
    keys on the `ok` AFTER 'resume', not the first one in the string."""
    import re
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj, SID)
    resume_match = re.search(r"\bresume\b", tick, re.IGNORECASE)
    assert resume_match, "tick prompt does not mention 'resume': %r" % tick
    # The healthy `ok` is the ELSE of the resume branch -> it comes AFTER 'resume'.
    after = tick[resume_match.end():]
    ok_match = re.search(r"""[`'\"]ok[`'\"]""", after, re.IGNORECASE)
    assert ok_match, "no quoted `ok` after 'resume' as the healthy reply: %r" % tick
    connector = after[:ok_match.start()]
    assert "." not in connector, (
        "'resume' and the healthy `ok` are in different sentences — the else-alternation is "
        "not structural: %r" % tick
    )
    alt = re.search(r"\b(otherwise|else)\b", connector, re.IGNORECASE)
    assert alt, (
        "no 'otherwise'/'else' alternation marker between 'resume' and the healthy `ok`: %r"
        % tick
    )


def test_watchdog_tick_prompt_stays_read_based_and_bounded(tmp_path, monkeypatch):
    """The visibility reduction removed the per-tick bash shell-out; auto-shutoff then added a
    teardown_ready Read plus a fail-safe delete-guard, so the tick prompt is intentionally LONGER
    than the pre-auto-shutoff version (the old 'shorter than the bash baseline' size guard no
    longer applies -- see docs/plans/watchdog_auto_shutoff.md). The remaining invariants: it stays
    Read-based (no shell-out) and does not balloon. Force all three embedded paths to short
    controlled values so the bound reflects the template body, not path bytes or tmp-path length."""
    import reviewer_state as _rs
    import reviewer_prompts as _rp

    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    short = Path("/s/a")
    # Patch at every plausible import site so this test does not encode where the green
    # implementation chose to import the state helpers from.
    for m in (_rs, _rp, rl):
        monkeypatch.setattr(m, "stopcontinue_flag_file", lambda p, _x=short: _x, raising=False)
        monkeypatch.setattr(
            m, "stopcontinue_watchdog_flag_file", lambda p, _x=short: _x, raising=False)
        monkeypatch.setattr(
            m, "watchdog_teardown_ready_file", lambda p, s, _x=short: _x, raising=False)
        monkeypatch.setattr(
            m, "watchdog_disarm_file", lambda p, s, _x=short: _x, raising=False)
    tick = rl.watchdog_tick_prompt(proj, SID)
    # No per-tick shell-out to a script -- the whole point of the visibility change.
    assert "python3" not in tick and "toggle.py" not in tick
    # Bounded: catches an accidental runaway while allowing the fail-safe, teardown_ready and
    # operator-has-the-turn (pause marker) clauses.
    assert len(tick) < 900, "tick prompt ballooned (%d bytes): %r" % (len(tick), tick)


def test_arm_instruction_signature_takes_proj_and_interval(tmp_path, monkeypatch):
    """watchdog_arm_instruction is now watchdog_arm_instruction(proj, interval) so the shared
    helper can build the per-project tick prompt."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    # Both callers pass proj; call it here the same way. If the signature still takes only
    # `interval`, this raises TypeError and the test is red.
    instr = rl.watchdog_arm_instruction(proj, 5, SID)
    assert "*/5 * * * *" in instr
    assert str(rl.stopcontinue_flag_file(proj)) in instr
    assert str(rl.stopcontinue_watchdog_flag_file(proj)) in instr


def test_arm_instruction_and_sessionstart_produce_identical_tick_prompts(tmp_path, monkeypatch):
    """Both the toggle's `on` output and SessionStart's re-arm output must embed byte-identical
    tick prompts for a given project + interval — the shared helper is the single source of
    truth."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "5"], proj, home)

    toggle_out = run(["on", "5"], proj, home).stdout

    p = run_session_start(proj, home)
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]

    # Resolve `expected_tick` under the SAME REVIEWER_HOME the two subprocesses saw, so the
    # embedded flag-file paths match theirs.
    monkeypatch.setenv("REVIEWER_HOME", home)
    expected_tick = rl.watchdog_tick_prompt(proj, SID)
    assert expected_tick in toggle_out
    assert expected_tick in ctx
