# SPDX-License-Identifier: MPL-2.0
"""Native Stop outcomes through real ticket/permit state and controlled reviewer rulings."""

import io
import json
import queue
import threading
from pathlib import Path

import pytest

from reviewer_codex import backend, liveness, mcp_server, state, supervisor
from reviewer_codex.cancellation import Cancellation


def event(repo, **changes):
    result = {
        "cwd": str(repo), "session_id": "native-session", "turn_id": "turn-1",
        "hook_event_name": "Stop", "stop_hook_active": False,
        "last_assistant_message": "Implemented the change; the targeted tests passed.",
    }
    result.update(changes)
    return result


@pytest.fixture
def review(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    calls = []

    def ruling(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, "STOP_OK", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    return calls


def test_approval_is_silent_and_does_not_leave_a_permit(git_repo, review):
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert len(review) == 1
    args, kwargs = review[0]
    assert args[1:4] == ("native-session", "stop", "read-only")
    assert event(git_repo)["last_assistant_message"] in args[4]
    assert kwargs["timeout"] == backend.STOP_REVIEW_TIMEOUT
    assert not state.consume_stop_permit(git_repo, "native-session")
    # A second stop cannot silently consume approval from the first review.
    assert supervisor.supervise_hook(event(git_repo, turn_id="turn-2")) == {}
    assert len(review) == 2


def test_incomplete_returns_only_actionable_continuation(git_repo, review, monkeypatch):
    directive = "Run the missing integration check and report its result."
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, directive, ""))
    result = supervisor.supervise_hook(event(git_repo))
    assert result == {"decision": "block", "reason": supervisor.CONTINUATION_PREFIX + "\n" + directive}
    assert "review_token" not in result["reason"]
    assert not state.consume_stop_permit(git_repo, "native-session")


@pytest.mark.parametrize("work", [False, True], ids=["operator-input", "tool-work"])
def test_approval_invalidated_during_review_must_continue(git_repo, review, monkeypatch, work):
    def ruling(*args, **kwargs):
        state.invalidate_stop_permits(git_repo, "native-session", work=work)
        return 0, "STOP_OK", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    assert result["decision"] == "block"
    assert result["reason"].startswith(supervisor.CONTINUATION_PREFIX)
    assert "review_token" not in result["reason"]
    assert not state.consume_stop_permit(git_repo, "native-session")


@pytest.mark.parametrize("outcome", ["STOP_OK", "Inspect the background work first."])
def test_background_markers_are_adjudicated_in_the_same_review(git_repo, review, monkeypatch, outcome):
    liveness.mark(git_repo, "native-session", "subagent", "agent-1", "worker")

    def ruling(*args, **kwargs):
        assert "- subagent: worker" in args[4]
        return 0, outcome, ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    remaining = liveness.outstanding(git_repo, "native-session")
    if outcome == "STOP_OK":
        assert result == {}
        assert remaining == []
    else:
        assert result["decision"] == "block"
        assert [item["id"] for item in remaining] == ["agent-1"]


def test_new_background_marker_survives_approval(git_repo, review, monkeypatch):
    liveness.mark(git_repo, "native-session", "subagent", "old", "old worker")

    def ruling(*args, **kwargs):
        liveness.mark(git_repo, "native-session", "subagent", "new", "new worker")
        return 0, "STOP_OK", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    assert result["decision"] == "block"
    assert [item["id"] for item in liveness.outstanding(git_repo, "native-session")] == ["new"]
    assert not state.consume_stop_permit(git_repo, "native-session")


