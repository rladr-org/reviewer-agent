# SPDX-License-Identifier: MPL-2.0
"""Immediate Stop routing into the cancellable completion-check MCP tool."""

import time

from . import liveness, state

CONTINUATION_PREFIX = "[reviewer-agent-codex continuation]"
SHELL_SETTLE_SECONDS = 0.25


def _block(reason, message):
    return {"decision": "block", "reason": CONTINUATION_PREFIX + "\n" + reason, "systemMessage": message}


def _settled_outstanding(cwd, session_id):
    """Give a concurrently completed shell call's PostToolUse clear a brief head start."""
    active = liveness.outstanding(cwd, session_id)
    if any(item.get("kind") == "shell" for item in active):
        time.sleep(SHELL_SETTLE_SECONDS)
        active = liveness.outstanding(cwd, session_id)
    return active


def supervise(data):
    cwd = data.get("cwd") or "."
    if not state.feature_enabled(cwd, "stop"):
        return None
    if state.consume_deferred_stop(cwd, turn_id=data.get("turn_id")):
        return None
    session_id = state.session_key(data)
    if not session_id:
        return None
    if state.consume_stop_abort_bypass(cwd, session_id, data.get("turn_id")):
        return {
            "systemMessage": (
                "[reviewer-agent-codex] Cancelled stopContinue acknowledged; "
                "allowing the interrupted turn to end."
            )
        }
    final_message = data.get("last_assistant_message") or ""
    if not str(final_message).strip():
        return None
    if state.consume_stop_permit(cwd, session_id):
        return {"systemMessage": "[reviewer-agent-codex] Stop supervisor approved this stop."}
    used, cap = state.read_nudges(cwd, session_id), state.max_nudges()
    if used >= cap:
        return {"systemMessage": "[reviewer-agent-codex] Stop nudge cap reached; allowing the turn to end."}

    active = _settled_outstanding(cwd, session_id)
    if active:
        liveness.clear_challenge(cwd, session_id)
        token = state.create_stop_ticket(
            cwd,
            session_id,
            final_message,
            turn_id=data.get("turn_id"),
            background_markers=active,
        )
        state.bump_nudges(cwd, session_id)
        return _block(
            "Call the reviewer-agent `stop_continue` MCP tool once with this repository cwd "
            "and review_token={}. Wait for that blocking call to return; it will adjudicate "
            "the reported background markers together with your proposed final response.".format(
                token
            ),
            "[reviewer-agent-codex] Background completion routed to cancellable "
            "stop_continue tool.",
        )

    liveness.clear_challenge(cwd, session_id)

    token = state.create_stop_ticket(
        cwd, session_id, final_message, turn_id=data.get("turn_id")
    )
    state.bump_nudges(cwd, session_id)
    return _block(
        "Call the reviewer-agent `stop_continue` MCP tool once with this repository cwd and "
        "review_token={}. Wait for that blocking call to return; its result either permits the "
        "next stop or tells you exactly what remains.".format(token),
        "[reviewer-agent-codex] Stop routed to cancellable stop_continue tool.",
    )
