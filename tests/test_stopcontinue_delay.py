# SPDX-License-Identifier: MPL-2.0
"""The stopContinue reading delay + the stop-prompt hardening.

Part B (delay): before the supervisor reviews a stop, the hook waits (default 60 s) so the
operator can read the agent's reply first. A reply typed during the wait skips the review, so no
reviewer tokens are spent on a turn the operator has already taken. The wait is capped so the full
review still fits in the Stop-hook budget, and it is skipped on every path that needs no review.
`/reviewer-agent:stopContinue delay` sets it. Part A (prompt): the supervisor classifies a polite
go-ahead request for named in-scope work as remaining work, and verifies a claimed approval
requirement rather than taking it at face value. See docs/plans/stopcontinue_reading_delay.md and
docs/plans/stopcontinue_delay_and_prompt_hardening.md.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
HOOKS_JSON = _BASE / "hooks" / "hooks.json"
SKILL = _BASE / "skills" / "stopContinue" / "SKILL.md"

# The five polite go-ahead phrasings the hardened prompt must name as remaining work. "i'll do
# that next" is the generic form of the observed "...I'll do the brief next" case.
GO_AHEAD_PHRASINGS = ("say the word", "shall i", "let me know", "i'll do that next", "ready when you are")
# The phrasings must sit INSIDE the classification clause, not merely occur somewhere.
CLASSIFICATION = "ready when you are) is authorized remaining work, not by itself a request for operator input"


def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    return str(repo)


def _transcript(tmp_path, final_text, name="transcript.jsonl", extra=()):
    p = tmp_path / name
    events = [{"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": final_text}]}}] + list(extra)
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(p)


def _input(cwd, transcript_path, session_id="claude-stop", stop_hook_active=False):
    return {"cwd": cwd, "session_id": session_id, "transcript_path": transcript_path,
            "hook_event_name": "Stop", "stop_hook_active": stop_hook_active}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated reviewer home with no config file and no delay override."""
    h = tmp_path / "rh"
    h.mkdir()
    monkeypatch.setenv("REVIEWER_HOME", str(h))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(h / "config.json"))
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raising=False)
    return h


# --------------------------------------------------------------------------
# Part B: configuration -- env > state file (the command) > config.json > default 60
# --------------------------------------------------------------------------
def test_delay_defaults_to_sixty(home):
    assert rl.stopcontinue_delay_detail() == (60, "default")
    assert rl.stopcontinue_delay_secs() == 60


def test_delay_from_config_file(home):
    (home / "config.json").write_text(json.dumps({"stopContinueDelaySecs": 12}))
    assert rl.stopcontinue_delay_secs() == 12


def test_saved_delay_beats_config(home):
    (home / "config.json").write_text(json.dumps({"stopContinueDelaySecs": 12}))
    rl.set_stopcontinue_delay(20)
    seconds, source = rl.stopcontinue_delay_detail()
    assert seconds == 20 and source == str(rl.stopcontinue_delay_file())


def test_saved_zero_turns_the_delay_off(home):
    rl.set_stopcontinue_delay(0)
    assert rl.stopcontinue_delay_secs() == 0


def test_env_beats_saved_delay(home, monkeypatch):
    rl.set_stopcontinue_delay(20)
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "45")
    assert rl.stopcontinue_delay_detail() == (45, "env REVIEWER_STOPCONTINUE_DELAY_SECS")


def test_clearing_the_saved_delay_restores_the_default(home):
    rl.set_stopcontinue_delay(20)
    rl.clear_stopcontinue_delay()
    assert rl.stopcontinue_delay_detail() == (60, "default")


def test_unusable_values_are_skipped(home, monkeypatch):
    (home / "config.json").write_text(json.dumps({"stopContinueDelaySecs": "soon"}))
    rl.stopcontinue_delay_file().parent.mkdir(parents=True, exist_ok=True)
    rl.stopcontinue_delay_file().write_text("abc\n")
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "later")
    assert rl.stopcontinue_delay_detail() == (60, "default")


