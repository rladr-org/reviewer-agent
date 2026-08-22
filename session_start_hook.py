#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""SessionStart hook for reviewerAgent.

When the reviewer is active for a Codex-backed project, brief a fresh Codex reviewer
session and capture its session id, persisting it for THIS Claude session. The hooks then
resume that session BY ID. Pi-backed projects skip SessionStart: Pi creates and captures its
session on the first gated action, so selecting Pi never launches or requires Codex.

Briefing here (rather than lazily on the first commit) means the reviewer session
is established at session start, exactly as intended. Inactive projects do nothing.
Failure is logged but never blocks the session (the PreToolUse hooks lazily brief as
a fallback).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    cwd = data.get("cwd") or rl.project_dir()
    if not rl.is_active(cwd):  # resolves cwd -> canonical project root internally
        return 0
    claude_sid = rl.claude_session_key(data)
    key = rl.canonical_project_key(cwd)
    backend = rl.reviewer_backend(cwd)
    if backend in ("pi", "claude"):
        # Neither backend has a briefing turn to run here: pi captures its session from the
        # first gated action's response, and claude mints its own id at that same moment. Doing
        # nothing at session start is what keeps selecting one of them from launching codex.
        rl.log(
            "[session_start] key={} claude_sid={} backend={} -> lazy first gated action".format(
                key, rl.short(str(claude_sid), 60), backend
            )
        )
        return 0
    sid, serr = rl.ensure_session(cwd, claude_sid)
    if sid:
        rl.log(
            "[session_start] key={} claude_sid={} codex_sid={} -> reviewer ready".format(
                key, rl.short(str(claude_sid), 60), sid
            )
        )
    else:
        rl.log(
            "[session_start] key={} claude_sid={} brief FAILED: {}".format(
                key, rl.short(str(claude_sid), 60), serr
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
