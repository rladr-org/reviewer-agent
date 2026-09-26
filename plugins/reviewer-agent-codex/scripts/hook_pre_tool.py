#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PreToolUse: gate commits, register shell liveness, and capture the current plan."""

import json
import sys


def main():
    # Imports belong inside the entrypoint's failure boundary too: a broken or
    # evicted plugin must not turn a commit interception into an empty response.
    import _bootstrap  # noqa: F401
    from reviewer_codex import gate, liveness, state

    data = json.loads(sys.stdin.read() or "{}")
    if not isinstance(data, dict):
        raise ValueError("hook input must be an object")
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    for field in ("cwd", "session_id", "turn_id", "tool_name", "tool_use_id"):
        if data.get(field) is not None and not isinstance(data[field], str):
            raise ValueError("invalid hook field: " + field)
    if data.get("tool_name") in ("Bash", "update_plan"):
        tool_input = data.get("tool_input")
        if tool_input is not None and not isinstance(tool_input, dict):
            raise ValueError("tool_input must be an object")
        if data.get("tool_name") == "Bash":
            command = (tool_input or {}).get("command")
            if command is not None and not isinstance(command, str):
                raise ValueError("command must be a string")
    try:
        state.invalidate_stop_permits(data.get("cwd") or ".", state.session_key(data), work=True)
    except OSError:
        pass
    if data.get("tool_name") == "update_plan":
        try:
            state.record_plan(
                data.get("cwd") or ".", state.session_key(data),
                data.get("tool_input") or {},
            )
        except OSError:
            pass
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    decision = gate.review_commit(data)
    denied = bool(
        decision
        and (decision.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
    )
    if not denied:
        tool_id = data.get("tool_use_id")
        command = ((data.get("tool_input") or {}).get("command") or "")
        try:
            liveness.mark(
                data.get("cwd") or ".", state.session_key(data), "shell", tool_id, command
            )
        except OSError:
            # Stop-supervision bookkeeping is best effort; the gate decision has
            # already completed and its errors are handled by the outer boundary.
            pass
    if decision:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        # Do not import the failing plugin or write state to report a failure.
        # Avoid echoing command contents or exception details into host output.
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "reviewer-agent-codex could not check this tool call ({}). "
                    "Repair the hook installation/input or state storage and retry. "
                    "No review token was issued by this response."
                ).format(type(error).__name__),
            },
        }))
        result = 0
    raise SystemExit(result)
