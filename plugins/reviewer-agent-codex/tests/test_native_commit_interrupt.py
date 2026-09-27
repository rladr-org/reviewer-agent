# SPDX-License-Identifier: MPL-2.0
"""Host Interrupt must cancel commits even without MCP notifications/cancelled."""
import io
import json
import queue
import subprocess
import threading

import pytest

from reviewer_codex import backend, commit_tool, mcp_server, state


def request(name, arguments, request_id):
    return dict(jsonrpc="2.0", id=request_id, method="tools/call",
                params=dict(name=name, arguments=arguments))


@pytest.mark.parametrize("interrupt_during_startup", [False, True])
def test_interrupt_prevents_late_approved_commit_without_mcp_cancellation(
    git_repo, isolated_state, tmp_path, monkeypatch, interrupt_during_startup
):
    state.set_feature(git_repo, "reviewer")
    state.publish_reviewer_session(git_repo, "owner", "reviewer", "fake-reviewer")
    (git_repo / "change.txt").write_text("staged test change\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "change.txt"], check=True)
    original_head = commit_tool.git_head(git_repo)
    ticket = state.create_commit_ticket(git_repo, "owner")
    # An explicit target may differ from the host cwd; the ticket can also have
    # been minted before an earlier turn was interrupted, as in the live failure.
    origin = tmp_path / "host-cwd"
    origin.mkdir()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    interrupted, unrelated = threading.Event(), threading.Event()
    lines = queue.Queue()
    seen = []
    outcomes = []

    class Output(io.StringIO):
        def write(self, text):
            result = super().write(text)
            response = json.loads(text)
            if response.get("id") == 42:
                interrupted.set()
            if response.get("id") == 43:
                unrelated.set()
            return result

    def incoming():
        while True:
            value = lines.get(timeout=10)
            if value is None:
                return
            yield value

    def review(*args, **kwargs):
        seen.append(kwargs["cancel_event"])
        if not interrupt_during_startup:
            started.set()
            assert release.wait(5)
        return 0, "APPROVED", ""

    def commit(*args, **kwargs):
        if interrupt_during_startup:
            seen.append(kwargs["cancel_event"])
            started.set()
            assert release.wait(5)
        try:
            result = commit_tool.reviewed_commit(*args, **kwargs)
            outcomes.append(result)
            return result
        finally:
            finished.set()

    monkeypatch.setattr(backend, "review_turn", review)
    output = Output()
    server = threading.Thread(target=mcp_server.serve, kwargs=dict(
        input_stream=incoming(), output_stream=output, commit_fn=commit))
    server.start()
    try:
        lines.put(json.dumps(request("review_commit", dict(
            cwd=str(git_repo), message="cancelled fixture", review_token=ticket), 41)))
        assert started.wait(5)
        # A different developer session must not cancel this review.
        event = dict(cwd=str(origin), session_id="another-owner", turn_id="current-turn",
                     hook_event_name="Interrupt")
        lines.put(json.dumps(request("interrupt_stop", event, 43)))
        assert unrelated.wait(3)
        assert not seen[0].is_set()
        event["session_id"] = "owner"
        lines.put(json.dumps(request("interrupt_stop", event, 42)))
        assert interrupted.wait(3)
        # Intentionally do NOT send notifications/cancelled. The old host path
        # abandons its tool wait while the server continues to approval/commit.
        release.set()
        assert finished.wait(5)
    finally:
        release.set()
        lines.put(None)
        server.join(5)
    assert not server.is_alive()
    assert commit_tool.git_head(git_repo) == original_head, "commit landed after host Interrupt"
    assert seen[0].is_set()
    assert outcomes[0]["status"] == "cancelled"
    assert subprocess.check_output(
        ["git", "-C", str(git_repo), "diff", "--cached", "--name-only"], text=True
    ).strip() == "change.txt"
    assert {json.loads(line)["id"] for line in output.getvalue().splitlines()} == {42, 43}


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "missing-owner", "expired"])
def test_stdio_refuses_review_without_cancellation_owner(git_repo, isolated_state, damage):
    ticket = state.create_commit_ticket(git_repo, "owner")
    path = isolated_state / "commit-tickets" / (ticket + ".json")
    if damage == "missing":
        path.unlink()
    elif damage == "invalid-json":
        path.write_text("{")
    else:
        value = json.loads(path.read_text())
        value["session_id" if damage == "missing-owner" else "created"] = None if damage == "missing-owner" else 0
        path.write_text(json.dumps(value))
    called = []
    output = io.StringIO()
    payload = request("review_commit", dict(cwd=str(git_repo), message="test", review_token=ticket), 1)
    mcp_server.serve(input_stream=io.StringIO(json.dumps(payload) + "\n"), output_stream=output,
                     commit_fn=lambda *a, **k: called.append(True))
    assert not called
    response = json.loads(output.getvalue())
    assert response["error"]["code"] == -32602
    assert "ticket" in response["error"]["message"]
