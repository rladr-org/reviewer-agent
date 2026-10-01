# SPDX-License-Identifier: MPL-2.0
"""Red tests for watchdog auto-shutoff / auto-rearm.

See docs/plans/watchdog_auto_shutoff.md (committed f9917d4). The design keeps ALL timing in
Python hooks: the Stop hook writes a session-scoped `disarm` marker (a timestamp) on a genuine
STOP_OK, and on later idle ticks — checking the clock IN PYTHON — writes a `teardown_ready`
marker once `now - disarm.ts >= interval`. The tick is reduced to a fail-safe PRESENCE check of
`teardown_ready`. `UserPromptSubmit` re-arms on `teardown_ready` or a reliable in-hook disarm-age
fallback. The arm/reconcile is prompt-aware so the new tick replaces an outdated one.

These are RED: the state helpers, the env session-id resolver, the two/three-arg tick/arm
signatures, the stop-hook marker bookkeeping, `userpromptsubmit_hook.py`, the prompt-aware
reconcile, and `_last_turn_prompt_text` do not exist yet. Each test names one contract the green
phase must satisfy.
"""
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
STOP = str(ROOT / "stop_hook.py")
UPS = str(ROOT / "userpromptsubmit_hook.py")
TOGGLE = str(ROOT / "stopcontinue_watchdog_toggle.py")
STOPCONTINUE_TOGGLE = str(ROOT / "stopcontinue_toggle.py")
MANIFEST = _BASE / "hooks" / "hooks.json"
SKILL_MD = _BASE / "skills" / "stopContinueWatchdog" / "SKILL.md"
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

# The reconcile is prompt-aware iff it talks about matching/replacing by PROMPT (a schedule-only
# instruction mentions "prompt" only as "whose prompt contains the marker" / "tick prompt verbatim").
_PROMPT_AWARE = re.compile(
    r"(match|matches|matching|identical|differ|different|replace|outdated|stale)"
    r"[^.\n]{0,70}\bprompt\b|\bprompt\b[^.\n]{0,70}"
    r"(match|matches|matching|identical|differ|different|replace|outdated|stale)",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """REVIEWER_STATE_DIR takes precedence over REVIEWER_HOME, and an inherited REVIEWER_BACKEND
    or CLAUDE session-id would leak the operator's environment into these tests (reading/writing
    shared state, or running the wrong reviewer backend). Clear them for every test; subprocess
    helpers build their env from os.environ, so this isolates them too."""
    for var in ("REVIEWER_STATE_DIR", "REVIEWER_BACKEND",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)

# A literal tick turn-start body. Detection keys on the version-stable prefix
# "stopContinueWatchdog tick", so the tests do not depend on the exact green tick wording.
_TICK_BODY = (
    "stopContinueWatchdog tick -- Read <ready>, <wd> and <sc>; if <ready> exists OR either flag "
    'file is missing, CronList and CronDelete every task whose prompt contains '
    '"stopContinueWatchdog" and stop. Else reply `ok` and stop.'
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return str(repo)


def _real_turn_transcript(tmp_path, human_prompt, final_text):
    events = [
        {"type": "user", "message": {"role": "user", "content": human_prompt}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": final_text}]}},
    ]
    return _write_jsonl(tmp_path, "real.jsonl", events)


def _tick_turn_transcript(tmp_path, final_text="ok"):
    """A fired tick: a `type: user` event with isMeta True (the shape verification found), then
    the tick's own CronList tool-use + tool_result carrier, then the final text."""
    events = [
        {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": _TICK_BODY}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": "CronList", "id": "t1", "input": {}}]}},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": final_text}]}},
    ]
    return _write_jsonl(tmp_path, "tick.jsonl", events)


def _tool_only_transcript(tmp_path):
    events = [
        {"type": "user", "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
    ]
    return _write_jsonl(tmp_path, "toolonly.jsonl", events)


def _tick_turn_tool_only_transcript(tmp_path):
    """A tick turn (isMeta tick turn-start) whose final message is tool-only, so the Stop hook
    takes its tool-only early return before supervision."""
    events = [
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": _TICK_BODY}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": "CronDelete", "input": {}}]}},
    ]
    return _write_jsonl(tmp_path, "tick_toolonly.jsonl", events)


def _bg_wait_transcript(tmp_path):
    events = [
        {"type": "user", "message": {"role": "user", "content": "kick off the run"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Agent", "id": "toolu_BG",
             "input": {"description": "d", "subagent_type": "Explore", "prompt": "p",
                       "run_in_background": True}}]}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "Launched in the background; waiting on it."}]}},
    ]
    return _write_jsonl(tmp_path, "bgwait.jsonl", events)


