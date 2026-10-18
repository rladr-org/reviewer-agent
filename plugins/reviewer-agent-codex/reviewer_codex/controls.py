# SPDX-License-Identifier: MPL-2.0
"""Shared feature-toggle behavior for trusted hooks and command-line fallbacks."""

from pathlib import Path

from . import prompts, state


DISCLAIMER_PATH = Path(__file__).resolve().parent.parent / "DISCLAIMER.md"


def apply_stop_delay_action(value=None):
    """Catch obsolete exact commands without changing settings or asking the model."""
    return 1, (
        "stopContinue: ERROR - the Codex reading delay was removed; completion reviews "
        "start immediately. Legacy delay configuration and environment values are ignored."
    )


def apply_feature_action(
    path, feature, action, turn_id=None, accept_risk=False, acceptance_source=None
):
    root = state.project_root(path)
    label = "reviewerAgent" if feature == "reviewer" else "stopContinue"
    if not root:
        return 1, "{}: ERROR - {} is not a git repository".format(label, Path(path))
    if action == "on":
        # The brief every review reads: a linked worktree falls back to the main checkout's.
        brief_file = prompts.brief_path(root)
        if feature == "reviewer" and not brief_file.is_file():
            return 1, "reviewerAgent: ERROR - brief not found at {}".format(brief_file)
        if feature == "reviewer":
            try:
                brief = brief_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return 1, "reviewerAgent: ERROR - cannot read reviewerAgent.md"
            if not brief.strip() or "Project-specific guidance (replace this paragraph)" in brief:
                return 1, (
                    "reviewerAgent: ERROR - replace the reviewerAgent.md template placeholder "
                    "with this project's review rules before enabling."
                )
        disclaimer_hash = state.disclaimer_sha256(DISCLAIMER_PATH)
        if not state.risk_accepted(root, disclaimer_hash):
            if not accept_risk:
                command = (
                    "$reviewer-agent-codex:reviewer-agent on acceptRisk"
                    if feature == "reviewer"
                    else "$reviewer-agent-codex:stop-continue on acceptRisk"
                )
                return 1, (
                    "Before enabling {} you must accept the risk as described in "
                    "DISCLAIMER.md by invoking:\n{}"
                ).format(label, command)
            state.record_risk_acceptance(
                root, disclaimer_hash, acceptance_source or "trusted-user-prompt"
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


def _session_limit_message(path=None, session_id=None, reviewer_id=None):
    limit, source = state.reviewer_session_audit_limit_detail()
    if limit == 0:
        return "automatic rotation disabled (from {})".format(source)
    progress = ""
    if reviewer_id:
        progress = "; current session has completed {}".format(
            state.reviewer_session_completed_audits(path, session_id, "reviewer")
        )
    return "automatic rotation after {} completed non-retry audit(s) (from {}){}".format(
        limit, source, progress
    )


def apply_reviewer_session_action(path, session_id, action, value=None):
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
            return 0, "reviewerSession: {} for {}; {}".format(
                current, root, _session_limit_message(path, owner, current)
            )
        return 0, "reviewerSession: no reviewer session for {}; {}".format(
            root, _session_limit_message()
        )
    if action == "limit":
        token = str(value or "").strip().lower()
        if token == "clear":
            state.clear_reviewer_session_audit_limit()
        elif token == "off":
            state.set_reviewer_session_audit_limit(0)
        else:
            parsed = state._valid_reviewer_session_audit_limit(token)
            if parsed is None or parsed == 0:
                return 1, (
                    "reviewerSession: ERROR - limit expects 1..1000, off, or clear"
                )
            state.set_reviewer_session_audit_limit(parsed)
        return 0, "reviewerSession: {}".format(_session_limit_message())
    if action != "rotate":
        return 1, "reviewerSession: ERROR - expected status, rotate, or limit"
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
