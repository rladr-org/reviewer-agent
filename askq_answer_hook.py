#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PostToolUse hook for AskUserQuestion -- SHOW the question, answer only on timeout.

The question widget renders normally (there is no PreToolUse interception), so the operator
SEES it and can answer or Escape. If the operator does NOT answer within the native
`askUserQuestionTimeout` window, Claude Code auto-closes the tool and the tool_response carries
`afkTimeoutMs` with an empty `answers` map. ONLY in that away-from-keyboard case does this hook
ask the reviewer to answer on the operator's behalf, delivering the answer via
`additionalContext` -- never a deny, so nothing the agent printed is ever hidden.

tool_response shapes (captured live from a real AskUserQuestion, 2026-07-24):
  answered:  {"questions":[...], "answers": {"<question text>": "<value>"}, "annotations": {}}
  timed out: {"questions":[...], "answers": {}, "annotations": {}, "afkTimeoutMs": 60000}

Every invocation also logs the raw tool_response ([askq-capture]) for observability. The
answer path is gated by the main reviewer flag (`is_active`) and uses that project's selected
backend. When the reviewer is off, the hook only captures and never answers. Fail-safe: any
error is swallowed so the agent is never wedged and no output is hidden.
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402

ASKQ_TIMEOUT = 300


def _tool_response(data):
    resp = data.get("tool_response")
    if resp is None:
        resp = data.get("tool_result")
    return resp


def _response_blob(data):
    resp = _tool_response(data)
    if resp is None:
        return "<none>"
    if isinstance(resp, str):
        return resp
    try:
        return json.dumps(resp, default=str)
    except (TypeError, ValueError):
        return str(resp)


def _operator_answered(tool_response):
    """True iff the operator supplied an answer. The answered shape carries a NON-EMPTY
    `answers` dict (question text -> chosen/typed value); a timeout leaves it `{}`. A non-empty
    map also covers the case where the operator pre-selected an option and then walked away --
    that is still their selection, not the reviewer's to override."""
    if not isinstance(tool_response, dict):
        return False
    answers = tool_response.get("answers")
    return isinstance(answers, dict) and len(answers) > 0


def _afk_timed_out(tool_response):
    """True iff Claude Code auto-closed the question on the away-from-keyboard timeout. The
    definitive marker is `afkTimeoutMs`, which appears ONLY on the timeout path."""
    return isinstance(tool_response, dict) and "afkTimeoutMs" in tool_response


def _should_reviewer_answer(tool_response):
    """The reviewer answers on the operator's behalf ONLY on a genuine away-from-keyboard
    timeout where the operator supplied no answer. Requiring BOTH conditions means the reviewer
    never overrides a real human answer and never fires on some other empty state."""
    return _afk_timed_out(tool_response) and not _operator_answered(tool_response)


def _capture(key, claude_sid, qtext, blob):
    rl.log(
        "[askq-capture] key={} sess={} q={!r}\n--- tool_response (raw) ---\n{}\n--- end ---".format(
            key, str(claude_sid)[:12], rl.short(qtext, 300), blob
        )
    )


