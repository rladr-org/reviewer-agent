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
    _write_disarm_age(repo, "claude-ups", 5)                 # fresh < interval
    p = run_ups(repo, "do the next thing", home, "claude-ups")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""                            # no re-arm during active work
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.is_watchdog_disarm_set(repo, "claude-ups") is False       # still cleared


def test_ups_is_silent_when_no_markers(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    _effective(repo)
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
