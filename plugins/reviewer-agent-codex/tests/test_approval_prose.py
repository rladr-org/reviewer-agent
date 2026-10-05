# SPDX-License-Identifier: MPL-2.0
"""Verdict markers, rather than explanatory uses of 'rejected', control commits."""
import subprocess

import pytest

from reviewer_codex import commit_tool, state

RECIPES_RULING = """APPROVED

Prior findings:
1. **Fixed** — the matching-cell case reaches the explicit `NotImplementedError` RED stub.
2. **Fixed** — the unrelated-cell case is rejected with a cell mismatch."""


@pytest.mark.parametrize("ruling", [
    RECIPES_RULING,
    RECIPES_RULING + "\n\nSUGGESTIONS:\n1. Add a short example.",
    "APPROVED\n\nThe previous REJECTED verdict is superseded; both findings are fixed.",
    "APPROVED\n\nRejected inputs now produce the documented validation error.",
    "APPROVED\n\nThe test asserts that the returned status equals `REJECTED`.",
])
def test_approval_survives_explanatory_rejected_prose(ruling):
    assert commit_tool.approved(ruling)


@pytest.mark.parametrize("ruling", [
    "APPROVED but also REJECTED due to a race",
    "APPROVED\nREJECTED\n- late finding",
    "APPROVED\nREJECTED: fix the bug",
    "APPROVED\n## REJECTED\n1. Fix the bug",
    "APPROVED\n**REJECTED**\n1. Fix the bug",
    "APPROVED\n- REJECTED: fix the bug",
    "APPROVED\n1. REJECTED: fix the bug",
    "REJECTED:\nThe earlier result was APPROVED.",
    "The result is APPROVED",
    "APPROVEDX",
    "APPROVED\nSUGGESTIONS:",
    "APPROVED\nSUGGESTIONS:\n1. A\nSUGGESTIONS:\n2. B",
    "APPROVED\nREVIEW_FAILED: cannot read the index",
    "APPROVED\nI could not inspect the staged changes.",
    "",
])
def test_invalid_or_contradictory_verdict_still_blocks(ruling):
    assert not commit_tool.approved(ruling)


def test_recipes_ruling_commits_and_preserves_complete_review(git_repo, isolated_state, monkeypatch):
    (git_repo / "change.txt").write_text("valid staged change\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "change.txt"], check=True)
    state.set_feature(git_repo, "reviewer")
    before = subprocess.check_output(["git", "-C", str(git_repo), "rev-parse", "HEAD"])

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.publish_reviewer_session(cwd, session_id, role, "recipes-regression")
        return 0, RECIPES_RULING, ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(
        git_repo, "Apply approved change", state.create_commit_ticket(git_repo, "recipes"))
    assert result["status"] == "committed"
    assert result["review"] == RECIPES_RULING
    assert state.last_review_file(git_repo, "recipes").read_text() == RECIPES_RULING
    assert RECIPES_RULING in state.review_history(git_repo)[-1].read_text()
    assert subprocess.check_output(["git", "-C", str(git_repo), "rev-parse", "HEAD"]) != before
    assert subprocess.check_output(
        ["git", "-C", str(git_repo), "show", "HEAD:change.txt"], text=True) == "valid staged change\n"
