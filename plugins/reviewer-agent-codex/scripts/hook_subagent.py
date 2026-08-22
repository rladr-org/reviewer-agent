#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Track subagent lifetime from SubagentStart/SubagentStop."""

import json
import sys

import _bootstrap  # noqa: F401
from reviewer_codex import liveness, state


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    data = json.loads(sys.stdin.read() or "{}")
    session_id = state.session_key(data)
    agent_id = data.get("agent_id")
    if action == "start":
        liveness.mark(
            data.get("cwd") or ".", session_id, "subagent", agent_id,
            data.get("agent_type") or "",
        )
    elif action == "stop":
        liveness.clear(data.get("cwd") or ".", session_id, "subagent", agent_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
