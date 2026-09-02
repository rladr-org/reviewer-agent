#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PreToolUse hook for AskUserQuestion -- reroute it through stopContinue.

Claude Code's away-from-keyboard auto-close (the `afkTimeoutMs` tool_response the old PostToolUse
answerer relied on) stopped firing, so an unanswered AskUserQuestion now blocks indefinitely and
the reviewer never gets a chance to answer. A PostToolUse hook cannot help -- it only fires after
the tool returns, by which point the operator has already answered -- and nothing fires while the
question is pending. So this hook intercepts at PreToolUse instead: it DENIES AskUserQuestion and
hands the agent the serialized question with an instruction to re-ask it as its final plain-prose
message and then stop. The existing stopContinue Stop hook then supervises that prose question --
the supervisor answers it from established operator context, or lets the agent stop so the
operator answers.

Gated on stopContinue because the ANSWER comes from the stop supervisor: with stopContinue off
there is no supervisor, so denying would strip the interactive widget with nothing to answer the
prose -- strictly worse. When stopContinue is off this hook stays out of the way (emits nothing)
and the widget renders normally. Question-answering therefore rides the stopContinue flag, not the
reviewerAgent flag.

Fail-open: any error emits nothing, so a hook fault can never wedge a question. Replaces
askq_answer_hook.py. See docs/plans/askq_stopcontinue_reroute.md.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


_INSTRUCTION = (
    "AskUserQuestion is disabled in this session. Ask the question below as your FINAL "
    "plain-prose message -- restate each question and its options, and give your recommendation "
    "-- then STOP and wait for the answer. Do not call AskUserQuestion again.\n\n"
)


def _main(data):
    if data.get("tool_name") != "AskUserQuestion":
        return 0
    cwd = data.get("cwd") or rl.project_dir()
    if not rl.is_stopcontinue_active(cwd):
        # No supervisor to answer the prose -> stay out of the way; the widget renders normally.
        return 0
    reason = _INSTRUCTION + rl.serialize_questions(data.get("tool_input") or {})
    system_message = (
        "[reviewerAgent] AskUserQuestion rerouted to stopContinue: the agent will ask in prose "
        "and the stop supervisor will answer it or defer it to you."
    )
    try:
        rl.log("[askq-reroute] denied AskUserQuestion for {} -> prose+stop".format(
            rl.canonical_project_key(cwd)))
    except Exception:
        pass  # a logging fault must not stop the deny
    print(json.dumps(rl.deny_output(reason, system_message)))
    return 0


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return _main(data)
    except Exception as e:  # noqa: BLE001 -- never wedge the agent
        try:
            rl.log("[askq-reroute] EXCEPTION {}: {}".format(
                type(e).__name__, rl.tail(traceback.format_exc(), 500)))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
