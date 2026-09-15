# SPDX-License-Identifier: MPL-2.0
"""Bind pre-final reviews to trusted PreToolUse context, not model-supplied identities."""

from pathlib import Path

from . import liveness, state


GUIDANCE = (
    "stopContinue is enabled for this repository. Before publishing any final response, "
    "call reviewer-agent review_completion directly with cwd and proposed_response containing "
    "the complete final draft. Omit review_token; the host hook supplies it. If the result is "
    "incomplete, perform the required work and review the revised draft within this same turn. "
    "After stop_ok, publish that exact draft without further tools. After failed_open or "
    "budget_exhausted, you may publish the draft under the bounded fail-open policy; do not "
    "claim reviewer approval. If disabled, continue normally. If binding fails, do not loop or "
    "invent a token: the native Stop hook remains the fallback. Never call stop_hook manually."
)


def bind_review(data):
    if data.get("hook_event_name") != "PreToolUse" or data.get("tool_name") not in (
        "mcp__reviewer_agent__review_completion", "mcp__reviewer-agent__review_completion",
    ):
        return None
    source = data.get("tool_input")
    source = source if isinstance(source, dict) else {}
    # Always erase a supplied token, even if binding fails. No model identity is forwarded.
    arguments = {
        "cwd": data.get("cwd") or "",
        "proposed_response": source.get("proposed_response"),
        "review_token": "",
    }
    try:
        cwd, draft = arguments["cwd"], arguments["proposed_response"]
        session_id, turn_id = data.get("session_id"), data.get("turn_id")
        if (
            isinstance(cwd, str) and Path(cwd).is_absolute()
            and all(isinstance(v, str) and v.strip() for v in (draft, session_id, turn_id))
            and state.feature_enabled(cwd, "stop")
        ):
            arguments["review_token"] = state.create_stop_ticket(
                cwd, session_id, draft, turn_id=turn_id,
                background_markers=liveness.outstanding(cwd, session_id),
                strict_completion=True,
            )
    except (OSError, ValueError):
        # The tool will report missing binding; Stop retains its fail-open fallback.
        pass
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": arguments,
    }}
