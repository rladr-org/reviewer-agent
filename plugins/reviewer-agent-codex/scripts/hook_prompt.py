#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""UserPromptSubmit: retain operator context without parsing rollouts."""

import json
import re
import sys

import _bootstrap  # noqa: F401
from reviewer_codex import controls, state
from reviewer_codex.supervisor import CONTINUATION_PREFIX


_CONTROL_RE = re.compile(
    r"^\s*\$reviewer-agent-codex:(reviewer-agent|stop-continue)\s+"
    r"(on|off|status)(?:\s+(acceptRisk))?\s*$"
)
_SESSION_CONTROL_RE = re.compile(
    r"^\s*\$reviewer-agent-codex:reviewer-session\s+"
    r"(status|rotate)(?:\s+([^\s]+))?\s*$|"
    r"^\s*\$reviewer-agent-codex:reviewer-session\s+limit\s+([^\s]+)\s*$"
)
_HISTORY_CONTROL_RE = re.compile(
    r"^\s*\$reviewer-agent-codex:reviewer-history\s+"
    r"(status|clear)\s*$"
)


def _control_output(data, prompt):
    match = _CONTROL_RE.fullmatch(prompt)
    session_match = _SESSION_CONTROL_RE.fullmatch(prompt)
    history_match = _HISTORY_CONTROL_RE.fullmatch(prompt)
    if match:
        skill, action, acceptance = match.groups()
        feature = "reviewer" if skill == "reviewer-agent" else "stop"
        label = "reviewerAgent" if feature == "reviewer" else "stopContinue"
        if acceptance and action != "on":
            code = 1
            message = "{}: ERROR - acceptRisk is valid only with on".format(label)
        else:
            try:
                code, message = controls.apply_feature_action(
                    data.get("cwd") or ".",
                    feature,
                    action,
                    turn_id=data.get("turn_id"),
                    accept_risk=bool(acceptance),
                    acceptance_source="trusted-user-prompt",
                )
            except (OSError, ValueError) as error:
                code = 1
                message = "{}: ERROR - could not update plugin state: {}".format(
                    label, error
                )
    elif session_match:
        try:
            action = session_match.group(1) or "limit"
            value = session_match.group(2) or session_match.group(3)
            if value and action != "limit":
                raise ValueError("{} takes no value".format(action))
            code, message = controls.apply_reviewer_session_action(
                data.get("cwd") or ".",
                state.session_key(data),
                action,
                value,
            )
        except (OSError, ValueError) as error:
            code = 1
            message = (
                "reviewerSession: ERROR - could not update plugin state: {}".format(
                    error
                )
            )
    elif history_match:
        try:
            code, message = controls.apply_history_action(
                data.get("cwd") or ".", history_match.group(1)
            )
        except (OSError, ValueError) as error:
            code = 1
            message = (
                "reviewerHistory: ERROR - could not update plugin state: {}".format(error)
            )
    else:
        return None
    return {
        "continue": False,
        "stopReason": message,
        "systemMessage": message,
    }


def main():
    data = json.loads(sys.stdin.read() or "{}")
    prompt = str(data.get("prompt") or "").strip()
    session_id = state.session_key(data)
    genuine_operator_prompt = bool(
        prompt and not prompt.startswith(CONTINUATION_PREFIX)
    )
    if genuine_operator_prompt:
        try:
            state.invalidate_stop_permits(data.get("cwd") or ".", session_id)
            state.clear_stop_abort_bypass(data.get("cwd") or ".", session_id)
            state.reset_nudges(data.get("cwd") or ".", session_id)
        except OSError:
            pass
    output = _control_output(data, prompt)
    if output:
        print(json.dumps(output))
        return 0
    if genuine_operator_prompt:
        try:
            state.record_prompt(data.get("cwd") or ".", session_id, prompt)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