def _write_jsonl(tmp_path, name, events):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(p)


def _stop_input(cwd, transcript_path, session_id="claude-stop"):
    return {"cwd": cwd, "session_id": session_id, "transcript_path": transcript_path,
            "hook_event_name": "Stop", "stop_hook_active": False}


def run_stop(stdin_obj, env_extra):
    env = dict(os.environ)
    env.pop("REVIEWER_STATE_DIR", None)         # must not override the test's REVIEWER_HOME
    env["REVIEWER_BACKEND"] = "codex"           # force codex so REVIEWER_CODEX_BIN=FAKE applies
    env.update(env_extra)
    return subprocess.run([sys.executable, STOP], input=json.dumps(stdin_obj),
                          capture_output=True, text=True, env=env)


def run_ups(cwd, prompt, home, session_id="claude-ups"):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = cwd
    stdin = {"cwd": cwd, "session_id": session_id, "prompt": prompt,
             "hook_event_name": "UserPromptSubmit"}
    return subprocess.run([sys.executable, UPS], input=json.dumps(stdin),
                          capture_output=True, text=True, env=env)


def enable_stopcontinue(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    p = subprocess.run([sys.executable, STOPCONTINUE_TOGGLE, "on", "acceptRisk"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr


def _write_disarm_age(repo, sid, seconds_old):
    """Write the session-scoped disarm marker with a controlled JSON `ts` (age = seconds_old).
    Pins the age checks to the JSON ts rather than the file mtime (which is now)."""
    f = Path(rl.watchdog_disarm_file(repo, sid))
    f.parent.mkdir(parents=True, exist_ok=True)
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(seconds=seconds_old)).isoformat()
    f.write_text(json.dumps({"ts": ts, "session_id": sid}))
    return ts


def _disarm_ts(repo, sid):
    return json.loads(Path(rl.watchdog_disarm_file(repo, sid)).read_text())["ts"]


def _write_tick_seen_age(repo, sid, seconds_old):
    """Write the session-scoped tick-seen marker with a controlled JSON `ts` (age = seconds_old).
    Same shape as the disarm marker, and pinned to the ts rather than the mtime for the same
    reason: the mtime is always `now`."""
    f = Path(rl.watchdog_tick_seen_file(repo, sid))
    f.parent.mkdir(parents=True, exist_ok=True)
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(seconds=seconds_old)).isoformat()
    f.write_text(json.dumps({"ts": ts, "session_id": sid}))
    return ts


def _tick_seen_ts(repo, sid):
    return json.loads(Path(rl.watchdog_tick_seen_file(repo, sid)).read_text())["ts"]


# --------------------------------------------------------------------------
# State helpers
# --------------------------------------------------------------------------
def test_disarm_and_teardown_ready_markers_roundtrip_session_scoped(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "S-A") is False
    rl.set_watchdog_disarm(repo, "S-A")
    assert rl.is_watchdog_disarm_set(repo, "S-A") is True
    assert rl.is_watchdog_disarm_set(repo, "S-B") is False          # session-scoped
    rl.set_watchdog_teardown_ready(repo, "S-A")
    assert rl.is_watchdog_teardown_ready(repo, "S-A") is True
    assert rl.is_watchdog_teardown_ready(repo, "S-B") is False
    assert rl.watchdog_disarm_file(repo, "S-A") != rl.watchdog_disarm_file(repo, "S-B")
    assert rl.watchdog_teardown_ready_file(repo, "S-A") != rl.watchdog_teardown_ready_file(repo, "S-B")
    rl.clear_watchdog_disarm(repo, "S-A")
    rl.clear_watchdog_teardown_ready(repo, "S-A")
    assert rl.is_watchdog_disarm_set(repo, "S-A") is False
    assert rl.is_watchdog_teardown_ready(repo, "S-A") is False


def test_claude_session_id_from_env_reads_the_exported_var(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    assert rl.claude_session_id_from_env() is None
    monkeypatch.setenv("CLAUDE_SESSION_ID", "legacy-sid")
    assert rl.claude_session_id_from_env() == "legacy-sid"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-sid")
    assert rl.claude_session_id_from_env() == "current-sid"   # current name wins


# --------------------------------------------------------------------------
# Tick prompt: fail-safe presence check of teardown_ready
# --------------------------------------------------------------------------
def test_tick_prompt_takes_session_id_and_reads_teardown_ready(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(repo, "S-A")    # 2-arg (was 1) -> TypeError = red
    assert str(rl.watchdog_teardown_ready_file(repo, "S-A")) in tick
    assert str(rl.stopcontinue_flag_file(repo)) in tick
    assert str(rl.stopcontinue_watchdog_flag_file(repo)) in tick


def test_tick_prompt_tears_down_on_teardown_ready_or_missing_flag_and_is_failsafe(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    tick = rl.watchdog_tick_prompt(repo, "S-A")
    ready = str(rl.watchdog_teardown_ready_file(repo, "S-A"))
    a_path = str(rl.stopcontinue_flag_file(repo))
    b_path = str(rl.stopcontinue_watchdog_flag_file(repo))

    assert tick.count("CronDelete") == 1
    cd = tick.find("CronDelete")
    assert 0 <= tick.find(ready) < cd
    assert 0 <= tick.find(a_path) < cd
    assert 0 <= tick.find(b_path) < cd

    clause = re.search(r"\b(if|when)\b(?P<body>[^.]*?)\bCronDelete\b", tick, re.IGNORECASE)
    assert clause, "CronDelete is not gated by an if/when clause: %r" % tick
    body = clause.group("body").lower()
    assert " or " in body
    assert "missing" in body
    assert "exist" in body or "present" in body       # teardown_ready existence

    # Fail-safe: on inability to read, do NOT delete.
    low = tick.lower()
    assert "cannot read" in low or "can't read" in low or "unable to read" in low
    assert "do not delete" in low or "don't delete" in low or "not delete" in low

    # Retry relies on the tick NOT deleting teardown_ready.
    assert ready in tick and tick.rfind(ready) < cd


def test_tick_prompt_directs_per_path_check_not_a_batched_ls(tmp_path, monkeypatch):
    """A model given the tick may check the three paths with a single batched `ls ready wd sc`.
    Because `teardown_ready` is armed only on a genuine STOP_OK, it is ABSENT in every healthy
    session, so that batched `ls` exits non-zero on the healthy case -- surfacing a scary
    `Error: Exit code 1` and, worse, colliding with the fail-safe `cannot read` clause (a
    legitimately-missing marker looks like an unreadable filesystem, which could suppress a real
    teardown and leak the cron). The tick must therefore (a) direct a PER-PATH check and warn off
    a single batched `ls`, and (b) state that the teardown-ready marker is normally ABSENT and that
    a merely-missing path is NOT a read failure -- so absence is never mistaken for unreadability."""
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    low = rl.watchdog_tick_prompt(repo, "S-A").lower()
    # (a) per-path, explicitly not one batched ls
    assert "individually" in low or "one at a time" in low or "one by one" in low
    assert "batch" in low or "single `ls`" in low or "single ls" in low
    # (b) teardown-ready normally absent; a missing path is not a read failure
    assert "normally absent" in low or "expected absent" in low or "normally missing" in low
    assert "not a read failure" in low or "not a read error" in low or "not unreadable" in low


# --------------------------------------------------------------------------
# Arm instruction: threads session id + prompt-aware reconcile
# --------------------------------------------------------------------------
def test_arm_instruction_threads_session_id_into_tick(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    instr = rl.watchdog_arm_instruction(repo, 5, "S-A")   # 3-arg (was 2) -> TypeError = red
    assert "*/5 * * * *" in instr
    assert str(rl.watchdog_teardown_ready_file(repo, "S-A")) in instr   # session-scoped path baked in


def test_arm_instruction_reconcile_is_prompt_aware(tmp_path, monkeypatch):
    """Upgrade path: the reconcile must keep a marker cron only when its PROMPT matches the
    current tick prompt (not schedule-only), so an outdated flag-only cron is deleted+recreated.
    A bare mention of the word "prompt" is insufficient — the schedule-only instruction already
    has that; the instruction must express matching/replacement BY prompt."""
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", home)
    instr = rl.watchdog_arm_instruction(repo, 5, "S-A")
    assert rl.watchdog_tick_prompt(repo, "S-A") in instr        # embeds the current tick verbatim
    assert _PROMPT_AWARE.search(instr), (
        "arm instruction does not express matching/replacing a marker cron BY its prompt "
        "(schedule-only reconcile would leave an outdated flag-only cron in place): %r" % instr
    )


def test_skill_md_reconcile_is_prompt_aware_and_synchronized(tmp_path):
    """The operator-facing skill drives the same reconcile the model performs, so it must carry
    the same prompt-aware contract — otherwise an operator `/stopContinueWatchdog on` keeps a
    stale-prompt cron even though the arm-instruction would not."""
    skill = SKILL_MD.read_text()
    assert _PROMPT_AWARE.search(skill), (
        "SKILL.md reconcile is still schedule-only (keeps any same-schedule cron without checking "
        "its prompt); it must be updated to the prompt-aware contract"
    )


# --------------------------------------------------------------------------
# Stop-hook DECISION
# --------------------------------------------------------------------------
def _armed(repo):
    rl.set_stopcontinue_active(repo)
    rl.set_stopcontinue_watchdog_active(repo, 1)               # interval 1 min for short age tests
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)


def test_real_stop_ok_writes_disarm_and_still_allows_stop(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    tr = _real_turn_transcript(tmp_path, "finish X", "Done. All tests pass.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- complete.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    assert "decision" not in json.loads(p.stdout)              # still allowed to stop
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is True
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False


def test_real_keep_working_writes_nothing(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    tr = _real_turn_transcript(tmp_path, "finish X", "Pausing here.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "Continue: finish the job.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["decision"] == "block"
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False


def test_background_wait_park_does_not_write_disarm(tmp_path, monkeypatch):
    """Correctness: a parked autonomous session must stay armed so the watchdog can recover it."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    tr = _bg_wait_transcript(tmp_path)
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False


def test_tool_only_allow_does_not_write_disarm(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    tr = _tool_only_transcript(tmp_path)
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False


def test_nudge_cap_allow_does_not_write_disarm(tmp_path, monkeypatch):
    """At the nudge cap the stop is allowed WITHOUT a STOP_OK verdict (work may remain), so the
    watchdog must stay armed to recover the strand — disarm must NOT be written."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    rl.bump_nudge_count(repo, "claude-stop")
    rl.bump_nudge_count(repo, "claude-stop")                  # used == 2 == cap
    tr = _real_turn_transcript(tmp_path, "finish X", "Still more to do, but pausing.")
    stdin = {"cwd": repo, "session_id": "claude-stop", "transcript_path": tr,
             "hook_event_name": "Stop", "stop_hook_active": True}   # mid-chain: budget NOT reset
    p = run_stop(stdin, {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "REVIEWER_STOPCONTINUE_MAX_NUDGES": "2", "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"})
    assert p.returncode == 0, p.stderr
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout             # codex not called past the cap
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False


def test_tick_with_stale_disarm_sets_teardown_ready_without_refreshing_disarm(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)                                              # interval = 1 min
    old_ts = _write_disarm_age(repo, "claude-stop", 120)      # 2 min old >= interval
    tr = _tick_turn_transcript(tmp_path, "ok")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- idle.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is True
    assert _disarm_ts(repo, "claude-stop") == old_ts          # ts not refreshed


def test_tick_with_fresh_disarm_does_not_set_teardown_ready(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    _write_disarm_age(repo, "claude-stop", 5)                 # 5s old < 1 min interval
    tr = _tick_turn_transcript(tmp_path, "ok")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- idle.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False


def test_recovery_tick_stop_ok_with_no_disarm_writes_disarm(tmp_path, monkeypatch):
    """A tick that resumed a stranded task to completion (STOP_OK, no prior disarm) must write
    disarm so the now-idle session counts down and shuts off."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)                                              # no disarm marker present
    tr = _tick_turn_transcript(tmp_path, "Recovered and finished the task.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- recovered work complete.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is True


def test_idle_heartbeat_tick_does_not_refresh_existing_disarm(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    fresh_ts = _write_disarm_age(repo, "claude-stop", 5)      # present, fresh
    tr = _tick_turn_transcript(tmp_path, "ok")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- idle.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert _disarm_ts(repo, "claude-stop") == fresh_ts        # NOT overwritten (set only if absent)


def test_aged_tick_that_keeps_working_does_not_arm_teardown_ready(tmp_path, monkeypatch):
    """Regression (green review): an aged idle-eligible tick that resumes a stranded task and gets
    KEEP_WORKING must NOT arm teardown_ready -- otherwise the next tick would delete the watchdog
    while work still remains. teardown_ready is armed only on a genuine STOP_OK verdict."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    _write_disarm_age(repo, "claude-stop", 120)               # aged past the 1-min interval
    tr = _tick_turn_transcript(tmp_path, "Resuming the stranded task...")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "Continue: finish the stranded task.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["decision"] == "block"        # KEEP_WORKING
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False


def test_aged_tick_tool_only_early_return_does_not_arm_teardown_ready(tmp_path, monkeypatch):
    """Regression (green review): an aged tick whose final turn is tool-only takes the tool-only
    early return before supervision, so it never reaches the STOP_OK branch and never arms
    teardown_ready (the pre-supervision path no longer writes it)."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    _write_disarm_age(repo, "claude-stop", 120)
    tr = _tick_turn_tool_only_transcript(tmp_path)
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout             # supervisor not consulted
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False


def test_flag_off_clears_both_markers(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)                          # stopContinue on; watchdog OFF
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    rl.set_watchdog_disarm(repo, "claude-stop")
    rl.set_watchdog_teardown_ready(repo, "claude-stop")
    tr = _real_turn_transcript(tmp_path, "finish X", "Done.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- done.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False


def test_stopcontinue_off_clears_the_tick_seen_marker(tmp_path, monkeypatch):
    """The nastier half of the disable story, and the one the stop hook used to miss entirely.

    With stopContinue off the tick prompt DELETES the cron itself (a missing flag file is its
    teardown trigger), so the cron is gone -- but the stop hook returned at `if not flag_active`
    before any marker clearing ran, leaving a fresh tick stamp behind. Re-enabling then looked
    'alive' and suppressed the re-arm for up to two intervals, with no cron in existence."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_watchdog_active(repo, 1)              # watchdog ON, stopContinue OFF
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    rl.set_watchdog_tick_seen(repo, "claude-stop")
    tr = _real_turn_transcript(tmp_path, "finish X", "Done.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- done.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert Path(rl.watchdog_tick_seen_file(repo, "claude-stop")).exists() is False


def test_ups_clears_the_tick_seen_marker_when_not_effective(tmp_path, monkeypatch):
    """The same hole on the prompt path: returning early without clearing would let a stamp from
    before the disable survive and suppress the re-arm after re-enabling."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_watchdog_active(repo, 1)              # watchdog ON, stopContinue OFF
    rl.set_watchdog_tick_seen(repo, "claude-ups")
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""                             # still no arm while disabled
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert Path(rl.watchdog_tick_seen_file(repo, "claude-ups")).exists() is False


def test_re_enabling_after_a_disable_re_arms_on_the_next_prompt(tmp_path, monkeypatch):
    """End to end over the disable/re-enable round trip: whatever liveness was recorded before the
    disable must not survive it, so the first prompt after re-enabling re-arms immediately rather
    than waiting out two intervals with no cron."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    rl.set_watchdog_tick_seen(repo, "claude-ups")             # cron looked alive...
    rl.clear_stopcontinue_active(repo)                        # ...then stopContinue goes off
    assert run_ups(repo, "while off", home, "claude-ups").stdout.strip() == ""
    rl.set_stopcontinue_active(repo)                          # back on
    p = run_ups(repo, "back to work", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert rl.WATCHDOG_MARKER in p.stdout


def test_flag_off_also_clears_the_tick_seen_marker(tmp_path, monkeypatch):
    """Otherwise a tick stamp from before the operator turned the watchdog off survives off/on and
    makes a cron that no longer exists look alive, suppressing the re-arm that re-enabling needs."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)                          # stopContinue on; watchdog OFF
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    rl.set_watchdog_tick_seen(repo, "claude-stop")
    tr = _real_turn_transcript(tmp_path, "finish X", "Done.")
    p = run_stop(_stop_input(repo, tr), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                 "FAKE_CODEX_STDOUT": "STOP_OK -- done.", "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert Path(rl.watchdog_tick_seen_file(repo, "claude-stop")).exists() is False


# --------------------------------------------------------------------------
# Detection helper
# --------------------------------------------------------------------------
def test_last_turn_prompt_text_finds_isMeta_tick_turnstart():
    import reviewer_transcript as rt
    events = [
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": _TICK_BODY}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": "CronList", "id": "t1", "input": {}}]}},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "ok"}]}},
    ]
    assert not any(rt._is_human_prompt(e) for e in events)     # the finding: isMeta is filtered out
    got = rt._last_turn_prompt_text(events)
    assert got.strip().startswith("stopContinueWatchdog tick")


def test_last_turn_prompt_text_returns_human_turnstart_for_a_real_turn():
    import reviewer_transcript as rt
    events = [
        {"type": "user", "message": {"role": "user", "content": "please do X"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "done"}]}},
    ]
    assert rt._last_turn_prompt_text(events).strip() == "please do X"


def test_last_turn_prompt_text_skips_sidechain_subagent_prompt():
    import reviewer_transcript as rt
    events = [
        {"type": "user", "message": {"role": "user", "content": "please do X"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "spawning a subagent"}]}},
        {"type": "user", "isSidechain": True,
            "message": {"role": "user", "content": "subagent task prompt"}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant",
            "content": [{"type": "text", "text": "subagent working"}]}},
    ]
    # The most recent user event is the sidechain prompt; the helper must skip it.
    assert rt._last_turn_prompt_text(events).strip() == "please do X"


# --------------------------------------------------------------------------
# Auto-rearm (UserPromptSubmit)
# --------------------------------------------------------------------------
def _effective(repo):
    rl.set_stopcontinue_active(repo)
    rl.set_stopcontinue_watchdog_active(repo, 1)              # interval 1 min


def test_ups_arms_when_teardown_ready_present(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    rl.set_watchdog_teardown_ready(repo, "claude-ups")
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert rl.WATCHDOG_MARKER in ctx and "*/1 * * * *" in ctx
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_teardown_ready(repo, "claude-ups") is False   # cleared


def test_ups_arms_on_disarm_age_fallback_without_teardown_ready(tmp_path, monkeypatch):
    """Closes the early-delete hole: no teardown_ready, but the disarm marker is older than one
    interval -> re-arm anyway (the reliable in-hook age fallback)."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    _write_disarm_age(repo, "claude-ups", 120)               # 2 min old >= 1 min interval
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert rl.WATCHDOG_MARKER in ctx
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is False       # cleared


def test_ups_is_silent_when_disarm_fresh_and_no_teardown_ready(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    _write_tick_seen_age(repo, "claude-ups", 5)              # ticks alive
    _write_disarm_age(repo, "claude-ups", 5)                 # fresh < interval
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""                            # no re-arm during active work
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is False       # still cleared


# --------------------------------------------------------------------------
# Tick liveness: the restart-safe re-arm path.
#
# A host restart destroys the in-memory cron. The SessionStart arm is the only thing that would
# recreate it, and on a RESUME the host does not deliver that hook's additionalContext -- measured
# 2026-09-19 -- so nothing re-arms while every flag still reports healthy. Rather than detect that
# specific host behaviour, detect the observable consequence: ticks stopped arriving. That also
# covers a hand-deleted cron, the 7-day auto-expiry and a crashed scheduler.
# --------------------------------------------------------------------------
def test_ticks_are_presumed_dead_when_no_tick_has_ever_been_seen(tmp_path, monkeypatch):
    """Absent marker must read as DEAD. This is the OPPOSITE fail direction to the disarm marker:
    there, absent must never read as 'old enough to tear down'; here, absent means no tick was ever
    observed, and re-arming redundantly is harmless while failing to re-arm is the bug."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = make_repo(tmp_path)
    _effective(repo)
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is True


def test_ticks_are_presumed_dead_when_the_marker_is_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = make_repo(tmp_path)
    _effective(repo)
    f = Path(rl.watchdog_tick_seen_file(repo, "claude-ups"))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{not json")
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is True


def test_ticks_are_alive_within_two_intervals_and_dead_beyond(tmp_path, monkeypatch):
    """Two intervals, not one: a tick only fires while the REPL is idle, so a busy session can
    legitimately miss one. Two gives that slack without letting a dead cron sit unnoticed."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = make_repo(tmp_path)
    _effective(repo)                                          # interval 1 min -> window 120s
    _write_tick_seen_age(repo, "claude-ups", 90)
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is False
    _write_tick_seen_age(repo, "claude-ups", 180)
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is True


def test_ups_stamps_tick_seen_on_a_tick_prompt_body(tmp_path, monkeypatch):
    """The tick reaches the agent THROUGH this hook, so liveness is free to observe. Still emits
    nothing -- a tick must never arm."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    p = run_ups(repo, _TICK_BODY, home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is False


def test_an_ordinary_prompt_does_not_refresh_the_tick_seen_marker(tmp_path, monkeypatch):
    """The marker must age on its own. An implementation that stamped on EVERY UserPromptSubmit
    would keep it permanently fresh, so a dead scheduler would never be noticed and the re-arm
    would never fire -- passing every other test here while defeating the entire mechanism."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)                                          # interval 1 min -> window 120s
    written = _write_tick_seen_age(repo, "claude-ups", 90)    # alive, but aging
    p = run_ups(repo, "an ordinary prompt", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""                             # alive -> no arm, so no stamp either
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert _tick_seen_ts(repo, "claude-ups") == written       # NOT reset to now
    _write_tick_seen_age(repo, "claude-ups", 180)             # and it can still go stale
    assert rl.watchdog_ticks_presumed_dead(repo, "claude-ups") is True


def test_tick_seen_is_session_scoped(tmp_path, monkeypatch):
    """Session-scoped like every other watchdog marker: one session's live cron must never make a
    different session's dead cron look alive. A project-global marker would break this."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    assert (rl.watchdog_tick_seen_file(repo, "session-a")
            != rl.watchdog_tick_seen_file(repo, "session-b"))
    rl.set_watchdog_tick_seen(repo, "session-a")
    assert rl.watchdog_ticks_presumed_dead(repo, "session-a") is False
    assert rl.watchdog_ticks_presumed_dead(repo, "session-b") is True
    # ... and the hook agrees: session B still gets its re-arm.
    p = run_ups(repo, "do the next thing", home, "session-b")
    assert rl.WATCHDOG_MARKER in p.stdout


def test_ups_arms_when_ticks_are_presumed_dead(tmp_path, monkeypatch):
    """The restart case: no markers at all, watchdog still enabled -> re-arm."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert rl.WATCHDOG_MARKER in ctx


def test_ups_arming_stamps_tick_seen_so_it_does_not_re_ask_every_prompt(tmp_path, monkeypatch):
    """Asking for an arm counts as liveness for the next window. Without this a long busy stretch
    -- which legitimately produces no ticks -- would append an arm block to every single prompt."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    first = run_ups(repo, "first", home, "claude-ups")
    assert rl.WATCHDOG_MARKER in first.stdout
    second = run_ups(repo, "second", home, "claude-ups")
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == ""


def test_ups_does_not_arm_for_dead_ticks_when_the_watchdog_is_disabled(tmp_path, monkeypatch):
    """Operator intent outranks liveness: a watchdog that is OFF stays off, however dead the
    ticks look. The effectiveness gate must be reached BEFORE the liveness check."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)                          # stopContinue on, watchdog OFF
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_ups_does_not_arm_for_dead_ticks_when_stopcontinue_is_off(tmp_path, monkeypatch):
    """The other half of `effective`: the watchdog supervises stopContinue, so with stopContinue
    off there is nothing to recover and no cron should be created."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_watchdog_active(repo, 1)              # watchdog on, stopContinue OFF
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_ups_is_silent_when_not_effective(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)                         # watchdog OFF -> not effective
    rl.set_watchdog_teardown_ready(repo, "claude-ups")
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_ups_is_silent_on_a_tick_prompt_body(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    rl.set_watchdog_teardown_ready(repo, "claude-ups")
    p = run_ups(repo, _TICK_BODY, home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


# --------------------------------------------------------------------------
# Toggle: session id from env, fail-closed
# --------------------------------------------------------------------------
def _run_toggle_on(proj, home, session_env):
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("CLAUDE_SESSION_ID", None)
    env.update(session_env)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    return subprocess.run([sys.executable, TOGGLE, "on"], capture_output=True, text=True, env=env)


def test_toggle_on_embeds_session_scoped_teardown_ready_path(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    enable_stopcontinue(repo, home)
    out = _run_toggle_on(repo, home, {"CLAUDE_CODE_SESSION_ID": "sess-XYZ"}).stdout
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert str(rl.watchdog_teardown_ready_file(repo, "sess-XYZ")) in out


def test_toggle_on_fails_closed_without_a_session_id(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    enable_stopcontinue(repo, home)
    p = _run_toggle_on(repo, home, {})                       # neither env var present
    assert p.returncode != 0
    assert "stopContinueWatchdog tick" not in p.stdout       # no arm instruction emitted


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------
def test_hooks_manifest_registers_userpromptsubmit():
    hooks = json.loads(MANIFEST.read_text()).get("hooks", {})
    assert "UserPromptSubmit" in hooks
    assert "userpromptsubmit_hook.py" in json.dumps(hooks["UserPromptSubmit"])


# --------------------------------------------------------------------------
# A tick holds while the operator has the turn (2026-09-30: ticks resumed work the operator had
# paused to read a long reply -- after an approved stop, and after an interrupt)
# --------------------------------------------------------------------------
def test_tick_prompt_holds_while_the_operator_has_the_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = make_repo(tmp_path)
    tick = rl.watchdog_tick_prompt(repo, "S-A")
    paused = str(rl.watchdog_disarm_file(repo, "S-A"))
    assert paused in tick
    low = " ".join(tick.split()).lower()
    assert "operator has the turn" in low
    assert "without doing anything else" in low
    # The pause check comes before the resume instruction, which it overrides.
    assert low.find(paused.lower()) < low.find("resume it")


def _ups_with_transcript(cwd, prompt, home, transcript, session_id="claude-ups"):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = cwd
    stdin = {"cwd": cwd, "session_id": session_id, "prompt": prompt,
             "transcript_path": transcript, "hook_event_name": "UserPromptSubmit"}
    return subprocess.run([sys.executable, UPS], input=json.dumps(stdin),
                          capture_output=True, text=True, env=env)


def _interrupted_transcript(tmp_path, after=()):
    events = [
        {"type": "user", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "Working on it."}]}},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
    ] + list(after) + [
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": _TICK_BODY}},
    ]
    return _write_jsonl(tmp_path, "interrupted.jsonl", events)


def test_a_tick_right_after_an_operator_interrupt_marks_the_pause(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    p = _ups_with_transcript(repo, _TICK_BODY, home, _interrupted_transcript(tmp_path))
    assert p.returncode == 0, p.stderr
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is True


def test_a_system_notice_after_the_interrupt_does_not_end_the_pause(tmp_path, monkeypatch):
    # A task notification is injected by the host, not typed by the operator.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    notice = [{"type": "user", "message": {"role": "user", "content":
               "<task-notification><task-id>b1</task-id><status>completed</status>"
               "</task-notification>"}}]
    p = _ups_with_transcript(repo, _TICK_BODY, home, _interrupted_transcript(tmp_path, notice))
    assert p.returncode == 0, p.stderr
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is True


def test_a_compaction_summary_or_tool_result_after_the_interrupt_does_not_end_the_pause(
        tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    synthetic = [
        {"type": "user", "isCompactSummary": True,
         "message": {"role": "user", "content": "Summary of the earlier conversation."}},
        {"type": "user", "toolUseResult": {"stdout": "x"}, "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t9", "content": "x"}]}},
    ]
    p = _ups_with_transcript(repo, _TICK_BODY, home, _interrupted_transcript(tmp_path, synthetic))
    assert p.returncode == 0, p.stderr
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is True


def test_a_tick_after_the_agent_resumed_does_not_mark_a_pause(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    resumed = [
        {"type": "user", "origin": {"kind": "human"},
         "message": {"role": "user", "content": "carry on"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "Carrying on."}]}},
    ]
    p = _ups_with_transcript(repo, _TICK_BODY, home, _interrupted_transcript(tmp_path, resumed))
    assert p.returncode == 0, p.stderr
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is False


def test_the_operators_next_message_ends_the_pause(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
    rl.set_watchdog_disarm(repo, "claude-ups")
    run_ups(repo, "ok, continue", home, "claude-ups")
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is False


def _supervisor_calls(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(
        path).exists() else []


def test_a_paused_ticks_ok_is_not_sent_to_the_supervisor(tmp_path, monkeypatch):
    # Live on the test build, 2026-09-30: the tick honoured the pause and replied `ok`, then the
    # supervisor reviewed that `ok` and pushed the agent back to work.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    rl.set_watchdog_disarm(repo, "claude-stop")
    argv = str(tmp_path / "argv.jsonl")
    p = run_stop(_stop_input(repo, _tick_turn_transcript(tmp_path, "ok")),
                 {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0",
                  "FAKE_CODEX_STDOUT": "Check the log and report.", "FAKE_CODEX_ARGV_FILE": argv})
    assert p.returncode == 0, p.stderr
    assert "decision" not in (json.loads(p.stdout) if p.stdout.strip() else {})
    assert _supervisor_calls(argv) == []


def test_a_paused_ticks_ok_still_advances_the_idle_teardown(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)                                              # interval = 1 min
    old_ts = _write_disarm_age(repo, "claude-stop", 120)      # idle for two intervals
    argv = str(tmp_path / "argv.jsonl")
    run_stop(_stop_input(repo, _tick_turn_transcript(tmp_path, "ok")),
             {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0",
              "FAKE_CODEX_STDOUT": "Check the log and report.", "FAKE_CODEX_ARGV_FILE": argv})
    assert _supervisor_calls(argv) == []
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is True
    assert _disarm_ts(repo, "claude-stop") == old_ts


def test_the_teardown_ticks_report_is_not_sent_to_the_supervisor(tmp_path, monkeypatch):
    # Live on the test build: the tick that removed the cron ended with "I deleted its cron ...",
    # not `ok`, so the supervisor reviewed it and pushed work during the pause. teardown_ready is
    # only ever set for an idle session, so its tick is not work to supervise.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    rl.set_watchdog_disarm(repo, "claude-stop")
    rl.set_watchdog_teardown_ready(repo, "claude-stop")
    argv = str(tmp_path / "argv.jsonl")
    tr = _tick_turn_transcript(tmp_path, "The teardown marker appeared, so I deleted its cron.")
    p = run_stop(_stop_input(repo, tr),
                 {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0",
                  "FAKE_CODEX_STDOUT": "Verify the retest and report.",
                  "FAKE_CODEX_ARGV_FILE": argv})
    assert p.returncode == 0, p.stderr
    assert "decision" not in (json.loads(p.stdout) if p.stdout.strip() else {})
    assert _supervisor_calls(argv) == []


def test_a_tick_that_did_more_than_reply_ok_is_still_supervised(tmp_path, monkeypatch):
    # A tick turn that resumed work is ordinary work, pause marker or not.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _armed(repo)
    rl.set_watchdog_disarm(repo, "claude-stop")
    tr = _tick_turn_transcript(tmp_path, "Resumed the migration; two steps left.")
    p = run_stop(_stop_input(repo, tr),
                 {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0",
                  "FAKE_CODEX_STDOUT": "Finish the two remaining steps."})
    assert json.loads(p.stdout)["decision"] == "block"
