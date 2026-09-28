# SPDX-License-Identifier: MPL-2.0
"""An audit that could not inspect its input cannot authorize publication."""
import subprocess

import pytest

from reviewer_codex import commit_tool, state


OBSERVED_VERDICT = (
    "APPROVED\n\nSUGGESTIONS:\n"
    "1. Re-run the audit when the execution sandbox is functional; "
    "staged files could not be inspected."
)


@pytest.mark.parametrize("verdict", [
    OBSERVED_VERDICT,
    "APPROVED\nSUGGESTIONS:\n1. I could not read the staged diff because the sandbox failed.",
    "APPROVED\nSUGGESTIONS:\n1. Unable to inspect the staged changes: permission denied.",
    "APPROVED\nSUGGESTIONS:\n1. The staged content was not reviewed.",
    "APPROVED\nSUGGESTIONS:\n1. The staged input was not inspected.",
    "REVIEW_FAILED: The sandbox could not start; the staged files were not inspected.",
    "APPROVED\nREVIEW_FAILED: The sandbox could not start.",
])
def test_inspection_failure_never_commits_or_advances_review_state(
    git_repo, isolated_state, monkeypatch, verdict
):
    (git_repo / "change.py").write_text("def average(values):\n    return sum(values) / len(values)\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "change.py"], check=True)
    state.set_feature(git_repo, "reviewer")
    state.publish_reviewer_session(git_repo, "owner", "reviewer", "review-thread")
    previous = state.save_review(git_repo, "owner", "REJECTED:\n1. Earlier finding")
    before_head = commit_tool.git_head(git_repo)
    before_tree = commit_tool.staged_tree(git_repo)
    cursor = {"reviewer_id": "review-thread", "head": before_head, "files": {}}
    state.write_commit_cursor(git_repo, "owner", cursor)
    state.record_completed_reviewer_audit(git_repo, "owner", "review-thread")
    monkeypatch.setattr(commit_tool.backend, "review_turn", lambda *a, **k: (0, verdict, ""))

    result = commit_tool.reviewed_commit(
        git_repo, "must not publish", state.create_commit_ticket(git_repo, "owner")
    )

    assert commit_tool.git_head(git_repo) == before_head, "inspection failure published a commit"
    assert commit_tool.staged_tree(git_repo) == before_tree
    assert result["status"] == "error"
    assert "inspect" in result["message"].lower()
    assert previous.read_text() == "REJECTED:\n1. Earlier finding"
    assert state.read_commit_cursor(git_repo, "owner") == cursor
    assert state.reviewer_session_completed_audits(git_repo, "owner") == 1
    records = [p.read_text() for p in state.review_history(git_repo)]
    assert any("OUTCOME: FAILED" in text and verdict in text for text in records)
    assert not commit_tool.approved(verdict)


@pytest.mark.parametrize("suggestion", [
    "GPU tests could not be run here; source inspection found no defects.",
    "The deployment check was unavailable; staged files were inspected.",
    "Document why the rejected alternative was not selected.",
    "Add a regression for cases where staged files could not be inspected.",
    "I could not run the staged tests because the optional GPU is unavailable.",
])
def test_nonblocking_suggestions_still_allow_approval(suggestion):
    assert commit_tool.approved("APPROVED\nSUGGESTIONS:\n1. " + suggestion)


def test_nonzero_inspection_failure_retains_the_unapplied_response(
    git_repo, isolated_state, monkeypatch
):
    (git_repo / "change.txt").write_text("staged\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "change.txt"], check=True)
    state.set_feature(git_repo, "reviewer")
    before = commit_tool.git_head(git_repo)
    output = "REVIEW_FAILED: The sandbox could not inspect the staged input."
    monkeypatch.setattr(commit_tool.backend, "review_turn", lambda *a, **k: (1, output, "sandbox failed"))
    result = commit_tool.reviewed_commit(
        git_repo, "must not publish", state.create_commit_ticket(git_repo, "owner")
    )
    assert result["status"] == "error"
    assert commit_tool.git_head(git_repo) == before
    assert any("OUTCOME: FAILED" in p.read_text() and output in p.read_text()
               for p in state.review_history(git_repo))
