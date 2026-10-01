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
import re
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
# Claude Code hands this hook the prompt AS TYPED (`/reviewer-agent:reviewerDirective list`), before
# the skill body carrying the sentinel is expanded (verified on 2.1.285), so the typed command is
# the form that actually arrives. It is read from the same JSON field, so it stays injection-proof.
_DIRECTIVE_COMMAND = re.compile(r"\A\s*/(?:reviewer-agent:)?reviewerDirective(?=\s|\Z)")


def _directive_remainder(prompt):
    """The directive arguments when `prompt` invokes reviewerDirective, else None."""
    if prompt.lstrip().startswith(DIRECTIVE_SENTINEL):
        return prompt.split(DIRECTIVE_SENTINEL, 1)[1].strip()
    match = _DIRECTIVE_COMMAND.match(prompt)
    return prompt[match.end():].strip() if match else None


def _directive_block(data, cwd):
    """If the prompt is a reviewerDirective invocation, record it under the payload cwd (the
    AUTHORITATIVE project, not the hook process's own directory) and return a top-level blocking
    result so the model turn is suppressed and the outcome is shown; else None."""
    remainder = _directive_remainder(str(data.get("prompt") or ""))
    if remainder is None:
        return None
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
    markers when the watchdog is effective, and stamps tick liveness."""
    prompt = str(data.get("prompt") or "")
    sid = rl.claude_session_key(data)
    # Operator intent outranks liveness: a watchdog that is off stays off, however dead its ticks
    # look. This gate is deliberately BEFORE the liveness check below. Clearing on the way out
    # matters as much as not arming: with stopContinue off the tick tears the cron down itself, so
    # a stamp left behind would make that dead cron look alive and suppress the re-arm that
    # re-enabling depends on.
    if not rl.is_stopcontinue_watchdog_effective(cwd):
        rl.clear_watchdog_tick_seen(cwd, sid)
        return None
    # Defense in depth: never arm in response to a watchdog tick. The tick reaches the agent
    # THROUGH this hook, so this is also where the cron's liveness is observed, for free.
    if "stopContinueWatchdog tick" in prompt:
        rl.set_watchdog_tick_seen(cwd, sid)
        # An interrupt is a deliberate pause, like an approved stop: mark it so the tick (which
        # reads this marker next) replies `ok` instead of resuming work. Only if unset, so an
        # aging marker keeps its timestamp for the idle teardown.
        if not rl.is_watchdog_disarm_set(cwd, sid) and rl.ends_with_operator_interrupt(
                rl._transcript_events(rl.resolve_transcript_path(data))):
            rl.set_watchdog_disarm(cwd, sid)
        return None
    rearm = (
        rl.is_watchdog_teardown_ready(cwd, sid)
        or rl.watchdog_disarm_deadline_passed(cwd, sid)
        # The restart-safe path: the cron is gone and no tick has arrived to say otherwise.
        or rl.watchdog_ticks_presumed_dead(cwd, sid)
    )
    rl.clear_watchdog_disarm(cwd, sid)
    rl.clear_watchdog_teardown_ready(cwd, sid)
    if not rearm:
        return None
    # Asking counts as liveness for one window, so a busy stretch that legitimately produces no
    # ticks does not append an arm block to every prompt. If the arm did not take, the marker
    # ages out and this retries.
    rl.set_watchdog_tick_seen(cwd, sid)
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
