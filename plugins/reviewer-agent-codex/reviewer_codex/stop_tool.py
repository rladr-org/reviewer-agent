# SPDX-License-Identifier: MPL-2.0
"""Cancellable minimal stopContinue ruling for the bundled MCP server."""

import re
from pathlib import Path

from . import backend, cancellation, liveness, prompts, state

# The sentinel as a whole TRAILING line: STOP_OK alone, tolerating only markdown emphasis /
# backticks around it and trailing sentence punctuation. Exact token, never a prefix (STOP_OKAY
# is not the sentinel, and a directive may END with a sentence that merely mentions it).
_STOP_OK_BARE_LINE = re.compile(r"^[\s*_`]*STOP_OK[\s*_`.!]*$")


def _stop_ok(output):
    """The ruling permits the stop when the STOP_OK sentinel is the FIRST non-empty line
    (exact token, unchanged: `STOP_OK.` followed by a directive is NOT a permit) or the bare
    sentinel is the LAST non-empty line. A model that explains itself and then answers puts it
    last; first-line-only parsing read that as an incomplete ruling. A sentinel line in the
    MIDDLE, followed by more directive, is contradictory and resolves to incomplete. Parity with
    the Claude Code hook's `is_stop_ok` (docs/plans/stopcontinue_trailing_stop_ok.md)."""
    lines = [ln.strip() for ln in str(output or "").splitlines() if ln.strip()]
    if not lines:
        return False
    if lines[0].upper() == "STOP_OK":
        return True
    return bool(_STOP_OK_BARE_LINE.match(lines[-1].upper()))


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
            root, ticket["session_id"], ticket.get("turn_id")
        )
    )
    return True


def _cancelled(root, ticket, abort_callback_registered):
    if not abort_callback_registered:
        state.grant_stop_abort_bypass(
            root, ticket["session_id"], ticket.get("turn_id")
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
    if background_markers:
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


def review_completion(cwd, proposed_response, review_token=None, cancel_event=None):
    return review_stop(cwd, review_token, cancel_event=cancel_event,
                       completion=True, proposed_response=proposed_response)


def review_stop(cwd, review_token, cancel_event=None, *, completion=False, proposed_response=None):
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
    if completion and (
        not ticket.get("strict_completion")
        or not isinstance(proposed_response, str) or not proposed_response.strip()
        or ticket["final_message"].strip() != proposed_response.strip()
    ):
        return _result("error", "pre-final review requires a host-bound draft; do not invent a token")
    # The review tool's own PreToolUse belongs to this handoff. Snapshot after it, but before
    # the blocking review, so later work still invalidates a late approval.
    if not ticket.get("strict_completion"):
        ticket["work_generation"] = state.stop_generation(root, session_id, work=True)
    background_markers = ticket.get("background_markers") or [
        dict(item, kind="shell") for item in ticket.get("shell_markers") or []
    ]
    abort_callback_registered = _register_abort_bypass(cancel_event, root, ticket)
    if completion:
        if state.read_nudges(root, session_id) >= state.max_nudges():
            outcome = _complete_allowed_stop(cancel_event, root, ticket, outcome="budget_exhausted")
            if outcome == "cancelled":
                return _cancelled(root, ticket, abort_callback_registered)
            if outcome == "new_work":
                return _result("incomplete", "task changed during review",
                               directive="Address the new input or work before stopping.")
            return _result("budget_exhausted", "completion review budget exhausted; stopping is allowed without approval")
        state.bump_nudges(root, session_id)
    try:
        code, output, stderr = backend.review_turn(
            root,
            session_id,
            "stop",
            "read-only",
            prompts.stop_prompt(
                root,
                session_id,
                ticket["final_message"],
                background_markers=background_markers,
                prefinal=completion,
            ),
            timeout=backend.STOP_REVIEW_TIMEOUT,
            cancel_event=cancel_event,
        )
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
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
    if code == backend.CANCELLED_EXIT or (cancel_event and cancel_event.is_set()):
        return _cancelled(root, ticket, abort_callback_registered)
    if code != 0 or not output.strip():
        completion = _complete_allowed_stop(cancel_event, root, ticket, outcome="failed_open")
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "task input or work changed while stopContinue was running",
                directive="Address the new input or work and inspect newly started background work.",
            )
        detail = stderr.strip() or "empty ruling"
        return _result(
            "failed_open",
            "stop supervisor failed; the next stop is allowed",
            detail=detail,
        )
    if _stop_ok(output):
        completion = _complete_allowed_stop(cancel_event, root, ticket)
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "task input or work changed while stopContinue was running",
                directive="Address the new input or work and inspect newly started background work.",
            )
        return _result("stop_ok", "the stop supervisor approved completion", ruling=output.strip())
    completed, _unused = cancellation.complete(cancel_event, lambda: None)
    if not completed:
        return _cancelled(root, ticket, abort_callback_registered)
    return _result(
        "incomplete",
        "the stop supervisor judged the task incomplete",
        directive=output.strip(),
    )