@pytest.mark.parametrize("raw,expected", [("9999", 300), ("-5", 0)])
def test_delay_clamped_to_0_through_300(home, monkeypatch, raw, expected):
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raw)
    assert rl.stopcontinue_delay_secs() == expected


# --------------------------------------------------------------------------
# Part B: apply -- announce BEFORE sleeping, no sleep at 0, budget cap wins
# --------------------------------------------------------------------------
def test_apply_delay_zero_does_not_sleep(monkeypatch):
    import reviewer_stopcontinue as sc

    seen = []
    monkeypatch.setattr(sc, "_delay_sleep", lambda n: seen.append(n), raising=False)
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "0")
    assert sc.apply_stopcontinue_delay() is False
    assert seen == []


def test_apply_delay_announces_before_sleeping(monkeypatch):
    import reviewer_stopcontinue as sc

    events = []

    class _RecStderr:
        def write(self, s):
            if s.strip():
                events.append(("write", s))

        def flush(self):
            pass

    monkeypatch.setattr(sc, "_delay_sleep", lambda n: events.append(("sleep", n)), raising=False)
    monkeypatch.setattr(sys, "stderr", _RecStderr())
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "20")
    sc.apply_stopcontinue_delay()
    # The heads-up must be written BEFORE the sleep -- sleeping first would deny the operator
    # any notice while still "announcing".
    assert [e[0] for e in events] == ["write", "sleep"]
    assert events[1] == ("sleep", 20)
    assert "supervisor review in 20s" in events[0][1]


def test_apply_delay_capped_by_remaining_budget(monkeypatch):
    import reviewer_stopcontinue as sc

    seen = []
    monkeypatch.setattr(sc, "_delay_sleep", lambda n: seen.append(n), raising=False)
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "300")
    sc.apply_stopcontinue_delay(budget_remaining=10)
    assert seen == [10]  # the remaining hook budget wins over the configured 300


def test_hook_budget_matches_hooks_json_stop_timeout():
    import reviewer_stopcontinue as sc

    hooks = json.loads(HOOKS_JSON.read_text())
    timeouts = [h["timeout"] for group in hooks["hooks"]["Stop"] for h in group["hooks"]]
    assert sc.HOOK_BUDGET_S == timeouts[0]


def test_the_spinner_says_the_stop_is_waiting_and_a_reply_skips_it():
    # Claude Code does not show a Stop hook's stderr, so the heads-up never reached the operator:
    # a 60 s wait looked exactly like a hung "running Stop hook" (2026-10-17). The hook's
    # statusMessage replaces that spinner text.
    hooks = json.loads(HOOKS_JSON.read_text())
    (stop,) = [h for group in hooks["hooks"]["Stop"] for h in group["hooks"]]
    message = stop.get("statusMessage", "")
    assert message.startswith("stopContinue:")
    assert "reading delay" in message and "reply to skip" in message


def test_hook_budget_fits_the_longest_delay_and_review():
    import reviewer_stopcontinue as sc
    import stop_hook as h

    assert sc.HOOK_BUDGET_S >= sc.DELAY_MAX_S + h.STOP_TIMEOUT + sc.DELAY_MARGIN_S


