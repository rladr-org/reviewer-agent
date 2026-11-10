# SPDX-License-Identifier: MPL-2.0
"""Bind draft checks to the host before they run as ordinary blocking tools."""

from pathlib import Path

from . import liveness, state


GUIDANCE = (
    "stopContinue is enabled for this repository. When ready to end your turn, call "
    "reviewer-agent review_completion with cwd and proposed_response containing the complete "
    "final draft, before publishing it. Omit review_token; PreToolUse supplies it. Wait for "
    "the blocking result. A reviewer directive is not new authorization: reconcile it with the "
    "current user request and observed work. Do not repeat an already-satisfied or superseded "
    "step solely because a reviewer requests it. If a directive repeats completed work, submit "
    "the completed draft with the relevant evidence instead of manufacturing another intermediate "
    "step. On incomplete, perform the authorized remaining work and check the "
    "revised draft in this same turn. On stop_ok or stop_wait, publish exactly that draft "
    "without further tools. On failed_open or budget_exhausted you may publish it without "
    "claiming approval. On disabled continue normally. On error, use the native Stop fallback "
    "by publishing your response; do not retry unbound calls or invent tokens. Never call "
    "stop_hook manually. Do not check completion during ongoing work."
)

TOOL_NAMES = (
    "mcp__reviewer_agent__review_completion", "mcp__reviewer-agent__review_completion",
)


def bind_review(data):
    if data.get("hook_event_name") != "PreToolUse" or data.get("tool_name") not in TOOL_NAMES:
        return None
    source = data.get("tool_input")
    source = source if isinstance(source, dict) else {}
    arguments = dict(cwd=data.get("cwd") or "", proposed_response=source.get("proposed_response"),
                     review_token="")
    try:
        cwd, draft = arguments["cwd"], arguments["proposed_response"]
        session, turn = data.get("session_id"), data.get("turn_id")
        if (isinstance(cwd, str) and Path(cwd).is_absolute()
                and all(isinstance(v, str) and v.strip() for v in (draft, session, turn))
                and state.feature_enabled(cwd, "stop")):
            # A draft-check call invalidates older approvals, but is not substantive
            # work: carry the no-progress count across its own generation bump.
            generations = state.stop_loop_generations(cwd, session)
            blocks = state.read_stop_loop(cwd, session, generations)
            state.invalidate_stop_permits(cwd, session, work=True)
            if blocks:
                state.record_stop_loop(cwd, session, state.stop_loop_generations(cwd, session), blocks)
            arguments["review_token"] = state.create_stop_ticket(
                cwd, session, draft, turn_id=turn,
                background_markers=liveness.outstanding(cwd, session), strict_completion=True,
            )
    except (OSError, ValueError):
        # An empty binding cannot authorize a draft. Native Stop remains the fallback.
        pass
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": arguments,
    }}
