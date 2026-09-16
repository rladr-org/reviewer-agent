# SPDX-License-Identifier: MPL-2.0
"""Native invocation context reaches the reviewer and invalidates old sessions.

These tests check wiring, not model judgment; the opt-in live checks exercise the
actual reviewer against incomplete, complete, cancelled, and quoted work.
"""
from reviewer_codex import backend, mcp_server, prompts, state, supervisor


def test_native_hook_identifies_the_active_review_and_preserves_task_evidence(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    request = "Calculate 17 times 19. First attempt to stop before calculating."
    state.record_prompt(git_repo, "native-session", request)
    seen = []

    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "Calculate and report the requested product.", ""

    monkeypatch.setattr(backend, "review_turn", review)
    result = supervisor.supervise_hook({
        "cwd": str(git_repo), "session_id": "native-session", "turn_id": "turn-1",
        "hook_event_name": "Stop", "last_assistant_message": "I have not calculated it yet.",
    })
    assert result["decision"] == "block"
    assert "Calculate and report" in result["reason"]
    assert len(seen) == 1
    assert request in seen[0] and "I have not calculated it yet." in seen[0]
    assert "NATIVE STOP REVIEW CONTEXT" in seen[0]
    assert "This invocation is the native Stop review" in seen[0]
    assert "does not complete the substantive task" in seen[0]
    assert "explicit request to stop, pause, or cancel" in seen[0]
    assert prompts.PREFINAL_REVIEW_CONTEXT not in seen[0]
    assert not state.consume_stop_permit(git_repo, "native-session")


def test_native_context_update_retires_existing_stop_reviewer(
    git_repo, isolated_state, monkeypatch
):
    state.publish_reviewer_session(git_repo, "native-session", "stop", "old-reviewer")
    assert state.read_reviewer_session(git_repo, "native-session", "stop") == "old-reviewer"
    commit_revision = prompts.session_revision("reviewer")
    monkeypatch.setattr(prompts, "NATIVE_STOP_REVIEW_CONTEXT", "Updated native guidance", raising=False)
    assert state.read_reviewer_session(git_repo, "native-session", "stop") is None
    assert prompts.session_revision("reviewer") == commit_revision


def test_prefinal_review_keeps_its_own_invocation_context(git_repo, isolated_state):
    prompt = prompts.stop_prompt(git_repo, "session", "Finished.", prefinal=True)
    assert prompts.PREFINAL_REVIEW_CONTEXT in prompt
    assert "NATIVE STOP REVIEW CONTEXT" not in prompt


def test_legacy_stop_tool_does_not_claim_a_native_hook_is_running(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    state.record_prompt(git_repo, "legacy-session", "Calculate the requested product.")
    token = state.create_stop_ticket(git_repo, "legacy-session", "I have not calculated it yet.")
    seen = []

    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "Calculate the requested product.", ""

    monkeypatch.setattr(backend, "review_turn", review)
    result = mcp_server.handle_message({
        "id": 1, "method": "tools/call", "params": {"name": "stop_continue", "arguments": {
            "cwd": str(git_repo), "review_token": token,
        }},
    })['result']['structuredContent']
    assert result['status'] == 'incomplete'
    assert len(seen) == 1
    assert 'Calculate the requested product.' in seen[0]
    assert 'I have not calculated it yet.' in seen[0]
    assert 'NATIVE STOP REVIEW CONTEXT' not in seen[0]
    assert prompts.PREFINAL_REVIEW_CONTEXT not in seen[0]


def test_live_native_check_requires_explicit_opt_in():
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / 'scripts' / 'check_native_stop_reviewer.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2
    assert '--run is required' in result.stderr
