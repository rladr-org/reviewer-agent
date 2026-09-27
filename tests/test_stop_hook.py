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
STOP = str(ROOT / "stop_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    return str(repo)


def _transcript(tmp_path, final_text):
    p = tmp_path / "transcript.jsonl"
    line = json.dumps(
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": final_text}]}}
    )
    p.write_text(line + "\n")
    return str(p)


def _tool_only_transcript(tmp_path):
    p = tmp_path / "transcript.jsonl"
    line = json.dumps(
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}
    )
    p.write_text(line + "\n")
    return str(p)


def _input(cwd, transcript_path, stop_hook_active=False, session_id="claude-stop"):
    return {
        "cwd": cwd,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "hook_event_name": "Stop",
        "stop_hook_active": stop_hook_active,
    }


def run_hook(stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, STOP],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


def test_inactive_produces_no_output(tmp_path):
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    tr = _transcript(tmp_path, "This is a reasonable stopping point.")
    p = run_hook(_input(repo, tr), {"REVIEWER_HOME": home})
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_tool_only_final_turn_allows_stop_without_calling_codex(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    tr = _tool_only_transcript(tmp_path)
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout


def test_premature_stop_non_question_is_continued(tmp_path, monkeypatch):
    # The key new behavior: a NON-question "reasonable stopping point" is forwarded, and a
    # keep-going directive blocks the stop.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "This is a reasonable stopping point; let me know if you want more.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Continue: wire the remaining seams and run the tests.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "wire the remaining seams" in out["reason"]
    # 2026-07-24: a blocked stop NO LONGER hides the agent's final message -- the operator
    # sees what the agent wrote before the stop was blocked. The old directive told the agent
    # its message "may NEVER be shown" and to RESTATE it, which now just produces redundant
    # repetition at the top of every continued turn. It must be gone.
    low = out["reason"].lower()
    assert "restate" not in low
    assert "never be shown" not in low
    # the forced-continuation budget was consumed
    rl_home = home
    monkeypatch.setenv("REVIEWER_HOME", rl_home)
    assert rl.read_nudge_count(repo, "claude-stop") == 1


def test_pi_backend_supervises_stop_without_starting_codex(tmp_path, monkeypatch, capsys):
    import stop_hook as h

    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    repo = make_repo(tmp_path)
    (Path(repo) / "reviewerAgent.md").write_text("strict\n")
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, "This is a reasonable stopping point.")
    calls = []

    def fake_pi(cwd, claude_sid, prompt, **kwargs):
        calls.append((cwd, claude_sid, prompt, kwargs))
        return 0, "Continue: finish the Pi-backed stop path.", ""

    monkeypatch.setattr(h.rl, "run_pi_review", fake_pi)
    monkeypatch.setattr(
        h.rl,
        "ensure_session",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Codex session started")),
    )
    monkeypatch.setattr(
        h.rl,
        "run_codex_resume_by_id",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Codex reviewer ran")),
    )

    assert h._main(_input(repo, tr, session_id="claude-pi-stop")) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "Pi-backed stop path" in out["reason"]
    assert len(calls) == 1
    cwd, claude_sid, prompt, kwargs = calls[0]
    assert cwd == repo
    assert claude_sid == "claude-pi-stop"
    assert "reasonable stopping point" in prompt
    assert kwargs["role"] == rl.ROLE_STOP
    assert kwargs["require_staged_read"] is False
    assert kwargs["thinking"] == "low"


def test_operational_decision_is_continued_not_deferred(tmp_path, monkeypatch):
    # The user is AFK: a "should I shut down the GPU?" stop must be DECIDED and continued,
    # never deferred back to the user (which would leave the instance idling).
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "The run finished. Should I shut down the GPU instance now?")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Shut down the GPU instance now; the run is complete.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "Shut down the GPU instance" in out["reason"]


