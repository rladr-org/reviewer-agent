#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Stop hook for reviewerAgent (stopContinue feature).

When the audited agent ENDS ITS TURN, this hook asks the project's selected reviewer to act as a STOP
SUPERVISOR: should the agent really stop, or is it stopping PREMATURELY ("this is a
reasonable stopping point", pausing for confirmation it doesn't need, or asking something
it could resolve itself)? If there is more to do, the reviewer returns a directive and the hook
BLOCKS the stop, feeding that directive back so the agent keeps working. If the task is
genuinely done -- or the only thing left is a human-only decision -- the reviewer returns STOP_OK
and the stop proceeds.

Gated by a SEPARATE per-project flag (`/stopContinue on`). Fail-open: any backend failure, an
empty final message, or a STOP_OK verdict lets the stop proceed to the user. Bounded by a
per-turn nudge cap (REVIEWER_STOPCONTINUE_MAX_NUDGES) so it can keep pushing without ever
running away; the budget resets on each fresh (user-initiated) stop.

OBSERVABILITY: every invocation logs a single `[stop] ENTER` line BEFORE any early return,
recording flag state, final-text length, stop_hook_active, and whether this is a background
job. That makes "the hook never fired" (NO line at the stop timestamp -> the Stop event
never reached the hook, e.g. a stale hook snapshot) distinguishable from "the hook fired but
bailed" (an ENTER line showing flag=off / text_len=0). A top-level guard logs any uncaught
exception (`[stop] EXCEPTION ...`) and fails open, so a hook crash can never silently read as
a non-firing hook.
"""
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402

STOP_TIMEOUT = 300


def _main(data):
    cwd = data.get("cwd") or rl.project_dir()
    key = rl.canonical_project_key(cwd)
    claude_sid = rl.claude_session_key(data)
    flag_active = rl.is_stopcontinue_active(cwd)
    stop_active = bool(data.get("stop_hook_active"))
    try:
        text = rl.last_assistant_text(data.get("transcript_path"))
    except Exception:
        text = ""
    # Did the agent END ITS TURN parked on a still-running harness-tracked background task
    # (a run_in_background sub-agent/shell the harness auto-re-invokes on completion)? If so
    # the stop is pure-suppressed below: allowed immediately with NO supervisor call and NO
    # nudge (the parked agent needs no prodding; it resumes when the harness wakes it).
    try:
        bg_wait = rl.has_outstanding_background_task(data.get("transcript_path"))
    except Exception:
        bg_wait = False

    # ENTER trace: proof-of-invocation, emitted for EVERY stop before any early return.
    # bg=1 marks a background job (CLAUDE_JOB_DIR set) so we can see whether the Stop event
    # is being delivered to background sessions at all.
    bg = "1" if os.environ.get("CLAUDE_JOB_DIR") else "0"
    rl.log(
        "[stop] ENTER key={} sess={} bg={} stop_hook_active={} flag={} text_len={} "
        "bg_wait={}".format(
            key, str(claude_sid)[:12], bg, stop_active,
            "on" if flag_active else "off", len(text), bg_wait,
        )
    )

    if not flag_active:
        return 0
    if not text.strip():
        return 0  # tool-only / nothing to evaluate -> allow the stop

    # Pure suppress: the agent ENDED ITS TURN parked on a still-running harness-tracked
    # background task. The harness auto-re-invokes this session the moment that task
    # completes, so nudging the parked agent only makes it spin. Allow the stop with NO
    # supervisor call and NO budget consumption; the session resumes on completion.
    if bg_wait:
        rl.log(
            "[stop] key={} background wait (harness auto-wakes on completion) -> allow stop, "
            "no supervisor call".format(key)
        )
        return 0

    # A fresh (NOT hook-driven) stop is the first stop of a user turn -> reset the budget.
    if not stop_active:
        rl.reset_nudge_count(cwd, claude_sid)
    used = rl.read_nudge_count(cwd, claude_sid)
    cap = rl.max_nudges()
    if used >= cap:
        rl.log("[stop] key={} nudge cap {} reached -> allow stop".format(key, cap))
        print(json.dumps(rl.info_output(
            "[reviewerAgent] stopContinue: nudge cap ({}) reached for this turn; letting the "
            "agent stop.".format(cap)
        )))
        return 0

    transcript_path = rl.resolve_transcript_path(data)
    prompt = rl.apply_context(
        rl.STOP_SUPERVISE_PROMPT.format(text),
        cwd,
        transcript_path,
        claude_sid,
        role=rl.ROLE_STOP,
    )
    backend = rl.reviewer_backend(cwd)
    if backend == "claude":
        # Its own ROLE_STOP session, for the same reason codex has one: supervision runs at
        # every turn end, and sharing a session let its framing bleed into the commit audit.
        effort = rl.stopcontinue_effort()
        if not rl.is_valid_backend_effort("claude", effort):
            # `low` is the floor claude accepts; codex's `none`/`minimal` and pi's `off` are not
            # levels here, and sending one fails the call -- which for a fail-OPEN supervisor
            # would quietly disable stopContinue rather than announce anything.
            rl.log("[stop] key={} invalid claude stop effort {!r} -> low".format(key, effort))
            effort = "low"
        code, out, err = rl.run_claude_review(
            cwd,
            claude_sid,
            prompt,
            timeout=STOP_TIMEOUT,
            effort=effort,
            role=rl.ROLE_STOP,
        )
        sid = rl.read_claude_session(cwd, claude_sid, role=rl.ROLE_STOP) or "unestablished"
    elif backend == "pi":
        thinking = rl.stopcontinue_effort()
        if not rl.is_valid_backend_effort("pi", thinking):
            rl.log(
                "[stop] key={} invalid Pi stop effort {!r} -> low".format(key, thinking)
            )
            thinking = "low"
        code, out, err = rl.run_pi_review(
            cwd,
            claude_sid,
            prompt,
            timeout=STOP_TIMEOUT,
            thinking=thinking,
            role=rl.ROLE_STOP,
            require_staged_read=False,
        )
        sid = rl.read_pi_session(cwd, claude_sid, role=rl.ROLE_STOP) or "unestablished"
    else:
        # The STOP role, not the commit/question reviewer's: supervision runs at every turn
        # end, and sharing one session let its framing bleed into the next commit audit.
        sid, serr = rl.ensure_session(cwd, claude_sid, role=rl.ROLE_STOP)
        if sid is None:
            if rl.model_rejection_detail(backend, serr):
                print(json.dumps(rl.info_output(
                    "[stopContinue] the supervisor could not start.\n"
                    + rl.model_rejection_message(backend, serr)
                )))
            rl.log(
                "[stop] key={} backend=codex ensure_session FAILED: {} -> allow stop".format(
                    key, serr
                )
            )
            return 0  # fail-open
        code, out, err = rl.run_codex_resume_by_id(
            sid,
            cwd,
            "read-only",
            prompt,
            timeout=STOP_TIMEOUT,
            extra_config=[rl.stopcontinue_effort_config()],
        )
    keep_going = code == 0 and bool(out.strip()) and not rl.is_stop_ok(out)
    if code != 0 and (rl.usage_limit_detail(err) or rl.model_rejection_detail(backend, err)):
        # Stop supervision fails OPEN, so this cannot block anything -- but silently allowing
        # every stop because the configured model is unreachable, or because the account's
        # allowance is spent, looks exactly like the feature being switched off. An exhausted
        # allowance is the likelier of the two on the claude backend, and it has its own remedy.
        print(json.dumps(rl.info_output(
            "[stopContinue] the supervisor could not run.\n"
            + (rl.usage_limit_message(backend, err) if rl.usage_limit_detail(err)
               else rl.model_rejection_message(backend, err))
        )))
    provenance = rl.context_provenance(cwd, transcript_path, claude_sid)
    rl.log(
        "[stop] key={} backend={} reviewer_sid={} exit={} keep_going={} used={}/{} final={!r} "
        "directive_tail={!r} stderr_tail={!r}{}".format(
            key, backend, sid, code, keep_going, used, cap, rl.short(text, 200),
            rl.tail(out, 400), rl.tail(err, 200),
            ("\n" + provenance) if provenance else "",
        )
    )

    if keep_going:
        rl.bump_nudge_count(cwd, claude_sid)
        reason = (
            # No "your message may never be shown -- restate it" directive here: a blocked
            # stop still shows the operator what the agent wrote, so that instruction only
            # produced redundant repetition at the top of every continued turn.
            #
            # Attribution is load-bearing, not cosmetic. This is the ROLE_STOP session, which
            # is deliberately NOT the session that audits commits. Saying "the reviewer" here
            # let an agent read "do X, then commit" as the gate having pre-agreed to the
            # commit -- it has not, it has never seen this exchange, and being fail-closed it
            # is the direction that rejects.
            "The stopContinue supervisor reviewed your stop and judges the task NOT yet "
            "complete. Do the following and KEEP WORKING; do NOT stop yet:\n\n"
            + out.strip()
        )
        msg = "[stopContinue supervisor] keep going:\n" + rl.short(out)
        print(json.dumps(rl.stop_block_output(reason, msg)))
    elif code == 0 and out.strip() and rl.is_stop_ok(out):
        # The stop proceeds, but the reviewer's explicit verdict is echoed so stop-path
        # silence always means "the reviewer was not consulted / did not answer".
        print(json.dumps(rl.info_output(
            "[stopContinue supervisor] approved this stop:\n" + rl.short(out)
        )))
    # else: empty / failure -> allow the stop silently (fail-open, logged above).
    return 0


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return _main(data)
    except Exception as e:
        # A hook error must NEVER wedge the agent -> fail open. But log it, so a crash is
        # visible as a crash rather than looking like the hook simply never fired.
        try:
            rl.log("[stop] EXCEPTION {}: {}".format(
                type(e).__name__, rl.tail(traceback.format_exc(), 500)))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
