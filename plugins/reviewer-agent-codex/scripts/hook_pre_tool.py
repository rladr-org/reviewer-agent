#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PreToolUse: gate commits, register shell liveness, and capture the current plan."""

import json
import sys

import _bootstrap  # noqa: F401
from reviewer_codex import completion, gate, liveness, state


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("hook_event_name") != "PreToolUse":
        return 0
    try:
        state.invalidate_stop_permits(data.get("cwd") or ".", state.session_key(data), work=True)
    except OSError:
        pass
    binding = completion.bind_review(data)
    if binding is not None:
        print(json.dumps(binding))
        return 0
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
        liveness.mark(
            data.get("cwd") or ".", state.session_key(data), "shell", tool_id, command
        )
    if decision:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
