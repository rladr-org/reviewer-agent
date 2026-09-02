# SPDX-License-Identifier: MPL-2.0
"""RED tests for the stopContinue pre-enactment delay + the stop-prompt hardening.

Part B (delay): a user-configurable pause applied to BOTH supervised verdicts -- a continue
directive and a stop approval -- BEFORE the decision is emitted, so the operator has a real window
to intervene. It announces before sleeping, is capped against the REMAINING Stop-hook budget
(reduced by however long the reviewer took) so it can never overrun host cancellation (which would
fail the stop open), and is skipped entirely on the fail-open paths (empty output / reviewer
failure) that produce no verdict. Part A (prompt): the supervisor classifies a polite go-ahead
request for named in-scope work as remaining work, and verifies a claimed approval requirement
rather than taking it at face value. See docs/plans/stopcontinue_delay_and_prompt_hardening.md.
"""
import json
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
HOOKS_JSON = _BASE / "hooks" / "hooks.json"

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


def _transcript(tmp_path, final_text, name="transcript.jsonl"):
    p = tmp_path / name
    p.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": final_text}]}}) + "\n")
    return str(p)


def _input(cwd, transcript_path, session_id="claude-stop"):
    return {"cwd": cwd, "session_id": session_id, "transcript_path": transcript_path,
            "hook_event_name": "Stop", "stop_hook_active": False}


# --------------------------------------------------------------------------
# Part B: configuration
# --------------------------------------------------------------------------
def test_delay_defaults_to_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raising=False)
    assert rl.stopcontinue_delay_secs() == 0


def test_delay_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "45")
    assert rl.stopcontinue_delay_secs() == 45


def test_delay_from_config_file(tmp_path, monkeypatch):
    home = tmp_path / "rh"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"stopContinueDelaySecs": 12}))
    monkeypatch.setenv("REVIEWER_HOME", str(home))
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raising=False)
    assert rl.stopcontinue_delay_secs() == 12


def test_delay_capped_at_300(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "9999")
    assert rl.stopcontinue_delay_secs() == 300


def test_delay_floored_at_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "-5")
    assert rl.stopcontinue_delay_secs() == 0


# --------------------------------------------------------------------------
# Part B: apply -- announce BEFORE sleeping, no sleep at 0, budget cap wins
# --------------------------------------------------------------------------
def test_apply_delay_zero_does_not_sleep(monkeypatch):
    import reviewer_stopcontinue as sc

    seen = []
    monkeypatch.setattr(sc, "_delay_sleep", lambda n: seen.append(n), raising=False)
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "0")
    sc.apply_stopcontinue_delay("continue")
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
    sc.apply_stopcontinue_delay("continue")
    # The heads-up must be written BEFORE the sleep -- sleeping first would deny the operator
    # any notice while still "announcing".
    assert [e[0] for e in events] == ["write", "sleep"]
    assert events[1] == ("sleep", 20)
    assert "continue" in events[0][1] and "20" in events[0][1]


def test_apply_delay_capped_by_remaining_budget(monkeypatch):
    import reviewer_stopcontinue as sc

    seen = []
    monkeypatch.setattr(sc, "_delay_sleep", lambda n: seen.append(n), raising=False)
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "300")
    sc.apply_stopcontinue_delay("stop-approval", budget_remaining=10)
    assert seen == [10]  # the remaining hook budget wins over the configured 300


def test_hook_budget_matches_hooks_json_stop_timeout():
    import reviewer_stopcontinue as sc

    hooks = json.loads(HOOKS_JSON.read_text())
    timeouts = [h["timeout"] for group in hooks["hooks"]["Stop"] for h in group["hooks"]]
    assert sc.HOOK_BUDGET_S == timeouts[0]


