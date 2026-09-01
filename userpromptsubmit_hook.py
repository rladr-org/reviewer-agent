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
import directive_toggle  # noqa: E402

# The literal the reviewerDirective skill emits (no bang) and this hook detects. The directive text
# arrives in the JSON `prompt` field -- never through a shell -- so it is injection-proof: shell
# metacharacters like `$(...)`/backticks and pasted content cannot execute. (A crafted directive is
# still a prompt-injection vector against the reviewer once recorded -- a separate, unchanged
# concern, per SECURITY.md.)
DIRECTIVE_SENTINEL = "<<reviewer-agent directive>>"


def _directive_block(data, cwd):
    """If the prompt is a reviewerDirective invocation, record it under the payload cwd (the
    AUTHORITATIVE project, not the hook process's own directory) and return a top-level blocking
    result so the model turn is suppressed and the outcome is shown; else None."""
    prompt = str(data.get("prompt") or "")
    if not prompt.lstrip().startswith(DIRECTIVE_SENTINEL):
        return None
    remainder = prompt.split(DIRECTIVE_SENTINEL, 1)[1].strip()
    toks = remainder.split()
    verb = toks[0].lower() if toks else ""
    is_command = (
        not toks
        or (len(toks) == 1 and verb in ("list", "clear"))
        or (len(toks) == 2 and verb == "remove")
    )
    # A recognized subcommand needs its tokens; free text is passed as ONE verbatim element so it is
    # recorded exactly as typed (no whitespace normalization, no re-parse).
    args = toks if is_command else [remainder]
    _code, message = directive_toggle.run(args, cwd)
    rl.log("[userpromptsubmit] key={} reviewerDirective handled".format(
        rl.canonical_project_key(cwd)))
    return {"decision": "block", "reason": message, "systemMessage": message}


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
        block = _directive_block(data, cwd)
        if block is not None:
            print(json.dumps(block))
            return 0
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
