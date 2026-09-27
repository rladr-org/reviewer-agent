# SPDX-License-Identifier: MPL-2.0
"""Cancellable standard-library MCP server for commit and stop rulings."""

import json
import sys
import threading
from pathlib import Path

from .cancellation import Cancellation
from .commit_tool import reviewed_commit, split_suggestions
from .supervisor import supervise_hook


SERVER_NAME = "reviewer-agent-codex"
SERVER_VERSION = "1.3.2-beta.1"
PROTOCOL_VERSION = "2025-06-18"

TOOL = {
    "name": "review_commit",
    "title": "Commit review - when completed, results are available here",
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

STOP_HOOK_TOOL = {
    "name": "stop_hook",
    "title": "Check task completion",
    "description": (
        "Invoked automatically by the native Stop lifecycle hook. Receives the host's Stop "
        "event and returns a hook decision after cancellable completion review. Do not call "
        "this tool manually or invent a Stop event."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cwd": {"type": "string", "description": "Absolute repository working directory."},
            "session_id": {"type": "string", "minLength": 1},
            "turn_id": {"type": ["string", "null"]},
            "hook_event_name": {"type": "string", "enum": ["Stop"]},
            "stop_hook_active": {"type": "boolean"},
            "last_assistant_message": {"type": ["string", "null"]},
        },
        "required": ["cwd", "session_id", "hook_event_name"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}

INTERRUPT_TOOL = {
    "name": "interrupt_stop",
    "title": "Cancel interrupted completion review",
    "description": (
        "Invoked automatically by the Interrupt lifecycle hook to cancel native Stop reviews "
        "for the interrupted cwd, session, and turn. Do not call manually."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cwd": {"type": "string"},
            "session_id": {"type": "string", "minLength": 1},
            "turn_id": {"type": "string", "minLength": 1},
            "hook_event_name": {"type": "string", "enum": ["Interrupt"]},
        },
        "required": ["cwd", "session_id", "turn_id", "hook_event_name"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}

# Native hook cancellation is not forwarded by every host. Interrupt uses the same
# connection and can reach these tokens while the Stop workers remain blocked.
_active_stops = {}
_active_stops_lock = threading.Lock()


def _stop_key(arguments, event_name):
    if arguments.get("hook_event_name") != event_name:
        return None
    fields = [arguments.get(name) for name in ("cwd", "session_id", "turn_id")]
    if not all(isinstance(value, str) and value.strip() for value in fields):
        return None
    cwd, session_id, turn_id = fields
    if not Path(cwd).is_absolute():
        return None
    try:
        return str(Path(cwd).resolve()), session_id, turn_id
    except (OSError, RuntimeError, ValueError):
        return None


def _native_stop(arguments, cancel_event):
    token = cancel_event if cancel_event is not None else Cancellation()
    key = _stop_key(arguments, "Stop")
    if key is not None:
        with _active_stops_lock:
            _active_stops.setdefault(key, set()).add(token)
    try:
        return supervise_hook(arguments, cancel_event=token)
    finally:
        if key is not None:
            with _active_stops_lock:
                tokens = _active_stops.get(key)
                if tokens is not None:
                    tokens.discard(token)
                    if not tokens:
                        _active_stops.pop(key, None)


def _interrupt_stop(arguments):
    key = _stop_key(arguments, "Interrupt")
    with _active_stops_lock:
        tokens = tuple(_active_stops.get(key, ()))
    # Callbacks may write bypass state and must not run while holding the registry lock.
    # A token that completed between snapshot and cancellation rejects cancellation itself.
    for token in tokens:
        token.set()
    return {}


def _hook_response(request_id, result):
    return _response(request_id, {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": False,
    })


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
    message, commit_fn=reviewed_commit, cancel_event=None
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
                    "Call review_commit once when named by a "
                    "reviewer-agent hook. Completion supervision runs automatically when "
                    "Codex emits its Stop event. Do not initiate a completion review during "
                    "ongoing work or manually call stop_hook. All reviews support MCP "
                    "cancellation, including Escape."
                ),
            },
        )
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": [TOOL, STOP_HOOK_TOOL, INTERRUPT_TOOL]})
    if method == "tools/call":
        if params.get("name") not in (TOOL["name"], STOP_HOOK_TOOL["name"], INTERRUPT_TOOL["name"]):
            return _error(request_id, -32601, "unknown tool")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments must be an object")
        if params.get("name") == STOP_HOOK_TOOL["name"]:
            return _hook_response(request_id, _native_stop(arguments, cancel_event))
        if params.get("name") == INTERRUPT_TOOL["name"]:
            return _hook_response(request_id, _interrupt_stop(arguments))
        try:
            result = commit_fn(
                arguments.get("cwd"),
                arguments.get("message"),
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
                "isError": result.get("status") in ("error", "disabled", "cancelled", "publication_unknown"),
            },
        )
    return _error(request_id, -32601, "method not found")


def serve(input_stream=None, output_stream=None, commit_fn=reviewed_commit):
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
            send(handle_message(message, commit_fn=commit_fn))
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
