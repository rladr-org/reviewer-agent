# SPDX-License-Identifier: MPL-2.0
"""Shared feature-toggle behavior for trusted hooks and command-line fallbacks."""

from pathlib import Path

from . import state


def apply_feature_action(path, feature, action, turn_id=None):
    root = state.project_root(path)
    label = "reviewerAgent" if feature == "reviewer" else "stopContinue"
    if not root:
        return 1, "{}: ERROR - {} is not a git repository".format(label, Path(path))
    if action == "on":
        if feature == "reviewer" and not (Path(root) / "reviewerAgent.md").is_file():
            return 1, "reviewerAgent: ERROR - brief not found at {}".format(
                Path(root) / "reviewerAgent.md"
            )
        state.set_feature(root, feature)
        if feature == "stop":
            state.defer_next_stop(root, turn_id=turn_id)
    elif action == "off":
        state.clear_feature(root, feature)
    enabled = state.feature_enabled(root, feature)
    return 0, "{}: {} for {}".format(label, "ON" if enabled else "OFF", root)


def apply_history_action(path, action):
    """Report or purge the retained review record for one repository."""
    root = state.project_root(path)
    if not root:
        return 1, "reviewerHistory: ERROR - {} is not a git repository".format(Path(path))
    if action == "status":
        retained = state.review_history(path)
        if not retained:
            return 0, (
                "reviewerHistory: nothing retained for {} in {} (retention keeps the newest "
                "{} and prunes oldest-first)".format(
                    root, state.review_history_dir(path), state.review_history_keep()
                )
            )
        return 0, (
            "reviewerHistory: {} retained review(s) for {} in {}; retention keeps the newest "
            "{} and prunes oldest-first".format(
                len(retained), root, state.review_history_dir(path),
                state.review_history_keep(),
            )
        )
    if action != "clear":
        return 1, "reviewerHistory: ERROR - expected status or clear"
    removed = state.clear_review_history(path)
    if not removed:
        return 0, (
            "reviewerHistory: nothing to clear for {} -- no retained reviews and no saved "
            "ruling".format(root)
        )
    return 0, (
        "reviewerHistory: cleared {} file(s) for {} (retained reviews and saved rulings); "
        "other repositories are untouched".format(len(removed), root)
    )


def apply_reviewer_session_action(path, session_id, action):
    """Report or retire the commit reviewer owned by one Codex root session."""
    root = state.project_root(path)
    if not root:
        return 1, "reviewerSession: ERROR - {} is not a git repository".format(
            Path(path)
        )
    owner = str(session_id or "").strip()
    if not owner:
        return 1, "reviewerSession: ERROR - current Codex session is unidentified"
    current = state.read_reviewer_session(path, owner, "reviewer")
    if action == "status":
        if current:
            return 0, "reviewerSession: {} for {}".format(current, root)
        return 0, "reviewerSession: no reviewer session for {}".format(root)
    if action != "rotate":
        return 1, "reviewerSession: ERROR - expected status or rotate"
    if not current:
        return 0, (
            "reviewerSession: no reviewer session to rotate for {}; next commit audit "
            "will start a fresh reviewer".format(root)
        )
    if not state.retire_reviewer_session(
        path,
        owner, "reviewer", expected_reviewer_id=current
    ):
        return 1, (
            "reviewerSession: ERROR - reviewer changed while rotating; retry after the "
            "active audit finishes"
        )
    state.clear_commit_cursor(root, owner, expected_reviewer_id=current)
    return 0, (
        "reviewerSession: rotated {} for {}; next commit audit will start a fresh "
        "reviewer".format(current, root)
    )