# --------------------------------------------------------------------------
# Part B: stop_hook waits BEFORE calling the supervisor, never after its verdict, passes the
# budget left for the review, and skips the wait on every path that needs no review.
# --------------------------------------------------------------------------
def _run_stop(tmp_path, monkeypatch, pi_result, session_id, entry_to_delay_secs=0.0,
              extra_events=(), stop_hook_active=False, prepare=None):
    """Run stop_hook._main with a faked pi verdict under a CONTROLLED clock: the first clock read
    is hook entry, every later read is `entry_to_delay_secs` after it. Records, in order,
    ("delay", kwargs), ("review",) and ("emit", kind)."""
    import stop_hook as h

    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    repo = make_repo(tmp_path, session_id)
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, "Still pending from HANDOVER: update the brief. Say the word.",
                     name=session_id + ".jsonl", extra=extra_events)
    if prepare:
        prepare(repo, session_id)

    reads = []

    def now():
        reads.append(1)
        return 1000.0 if len(reads) == 1 else 1000.0 + entry_to_delay_secs

    monkeypatch.setattr(h, "_hook_now", now, raising=False)

    events = []

    def fake_pi(*a, **k):
        events.append(("review",))
        return pi_result

    monkeypatch.setattr(h.rl, "run_pi_review", fake_pi)
    real_block, real_info = rl.stop_block_output, rl.info_output
    monkeypatch.setattr(h.rl, "apply_stopcontinue_delay",
                        lambda **k: events.append(("delay", k)) or False, raising=False)
    monkeypatch.setattr(h.rl, "stop_block_output",
                        lambda reason, msg: (events.append(("emit", "block")), real_block(reason, msg))[1])
    monkeypatch.setattr(h.rl, "info_output",
                        lambda msg: (events.append(("emit", "info")), real_info(msg))[1])
    code = h._main(_input(repo, tr, session_id=session_id, stop_hook_active=stop_hook_active))
    return code, events


def _kinds(events):
    return [e[0] for e in events]


def test_stop_hook_waits_before_reviewing_a_continue(tmp_path, monkeypatch):
    code, events = _run_stop(tmp_path, monkeypatch, (0, "Continue: update the brief.", ""), "dcont")
    assert code == 0
    assert _kinds(events) == ["delay", "review", "emit"]
    assert events[-1] == ("emit", "block")


def test_stop_hook_waits_before_reviewing_an_approval_and_not_after(tmp_path, monkeypatch):
    code, events = _run_stop(tmp_path, monkeypatch, (0, "STOP_OK", ""), "dstop")
    assert code == 0
    assert _kinds(events) == ["delay", "review", "emit"]  # nothing holds the approval back
    assert events[-1] == ("emit", "info")


def test_stop_hook_leaves_the_review_its_full_timeout(tmp_path, monkeypatch):
    import reviewer_stopcontinue as sc
    import stop_hook as h

    _, fast = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dfast")
    assert fast[0][1]["budget_remaining"] == sc.HOOK_BUDGET_S - h.STOP_TIMEOUT - sc.DELAY_MARGIN_S
    _, slow = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dslow",
                        entry_to_delay_secs=7.0)
    assert slow[0][1]["budget_remaining"] == (
        sc.HOOK_BUDGET_S - 7 - h.STOP_TIMEOUT - sc.DELAY_MARGIN_S)
    assert callable(slow[0][1]["should_cancel"])


def test_stop_hook_skips_the_wait_when_a_reply_is_already_queued(tmp_path, monkeypatch):
    queued = {"type": "queue-operation", "operation": "enqueue",
              "timestamp": "2026-10-17T12:00:00.000Z", "sessionId": "dq", "content": "yes"}
    code, events = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dq",
                             extra_events=[queued])
    assert code == 0
    assert events == [("emit", "info")]  # the operator-reply notice only


def test_stop_hook_skips_the_wait_at_the_nudge_cap(tmp_path, monkeypatch):
    def at_cap(repo, sid):
        for _ in range(rl.max_nudges()):
            rl.bump_nudge_count(repo, sid)

    code, events = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dcap",
                             stop_hook_active=True, prepare=at_cap)
    assert code == 0
    assert "delay" not in _kinds(events) and "review" not in _kinds(events)


def test_stop_hook_skips_the_wait_when_stopcontinue_is_off(tmp_path, monkeypatch):
    def off(repo, sid):
        rl.clear_stopcontinue_active(repo)

    code, events = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "doff", prepare=off)
    assert code == 0
    assert events == []


