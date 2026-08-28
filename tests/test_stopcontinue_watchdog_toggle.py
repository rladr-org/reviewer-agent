# SPDX-License-Identifier: MPL-2.0
"""`/stopContinueWatchdog on [interval]|off|status` toggle.

The watchdog REQUIRES stopContinue active (its tick relies on the Stop hook, inert otherwise),
so `on` errors until stopContinue is enabled. The flag file's content is the interval; `on N`
after `on M` re-writes it. `status` prints ACTIVE/INACTIVE for the tick's self-cancel check.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "stopcontinue_watchdog_toggle.py")
STOPCONTINUE_TOGGLE = str(ROOT / "stopcontinue_toggle.py")
SESSION_START = str(ROOT / "session_start_hook.py")


def run(action, proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
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


def test_status_prints_active_inactive(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    assert run("status", proj, home).stdout.strip() == "INACTIVE"
    run("on", proj, home)
    assert run("status", proj, home).stdout.strip() == "ACTIVE"


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
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    return subprocess.run(
        [sys.executable, SESSION_START],
        input=json.dumps({"cwd": proj}),
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
# `status` reports INACTIVE so the tick self-cancels, and SessionStart stops re-arming.
# --------------------------------------------------------------------------
def test_status_inactive_when_stopcontinue_turned_off_after_arming(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    assert run("status", proj, home).stdout.strip() == "ACTIVE"
    disable_stopcontinue(proj, home)
    assert wd_active(home, proj, monkeypatch)  # the watchdog flag itself is still set...
    assert run("status", proj, home).stdout.strip() == "INACTIVE"  # ...but it is not effective


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
    tick = rl.watchdog_tick_prompt(proj)
    # Both flag files must appear by absolute path so the tick can Read them.
    assert str(rl.stopcontinue_flag_file(proj)) in tick
    assert str(rl.stopcontinue_watchdog_flag_file(proj)) in tick


def test_watchdog_tick_prompt_uses_read_not_bash(tmp_path, monkeypatch):
    """The new tick prompt directs the model to Read the flag files instead of shelling into
    stopcontinue_watchdog_toggle.py status — the whole point of the visibility change."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj)
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
    tick = rl.watchdog_tick_prompt(proj)
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
    tick = rl.watchdog_tick_prompt(proj)
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

    Assert (a) 'resume' precedes the quoted `ok`, and (b) an 'otherwise' (or 'else')
    else-marker sits between 'resume' and `ok` within a single clause (no intervening '.')
    — the alternation must be structural, not incidental."""
    import re
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(proj)
    # A quoted `ok`/`OK` is the healthy reply.
    ok_match = re.search(r"""[`'\"]ok[`'\"]""", tick, re.IGNORECASE)
    assert ok_match, "tick prompt does not quote `ok` as the healthy reply: %r" % tick
    resume_match = re.search(r"\bresume\b", tick, re.IGNORECASE)
    assert resume_match, "tick prompt does not mention 'resume': %r" % tick
    assert resume_match.start() < ok_match.start(), (
        "`ok` reply appears BEFORE 'resume' — the recovery branch is dead code: %r" % tick
    )
    # Between 'resume' and the `ok` quote, and within a single clause (no '.' separator),
    # there must be an alternation marker.
    connector = tick[resume_match.end():ok_match.start()]
    assert "." not in connector, (
        "'resume' and `ok` are in different sentences — the else-alternation is not "
        "structural: %r" % tick
    )
    alt = re.search(r"\b(otherwise|else)\b", connector, re.IGNORECASE)
    assert alt, (
        "no 'otherwise'/'else' alternation marker between 'resume' and `ok`: %r" % tick
    )


# Frozen snapshot of reviewer_prompts.WATCHDOG_TICK_PROMPT (pre-visibility-reduction). The
# GREEN phase removes that module-level literal, so we snapshot it here to keep the size
# baseline stable. Any future edit to the OLD prompt would happen in tandem with a git
# history rewrite that is not planned — this snapshot is expected to be stable.
_BASELINE_TICK_TEMPLATE = (
    "stopContinueWatchdog tick -- run "
    '`python3 "{}" status`. '
    "If it prints INACTIVE, delete THIS watchdog cron (CronDelete) and stop. If it prints "
    "ACTIVE: if you have unfinished work from before, resume it; otherwise reply briefly "
    "that you are done and stop."
)


def test_watchdog_tick_prompt_size_is_reduced(tmp_path, monkeypatch):
    """The visibility reduction promises a shorter tick prompt than today's baseline. Force
    both new-prompt flag-file paths AND the baseline's toggle-script path to equally short
    controlled values (3-byte basenames under a 2-byte parent), so the size delta reflects
    template shape — not path-byte accounting or pytest tmp path length.

    The new prompt embeds TWO paths (both flag files) versus the baseline's ONE (the toggle
    script). Require the total to still be at least 30 bytes shorter than the baseline —
    which proves the template body itself trimmed by well more than the extra path bytes."""
    import reviewer_state as _rs
    import reviewer_prompts as _rp

    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    short_a = Path("/s/a")
    short_b = Path("/s/b")
    # Patch at every plausible import site so this test does not encode where the green
    # implementation chose to import the state helpers from.
    for m in (_rs, _rp, rl):
        monkeypatch.setattr(m, "stopcontinue_flag_file", lambda p, _a=short_a: _a, raising=False)
        monkeypatch.setattr(
            m, "stopcontinue_watchdog_flag_file", lambda p, _b=short_b: _b, raising=False
        )
    tick = rl.watchdog_tick_prompt(proj)
    baseline_len = len(_BASELINE_TICK_TEMPLATE.format(str(short_a)))
    assert len(tick) < baseline_len - 30, (
        "tick prompt did not shrink meaningfully vs baseline "
        "(new=%d, baseline=%d): %r" % (len(tick), baseline_len, tick)
    )


def test_arm_instruction_signature_takes_proj_and_interval(tmp_path, monkeypatch):
    """watchdog_arm_instruction is now watchdog_arm_instruction(proj, interval) so the shared
    helper can build the per-project tick prompt."""
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    # Both callers pass proj; call it here the same way. If the signature still takes only
    # `interval`, this raises TypeError and the test is red.
    instr = rl.watchdog_arm_instruction(proj, 5)
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
    expected_tick = rl.watchdog_tick_prompt(proj)
    assert expected_tick in toggle_out
    assert expected_tick in ctx
