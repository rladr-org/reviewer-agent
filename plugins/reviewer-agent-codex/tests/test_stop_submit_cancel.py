# SPDX-License-Identifier: MPL-2.0
import io
import json
import queue
import sys
import threading
import time

import pytest

from reviewer_codex import backend, mcp_server, state, stop_tool, supervisor
from reviewer_codex.cancellation import Cancellation
from reviewer_codex.supervisor import CONTINUATION_PREFIX
from test_hook_scripts import _run


def event(repo):
    return dict(cwd=str(repo), session_id="submit", turn_id="old-turn",
                hook_event_name="Stop", last_assistant_message="Done.")


def submit(repo, isolated_state, **changes):
    payload = dict(event(repo), hook_event_name="UserPromptSubmit", turn_id="new-turn", prompt="Wait, change that.")
    payload.update(changes)
    result = _run("hook_prompt.py", payload, isolated_state)
    assert result.returncode == 0, result.stderr


def test_submitted_message_cancels_pending_stop_without_bypassing_next_task(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    started = threading.Event()
    calls, results = [], []
    token = Cancellation()

    def review(*args, **kwargs):
        calls.append(True)
        started.set()
        assert kwargs["cancel_event"].wait(5)
        return 0, "A stale continuation that must never be delivered.", ""

    monkeypatch.setattr(backend, "review_turn", review)
    worker = threading.Thread(target=lambda: results.append(mcp_server._native_stop(event(git_repo), token)))
    worker.start()
    try:
        assert started.wait(5)
        submit(git_repo, isolated_state)
        worker.join(2)
        assert not worker.is_alive(), "submitted message did not cancel the pending review"
        assert not token.is_set(), "submission must let the host receive an empty hook result"
        assert results == [{}]
        assert calls == [True]
        assert not state.consume_stop_permit(git_repo, "submit")
        assert not state.consume_stop_abort_bypass(git_repo, "submit", "new-turn")
    finally:
        token.cancel()
        worker.join(5)


@pytest.mark.parametrize("outcome", ["STOP_OK", "Continue stale work.", "error"])
def test_submit_at_verdict_discards_all_old_outcomes(git_repo, isolated_state, monkeypatch, outcome):
    state.set_feature(git_repo, "stop")
    token = Cancellation()

    def review(*args, **kwargs):
        submit(git_repo, isolated_state)
        if outcome == "error":
            raise OSError("review failed just as the operator replied")
        return 0, outcome, ""

    monkeypatch.setattr(backend, "review_turn", review)
    assert mcp_server._native_stop(event(git_repo), token) == {}
    assert not token.is_set()
    assert not state.consume_stop_abort_bypass(git_repo, "submit", "new-turn")


@pytest.mark.parametrize("kind", ["session", "repository", "continuation", "work"])
def test_unrelated_or_generated_input_does_not_cancel_review(git_repo, isolated_state, monkeypatch, kind):
    state.set_feature(git_repo, "stop")
    token = Cancellation()

    def review(*args, **kwargs):
        if kind == "work":
            state.invalidate_stop_permits(git_repo, "submit", work=True)
        else:
            change = {"session_id": "other"} if kind == "session" else (
                {"cwd": str(isolated_state)} if kind == "repository" else
                {"prompt": CONTINUATION_PREFIX + "\nKeep working."})
            submit(git_repo, isolated_state, **change)
        assert not token.wait(0.2)
        return 0, "Finish the actual task.", ""

    monkeypatch.setattr(backend, "review_turn", review)
    assert mcp_server._native_stop(event(git_repo), token)["decision"] == "block"
    assert not token.is_set()


@pytest.mark.parametrize("ruling", ["STOP_OK", "Old directive."])
def test_submit_after_review_before_hook_result_suppresses_continuation(git_repo, isolated_state, monkeypatch, ruling):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, ruling, ""))
    original = stop_tool.review_stop

    def review(*args, **kwargs):
        result = original(*args, **kwargs)
        submit(git_repo, isolated_state)
        return result

    monkeypatch.setattr(stop_tool, "review_stop", review)
    assert mcp_server._native_stop(event(git_repo), Cancellation()) == {}


def test_submission_completes_stdio_hook_without_stale_continuation(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    started, cancelled = threading.Event(), threading.Event()
    replied = threading.Event()

    class Output(io.StringIO):
        def write(self, text):
            result = super().write(text)
            replied.set()
            return result

    lines, output = queue.Queue(), Output()

    def review(*args, **kwargs):
        started.set()
        assert kwargs["cancel_event"].wait(5)
        cancelled.set()
        return 0, "Continue obsolete work.", ""

    def incoming():
        while True:
            line = lines.get(timeout=10)
            if line is None:
                return
            yield line

    monkeypatch.setattr(backend, "review_turn", review)
    worker = threading.Thread(target=mcp_server.serve, kwargs=dict(input_stream=incoming(), output_stream=output))
    worker.start()
    try:
        lines.put(json.dumps(dict(jsonrpc="2.0", id=41, method="tools/call",
                                  params=dict(name="stop_hook", arguments=event(git_repo)))) + "\n")
        assert started.wait(5)
        submit(git_repo, isolated_state)
        assert cancelled.wait(2)
        assert replied.wait(2), "host is still waiting for its Stop hook response"
    finally:
        lines.put(None)
        worker.join(5)
    assert not worker.is_alive()
    response = json.loads(output.getvalue())
    assert response["id"] == 41
    assert response["result"]["structuredContent"] == {}


def test_submission_terminates_running_reviewer_process(git_repo, isolated_state, monkeypatch, tmp_path):
    state.set_feature(git_repo, "stop")
    ready = tmp_path / "reviewer-ready"
    calls, results = [], []
    token = Cancellation()

    def review(*args, **kwargs):
        outcome = backend._run([sys.executable, "-c",
            "from pathlib import Path; import sys,time; Path(sys.argv[1]).write_text('ready'); time.sleep(60)",
            str(ready)], timeout=10, cancel_event=kwargs["cancel_event"])
        calls.append(outcome[0])
        return outcome

    monkeypatch.setattr(backend, "review_turn", review)
    worker = threading.Thread(target=lambda: results.append(mcp_server._native_stop(event(git_repo), token)))
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        submit(git_repo, isolated_state)
        worker.join(3)
        assert not worker.is_alive()
        assert calls == [backend.CANCELLED_EXIT]
        assert results == [{}]
    finally:
        token.cancel()
        worker.join(5)


def test_submit_during_stop_setup_prevents_stale_review(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")

    def settle(*args):
        submit(git_repo, isolated_state)
        return []

    monkeypatch.setattr(supervisor, "_settled_outstanding", settle)
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("reviewed superseded final answer"))
    assert mcp_server._native_stop(event(git_repo), Cancellation()) == {}


@pytest.mark.parametrize("factory", [Cancellation, threading.Event])
def test_pre_cancelled_host_never_starts_reviewer(git_repo, isolated_state, monkeypatch, factory):
    state.set_feature(git_repo, "stop")
    token = factory()
    token.set()
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: pytest.fail("review started after cancellation"))
    assert mcp_server._native_stop(event(git_repo), token) == {}
