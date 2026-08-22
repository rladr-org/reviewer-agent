# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
HOOK = str(ROOT / "askq_answer_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

PROJ = "/Users/x/proj"

TOOL_INPUT = {
    "questions": [
        {
            "question": "Pick one?",
            "header": "H",
            "options": [
                {"label": "A", "description": "aa"},
                {"label": "B", "description": "bb"},
            ],
        }
    ]
}

# --- REAL tool_response shapes captured from a live AskUserQuestion on 2026-07-24.
# --- These are the ground truth the detector keys off; do not "simplify"
# --- them into invented shapes.
ANSWERED_RESPONSE = {
    "questions": TOOL_INPUT["questions"],
    "answers": {"Pick one?": "it didn't time out"},
    "annotations": {},
}
TIMEOUT_RESPONSE = {
    "questions": TOOL_INPUT["questions"],
    "answers": {},
    "annotations": {},
    "afkTimeoutMs": 60000,
}


def _event(tool_response, cwd=PROJ, session_id="claude-Q"):
    return {
        "cwd": cwd,
        "session_id": session_id,
        "tool_name": "AskUserQuestion",
        "hook_event_name": "PostToolUse",
        "tool_input": TOOL_INPUT,
        "tool_response": tool_response,
    }


def run_hook(stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


# --------------------------------------------------------------------------
# detector unit tests -- keyed on the REAL captured shapes, not invented ones
# --------------------------------------------------------------------------
def test_operator_answered_recognizes_real_answered_shape():
    import askq_answer_hook as h

    assert h._operator_answered(ANSWERED_RESPONSE) is True
    assert h._afk_timed_out(ANSWERED_RESPONSE) is False


def test_afk_timeout_recognizes_real_timeout_shape():
    import askq_answer_hook as h

    assert h._afk_timed_out(TIMEOUT_RESPONSE) is True
    assert h._operator_answered(TIMEOUT_RESPONSE) is False


def test_reviewer_answers_only_on_genuine_timeout():
    import askq_answer_hook as h

    # the two real shapes
    assert h._should_reviewer_answer(TIMEOUT_RESPONSE) is True
    assert h._should_reviewer_answer(ANSWERED_RESPONSE) is False
    # empty answers WITHOUT the afk marker is not a timeout -> reviewer stays out
    assert h._should_reviewer_answer({"answers": {}, "annotations": {}}) is False
    # afk marker but a selection is present (operator pre-selected then left) -> operator wins
    assert (
        h._should_reviewer_answer({"answers": {"Pick one?": "A"}, "afkTimeoutMs": 60000})
        is False
    )
    # non-dict / missing responses never trigger an answer
    assert h._should_reviewer_answer("weird") is False
    assert h._should_reviewer_answer(None) is False


def test_response_blob_handles_str_dict_and_missing():
    import askq_answer_hook as h

    assert h._response_blob({"tool_response": "hello"}) == "hello"
    assert "sel" in h._response_blob({"tool_response": {"sel": "A"}})
    assert "away" in h._response_blob({"tool_result": {"note": "user away"}})  # fallback key
    assert h._response_blob({}) == "<none>"


# --------------------------------------------------------------------------
# answering-path integration tests (fake codex stands in for the reviewer)
# --------------------------------------------------------------------------
def test_timeout_active_injects_reviewer_answer(tmp_path, monkeypatch):
    # Genuine timeout + reviewer active -> the reviewer answers, delivered via
    # additionalContext (NEVER a deny, so nothing the agent printed is hidden).
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_active(PROJ)
    rl.write_session_id(PROJ, "claude-Q", "sid-q")  # SessionStart already captured the reviewer
    p = run_hook(
        _event(TIMEOUT_RESPONSE),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDOUT": "Use option A.",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "Use option A." in hso["additionalContext"]
    assert "permissionDecision" not in hso  # never a deny -> output not hidden
    # echoes the exchange so the operator can see what was asked and answered
    assert "Pick one?" in out["systemMessage"]
    assert "Use option A." in out["systemMessage"]


def test_timeout_routes_to_pi_without_starting_codex(tmp_path, monkeypatch, capsys):
    import askq_answer_hook as h

    home = str(tmp_path / "rh")
    repo = tmp_path / "repo-pi"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    rl.set_active(str(repo))
    calls = []

    def fake_pi(cwd, claude_sid, prompt, **kwargs):
        calls.append((cwd, claude_sid, prompt, kwargs))
        return 0, "Use option B.", ""

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

    assert h._main(_event(TIMEOUT_RESPONSE, cwd=str(repo), session_id="claude-pi")) == 0

    out = json.loads(capsys.readouterr().out)
    assert "Use option B." in out["hookSpecificOutput"]["additionalContext"]
    assert len(calls) == 1
    cwd, claude_sid, prompt, kwargs = calls[0]
    assert cwd == str(repo)
    assert claude_sid == "claude-pi"
    assert "Pick one?" in prompt
    assert kwargs["role"] == rl.ROLE_REVIEWER
    assert kwargs["require_staged_read"] is False
    assert kwargs["thinking"] == rl.reviewer_effort("pi")


def test_timeout_routes_to_claude_without_starting_codex_or_pi(tmp_path, monkeypatch, capsys):
    """The claude backend answers timed-out questions too, and reaches neither other backend.

    Same guarantee the pi test above pins, and it has to be pinned separately: the hook branches
    per backend, so a claude project falling through to the codex branch would silently start a
    Codex session on a machine that may not have one."""
    import askq_answer_hook as h

    home = str(tmp_path / "rh")
    repo = tmp_path / "repo-claude"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_BACKEND", "claude")
    rl.set_active(str(repo))
    calls = []

    def fake_claude(cwd, claude_sid, prompt, **kwargs):
        calls.append((cwd, claude_sid, prompt, kwargs))
        return 0, "Use option C.", ""

    monkeypatch.setattr(h.rl, "run_claude_review", fake_claude)
    for name in ("ensure_session", "run_codex_resume_by_id", "run_pi_review"):
        monkeypatch.setattr(
            h.rl, name,
            lambda *_a, _n=name, **_k: (_ for _ in ()).throw(
                AssertionError("{} was called for a claude project".format(_n))),
        )

    assert h._main(_event(TIMEOUT_RESPONSE, cwd=str(repo), session_id="claude-c")) == 0

    out = json.loads(capsys.readouterr().out)
    assert "Use option C." in out["hookSpecificOutput"]["additionalContext"]
    assert len(calls) == 1
    cwd, claude_sid, prompt, kwargs = calls[0]
    assert cwd == str(repo)
    assert claude_sid == "claude-c"
    assert "Pick one?" in prompt
    assert kwargs["role"] == rl.ROLE_REVIEWER
    assert kwargs["effort"] == rl.reviewer_effort("claude")


def test_a_usage_limit_on_the_question_path_says_so_and_lets_the_turn_proceed(
    tmp_path, monkeypatch, capsys
):
    """Fail-OPEN, but not silent. An exhausted allowance costs an answer rather than a commit, and
    the operator should not have to wait for a blocked commit to learn which control to reach
    for -- the same argument that put the diagnosis on the model-refusal path."""
    import askq_answer_hook as h

    home = str(tmp_path / "rh")
    repo = tmp_path / "repo-claude-limit"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_BACKEND", "claude")
    rl.set_active(str(repo))
    monkeypatch.setattr(
        h.rl, "run_claude_review",
        lambda *_a, **_k: (1, "", "the Claude usage limit was reached, so the review could not "
                                 "run: Claude usage limit reached. Resets at 8pm."),
    )

    assert h._main(_event(TIMEOUT_RESPONSE, cwd=str(repo), session_id="claude-lim")) == 0

    out = json.loads(capsys.readouterr().out)
    # No answer was injected -- the agent proceeds on its own judgment.
    assert "additionalContext" not in json.dumps(out.get("hookSpecificOutput") or {})
    message = json.dumps(out)
    assert "usage limit" in message.lower()
    assert "backend" in message.lower() or "/reviewerAgent off" in message


def test_answered_active_stays_out_of_the_way(tmp_path, monkeypatch):
    # The operator answered -> the reviewer must NOT inject anything, even when active.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_active(PROJ)
    rl.write_session_id(PROJ, "claude-Q", "sid-q")
    p = run_hook(
        _event(ANSWERED_RESPONSE),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDOUT": "Use option A.",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_timeout_inactive_only_captures(tmp_path):
    # Reviewer OFF for the project: even on a genuine timeout the hook must not answer
    # (the is_active gate). It still captures the raw response to the log.
    home = str(tmp_path / "rh")
    p = run_hook(_event(TIMEOUT_RESPONSE), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""
    logfile = Path(home) / "logs" / "reviewer.log"
    assert logfile.exists(), "capture hook did not write a log"
    assert "[askq-capture]" in logfile.read_text()


def test_timeout_active_codex_failure_surfaces_error(tmp_path, monkeypatch):
    # Timeout + active but the reviewer resume fails: inject nothing (fail safe -> Claude
    # proceeds on its own judgment, exactly as the afk timeout already told it) but make the
    # failure visible.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_active(PROJ)
    rl.write_session_id(PROJ, "claude-Q", "sid-q")
    p = run_hook(
        _event(TIMEOUT_RESPONSE),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDERR": "kaboom",
            "FAKE_CODEX_EXIT": "2",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert "hookSpecificOutput" not in out  # nothing injected as an answer
    assert "ERROR" in out["systemMessage"]  # but the failure is surfaced


def test_capture_logs_the_raw_tool_response(tmp_path):
    # Every invocation captures the exact tool_response so the answered-vs-timeout signal
    # stays verifiable against reality.
    home = str(tmp_path / "rh")
    marker = "SENTINEL_RESPONSE_SHAPE_42"
    p = run_hook(_event({"weird": marker, "answers": {}}), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    logfile = Path(home) / "logs" / "reviewer.log"
    text = logfile.read_text()
    assert "[askq-capture]" in text
    assert marker in text  # the raw response body was captured