def test_a_blocked_stop_is_attributed_to_the_supervisor_not_the_commit_reviewer(
    tmp_path, monkeypatch
):
    """REGRESSION, operator-reported 2026-08-02.

    The blocked-stop text read "[reviewerAgent] stopContinue -> the reviewer says keep going"
    and "reviewerAgent reviewed your stop". Both name the COMMIT REVIEWER, but the stop
    supervisor is a different codex session (`role=ROLE_STOP`) with a different history -- the
    very split `reviewer_state.py` documents after a 2026-07-31 cross-contamination blocked a
    good commit.

    The damage is not cosmetic. When a supervisor says "make these changes, then commit", an
    agent reading that as the reviewer's word believes the gate has pre-agreed to the commit.
    It has not: the audit is a separate session that has never seen this exchange, and being
    fail-closed it is the direction that rejects. So the attribution must name the supervisor,
    and must not assert that "the reviewer" said anything.
    """
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "The run finished. Should I shut down the GPU instance now?")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Trim the script, then commit all five deliverables.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"

    both = out["systemMessage"] + "\n" + out["reason"]
    # Names the actual speaker.
    assert "stopContinue supervisor" in both
    # Never claims the commit reviewer spoke, in either of the two original phrasings.
    assert "the reviewer says" not in both
    assert "reviewerAgent reviewed your stop" not in both
    # The disclaimer that USED to follow -- "...is a separate reviewer session from the commit
    # gate, and does not pre-approve any commit" -- was removed on operator instruction
    # (2026-08-02). Naming the speaker was the fix; the extra sentence was paid on every blocked
    # stop to pre-empt an inference the correct attribution already prevents. Asserted as ABSENT
    # so it cannot drift back in: the wording, not just the behaviour, is what was rejected.
    assert "does not pre-approve" not in out["reason"]
    # The relayed directive itself must still survive intact -- removing the disclaimer must not
    # take any of the supervisor's actual instruction with it.
    assert "Trim the script" in out["reason"]


def test_genuinely_done_stop_ok_allows_stop(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Done. All tests pass and the feature is shipped.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "STOP_OK -- the task is genuinely complete.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0
    # Spec change (REVIEWER_OUTPUT_ECHO_PLAN.md): the stop is still ALLOWED (no block
    # decision), but the reviewer's STOP_OK verdict is now echoed to the terminal.
    out = json.loads(p.stdout)
    assert "decision" not in out               # allowed to stop
    assert "STOP_OK -- the task is genuinely complete." in out["systemMessage"]


def test_codex_failure_fails_open_allows_stop(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Stopping here.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDERR": "boom", "FAKE_CODEX_EXIT": "3"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_nudge_cap_reached_allows_stop_without_calling_codex(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    rl.bump_nudge_count(repo, "claude-stop")
    rl.bump_nudge_count(repo, "claude-stop")  # used == 2 == cap
    tr = _transcript(tmp_path, "Still more to do, but pausing.")
    p = run_hook(
        _input(repo, tr, stop_hook_active=True),  # mid-chain, so the budget is NOT reset
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "REVIEWER_STOPCONTINUE_MAX_NUDGES": "2",
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"},
    )
    assert p.returncode == 0
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout      # codex not called past the cap
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    assert out.get("decision") != "block"              # allowed to stop


def _log_text(home):
    p = Path(home) / "logs" / "reviewer.log"
    return p.read_text() if p.exists() else ""


def test_enter_line_is_logged_even_when_inactive(tmp_path):
    # Diagnostic: EVERY invocation must leave a trace so "hook not invoked" (no line at all)
    # is distinguishable from "invoked but stopContinue is off" (an ENTER line, flag=off).
    home = str(tmp_path / "rh")
    repo = make_repo(tmp_path)
    tr = _transcript(tmp_path, "This is a reasonable stopping point.")
    p = run_hook(_input(repo, tr), {"REVIEWER_HOME": home})
    assert p.returncode == 0
    assert p.stdout.strip() == ""            # unchanged: inactive still emits nothing to stdout
    t = _log_text(home)
    assert "[stop] ENTER" in t
    assert "flag=off" in t


def test_enter_line_is_logged_when_active_and_fires(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Reasonable place to pause.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Continue: finish the job.", "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    t = _log_text(home)
    assert "[stop] ENTER" in t
    assert "flag=on" in t
    assert "text_len=" in t


def test_uncaught_exception_is_logged_and_fails_open(tmp_path, monkeypatch):
    # A hook error must NEVER wedge the agent: it fails open (return 0) AND leaves a visible
    # EXCEPTION line instead of a silent crash that reads as "the hook never fired".
    import io
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, "some final text")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_input(repo, tr))))
    import stop_hook

    def boom(*a, **k):
        raise RuntimeError("kaboom-sentinel")

    monkeypatch.setattr(stop_hook.rl, "ensure_session", boom)
    rc = stop_hook.main()
    assert rc == 0                            # fail-open
    t = _log_text(home)
    assert "EXCEPTION" in t
    assert "kaboom-sentinel" in t


