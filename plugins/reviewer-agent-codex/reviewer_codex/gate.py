# SPDX-License-Identifier: MPL-2.0
"""Redirect reviewed commits away from yieldable shell execution."""

import shlex

from . import state
from .command_intent import commit_intent
from .git import git_commit_has_absolute_target


def _deny(reason, message):
    return {
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def _supported_options(command):
    """Parse the entire intercepted command; never silently discard shell/Git options."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    if not words or words.pop(0) != "git":
        raise ValueError("use a single git command without environment assignments")
    while words[:1] == ["-C"] and len(words) >= 2:
        del words[:2]
    if not words or words.pop(0) != "commit":
        raise ValueError("only the global -C option is supported")
    options = []
    while words:
        word = words.pop(0)
        if word in ("--signoff", "--quiet"):
            options.append(word)
        elif word == "--message":
            if not words:
                raise ValueError("--message requires a value")
            words.pop(0)
        elif word.startswith("--message="):
            continue
        elif word.startswith("-") and not word.startswith("--"):
            cluster = word[1:]
            if not cluster:
                raise ValueError("bare '-' is not supported")
            while cluster:
                flag, cluster = cluster[0], cluster[1:]
                if flag in "sq":
                    options.append({"s": "--signoff", "q": "--quiet"}[flag])
                elif flag == "m":
                    if not cluster:
                        if not words:
                            raise ValueError("-m requires a value")
                        words.pop(0)
                    break
                else:
                    raise ValueError("unsupported commit option -" + flag)
        else:
            raise ValueError("unsupported commit argument or shell operator: " + word)
    return options


def review_commit(data):
    command = ((data.get("tool_input") or {}).get("command") or "")
    # Hook cwd does not expose a shell call's workdir. Resolve targets only from
    # command text, and keep looking after inactive targets in compound commands.
    intent = commit_intent(command, None, _accept=lambda item: (
        item.target is None or state.feature_enabled(item.target, "reviewer")
    ))
    if intent.kind is None:
        return None
    cwd = intent.target
    if cwd is not None and intent.kind == "other":
        return _deny(
            "Unsupported commit-producing form: {}. No review token was created; "
            "stage changes separately and use a single git -C /absolute/repository commit.".format(
                intent.reason
            ),
            "[reviewer-agent-codex] Unsupported commit-producing form.",
        )
    if cwd is None or not git_commit_has_absolute_target(command):
        enabled_roots = state.enabled_feature_roots("reviewer")
        if not enabled_roots:
            return None
        current_root = state.project_root(cwd or data.get("cwd") or ".")
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
    try:
        options = _supported_options(command)
    except ValueError as error:
        return _deny(
            "Unsupported commit form: {}. Supported: -m/--message, -s/--signoff, "
            "-q/--quiet, and an explicit git -C target. Stage changes separately; "
            "run other shell commands separately. No review token was created.".format(error),
            "[reviewer-agent-codex] Unsupported commit form.",
        )
    token = state.create_commit_ticket(cwd, state.session_key(data), commit_options=options)
    return _deny(
        "This repository requires the reviewer-agent `review_commit` MCP tool. "
        "Call it once with cwd={}, the commit message, and review_token={}. "
        "The blocking call returns the verdict and commits only if approved.".format(
            root, token
        ),
        "[reviewer-agent-codex] Shell commit redirected to blocking review_commit tool.",
    )
