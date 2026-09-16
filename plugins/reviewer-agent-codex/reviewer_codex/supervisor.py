# SPDX-License-Identifier: MPL-2.0
"""Native Stop review, with legacy routing for already-running Codex sessions."""

import time
from pathlib import Path

from . import liveness, state, stop_tool

CONTINUATION_PREFIX = "[reviewer-agent-codex continuation]"
SHELL_SETTLE_SECONDS = 0.25
# Module-level seam for the settle delay. Tests patch THIS name, never `time.sleep` itself:
# patching the global captured CPython's own `subprocess` wait-with-timeout polling (0.0005 s
# doubling to 0.05 s while a child is still running), which fires only on a slow runner and
# made the settle assertion flaky (macOS system-interpreter CI job, 2026-09-02).
_sleep = time.sleep


def _block(reason, message=None):
    result = {"decision": "block", "reason": CONTINUATION_PREFIX + "\n" + reason}
    if message:
        result["systemMessage"] = message
    return result


def _failed_open():
    return {"systemMessage": "[reviewer-agent-codex] Completion check failed; allowing this stop under the fail-open policy."}


def supervise_hook(data, cancel_event=None):
    """Return hook JSON directly; native MCP hooks do not emit their own tool events."""
    try:
        if (
            not isinstance(data, dict)
            or data.get("hook_event_name") != "Stop"
            or not isinstance(data.get("cwd"), str)
            or not Path(data["cwd"]).is_absolute()
            or not isinstance(data.get("session_id"), str)
            or not data["session_id"].strip()
            or not isinstance(data.get("last_assistant_message"), (str, type(None)))
            or not isinstance(data.get("turn_id"), (str, type(None)))
            or not isinstance(data.get("stop_hook_active", False), bool)
        ):
            return _failed_open()
        return supervise(data, direct=True, cancel_event=cancel_event) or {}
    except Exception:
        # Match the legacy command hook's fail-open boundary, without exposing a traceback
        # as a continuation or silently reporting approval after a state/backend error.
        return _failed_open()


def _review_in_hook(cwd, session_id, token, cancel_event):
    result = stop_tool.review_stop(cwd, token, cancel_event=cancel_event, native=True)
    status = result.get("status")
    if status in ("stop_ok", "failed_open"):
        # No second Stop is needed. Consume now to validate generations and avoid leaving
        # approval that could authorize another event. review_stop snapshots work itself,
        # so it also works when the native hook emits no PreToolUse event.
        permit = state.consume_stop_permit(cwd, session_id, strict_io=True)
        if not permit:
            return _block(
                "Task input or work changed during completion review. Address the new "
                "request or work before stopping."
            )
        return {} if permit == "stop_ok" else _failed_open()
    if status in ("cancelled", "disabled"):
        return {}
    if status == "incomplete":
        return _block(result["directive"])
    return _failed_open()


def _settled_outstanding(cwd, session_id):
    """Give a concurrently completed shell call's PostToolUse clear a brief head start."""
    active = liveness.outstanding(cwd, session_id)
    if any(item.get("kind") == "shell" for item in active):
        _sleep(SHELL_SETTLE_SECONDS)
        active = liveness.outstanding(cwd, session_id)
    return active


def supervise(data, *, direct=False, cancel_event=None):
    cwd = data.get("cwd") or "."
    if not state.feature_enabled(cwd, "stop"):
        return None
    if state.consume_deferred_stop(cwd, turn_id=data.get("turn_id")):
        return None
    session_id = state.session_key(data)
    if not session_id:
        return None
    if state.consume_stop_abort_bypass(cwd, session_id, data.get("turn_id")):
        if direct:
            return {}
        return {
            "systemMessage": (
                "[reviewer-agent-codex] Cancelled stopContinue acknowledged; "
                "allowing the interrupted turn to end."
            )
        }
    final_message = data.get("last_assistant_message") or ""
    if not str(final_message).strip():
        return None
    permit = state.consume_stop_permit(cwd, session_id, data.get("turn_id"), final_message)
    if permit == "stop_ok":
        return {} if direct else {"systemMessage": "[reviewer-agent-codex] Stop supervisor approved this stop."}
    if permit == "budget_exhausted":
        return {} if direct else {"systemMessage": "[reviewer-agent-codex] Stop nudge cap reached; allowing the turn to end."}
    if permit == "failed_open":
        return {"systemMessage": "[reviewer-agent-codex] Stop supervisor failed; allowing this stop under the fail-open policy."}
    used, cap = state.read_nudges(cwd, session_id), state.max_nudges()
    if used >= cap:
        return {"systemMessage": "[reviewer-agent-codex] Stop nudge cap reached; allowing the turn to end."}

    active = _settled_outstanding(cwd, session_id)
    liveness.clear_challenge(cwd, session_id)
    token = state.create_stop_ticket(
        cwd, session_id, final_message, turn_id=data.get("turn_id"),
        background_markers=active,
    )
    state.bump_nudges(cwd, session_id)
    if direct:
        return _review_in_hook(cwd, session_id, token, cancel_event)
    if active:
        return _block(
            "Call the reviewer-agent `stop_continue` MCP tool once with this repository cwd "
            "and review_token={}. Wait for that blocking call to return; it will adjudicate "
            "the reported background markers together with your proposed final response.".format(
                token
            ),
            "[reviewer-agent-codex] Background completion routed to cancellable "
            "stop_continue tool.",
        )

    return _block(
        "Call the reviewer-agent `stop_continue` MCP tool once with this repository cwd and "
        "review_token={}. Wait for that blocking call to return; its result either permits the "
        "next stop or tells you exactly what remains.".format(token),
        "[reviewer-agent-codex] Stop routed to cancellable stop_continue tool.",
    )
