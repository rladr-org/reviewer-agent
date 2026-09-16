# SPDX-License-Identifier: MPL-2.0
"""Completion supervision starts at Stop, never at request receipt or during work."""
import pytest
from reviewer_codex import backend, liveness, mcp_server, state, supervisor
from test_hook_scripts import _run


def event(repo, name, **extra):
    return dict(cwd=str(repo), session_id="timing", turn_id="turn-1",
                hook_event_name=name, **extra)


@pytest.mark.parametrize("prompt", ["Implement the change.", "What does Stop mean?"])
def test_request_receipt_records_context_without_early_review_instructions(
    git_repo, isolated_state, prompt
):
    state.set_feature(git_repo, "stop")
    result = _run("hook_prompt.py", event(git_repo, "UserPromptSubmit", prompt=prompt), isolated_state)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert state.recent_prompts(git_repo, "timing") == [prompt]
    assert state.read_nudges(git_repo, "timing") == 0
    assert state.read_reviewer_session(git_repo, "timing", "stop") is None


def test_ordinary_work_then_attempted_stop_invokes_reviewer_only_at_stop(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    calls = []
    def review(*args, **kwargs):
        calls.append(args[4])
        return 0, "Run the unfinished test." if len(calls) == 1 else "STOP_OK", ""
    monkeypatch.setattr(backend, "review_turn", review)
    payload = event(git_repo, "PreToolUse", tool_name="Bash", tool_use_id="shell-1",
                    tool_input={"command": "python -m pytest"})
    assert _run("hook_pre_tool.py", payload, isolated_state).stdout == ""
    assert len(liveness.outstanding(git_repo, "timing")) == 1
    payload["hook_event_name"] = "PostToolUse"
    assert _run("hook_post_tool.py", payload, isolated_state).stdout == ""
    assert not liveness.outstanding(git_repo, "timing")
    assert not calls and state.read_nudges(git_repo, "timing") == 0
    result = supervisor.supervise_hook(event(git_repo, "Stop", last_assistant_message="Tests remain."))
    assert result["decision"] == "block" and len(calls) == 1
    assert supervisor.supervise_hook(event(git_repo, "Stop", last_assistant_message="Tests passed.")) == {}
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop"])
def test_non_stop_events_cannot_invoke_completion_review(git_repo, isolated_state, monkeypatch, name):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("review before Stop"))
    result = supervisor.supervise_hook(event(git_repo, name, last_assistant_message="Working."))
    assert "decision" not in result
    assert state.read_nudges(git_repo, "timing") == 0


def test_obsolete_early_call_is_unadvertised_unbound_and_cannot_review(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("early review ran"))
    tools = mcp_server.handle_message({"id": 1, "method": "tools/list"})["result"]["tools"]
    assert "review_completion" not in {tool["name"] for tool in tools}
    instructions = mcp_server.handle_message({"id": 2, "method": "initialize"})["result"]["instructions"]
    assert "review_completion" not in instructions
    assert "automatically" in instructions and "Stop" in instructions
    args = {"cwd": str(git_repo), "proposed_response": "Finished."}
    hook = _run("hook_pre_tool.py", event(git_repo, "PreToolUse",
                tool_name="mcp__reviewer_agent__review_completion", tool_input=args), isolated_state)
    assert hook.returncode == 0 and hook.stdout == ""
    response = mcp_server.handle_message({"id": 3, "method": "tools/call",
                "params": {"name": "review_completion", "arguments": args}})
    assert response["error"]["code"] == -32601
    assert not state.consume_stop_permit(git_repo, "timing")
