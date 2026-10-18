# SPDX-License-Identifier: MPL-2.0
"""Cancellable minimal stopContinue ruling for the bundled MCP server."""

import re
from pathlib import Path

from . import backend, cancellation, liveness, prompts, state, stop_input

# The sentinel ending the TRAILING line, alone or as its own sentence after a full stop, `!` or `?`
# ("...an unrequested code fix. STOP_OK"), tolerating only markdown emphasis / backticks and
# trailing punctuation. Exact token, never a prefix (STOP_OKAY is not the sentinel), and a
# directive that ends by naming it ("then reply STOP_OK", "then reply: STOP_OK") is not a
# permit. Parity with the Claude Code hook's `_STOP_OK_FINAL_SENTENCE`.
_STOP_OK_FINAL_SENTENCE = re.compile(r"(?:^|[.!?]\s+)[*_`]*STOP_OK[\s*_`.!]*$")


def _stop_ok(output):
    """The ruling permits the stop when the STOP_OK sentinel is the FIRST non-empty line
    (exact token, unchanged: `STOP_OK.` followed by a directive is NOT a permit) or the bare
    sentinel ends the LAST non-empty line as its own sentence. A model that explains itself and then answers puts it
    last; first-line-only parsing read that as an incomplete ruling. A sentinel line in the
    MIDDLE, followed by more directive, is contradictory and resolves to incomplete. Parity with
    the Claude Code hook's `is_stop_ok` (docs/plans/stopcontinue_trailing_stop_ok.md)."""
    lines = [ln.strip() for ln in str(output or "").splitlines() if ln.strip()]
    if not lines:
        return False
    if lines[0].upper() == "STOP_OK":
        return True
    return bool(_STOP_OK_FINAL_SENTENCE.search(lines[-1].upper()))


def _result(status, message, **values):
    result = {"status": status, "message": str(message)}
    result.update(values)
    return result


def _register_abort_bypass(cancel_event, root, ticket):
    register = getattr(cancel_event, "on_cancel", None)
    if register is None:
        return False
    register(
        lambda: state.grant_stop_abort_bypass(
            root, ticket["session_id"], ticket.get("turn_id"), ticket["operator_generation"]
        )
    )
    return True


def _cancelled(root, ticket, abort_callback_registered):
    if not abort_callback_registered:
        state.grant_stop_abort_bypass(
            root, ticket["session_id"], ticket.get("turn_id"), ticket["operator_generation"]
        )
    return _result("cancelled", "stopContinue was cancelled")


def _allow_stop(root, ticket, outcome):
    session_id = ticket["session_id"]
    if ticket.get("strict_completion") and (
        ticket["operator_generation"] != state.stop_generation(root, session_id, strict_io=True)
        or ticket["work_generation"] != state.stop_generation(root, session_id, work=True, strict_io=True)
    ):
        return False
    background_markers = ticket.get("background_markers") or [
        dict(item, kind="shell") for item in ticket.get("shell_markers") or []
    ]
    if outcome == "stop_wait":
        # A temporary yield is not completion: retain the reviewed markers. Also reject
        # newly observed work even if its caller omitted the generation invalidation.
        reviewed = {(item.get("kind"), item.get("id")) for item in background_markers}
        if any((item.get("kind"), item.get("id")) not in reviewed
               for item in liveness.outstanding(root, session_id)):
            return False
    elif background_markers:
        try:
            liveness.clear_items(root, session_id, background_markers)
            if liveness.outstanding(root, session_id):
                return False
        except Exception:
            # Liveness recovery is advisory once the minimal supervisor has ruled.
            # State I/O failures must not defeat stopContinue's fail-open contract.
            pass
    state.grant_stop_permit(
        root, session_id, ticket.get("turn_id"), ticket["final_message"],
        outcome=outcome, operator_generation=ticket.get("operator_generation", ""),
        work_generation=ticket["work_generation"],
        strict_completion=ticket.get("strict_completion", False),
    )
    return True


def _complete_allowed_stop(cancel_event, root, ticket, outcome="stop_ok"):
    completed, allowed = cancellation.complete(
        cancel_event, lambda: _allow_stop(root, ticket, outcome)
    )
    if not completed:
        return "cancelled"
    return "allowed" if allowed else "new_work"


