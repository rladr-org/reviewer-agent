#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""UserPromptSubmit hook: watchdog auto-rearm.

When the operator submits a prompt after the watchdog has torn its cron down (the session went
idle for a full interval), re-arm it. The re-arm gate is deterministic and hook-side: re-arm iff
the session's `teardown_ready` marker is present OR (the reliable in-hook fallback) the disarm
marker is at least one interval old -- both mean a tick has, or is about to have, torn the cron
down. Neither is true during active work, so no reconcile noise is added there. Clears both
markers so the next cycle starts fresh.

Fail-open: any error emits nothing (a missing re-arm just leaves the SessionStart arm / next
prompt to recover it -- it never wedges the turn). See docs/plans/watchdog_auto_shutoff.md.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _rearm_context(data, cwd):
    """The additionalContext that re-arms the cron, or None. Side effect: clears both watchdog
    markers when the watchdog is effective."""
    prompt = str(data.get("prompt") or "")
    # Defense in depth: never arm in response to a watchdog tick.
    if "stopContinueWatchdog tick" in prompt:
        return None
    if not rl.is_stopcontinue_watchdog_effective(cwd):
        return None
    sid = rl.claude_session_key(data)
    rearm = (
        rl.is_watchdog_teardown_ready(cwd, sid)
        or rl.watchdog_disarm_deadline_passed(cwd, sid)
    )
    rl.clear_watchdog_disarm(cwd, sid)
    rl.clear_watchdog_teardown_ready(cwd, sid)
    if not rearm:
        return None
    return rl.watchdog_arm_instruction(cwd, rl.watchdog_interval(cwd), sid)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        cwd = data.get("cwd") or rl.project_dir()
        ctx = _rearm_context(data, cwd)
        if ctx:
            rl.log("[userpromptsubmit] key={} watchdog re-arm context injected".format(
                rl.canonical_project_key(cwd)))
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": ctx,
            }}))
    except Exception as e:
        try:
            rl.log("[userpromptsubmit] EXCEPTION {}: {}".format(type(e).__name__, e))
        except Exception:
            pass
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
