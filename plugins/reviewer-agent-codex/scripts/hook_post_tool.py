#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PostToolUse: clear a completed shell command's liveness marker."""

import json
import sys

import _bootstrap  # noqa: F401
from reviewer_codex import liveness, state


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("tool_name") == "Bash":
        liveness.clear(
            data.get("cwd") or ".", state.session_key(data), "shell",
            data.get("tool_use_id"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
