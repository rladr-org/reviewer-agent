# SPDX-License-Identifier: MPL-2.0
import json
import subprocess
import sys
import threading
from pathlib import Path

from reviewer_codex import mcp_server


ROOT = Path(__file__).resolve().parents[1]


def _request(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def test_tool_call_is_one_blocking_response():
    started = threading.Event()
    release = threading.Event()
    responses = []

    def fake_commit(cwd, message, review_token, cancel_event=None):
        started.set()
        assert review_token == "token-1"
        assert release.wait(2)
        return {"status": "rejected", "review": "REJECTED\n- fix it"}

    def invoke():
        responses.append(
            mcp_server.handle_message(
                _request(
                    "tools/call",
                    {
                        "name": "review_commit",
                        "arguments": {
                            "cwd": "/repo",
                            "message": "test",
                            "review_token": "token-1",
                        },
                    },
                ),
                commit_fn=fake_commit,
            )
        )

    thread = threading.Thread(target=invoke)
    thread.start()
    assert started.wait(1)
    assert responses == []
    assert thread.is_alive()
    release.set()
    thread.join(2)

    result = responses[0]["result"]
    assert result["structuredContent"]["status"] == "rejected"
    assert "REJECTED" in result["content"][0]["text"]


def test_rejection_shows_full_review_path_before_inline_verdict():
    review_path = "/tmp/reviewer-agent/reviews/full-verdict.txt"

    def fake_commit(cwd, message, review_token, cancel_event=None):
        return {
            "status": "rejected",
            "message": "the independent reviewer rejected the staged changes",
            "review": "REJECTED\n" + ("long finding\n" * 1000),
            "review_path": review_path,
        }

    response = mcp_server.handle_message(
        _request(
            "tools/call",
            {
                "name": "review_commit",
                "arguments": {
                    "cwd": "/repo",
                    "message": "test",
                    "review_token": "token",
                },
            },
        ),
        commit_fn=fake_commit,
    )

    rendered = response["result"]["content"][0]["text"]
    assert rendered.index(review_path) < rendered.index("REJECTED")
    assert "inline output is truncated" in rendered


def test_rejection_labels_suggestions_as_nonblocking():
    def fake_commit(cwd, message, review_token, cancel_event=None):
        return {
            "status": "rejected",
            "message": "the independent reviewer rejected the staged changes",
            "review": (
                "REJECTED:\n1. Fix change.py:10\n"
                "SUGGESTIONS:\n1. Consider renaming the helper"
            ),
            "review_path": "/tmp/full-review.txt",
        }

    response = mcp_server.handle_message(
        _request(
            "tools/call",
            {
                "name": "review_commit",
                "arguments": {
                    "cwd": "/repo",
                    "message": "test",
                    "review_token": "token",
                },
            },
        ),
        commit_fn=fake_commit,
    )

    rendered = response["result"]["content"][0]["text"]
    assert "--- REQUIRED FIXES (blocking) ---" in rendered
    assert "--- SUGGESTIONS (non-blocking, not required for this commit) ---" in rendered
    assert rendered.index("Fix change.py:10") < rendered.index("Consider renaming")


def test_approval_labels_suggestions_as_nonblocking():
    def fake_commit(cwd, message, review_token, cancel_event=None):
        return {
            "status": "committed",
            "message": "the staged changes were approved and committed",
            "review": "APPROVED\nSUGGESTIONS:\n1. Consider a clearer name",
            "review_path": "/tmp/full-review.txt",
            "commit": "abc123",
        }

    response = mcp_server.handle_message(
        _request(
            "tools/call",
            {
                "name": "review_commit",
                "arguments": {
                    "cwd": "/repo",
                    "message": "test",
                    "review_token": "token",
                },
            },
        ),
        commit_fn=fake_commit,
    )

    rendered = response["result"]["content"][0]["text"]
    assert "APPROVED" in rendered
    assert "--- SUGGESTIONS (non-blocking, not required for this commit) ---" in rendered
    assert "Consider a clearer name" in rendered


def test_server_advertises_review_tools_and_native_stop_hook():
    response = mcp_server.handle_message(_request("tools/list"))
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["review_commit", "stop_hook", "interrupt_stop"]
    assert tools[0]["title"] == "Commit review - when completed, results are available here"
    assert tools[0]["inputSchema"]["required"] == ["cwd", "message", "review_token"]


def test_stdio_cancellation_interrupts_running_tool_without_late_response():
    import io
    import queue

    started = threading.Event()
    received_cancel = threading.Event()

    class Input:
        def __init__(self):
            self.lines = queue.Queue()

        def __iter__(self):
            return self

        def __next__(self):
            value = self.lines.get(timeout=2)
            if value is None:
                raise StopIteration
            return value

    def cancellable(cwd, message, token, cancel_event=None):
        started.set()
        assert cancel_event.wait(2)
        received_cancel.set()
        return {"status": "cancelled", "message": "cancelled"}

    source = Input()
    output = io.StringIO()
    server = threading.Thread(
        target=mcp_server.serve,
        kwargs={"input_stream": source, "output_stream": output, "commit_fn": cancellable},
    )
    server.start()
    source.lines.put(
        json.dumps(
            _request(
                "tools/call",
                {
                    "name": "review_commit",
                    "arguments": {
                        "cwd": "/repo",
                        "message": "test",
                        "review_token": "token",
                    },
                },
                41,
            )
        )
        + "\n"
    )
    assert started.wait(1)
    source.lines.put(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 41, "reason": "Escape"},
            }
        )
        + "\n"
    )
    assert received_cancel.wait(1)
    source.lines.put(None)
    server.join(2)

    assert output.getvalue() == ""


def test_stdio_server_initializes_and_lists_tools():
    payload = "\n".join(
        json.dumps(item)
        for item in (
            _request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
                1,
            ),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request("tools/list", request_id=2),
        )
    ) + "\n"

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mcp_server.py")],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )

    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["id"] for item in responses] == [1, 2]
    assert responses[0]["result"]["serverInfo"]["name"] == "reviewer-agent-codex"
    assert responses[1]["result"]["tools"][0]["name"] == "review_commit"