# --------------------------------------------------------------------------
# Part B: stop_hook applies the delay BEFORE the emit, on BOTH verdicts, passing the
# remaining budget (reduced by elapsed reviewer time), and skips it on the fail-open paths.
# --------------------------------------------------------------------------
def _run_stop(tmp_path, monkeypatch, pi_result, session_id, reviewer_secs=0.0):
    """Run stop_hook._main with a faked pi verdict under a CONTROLLED clock (the fake reviewer
    advances it by `reviewer_secs`). Record the ORDER of the delay call vs the decision emit.
    Returns (code, events): ("delay", decision, kwargs) and ("emit", kind), in occurrence order."""
    import stop_hook as h

    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    repo = make_repo(tmp_path, session_id)
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, "Still pending from HANDOVER: update the brief. Say the word.",
                     name=session_id + ".jsonl")

    clock = [1000.0]
    monkeypatch.setattr(h, "_hook_now", lambda: clock[0], raising=False)

    def fake_pi(*a, **k):
        clock[0] += reviewer_secs      # the reviewer "took" reviewer_secs seconds
        return pi_result

    monkeypatch.setattr(h.rl, "run_pi_review", fake_pi)

    events = []
    real_block, real_info = rl.stop_block_output, rl.info_output
    monkeypatch.setattr(h.rl, "apply_stopcontinue_delay",
                        lambda decision, **k: events.append(("delay", decision, k)), raising=False)
    monkeypatch.setattr(h.rl, "stop_block_output",
                        lambda reason, msg: (events.append(("emit", "block")), real_block(reason, msg))[1])
    monkeypatch.setattr(h.rl, "info_output",
                        lambda msg: (events.append(("emit", "info")), real_info(msg))[1])
    code = h._main(_input(repo, tr, session_id=session_id))
    return code, events


def _delay_kwargs(events):
    return [e for e in events if e[0] == "delay"][0][2]


def test_stop_hook_delays_before_a_continue_directive(tmp_path, monkeypatch):
    code, events = _run_stop(tmp_path, monkeypatch, (0, "Continue: update the brief.", ""), "dcont")
    assert code == 0
    assert [e[0] for e in events] == ["delay", "emit"]     # delay ENACTED before the block emit
    assert events[0][1] == "continue"
    assert events[1] == ("emit", "block")


def test_stop_hook_delays_before_a_stop_approval(tmp_path, monkeypatch):
    code, events = _run_stop(tmp_path, monkeypatch, (0, "STOP_OK", ""), "dstop")
    assert code == 0
    assert [e[0] for e in events] == ["delay", "emit"]     # delay ENACTED before the approval emit
    assert events[0][1] == "stop-approval"
    assert events[1] == ("emit", "info")


def test_stop_hook_passes_remaining_budget_reduced_by_elapsed(tmp_path, monkeypatch):
    import reviewer_stopcontinue as sc

    # A fast (0 s) reviewer leaves the full budget minus the safety margin; a 50 s reviewer leaves
    # exactly 50 s less. Pinning BOTH proves the hook passes the REMAINING budget, not the constant.
    _, fast = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dfast", reviewer_secs=0.0)
    assert _delay_kwargs(fast)["budget_remaining"] == sc.HOOK_BUDGET_S - sc.DELAY_MARGIN_S

    _, slow = _run_stop(tmp_path, monkeypatch, (0, "Continue: x.", ""), "dslow", reviewer_secs=50.0)
    assert _delay_kwargs(slow)["budget_remaining"] == sc.HOOK_BUDGET_S - 50 - sc.DELAY_MARGIN_S


def test_stop_hook_skips_delay_on_empty_verdict(tmp_path, monkeypatch):
    # Empty output is a fail-open allow with no verdict -> the stop proceeds silently, NO delay.
    code, events = _run_stop(tmp_path, monkeypatch, (0, "", ""), "dempty")
    assert code == 0
    assert [e for e in events if e[0] == "delay"] == []


def test_stop_hook_skips_delay_on_reviewer_failure(tmp_path, monkeypatch):
    # A reviewer failure is fail-open with no verdict -> the stop proceeds, NO delay.
    code, events = _run_stop(tmp_path, monkeypatch, (1, "", "boom"), "dfail")
    assert code == 0
    assert [e for e in events if e[0] == "delay"] == []


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
