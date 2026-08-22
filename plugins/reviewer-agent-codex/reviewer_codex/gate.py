# SPDX-License-Identifier: MPL-2.0
"""Redirect reviewed commits away from yieldable shell execution."""

import shlex

from . import state
from .git import git_c_effective_dir, git_commit_has_absolute_target, is_git_commit


def _deny(reason, message):
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def review_commit(data):
    command = ((data.get("tool_input") or {}).get("command") or "")
    if not is_git_commit(command):
        return None
    cwd = git_c_effective_dir(command, data.get("cwd") or ".")
    if not git_commit_has_absolute_target(command):
        enabled_roots = state.enabled_feature_roots("reviewer")
        if not enabled_roots:
            return None
        current_root = state.project_root(cwd)
        target = (
            shlex.quote(current_root)
            if current_root in enabled_roots
            else "<absolute-repository>"
        )
        return _deny(
            "No review token was created because Codex hook events do not expose a shell tool's "
            "separate workdir. Retry the same commit with an explicit absolute repository target: "
            "`git -C {} commit ...` (preserve the original commit options and message).".format(
                target
            ),
            "[reviewer-agent-codex] Commit needs an explicit repository target.",
        )
    if not state.feature_enabled(cwd, "reviewer"):
        return None
    root = state.project_root(cwd)
    token = state.create_commit_ticket(cwd, state.session_key(data))
    return _deny(
        "This repository requires the reviewer-agent `review_commit` MCP tool. "
        "Call it once with cwd={}, the commit message, and review_token={}. "
        "The blocking call returns the verdict and commits only if approved.".format(
            root, token
        ),
        "[reviewer-agent-codex] Shell commit redirected to blocking review_commit tool.",
    )
