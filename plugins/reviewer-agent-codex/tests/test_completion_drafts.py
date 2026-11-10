# SPDX-License-Identifier: MPL-2.0
"""Draft retries must not require a Stop continuation or repeat an approved review."""

import json
import io
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from reviewer_codex import backend, cancellation, liveness, mcp_server, prompts, state, supervisor
from test_hook_scripts import _run


SESSION = "draft-session"
TURN = "draft-turn"


def event(repo, name="Stop", **extra):
    return dict(cwd=str(repo), session_id=SESSION, turn_id=TURN,
                hook_event_name=name, **extra)


def bind(repo, isolated_state, draft="17 times 19 is 323.", **overrides):
    payload = event(repo, "PreToolUse", tool_name="mcp__reviewer_agent__review_completion",
                    tool_input=dict(cwd="/invented", proposed_response=draft, review_token="invented"))
    payload.update(overrides)
    response = _run("hook_pre_tool.py", payload, isolated_state)
    assert response.returncode == 0, response.stderr
    output = json.loads(response.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    return output["updatedInput"]


def call(arguments, token=None):
    response = mcp_server.handle_message({"id": 1, "method": "tools/call",
        "params": {"name": "review_completion", "arguments": arguments}}, cancel_event=token)
    assert "error" not in response, response
    result = response["result"]
    assert result["content"][0]["text"]
    return result["structuredContent"]


def test_reject_then_approve_draft_and_silent_stop(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    seen = []

    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "Calculate the product." if len(seen) == 1 else "STOP_OK", ""

    monkeypatch.setattr(backend, "review_turn", review)
    first = call(bind(git_repo, isolated_state, "I have not calculated it yet."))
    assert first["status"] == "incomplete" and first["directive"] == "Calculate the product."
    assert "decision" not in first and not state.consume_stop_permit(git_repo, SESSION)
    arguments = bind(git_repo, isolated_state)
    assert call(arguments)["status"] == "stop_ok"
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message=arguments["proposed_response"])) == {}
    assert len(seen) == 2 and state.read_nudges(git_repo, SESSION) == 2
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert all(text.startswith(prompts.STOP_BRIEF) for text in seen)
    assert "pending publication" in seen[0]
    assert "this invocation" in seen[0].lower()


@pytest.mark.parametrize("field,value", [("session_id", ""), ("turn_id", ""), ("cwd", "relative")])
def test_missing_host_identity_erases_invented_ticket(git_repo, isolated_state, monkeypatch, field, value):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("unbound review"))
    args = bind(git_repo, isolated_state, **{field: value})
    assert args["review_token"] == ""
    result = call(args)
    assert result["status"] == "error"
    assert "native Stop" in result["message"]
    assert not state.consume_stop_permit(git_repo, SESSION)


def test_binding_uses_host_scope_and_normalizes_only_outer_whitespace(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "STOP_OK", ""))
    args = bind(git_repo, isolated_state, "  Complete draft.\n")
    assert args["cwd"] == str(git_repo) and args["review_token"] not in ("", "invented")
    args["proposed_response"] = "Complete draft."
    assert call(args)["status"] == "stop_ok"
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message="Complete draft.")) == {}
    args = bind(git_repo, isolated_state, "Complete draft.")
    args["proposed_response"] = "Complete  draft."
    assert call(args)["status"] == "error"
    assert not state.consume_stop_permit(git_repo, SESSION)


@pytest.mark.parametrize("kind", ["missing", "legacy", "replayed"])
def test_unbound_legacy_and_replayed_calls_cannot_review(git_repo, isolated_state, monkeypatch, kind):
    state.set_feature(git_repo, "stop")
    args = dict(cwd=str(git_repo), proposed_response="Done.")
    if kind == "legacy":
        args["review_token"] = state.create_stop_ticket(git_repo, SESSION, "Done.")
    elif kind == "replayed":
        args = bind(git_repo, isolated_state)
        monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "Finish the task.", ""))
        assert call(args)["status"] == "incomplete"
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("unbound review"))
    assert call(args)["status"] == "error"
    assert not state.consume_stop_permit(git_repo, SESSION)


@pytest.mark.parametrize("change", ["draft", "turn", "operator", "work", "expired"])
def test_invalidated_approval_runs_native_fallback(git_repo, isolated_state, monkeypatch, change):
    state.set_feature(git_repo, "stop")
    calls = []
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (calls.append(a[4]) or 0, "STOP_OK", ""))
    args = bind(git_repo, isolated_state)
    assert call(args)["status"] == "stop_ok"
    stop = event(git_repo, last_assistant_message=args["proposed_response"])
    if change == "draft":
        stop["last_assistant_message"] += " Changed."
    elif change == "turn":
        stop["turn_id"] = "other-turn"
    elif change == "operator":
        state.invalidate_stop_permits(git_repo, SESSION)
    elif change == "work":
        state.invalidate_stop_permits(git_repo, SESSION, work=True)
    else:
        now = state.time.time()
        monkeypatch.setattr(state.time, "time", lambda: now + 301)
    assert supervisor.supervise_hook(stop) == {}
    assert len(calls) == 2