def _main(data):
    cwd = data.get("cwd") or rl.project_dir()
    key = rl.canonical_project_key(cwd)
    claude_sid = rl.claude_session_key(data)
    tool_input = data.get("tool_input", {})
    qtext = rl.serialize_questions(tool_input)
    tool_response = _tool_response(data)

    # Observability: always capture the raw tool_response so the answered-vs-timeout signal
    # stays verifiable against reality.
    _capture(key, claude_sid, qtext, _response_blob(data))

    # Only a genuine away-from-keyboard timeout (operator did not answer) engages the reviewer.
    if not _should_reviewer_answer(tool_response):
        return 0

    # is_active gate: the reviewer answers only where it is turned on for the project.
    if not rl.is_active(cwd):
        return 0

    prompt = (
        "QUESTION FROM AGENT (the operator did not answer within the timeout; answer on their "
        "behalf per your brief, choosing among the options):\n" + qtext
    )
    transcript_path = rl.resolve_transcript_path(data)
    backend = rl.reviewer_backend(cwd)
    if backend == "claude":
        # No staged-read requirement to apply: this turn has no staged set, and the claude
        # backend never gates on one anyway -- the patch is handed to it or the commit is
        # blocked before the reviewer is called at all.
        code, out, err = rl.run_claude_review(
            cwd,
            claude_sid,
            rl.apply_context(prompt, cwd, transcript_path, claude_sid),
            timeout=ASKQ_TIMEOUT,
            effort=rl.reviewer_effort("claude"),
            role=rl.ROLE_REVIEWER,
        )
        sid = rl.read_claude_session(cwd, claude_sid, role=rl.ROLE_REVIEWER) or "unestablished"
    elif backend == "pi":
        code, out, err = rl.run_pi_review(
            cwd,
            claude_sid,
            rl.apply_context(prompt, cwd, transcript_path, claude_sid),
            timeout=ASKQ_TIMEOUT,
            thinking=rl.reviewer_effort("pi"),
            role=rl.ROLE_REVIEWER,
            require_staged_read=False,
        )
        sid = rl.read_pi_session(cwd, claude_sid, role=rl.ROLE_REVIEWER) or "unestablished"
    else:
        sid, serr = rl.ensure_session(cwd, claude_sid)
        if sid is None:
            # Same reason as the commit gate: a refused model fails at the brief, so this is
            # where the provider's sentence first appears.
            diagnosis = (
                rl.model_rejection_message(backend, serr)
                if rl.model_rejection_detail(backend, serr) else ""
            )
            msg = (
                "[reviewerAgent] AskUserQuestion timed out unanswered but the reviewer could not "
                "start ({}). Proceeding on the agent's own judgment.{}".format(
                    rl.short(serr, 200), ("\n" + diagnosis) if diagnosis else "")
            )
            rl.log(
                "[askq-answer] key={} backend=codex ensure_session FAILED: {} -> no "
                "injection".format(key, serr)
            )
            print(json.dumps(rl.info_output(msg)))
            return 0
        code, out, err = rl.run_codex_resume_by_id(
            sid,
            cwd,
            "read-only",
            rl.apply_context(prompt, cwd, transcript_path, claude_sid),
            timeout=ASKQ_TIMEOUT,
        )
    provenance = rl.context_provenance(cwd, transcript_path, claude_sid)
    rl.log(
        "[askq-answer] key={} backend={} reviewer_sid={} exit={} q={!r} answer_tail={!r} "
        "stderr_tail={!r}{}".format(
            key, backend, sid, code, rl.short(prompt, 200), rl.tail(out, 600),
            rl.tail(err, 300),
            ("\n" + provenance) if provenance else "",
        )
    )

    if code == 0 and out.strip():
        # additionalContext feeds the reviewer's answer to Claude next to the (already-visible)
        # tool result -- Claude acts on it, and nothing is hidden.
        context = (
            "The operator did not answer the AskUserQuestion within the timeout, so "
            "reviewerAgent answered on their behalf. Use this answer and continue; do not "
            "re-ask:\n\n" + out.strip()
        )
        # Echo the full exchange to the operator. AskUserQuestion's own schema bounds the
        # question size, so the question is echoed uncapped; the answer is lightly capped.
        msg = (
            "[reviewerAgent] AskUserQuestion timed out -> answered on the user's behalf.\n"
            "--- the agent asked ---\n{}\n"
            "--- the reviewer answered ---\n{}".format(qtext, rl.short(out, 3000))
        )
        print(json.dumps(rl.posttool_context_output(context, msg)))
    else:
        # This path is fail-OPEN, so a refused model costs an answer rather than a commit -- but
        # it is the same misconfiguration, and an operator who sees it here should not have to
        # wait for a blocked commit to learn what to type.
        # A usage limit is checked first and separately, for the same reason the commit gate
        # separates them: its remedy is the opposite of a model refusal's -- wait or switch
        # backend, rather than change the selection -- and on this fail-open path the operator
        # is otherwise told only that an answer did not arrive.
        if rl.usage_limit_detail(err):
            diagnosis = rl.usage_limit_message(backend, err)
        elif rl.model_rejection_detail(backend, err):
            diagnosis = rl.model_rejection_message(backend, err)
        else:
            diagnosis = ""
        msg = (
            "[reviewerAgent] ERROR: AskUserQuestion timed out but the reviewer failed to answer "
            "(exit {}). {} Proceeding on the agent's own judgment.{}".format(
                code, rl.short(err, 300), ("\n" + diagnosis) if diagnosis else "")
        )
        print(json.dumps(rl.info_output(msg)))
    return 0


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return _main(data)
    except Exception as e:
        try:
            rl.log("[askq-answer] EXCEPTION {}: {}".format(
                type(e).__name__, rl.tail(traceback.format_exc(), 500)))
        except Exception:
            pass
        return 0  # never wedge the agent


if __name__ == "__main__":
    raise SystemExit(main())
