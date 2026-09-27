# SPDX-License-Identifier: MPL-2.0
import gc
import io
import json
import queue
import threading
import weakref

import pytest

from reviewer_codex import backend, mcp_server, state
from reviewer_codex.cancellation import Cancellation


def event(repo, **changes):
    data = dict(cwd=str(repo), session_id="session", turn_id="turn",
                hook_event_name="Stop", last_assistant_message="Done.", stop_hook_active=False)
    data.update(changes)
    return data


def request(name, arguments, request_id=1):
    return dict(jsonrpc="2.0", id=request_id, method="tools/call",
                params=dict(name=name, arguments=arguments))


def interrupt(repo, **changes):
    data = dict(cwd=str(repo), session_id="session", turn_id="turn", hook_event_name="Interrupt")
    data.update(changes)
    response = mcp_server.handle_message(request("interrupt_stop", data, 99))
    assert response["result"]["structuredContent"] == {}
    assert json.loads(response["result"]["content"][0]["text"]) == {}
    return response


@pytest.mark.parametrize("mismatch", [
    {"session_id": "other"}, {"turn_id": "other"}, {"cwd": "/another/repository"},
    {"hook_event_name": "Stop"}, {"turn_id": ""}, {"cwd": "relative"},
])
def test_interrupt_only_cancels_the_matching_active_stop(git_repo, isolated_state, monkeypatch, mismatch):
    state.set_feature(git_repo, "stop")
    token, started = Cancellation(), threading.Event()
    reference = weakref.ref(token)
    calls = []

    def review(*args, **kwargs):
        calls.append(True)
        started.set()
        assert kwargs["cancel_event"].wait(5)
        return backend.CANCELLED_EXIT, "", ""

    monkeypatch.setattr(backend, "review_turn", review)
    worker = threading.Thread(target=mcp_server.handle_message,
                              args=(request("stop_hook", event(git_repo)),),
                              kwargs={"cancel_event": token})
    worker.start()
    try:
        # Real Git-backed setup can exceed three seconds on a busy runner.
        assert started.wait(10)
        interrupt(git_repo, **mismatch)
        assert not token.is_set()
        assert calls == [True]
        interrupt(git_repo)
        assert token.is_set()
    finally:
        token.cancel()
        worker.join(5)
    assert not worker.is_alive()
    del token
    gc.collect()
    assert reference() is None, "cancelled Stop retained its cancellation token"
    assert not state.consume_stop_permit(git_repo, "session")
    assert state.consume_stop_abort_bypass(git_repo, "session", "fresh-interrupt-turn")
    interrupt(git_repo)  # repeated delivery does not launch work or raise
    assert calls == [True]


def test_interrupt_canonicalizes_cwd_and_cancels_all_matching_requests(git_repo, isolated_state, monkeypatch, tmp_path):
    state.set_feature(git_repo, "stop")
    alias = tmp_path / "alias"
    alias.symlink_to(git_repo, target_is_directory=True)
    tokens = [Cancellation() for _ in range(3)]
    started = [threading.Event() for _ in tokens]

    def review(*args, **kwargs):
        token = kwargs["cancel_event"]
        started[tokens.index(token)].set()
        assert token.wait(15)
        return backend.CANCELLED_EXIT, "", ""

    monkeypatch.setattr(backend, "review_turn", review)
    workers = []
    for index, token in enumerate(tokens):
        payload = event(git_repo, turn_id="unrelated" if index == 2 else "turn")
        worker = threading.Thread(target=mcp_server.handle_message,
                                  args=(request("stop_hook", payload, index + 1),),
                                  kwargs={"cancel_event": token})
        workers.append(worker)
        worker.start()
    try:
        assert all(item.wait(10) for item in started)
        interrupt(alias)
        assert tokens[0].is_set() and tokens[1].is_set()
        assert not tokens[2].is_set()
    finally:
        for token in tokens:
            token.cancel()
        for worker in workers:
            worker.join(5)
    assert not any(worker.is_alive() for worker in workers)


@pytest.mark.parametrize("outcome", ["STOP_OK", "Finish the task.", "error"])
def test_finished_requests_release_their_cancellation_tokens(git_repo, isolated_state, monkeypatch, outcome):
    state.set_feature(git_repo, "stop")
    calls = []

    def review(*args, **kwargs):
        calls.append(True)
        if outcome == "error":
            raise OSError("review failed")
        return 0, outcome, ""

    monkeypatch.setattr(backend, "review_turn", review)
    interrupt(git_repo)  # idle
    assert calls == []
    token = Cancellation()
    reference = weakref.ref(token)
    mcp_server.handle_message(request("stop_hook", event(git_repo)), cancel_event=token)
    del token
    gc.collect()
    assert reference() is None, "completed Stop retained its cancellation token"
    interrupt(git_repo)
    interrupt(git_repo)  # completed and repeated
    assert calls == [True]


def test_interrupt_does_not_cancel_unregistered_direct_calls(git_repo, isolated_state):
    # Real commit calls are registered by the stdio reader using a valid ticket.
    # This direct helper call has no ticket owner and must not enter that registry.
    token, started, release = Cancellation(), threading.Event(), threading.Event()
    results = []

    def commit(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return {"status": "rejected", "message": "test review"}

    def run():
        results.append(mcp_server.handle_message(request("review_commit", dict(
            cwd=str(git_repo), message="test", review_token="test-token")),
            cancel_event=token, commit_fn=commit))

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert started.wait(2)
        interrupt(git_repo)
        assert not token.is_set()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results[0]["result"]["structuredContent"]["status"] == "rejected"


def test_native_interrupt_suppresses_late_stdio_stop_response(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    started, cancelled, replied = threading.Event(), threading.Event(), threading.Event()
    lines = queue.Queue()

    class Output(io.StringIO):
        def write(self, text):
            result = super().write(text)
            replied.set()
            return result

    output = Output()

    def review(*args, **kwargs):
        started.set()
        assert kwargs["cancel_event"].wait(5)
        cancelled.set()
        return backend.CANCELLED_EXIT, "", ""

    def incoming():
        while True:
            line = lines.get(timeout=15)
            if line is None:
                return
            yield line

    monkeypatch.setattr(backend, "review_turn", review)
    server = threading.Thread(target=mcp_server.serve, kwargs={"input_stream": incoming(), "output_stream": output})
    server.start()
    try:
        lines.put(json.dumps(request("stop_hook", event(git_repo), 41)) + "\n")
        assert started.wait(10)
        lines.put(json.dumps(request("interrupt_stop", dict(cwd=str(git_repo), session_id="session",
                            turn_id="turn", hook_event_name="Interrupt"), 42)) + "\n")
        assert cancelled.wait(3)
        assert replied.wait(3)
    finally:
        lines.put(None)
        server.join(5)
    assert not server.is_alive()
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [response["id"] for response in responses] == [42]
    assert responses[0]["result"]["structuredContent"] == {}
    assert not state.consume_stop_permit(git_repo, "session")