@pytest.mark.parametrize("failure", ["exception", "timeout", "empty"])
def test_reviewer_failures_allow_stop_with_warning(git_repo, review, monkeypatch, failure):
    def ruling(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("review process unavailable")
        if failure == "timeout":
            return 124, "", "review timed out"
        return 0, "", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    assert "decision" not in result
    assert "failed" in result["systemMessage"].lower()
    assert "approved" not in result["systemMessage"].lower()
    assert not state.consume_stop_permit(git_repo, "native-session")


@pytest.mark.parametrize("fault", ["permit-rename", "permit-read", "operator-read", "work-read"])
def test_permit_io_failure_is_fail_open_not_a_continuation(git_repo, review, monkeypatch, fault):
    # Use non-empty generations so an unreadable generation cannot look unchanged.
    state.invalidate_stop_permits(git_repo, "native-session")
    state.invalidate_stop_permits(git_repo, "native-session", work=True)
    active = [False]
    reviewed = []
    original_rename, original_read = Path.rename, Path.read_text

    def ruling(*args, **kwargs):
        reviewed.append(True)
        active[0] = True
        return 0, "STOP_OK", ""

    def rename(path, target):
        if active[0] and fault == "permit-rename" and path.suffix == ".permit":
            raise PermissionError("permit rename denied")
        return original_rename(path, target)

    def read(path, *args, **kwargs):
        if active[0]:
            if fault == "permit-read" and path.suffix == ".claim":
                raise PermissionError("permit read denied")
            directory = {"operator-read": "stop-generations", "work-read": "stop-work-generations"}.get(fault)
            if directory and path.parent.name == directory:
                raise PermissionError("generation read denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(backend, "review_turn", ruling)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(Path, "read_text", read)
    result = supervisor.supervise_hook(event(git_repo))
    assert reviewed == [True]
    assert "decision" not in result
    assert "failed" in result["systemMessage"].lower()
    assert "approved" not in result["systemMessage"].lower()
    active[0] = False
    assert not state.consume_stop_permit(git_repo, "native-session")


def test_incomplete_budget_survives_new_host_turns(git_repo, review, monkeypatch):
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "2")
    calls = []

    def ruling(*args, **kwargs):
        calls.append(1)
        return 0, "Finish the missing check.", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    for turn in ("turn-1", "turn-2"):
        assert supervisor.supervise_hook(event(git_repo, turn_id=turn))["decision"] == "block"
    result = supervisor.supervise_hook(event(git_repo, turn_id="turn-3"))
    assert "decision" not in result
    assert "cap" in result["systemMessage"]
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"hook_event_name": "PreToolUse"}, {"cwd": "relative/path"}, {"session_id": ""},
    {"session_id": []}, {"last_assistant_message": ["not text"]},
])
def test_malformed_event_never_launches_reviewer(git_repo, review, changes):
    result = supervisor.supervise_hook(event(git_repo, **changes))
    assert "decision" not in result
    assert result.get("systemMessage")
    assert review == []


def test_disabled_and_empty_stops_are_silent(git_repo, isolated_state, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("inert Stop launched a reviewer")

    monkeypatch.setattr(backend, "review_turn", unexpected)
    assert supervisor.supervise_hook(event(git_repo)) == {}
    state.set_feature(git_repo, "stop")
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message=None)) == {}


def test_cancelled_review_preserves_one_use_abort_bypass(git_repo, review, monkeypatch):
    token = Cancellation()

    def ruling(*args, **kwargs):
        assert kwargs["cancel_event"] is token
        token.cancel()
        return backend.CANCELLED_EXIT, "", ""

    monkeypatch.setattr(backend, "review_turn", ruling)
    assert supervisor.supervise_hook(event(git_repo), cancel_event=token) == {}
    assert not state.consume_stop_permit(git_repo, "native-session")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "Finish the check.", ""))
    # Interrupted Stop may acquire a fresh turn ID; the bypass is consumed once.
    assert supervisor.supervise_hook(event(git_repo, turn_id="interrupt")) == {}
    assert supervisor.supervise_hook(event(git_repo, turn_id="later"))["decision"] == "block"


@pytest.mark.parametrize("ruling", ["STOP_OK", "Run the missing test."])
def test_mcp_hook_returns_hook_json_in_text_and_structured_content(git_repo, review, monkeypatch, ruling):
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, ruling, ""))
    response = mcp_server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "stop_hook", "arguments": event(git_repo)},
    })
    result = response["result"]
    decision = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == decision
    assert result.get("isError", False) is False
    assert decision == ({} if ruling == "STOP_OK" else {
        "decision": "block", "reason": supervisor.CONTINUATION_PREFIX + "\n" + ruling,
    })


def test_native_stdio_cancellation_has_no_late_response(git_repo, review, monkeypatch):
    started, cancelled = threading.Event(), threading.Event()
    source = queue.Queue()
    output = io.StringIO()

    def ruling(*args, **kwargs):
        started.set()
        assert kwargs["cancel_event"].wait(3)
        cancelled.set()
        return backend.CANCELLED_EXIT, "", ""

    def incoming():
        while True:
            line = source.get(timeout=5)
            if line is None:
                return
            yield line

    monkeypatch.setattr(backend, "review_turn", ruling)
    server = threading.Thread(target=mcp_server.serve, kwargs={
        "input_stream": incoming(), "output_stream": output,
    })
    server.start()
    try:
        source.put(json.dumps({"jsonrpc": "2.0", "id": 41, "method": "tools/call", "params": {
            "name": "stop_hook", "arguments": event(git_repo),
        }}) + "\n")
        assert started.wait(2)
        assert output.getvalue() == ""
        source.put(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": 41, "reason": "Escape"}}) + "\n")
        assert cancelled.wait(2)
    finally:
        source.put(None)
        server.join(5)
    assert not server.is_alive()
    assert output.getvalue() == ""
    assert not state.consume_stop_permit(git_repo, "native-session")