def test_fresh_user_turn_resets_the_nudge_budget(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    for _ in range(9):
        rl.bump_nudge_count(repo, "claude-stop")            # near a default cap of 10
    tr = _transcript(tmp_path, "Reasonable place to pause.")
    p = run_hook(
        _input(repo, tr, stop_hook_active=False),     # FRESH user turn -> budget resets to 0
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Continue: finish the migration.", "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"                  # reset -> budget available -> nudged
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.read_nudge_count(repo, "claude-stop") == 1     # reset then one bump


# --------------------------------------------------------------------------
# Background-wait handling (pure suppress): when the agent parked its turn on a
# harness-tracked background task, allow the stop immediately with NO supervisor
# call and NO nudge -- the harness auto-re-invokes the session on completion.
# --------------------------------------------------------------------------
def _bg_wait_transcript(tmp_path, final_text, completed=False):
    events = [
        {"type": "user", "message": {"role": "user", "content": "kick off the run"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Agent", "id": "toolu_BG",
             "input": {"description": "d", "subagent_type": "Explore", "prompt": "p",
                       "run_in_background": True}}]}},
    ]
    if completed:
        events.append({"type": "user", "message": {"role": "user", "content":
            "<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>toolu_BG"
            "</tool-use-id>\n<status>completed</status>\n</task-notification>"}})
    events.append({"type": "assistant", "message": {"role": "assistant",
        "content": [{"type": "text", "text": final_text}]}})
    p = tmp_path / "bgwait.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(p)


def test_background_wait_pure_suppresses_without_supervisor(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _bg_wait_transcript(tmp_path, "Launched the run in the background; waiting on it.")
    dump = str(tmp_path / "prompt.txt")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_PROMPT_DUMP": dump,
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN", "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""                        # parked -> stop allowed
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout         # supervisor NOT invoked...
    assert not Path(dump).exists()                        # ...the fake codex never ran
    assert "background wait" in _log_text(home)           # suppression is logged
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.read_nudge_count(repo, "claude-stop") == 0        # budget untouched


def test_after_harness_wake_scoping_restores_normal_supervision(tmp_path, monkeypatch):
    # A completed background launch (task-notification) resets the turn scope: bg_wait is
    # False, so the stop is supervised NORMALLY -- proving a long autonomous session is not
    # muted after the harness wakes it on completion.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _bg_wait_transcript(tmp_path, "The run finished. Reasonable place to pause.",
                             completed=True)
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "Run the frozen eval and commit the results.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"                    # normal supervision still active
    assert "frozen eval" in out["reason"]


# --------------------------------------------------------------------------
# Trailing STOP_OK (docs/plans/stopcontinue_trailing_stop_ok.md). Observed: the supervisor
# reasoned first and put STOP_OK last; the first-line-only parser read it as a continue
# directive and nudged a finished task six times. The verdict position is logged so a
# contradictory (mid-reply) sentinel is visible rather than silently inverted.
# --------------------------------------------------------------------------
_REASONED_STOP_OK = (
    "The operator's stopping condition (\"all ten boxes ticked and placeholder text gone\") is "
    "fully met, and there is no authorized work left to do.\n\nSTOP_OK"
)


