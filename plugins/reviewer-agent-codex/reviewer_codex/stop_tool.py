# SPDX-License-Identifier: MPL-2.0
"""Cancellable minimal stopContinue ruling for the bundled MCP server."""

from pathlib import Path

from . import backend, cancellation, liveness, prompts, state


def _stop_ok(output):
    for line in str(output or "").splitlines():
        if line.strip():
            return line.strip().upper() == "STOP_OK"
    return False


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


def _allow_stop(root, ticket):
    session_id = ticket["session_id"]
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
    state.grant_stop_permit(root, session_id)
    return True


def _complete_allowed_stop(cancel_event, root, ticket):
    completed, allowed = cancellation.complete(
        cancel_event, lambda: _allow_stop(root, ticket)
    )
    if not completed:
        return "cancelled"
    return "allowed" if allowed else "new_work"


def review_stop(cwd, review_token, cancel_event=None):
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
    background_markers = ticket.get("background_markers") or [
        dict(item, kind="shell") for item in ticket.get("shell_markers") or []
    ]
    abort_callback_registered = _register_abort_bypass(cancel_event, root, ticket)
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
            ),
            timeout=backend.STOP_REVIEW_TIMEOUT,
            cancel_event=cancel_event,
        )
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            return _cancelled(root, ticket, abort_callback_registered)
        completion = _complete_allowed_stop(cancel_event, root, ticket)
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "new background work appeared while stopContinue was running",
                directive="Wait for and inspect the newly started background work.",
            )
        return _result(
            "failed_open",
            "stop supervisor failed; the next stop is allowed",
            detail=str(error),
        )
    if code == backend.CANCELLED_EXIT or (cancel_event and cancel_event.is_set()):
        return _cancelled(root, ticket, abort_callback_registered)
    if code != 0 or not output.strip():
        completion = _complete_allowed_stop(cancel_event, root, ticket)
        if completion == "cancelled":
            return _cancelled(root, ticket, abort_callback_registered)
        if completion == "new_work":
            return _result(
                "incomplete",
                "new background work appeared while stopContinue was running",
                directive="Wait for and inspect the newly started background work.",
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
                "new background work appeared while stopContinue was running",
                directive="Wait for and inspect the newly started background work.",
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