@pytest.mark.parametrize("work", [False, True])
def test_work_or_input_during_draft_review_cannot_approve(git_repo, isolated_state, monkeypatch, work):
    state.set_feature(git_repo, "stop")
    args = bind(git_repo, isolated_state)

    def review(*a, **k):
        state.invalidate_stop_permits(git_repo, SESSION, work=work)
        return 0, "STOP_OK", ""

    monkeypatch.setattr(backend, "review_turn", review)
    assert call(args)["status"] in ("incomplete", "cancelled")
    assert not state.consume_stop_permit(git_repo, SESSION)


def test_draft_and_native_reviews_share_budget(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "2")
    calls = []
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (calls.append(a[4]) or 0, "Finish the work.", ""))
    assert call(bind(git_repo, isolated_state))["status"] == "incomplete"
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message="Still unfinished."))["decision"] == "block"
    args = bind(git_repo, isolated_state)
    assert call(args)["status"] == "budget_exhausted"
    result = supervisor.supervise_hook(event(git_repo, last_assistant_message=args["proposed_response"]))
    assert "decision" not in result and "cap" in result["systemMessage"].lower()
    assert len(calls) == 2


def test_draft_background_wait_retains_markers(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, SESSION, "subagent", "child", "Waiting for the arranged notification")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "STOP_WAIT", ""))
    args = bind(git_repo, isolated_state)
    assert call(args)["status"] == "stop_wait"
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message=args["proposed_response"])) == {}
    assert liveness.outstanding(git_repo, SESSION)[0]["id"] == "child"


def test_failed_open_draft_does_not_claim_approval_or_review_twice(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (1, "", "reviewer unavailable"))
    args = bind(git_repo, isolated_state)
    assert call(args)["status"] == "failed_open"
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("duplicate failed review"))
    result = supervisor.supervise_hook(event(git_repo, last_assistant_message=args["proposed_response"]))
    assert "decision" not in result and "failed" in result["systemMessage"].lower()


def test_interrupt_cancels_matching_draft_review(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    args = bind(git_repo, isolated_state)
    started, finished = threading.Event(), threading.Event()
    token = cancellation.Cancellation()
    results = []

    def review(*a, **k):
        started.set()
        assert k["cancel_event"].wait(5), "Interrupt did not cancel draft review"
        return backend.CANCELLED_EXIT, "", ""

    def run():
        try:
            results.append(call(args, token))
        finally:
            finished.set()

    monkeypatch.setattr(backend, "review_turn", review)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert started.wait(5)
        mcp_server.handle_message({"id": 2, "method": "tools/call", "params": {
            "name": "interrupt_stop", "arguments": event(git_repo, "Interrupt")}})
        assert finished.wait(5)
        assert results[0]["status"] == "cancelled"
        assert not state.consume_stop_permit(git_repo, SESSION)
    finally:
        token.set()
        worker.join(5)


def test_unchanged_draft_retries_use_fresh_reassessment(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    calls = []
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (calls.append("session") or 0, "Finish work.", ""))
    monkeypatch.setattr(backend, "review_once", lambda *a, **k: (calls.append("fresh") or 0, "STOP_OK", ""))
    assert call(bind(git_repo, isolated_state))["status"] == "incomplete"
    assert call(bind(git_repo, isolated_state))["status"] == "incomplete"
    assert call(bind(git_repo, isolated_state))["status"] == "stop_ok"
    assert calls == ["session", "session", "fresh"]


def test_disabled_repository_and_precancelled_call_do_not_review(git_repo, isolated_state, monkeypatch):
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("unexpected review"))
    assert call(dict(cwd=str(git_repo), proposed_response="Done."))["status"] == "disabled"
    state.set_feature(git_repo, "stop")
    args = bind(git_repo, isolated_state)
    token = cancellation.Cancellation()
    token.set()
    assert call(args, token)["status"] == "cancelled"
    assert not state.consume_stop_permit(git_repo, SESSION)


def test_live_draft_check_requires_explicit_opt_in():
    script = Path(__file__).resolve().parents[1] / "scripts/check_completion_drafts.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "--run is required" in result.stderr


def test_interrupt_reaches_draft_call_before_worker_claims_ticket(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    args = bind(git_repo, isolated_state)
    incoming = queue.Queue()
    entered, release, interrupted = threading.Event(), threading.Event(), threading.Event()
    original = mcp_server._review_completion
    cancellation_seen = []

    def delayed(arguments, token):
        entered.set()
        assert release.wait(5)
        cancellation_seen.append(token.is_set())
        return original(arguments, token)

    class Output(io.StringIO):
        def write(self, text):
            result = super().write(text)
            if json.loads(text).get("id") == 42:
                interrupted.set()
            return result

    def source():
        while True:
            value = incoming.get(timeout=10)
            if value is None:
                return
            yield json.dumps(value) + "\n"

    monkeypatch.setattr(mcp_server, "_review_completion", delayed)
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("cancelled work started"))
    output = Output()
    server = threading.Thread(target=mcp_server.serve, args=(source(), output))
    server.start()
    try:
        incoming.put({"id": 41, "method": "tools/call", "params": {
            "name": "review_completion", "arguments": args}})
        assert entered.wait(5)
        incoming.put({"id": 42, "method": "tools/call", "params": {
            "name": "interrupt_stop", "arguments": event(git_repo, "Interrupt")}})
        assert interrupted.wait(5)
        release.set()
    finally:
        release.set()
        incoming.put(None)
        server.join(10)
    assert not server.is_alive()
    assert cancellation_seen == [True]
    assert [json.loads(line)["id"] for line in output.getvalue().splitlines()] == [42]
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert not mcp_server._active_stops