def test_trailing_stop_ok_allows_stop_and_logs_position(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "All ten boxes are ticked and the placeholder text is gone.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": _REASONED_STOP_OK, "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert "decision" not in out                       # allowed to stop
    assert "approved this stop" in out["systemMessage"]
    assert rl.read_nudge_count(repo, "claude-stop") == 0   # no forced continuation was spent
    t = _log_text(home)
    assert "keep_going=False" in t
    assert "stop_ok=last" in t


def test_mid_reply_stop_ok_still_continues_but_is_logged_as_mid(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Done, I think.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "The list is complete.\n\nSTOP_OK\n\nHowever, run the linter first.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"                  # contradictory reply resolves to continue
    t = _log_text(home)
    assert "keep_going=True" in t
    assert "stop_ok=mid" in t


# --------------------------------------------------------------------------
# The commit gate's verdict word is not a stop verdict (docs/plans/claude_linux_issues.md, Task
# C2.1). Observed 2026-09-26: framed as the commit auditor, the supervisor answered `APPROVED`
# three stops running, and each was relayed as "NOT yet complete ... Do the following: APPROVED".
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reply", ["APPROVED", "APPROVED\nSUGGESTIONS:\n1. rename x"])
def test_a_commit_gate_approval_is_not_relayed_as_a_directive(tmp_path, monkeypatch, reply):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.set_stopcontinue_watchdog_active(repo)  # only a real STOP_OK may arm its idle countdown
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Phases A-C are committed and the suite passes.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": reply, "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert "decision" not in out                            # the stop is allowed
    assert "commit gate's verdict" in out["systemMessage"]  # and the operator is told why
    assert rl.read_nudge_count(repo, "claude-stop") == 0    # no forced continuation was spent
    # Not a completion verdict, so the watchdog stays armed to recover the strand.
    assert rl.is_watchdog_disarm_set(repo, "claude-stop") is False
    assert rl.is_watchdog_teardown_ready(repo, "claude-stop") is False
    t = _log_text(home)
    assert "keep_going=False" in t
    assert "stop_ok=gate" in t


def test_a_commit_gate_rejection_is_still_a_directive(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(tmp_path, "Done.")
    p = run_hook(
        _input(repo, tr),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "REJECTED:\n1. The README still names the old flag.",
         "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "README still names the old flag" in out["reason"]


def test_the_supervisor_judges_the_payloads_final_message(tmp_path, monkeypatch):
    # Task 10 (#17): the transcript still ends on the opening paragraph and a thinking block;
    # Claude Code's `last_assistant_message` is the real final message and must reach the prompt.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = tmp_path / "transcript.jsonl"
    tr.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Running everything now"}]}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."}]}}) + "\n")
    argv_file = tmp_path / "argv.jsonl"  # the stop prompt travels as a resume argument
    payload = dict(_input(repo, str(tr)), last_assistant_message="Summary: all probes done")
    p = run_hook(
        payload,
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_STDOUT": "STOP_OK",
         "FAKE_CODEX_EXIT": "0", "FAKE_CODEX_ARGV_FILE": str(argv_file)},
    )
    assert p.returncode == 0, p.stderr
    assert "Summary: all probes done" in argv_file.read_text()


def test_the_stop_capsule_is_not_the_commit_reviewers(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = make_repo(tmp_path)
    stop = rl.apply_context("TASK", repo, role=rl.ROLE_STOP)
    assert "find bugs" not in stop
    assert "never block a commit" not in stop
    assert "NOT a commit audit" in stop
    assert "STOP_OK" in stop
    # The audit command is named only as something NOT to run.
    assert "do not run `git diff --cached`" in stop
    assert stop.count("git diff --cached") == 1
    # The reviewer role's capsule is unchanged.
    assert "REVIEWER STANDING REMINDER" in rl.apply_context("TASK", repo)