def test_a_reply_during_the_wait_skips_the_review(tmp_path, monkeypatch, capsys):
    import stop_hook as h

    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    repo = make_repo(tmp_path, "dwait")
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, "Shall I go ahead with A?", name="dwait.jsonl")
    reviews = []
    monkeypatch.setattr(h.rl, "run_pi_review", lambda *a, **k: reviews.append(1) or (0, "x", ""))
    monkeypatch.setattr(h.rl, "apply_stopcontinue_delay", lambda **k: True, raising=False)
    assert h._main(_input(repo, tr, session_id="dwait")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("decision") != "block"
    assert "during the reading delay" in out["systemMessage"]
    assert reviews == []


# --------------------------------------------------------------------------
# Part B: /reviewer-agent:stopContinue delay [<0..300>|status|clear]
# --------------------------------------------------------------------------
def _delay_cmd(capsys, *args):
    import reviewer_cmd

    code = reviewer_cmd.main(["reviewer_cmd.py", "stopcontinue", " ".join(("delay",) + args)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize("args", [(), ("status",)])
def test_delay_command_reports_the_default(home, capsys, args):
    code, out, _ = _delay_cmd(capsys, *args)
    assert code == 0
    assert out.startswith("stopContinue: delay 60 seconds (from default")


def test_delay_command_saves_and_clears(home, capsys):
    code, out, _ = _delay_cmd(capsys, "30")
    assert code == 0 and "stopContinue: delay 30 seconds" in out
    assert rl.stopcontinue_delay_secs() == 30
    assert "delay 30 seconds" in _delay_cmd(capsys)[1]
    code, out, _ = _delay_cmd(capsys, "0")
    assert code == 0 and rl.stopcontinue_delay_secs() == 0
    code, out, _ = _delay_cmd(capsys, "clear")
    assert code == 0 and "delay 60 seconds (from default" in out
    assert not rl.stopcontinue_delay_file().exists()


def test_delay_command_does_not_turn_stopcontinue_on(home, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", repo)
    _delay_cmd(capsys, "30")
    assert not rl.is_stopcontinue_active(repo)


def test_delay_command_names_an_environment_override(home, monkeypatch, capsys):
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "5")
    code, out, _ = _delay_cmd(capsys, "30")
    assert code == 0
    assert "delay 5 seconds" in out and "REVIEWER_STOPCONTINUE_DELAY_SECS" in out
    assert rl.stopcontinue_delay_file().read_text().strip() == "30"


@pytest.mark.parametrize("args", [("-1",), ("301",), ("1.5",), ("oops",), ("30", "extra")])
def test_invalid_delay_command_changes_nothing(home, capsys, args):
    code, out, err = _delay_cmd(capsys, *args)
    assert code == 2
    assert "stopContinue: ERROR" in err
    assert not rl.stopcontinue_delay_file().exists()


def test_skill_documents_the_delay_command():
    text = SKILL.read_text()
    head = text.split("---", 2)[1]
    assert "delay" in head  # description and argument hint
    assert "stopContinue: delay" in text  # the relay instruction names its output


# --------------------------------------------------------------------------
# Part A: the Claude Code stop prompt is hardened (parity pinned in the codex suite too)
# --------------------------------------------------------------------------
def test_stop_prompt_classifies_go_ahead_requests_as_remaining_work():
    norm = " ".join(rl.STOP_SUPERVISE_PROMPT.split()).lower()
    for phrase in GO_AHEAD_PHRASINGS:
        assert phrase in norm, phrase
    assert CLASSIFICATION in norm     # the phrasings sit INSIDE the classification clause


def test_stop_prompt_requires_verifying_a_claimed_approval():
    norm = " ".join(rl.STOP_SUPERVISE_PROMPT.split()).lower()
    assert "do not take that claim at face value" in norm
    assert "genuinely needs operator authorization" in norm
    assert "not already granted" in norm


def test_stop_prompt_prefers_known_tasks_with_generic_fallback():
    norm = " ".join(rl.STOP_SUPERVISE_PROMPT.split()).lower()
    assert "name the specific unfinished work" in norm
    assert "cannot identify the unfinished tasks" in norm
    assert "do not invent tasks" in norm
    assert "quoted transcripts" in norm