def review_stop(cwd, review_token, cancel_event=None, *, native=False, fresh=False):
    cwd = Path(str(cwd or ""))
    if not cwd.is_absolute():
        return _result("error", "cwd must be an absolute repository path")
    root = state.project_root(cwd)
    if not root:
        return _result("error", "cwd is not inside a Git repository")
    if not state.feature_enabled(root, "stop"):
        return _result("disabled", "stopContinue is not enabled for this repository")
    ticket = state.claim_stop_ticket(review_token, root)
    if not ticket:
        return _result(
            "error",
            "review_token is missing, expired, already used, or for another repository; "
            "propose the final response again to obtain a new token",
        )
    session_id = ticket["session_id"]
    # The review tool's own PreToolUse belongs to this handoff. Snapshot after it, but before
    # the blocking review, so later work still invalidates a late approval.
    if not ticket.get("strict_completion"):
        ticket["work_generation"] = state.stop_generation(root, session_id, work=True)
    background_markers = ticket.get("background_markers") or [
        dict(item, kind="shell") for item in ticket.get("shell_markers") or []
    ]
    abort_callback_registered = _register_abort_bypass(cancel_event, root, ticket)
    with stop_input.watch(root, ticket, cancel_event, enabled=native) as (review_cancel, submitted):
        return _review_ticket(root, ticket, background_markers, review_cancel, native, fresh,
                              abort_callback_registered, submitted)


def _review_ticket(root, ticket, background_markers, cancel_event, native, fresh,
                   abort_callback_registered, submitted):
    session_id = ticket["session_id"]
    try:
        if submitted() or (cancel_event is not None and cancel_event.is_set()):
            return _cancelled(root, ticket, abort_callback_registered)
        prompt = prompts.stop_prompt(
            root, session_id, ticket["final_message"],
            background_markers=background_markers, native=native, fresh=fresh,
        )
        if fresh:
            code, output, stderr = backend.review_once(
                root, "read-only", prompt, timeout=backend.STOP_REVIEW_TIMEOUT,
                cancel_event=cancel_event,
            )
        else:
            code, output, stderr = backend.review_turn(
                root, session_id, "stop", "read-only", prompt,
                timeout=backend.STOP_REVIEW_TIMEOUT, cancel_event=cancel_event,
            )
    except Exception as error:
        if submitted() or (cancel_event is not None and cancel_event.is_set()):
            return _cancelled(root, ticket, abort_callback_registered)
        completion = _complete_allowed_stop(cancel_event, root, ticket, outcome="failed_open")
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "task input or work changed while stopContinue was running",
                directive="Address the new input or work and inspect newly started background work.",
            )
        return _result(
            "failed_open",
            "stop supervisor failed; the next stop is allowed",
            detail=str(error),
        )
    if submitted() or code == backend.CANCELLED_EXIT or (cancel_event and cancel_event.is_set()):
        return _cancelled(root, ticket, abort_callback_registered)
    # A commit approval is neither a completion verdict nor executable remaining
    # work. Treat role confusion like an invalid/empty ruling, not a directive.
    commit_approval = bool(re.match(r"^\s*APPROVED\b", output, re.IGNORECASE))
    if code != 0 or not output.strip() or commit_approval:
        completion = _complete_allowed_stop(cancel_event, root, ticket, outcome="failed_open")
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "task input or work changed while stopContinue was running",
                directive="Address the new input or work and inspect newly started background work.",
            )
        detail = ("commit-gate approval is not a stop verdict" if commit_approval
                  else stderr.strip() or "empty ruling")
        return _result(
            "failed_open",
            "stop supervisor failed; the next stop is allowed",
            detail=detail,
        )
    waiting = output.strip().upper() == "STOP_WAIT"
    if waiting or _stop_ok(output):
        outcome = "stop_wait" if waiting else "stop_ok"
        completion = _complete_allowed_stop(cancel_event, root, ticket, outcome=outcome)
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "task input or work changed while stopContinue was running",
                directive="Address the new input or work and inspect newly started background work.",
            )
        return _result(outcome, ("the stop supervisor approved a background wait" if waiting
                                 else "the stop supervisor approved completion"),
                       ruling=output.strip())
    completed, _unused = cancellation.complete(cancel_event, lambda: None)
    if not completed:
        return _cancelled(root, ticket, abort_callback_registered)
    return _result(
        "incomplete",
        "the stop supervisor judged the task incomplete",
        directive=output.strip(),
    )
