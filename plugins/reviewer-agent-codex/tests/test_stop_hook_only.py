# SPDX-License-Identifier: MPL-2.0
"""Only the host's Stop handler can initiate completion supervision."""
import json
import io
from pathlib import Path
import runpy
import sys

import pytest

from reviewer_codex import backend, mcp_server, state


def call(name, arguments):
    return mcp_server.handle_message({"id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}})


def event(repo, name="Stop", **extra):
    return dict(cwd=str(repo), session_id="only-stop", turn_id="turn-1",
                hook_event_name=name, last_assistant_message="17 × 19 = 323.", **extra)


@pytest.mark.parametrize("name", ["stop_continue", "review_completion"])
def test_obsolete_tool_cannot_review_even_with_valid_ticket(git_repo, isolated_state, monkeypatch, name):
    state.set_feature(git_repo, "stop")
    token = state.create_stop_ticket(git_repo, "only-stop", "Unfinished work.")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("obsolete tool started review"))
    result = call(name, {"cwd": str(git_repo), "review_token": token})
    assert result["error"]["code"] == -32601
    assert state.claim_stop_ticket(token, git_repo), "obsolete call consumed a Stop ticket"
    assert not state.consume_stop_permit(git_repo, "only-stop")


def test_mcp_exposes_only_host_completion_handler_and_no_agent_instructions():
    tools = mcp_server.handle_message({"id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"review_commit", "stop_hook", "interrupt_stop"}
    instructions = mcp_server.handle_message({"id": 2, "method": "initialize"})["result"]["instructions"]
    assert "stop_continue" not in instructions and "review_completion" not in instructions
    assert "automatically" in instructions and "Stop" in instructions


def test_legacy_command_routing_entry_point_is_removed():
    assert not (Path(__file__).resolve().parents[1] / "scripts/hook_stop.py").exists()


@pytest.mark.parametrize("operator_request,answer", [
    ("What is 17 times 19?", "17 × 19 = 323."),
    ("Implement the parser and test it.", "Implemented the parser; all requested tests passed."),
])
def test_completed_answer_runs_one_review_only_at_stop(git_repo, isolated_state, monkeypatch, capsys, operator_request, answer):
    state.set_feature(git_repo, "stop")
    seen = []
    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "STOP_OK", ""
    monkeypatch.setattr(backend, "review_turn", review)
    prompt = event(git_repo, "UserPromptSubmit", prompt=operator_request)
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(prompt)))
    assert runpy.run_path(str(scripts / "hook_prompt.py"))["main"]() == 0
    assert capsys.readouterr().out == ""
    assert seen == [] and state.read_nudges(git_repo, "only-stop") == 0
    payload = event(git_repo)
    payload["last_assistant_message"] = answer
    response = call("stop_hook", payload)["result"]
    assert response["structuredContent"] == {}
    assert json.loads(response["content"][0]["text"]) == {}
    assert len(seen) == 1 and operator_request in seen[0] and answer in seen[0]
    assert not state.consume_stop_permit(git_repo, "only-stop")


def test_old_handoff_permit_cannot_skip_a_stop_review(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    state.grant_stop_permit(git_repo, "only-stop", "turn-1", "17 × 19 = 323.")
    seen = []
    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "Finish the authorized remaining work.", ""
    monkeypatch.setattr(backend, "review_turn", review)
    result = call("stop_hook", event(git_repo))["result"]["structuredContent"]
    assert len(seen) == 1
    assert result["decision"] == "block"
    assert "review_token" not in result["reason"] and "stop_continue" not in result["reason"]


@pytest.mark.parametrize("name", ["UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop", "Interrupt"])
def test_other_events_never_start_supervision(git_repo, isolated_state, monkeypatch, name):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("non-Stop review"))
    result = call("stop_hook", event(git_repo, name))["result"]["structuredContent"]
    assert "decision" not in result
    assert state.read_nudges(git_repo, "only-stop") == 0
    assert call("interrupt_stop", event(git_repo, "Interrupt"))["result"]["structuredContent"] == {}
