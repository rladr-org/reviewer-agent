# SPDX-License-Identifier: MPL-2.0
"""Cancellable standard-library MCP server for commit and stop rulings."""

import json
import sys
import threading

from .cancellation import Cancellation
from .commit_tool import reviewed_commit, split_suggestions
from .stop_tool import review_stop


SERVER_NAME = "reviewer-agent-codex"
SERVER_VERSION = "1.2.1-beta.1"
PROTOCOL_VERSION = "2025-06-18"

TOOL = {
    "name": "review_commit",
    "title": "Review and commit staged changes",
    "description": (
        "Use instead of shell git commit when reviewer-agent is enabled. This is one "
        "blocking operation: Codex remains suspended until the independent staged-index "
        "review returns, then the tool commits only an APPROVED staged set and returns "
        "the full verdict. Never poll while this tool is pending."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cwd": {
                "type": "string",
                "description": "Absolute path inside the target Git repository.",
            },
            "message": {
                "type": "string",
                "minLength": 1,
                "description": "Git commit message to use only after approval.",
            },
            "review_token": {
                "type": "string",
                "description": "One-use token returned by the intercepted shell git commit.",
            },
        },
        "required": ["cwd", "message", "review_token"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "openWorldHint": False,
    },
}

STOP_TOOL = {
    "name": "stop_continue",
    "title": "Check whether the current task may stop",
    "description": (
        "Use once when the Stop hook routes completion checking here. The call remains pending "
        "until the dedicated minimal stop supervisor rules. It can be cancelled with Escape."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cwd": {
                "type": "string",
                "description": "Absolute path inside the current Git repository.",
            },
            "review_token": {
                "type": "string",
                "description": "One-use token returned by the Stop hook.",
            },
        },
        "required": ["cwd", "review_token"],
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
}


def _response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": str(message)},
    }


def _render(result):
    parts = [str(result.get("message") or "review_commit finished")]
    if result.get("review_path"):
        parts += [
            "",
            "Full review (open this file if inline output is truncated): "
            + str(result["review_path"]),
        ]
    if result.get("review"):
        review = str(result["review"])
        verdict, suggestions = split_suggestions(review)
        if suggestions is None:
            parts += ["", review]
        else:
            suggestion_body = suggestions.split(":", 1)[1].strip()
            if result.get("status") == "rejected":
                parts += ["", "--- REQUIRED FIXES (blocking) ---", verdict]
            else:
                parts += ["", verdict]
            parts += [
                "",
                "--- SUGGESTIONS (non-blocking, not required for this commit) ---",
                suggestion_body,
            ]
    if result.get("git_output"):
        parts += ["", str(result["git_output"])]
    if result.get("ruling"):
        parts += ["", str(result["ruling"])]
    if result.get("directive"):
        parts += ["", str(result["directive"])]
    if result.get("detail"):
        parts += ["", str(result["detail"])]
    return "\n".join(parts)


def handle_message(
    message, commit_fn=reviewed_commit, stop_fn=review_stop, cancel_event=None
):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if request_id is None:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _response(
            request_id,
            {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Call the one tool named by a reviewer-agent hook exactly once. Both long "
                    "rulings support MCP cancellation, including Escape from Codex."
                ),
            },
        )
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": [TOOL, STOP_TOOL]})
    if method == "tools/call":
        if params.get("name") not in (TOOL["name"], STOP_TOOL["name"]):
            return _error(request_id, -32601, "unknown tool")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        try:
            if params.get("name") == TOOL["name"]:
                result = commit_fn(
                    arguments.get("cwd"),
                    arguments.get("message"),
                    arguments.get("review_token"),
                    cancel_event=cancel_event,
                )
            else:
                result = stop_fn(
                    arguments.get("cwd"),
                    arguments.get("review_token"),
                    cancel_event=cancel_event,
                )
        except Exception as error:
            result = {"status": "error", "message": "reviewer tool failed: {}".format(error)}
        return _response(
            request_id,
            {
                "content": [{"type": "text", "text": _render(result)}],
                "structuredContent": result,
                "isError": result.get("status") in ("error", "disabled", "cancelled"),
            },
        )
    return _error(request_id, -32601, "method not found")


def serve(input_stream=None, output_stream=None, commit_fn=reviewed_commit, stop_fn=review_stop):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    active = {}
    active_lock = threading.Lock()
    output_lock = threading.Lock()

    def send(response):
        if response is not None:
            with output_lock:
                output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
                output_stream.flush()

    def run_call(message, cancelled):
        request_id = message.get("id")
        response = handle_message(
            message,
            commit_fn=commit_fn,
            stop_fn=stop_fn,
            cancel_event=cancelled,
        )
        with active_lock:
            active.pop(request_id, None)
        completed, _unused = cancelled.complete()
        if completed:
            send(response)

    try:
        for line in input_stream:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except (TypeError, ValueError) as error:
                send(_error(None, -32700, "invalid JSON-RPC message: {}".format(error)))
                continue
            if message.get("method") == "notifications/cancelled":
                request_id = (message.get("params") or {}).get("requestId")
                with active_lock:
                    running = active.get(request_id)
                if running is not None:
                    running[0].cancel()
                continue
            if message.get("method") == "tools/call" and message.get("id") is not None:
                cancelled = Cancellation()
                worker = threading.Thread(
                    target=run_call, args=(message, cancelled), daemon=True
                )
                with active_lock:
                    active[message["id"]] = (cancelled, worker)
                worker.start()
                continue
            send(handle_message(message, commit_fn=commit_fn, stop_fn=stop_fn))
    finally:
        with active_lock:
            running = list(active.values())
        for cancelled, _worker in running:
            cancelled.cancel()
        for _cancelled, worker in running:
            worker.join(timeout=2)


def main():
    serve()
    return 0
