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

SEPARATELY -- and independent of the main reviewer flag -- when stopContinueWatchdog is on
for the project, this hook injects `additionalContext` telling the fresh/resumed session to
re-arm its CronCreate recovery cron (the backstop for the `/stopContinueWatchdog on`-turn
arming). See docs/plans/stopcontinue_watchdog.md.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _brief_if_active(data, cwd):
    """Establish/capture the reviewer session at session start (Codex backend only). Pure
    side effects + logging; never prints and never raises out."""
    if not rl.is_active(cwd):  # resolves cwd -> canonical project root internally
        return
    claude_sid = rl.claude_session_key(data)
    key = rl.canonical_project_key(cwd)
    backend = rl.reviewer_backend(cwd)
    if backend in ("pi", "claude"):
        rl.log(
            "[session_start] key={} claude_sid={} backend={} -> lazy first gated action".format(
                key, rl.short(str(claude_sid), 60), backend
            )
        )
        return
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


def _watchdog_rearm_context(cwd):
    """The SessionStart additionalContext that re-arms the watchdog cron, or None. Gated on the
    EFFECTIVE state (watchdog flag AND stopContinue active), so a session that resumes after
    stopContinue was turned off does not re-arm ticks the now-inert Stop hook cannot supervise.
    Independent of the main reviewer flag -- the watchdog is its own opt-in."""
    if not rl.is_stopcontinue_watchdog_effective(cwd):
        return None
    return rl.watchdog_arm_instruction(rl.watchdog_interval(cwd))


def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    cwd = data.get("cwd") or rl.project_dir()
    # Reclaim abandoned reviewer scratch on EVERY session start -- unconditional, so it also runs for
    # inactive, Pi and Claude sessions (unlike the codex-only brief below). Liveness is the mtime
    # lease, so a live session's dir is within the TTL and kept. Never blocks the session.
    try:
        rl.sweep_orphan_scratch_dirs()
    except Exception as e:
        rl.log("[session_start] scratch sweep EXCEPTION {}: {}".format(type(e).__name__, e))
    try:
        _brief_if_active(data, cwd)
    except Exception as e:
        rl.log("[session_start] brief EXCEPTION {}: {}".format(type(e).__name__, e))
    ctx = _watchdog_rearm_context(cwd)
    if ctx:
        rl.log("[session_start] key={} watchdog re-arm context injected".format(
            rl.canonical_project_key(cwd)))
        print(json.dumps(rl.sessionstart_context_output(ctx)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
