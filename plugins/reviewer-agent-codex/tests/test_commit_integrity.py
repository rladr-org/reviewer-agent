# SPDX-License-Identifier: MPL-2.0
"""Real Git regressions for approval/content binding; only the model verdict is stubbed."""
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from reviewer_codex import backend, cancellation, commit_tool, gate, state


def git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, **kwargs).stdout.strip()


def stage(repo, content="reviewed\n"):
    (repo / "change.txt").write_text(content)
    git(repo, "add", "change.txt")


@pytest.fixture
def ready(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "reviewer")
    stage(git_repo)

    def review(cwd, session, role, *args, **kwargs):
        state.publish_reviewer_session(cwd, session, role, "review-thread")
        return 0, "APPROVED", ""

    monkeypatch.setattr(backend, "review_turn", review)
    return git_repo


def commit(repo):
    return commit_tool.reviewed_commit(repo, "reviewed change",
                                      state.create_commit_ticket(repo, "session"))


def after_review(monkeypatch, action):
    original = state.save_review

    def save(*args, **kwargs):
        result = original(*args, **kwargs)
        action()
        return result

    monkeypatch.setattr(state, "save_review", save)


def test_staging_after_final_review_check_cannot_enter_commit(ready, monkeypatch):
    before = git(ready, "rev-parse", "HEAD")
    after_review(monkeypatch, lambda: stage(ready, "UNREVIEWED\n"))

    result = commit(ready)

    assert result["status"] == "error"
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "show", ":change.txt") == "UNREVIEWED"


def test_reviewer_reads_captured_index_even_when_live_staging_moves(ready, monkeypatch):
    seen = []
    original_environment = dict(os.environ)

    def review(cwd, session, role, *args, **kwargs):
        stage(ready, "UNREVIEWED\n")
        # Exercise exactly the request-local environment used to launch the reviewer.
        assert dict(os.environ) == original_environment
        environment = backend.child_environment()
        assert environment.get("GIT_INDEX_FILE")
        seen.append(git(cwd, "show", ":change.txt", env=environment))
        outside = []
        worker = threading.Thread(target=lambda: outside.append(backend.child_environment()))
        worker.start()
        worker.join()
        assert outside[0].get("GIT_INDEX_FILE") == original_environment.get("GIT_INDEX_FILE")
        state.publish_reviewer_session(cwd, session, role, "review-thread")
        return 0, "APPROVED", ""

    monkeypatch.setattr(backend, "review_turn", review)
    result = commit(ready)

    assert seen == ["reviewed"]
    assert result["status"] == "error"
    assert git(ready, "show", ":change.txt") == "UNREVIEWED"
    assert "GIT_INDEX_FILE" not in backend.child_environment()


def test_same_parent_branch_switch_cannot_redirect_approved_commit(ready, monkeypatch):
    branch = git(ready, "symbolic-ref", "HEAD")
    before = git(ready, "rev-parse", "HEAD")
    after_review(monkeypatch, lambda: git(ready, "checkout", "-qb", "other"))

    result = commit(ready)

    assert result["status"] == "error"
    assert git(ready, "rev-parse", branch) == before
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "symbolic-ref", "HEAD") == "refs/heads/other"


def test_commit_hook_cannot_add_unreviewed_content(ready):
    hooks = Path(git(ready, "rev-parse", "--absolute-git-dir")) / "hooks"
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'unreviewed\\n' > added-by-hook.txt\n"
                    "git add added-by-hook.txt\n")
    hook.chmod(0o755)
    before = git(ready, "rev-parse", "HEAD")

    result = commit(ready)

    assert result["status"] == "error"
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "diff", "--cached", "--name-only") == "change.txt"
    assert not (ready / ".git" / "index.lock").exists()


def test_mode_only_fix_is_presented_as_changed_on_retry(ready, monkeypatch):
    prompts = []

    def review(cwd, session, role, sandbox, prompt, **kwargs):
        prompts.append(prompt)
        state.publish_reviewer_session(cwd, session, role, "review-thread")
        return 0, "REJECTED\nmake the script executable", ""

    monkeypatch.setattr(backend, "review_turn", review)
    assert commit(ready)["status"] == "rejected"
    git(ready, "update-index", "--chmod=+x", "change.txt")
    assert commit(ready)["status"] == "rejected"

    assert "NOTHING MOVED" not in prompts[1]
    assert "CHANGED: change.txt" in prompts[1]
    assert "100644" in prompts[1] and "100755" in prompts[1]


def test_legacy_cursor_without_modes_does_not_claim_nothing_changed(ready):
    state.write_commit_cursor(ready, "session", {
        "reviewer_id": "review-thread", "head": git(ready, "rev-parse", "HEAD"),
        "files": {"change.txt": git(ready, "rev-parse", ":change.txt")},
    })
    block = commit_tool._change_block(ready, "session", "review-thread",
                                     commit_tool._stage_snapshot(ready))
    assert block == "", "legacy evidence must cause a fresh audit"


def payload(repo, options):
    return {"cwd": str(repo), "session_id": "session", "tool_name": "Bash",
            "tool_input": {"command": "git -C {} commit {}".format(repo, options)}}


def test_signoff_survives_shell_to_tool_handoff(ready):
    response = gate.review_commit(payload(ready, '--signoff -m "reviewed change"'))
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    token = re.search(r"review_token=([a-f0-9]+)", reason).group(1)

    result = commit_tool.reviewed_commit(ready, "reviewed change", token)

    assert result["status"] == "committed"
    assert "Signed-off-by: Test User <test@example.com>" in git(ready, "log", "-1", "--format=%B")


@pytest.mark.parametrize("options", ["--amend -m x", "--author=someone -m x",
                                    "--all -m x", "-m x -- change.txt",
                                    "-m x && echo unrelated"])
def test_unsupported_commit_forms_fail_before_minting_token(ready, options, monkeypatch):
    minted = []
    original = state.create_commit_ticket

    def mint(*args, **kwargs):
        minted.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(state, "create_commit_ticket", mint)
    response = gate.review_commit(payload(ready, options))
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review_token=" not in reason
    assert "unsupported" in reason.lower()
    assert not minted


@pytest.mark.parametrize("detached", [False, True])
def test_success_keeps_unstaged_edits_and_exact_parent(ready, detached):
    if detached:
        git(ready, "checkout", "--detach")
    parent = git(ready, "rev-parse", "HEAD")
    reviewed_tree = git(ready, "write-tree")
    (ready / "change.txt").write_text("unstaged work\n")

    result = commit(ready)

    assert result["status"] == "committed"
    assert git(ready, "rev-parse", "HEAD^{tree}") == reviewed_tree
    assert git(ready, "rev-parse", "HEAD^") == parent
    assert (ready / "change.txt").read_text() == "unstaged work\n"
    assert git(ready, "diff", "--cached") == ""


def test_head_advancement_after_review_is_preserved_and_rejected(ready, monkeypatch):
    parent = git(ready, "rev-parse", "HEAD")
    tree = git(ready, "rev-parse", "HEAD^{tree}")
    competing = git(ready, "commit-tree", tree, "-p", parent, "-m", "competing")
    after_review(monkeypatch, lambda: git(ready, "update-ref", "HEAD", competing, parent))
    assert commit(ready)["status"] == "error"
    assert git(ready, "rev-parse", "HEAD") == competing
    assert git(ready, "show", ":change.txt") == "reviewed"


def test_unborn_branch_publishes_exact_tree_without_parent(ready):
    git(ready, "checkout", "--orphan", "first")
    tree = git(ready, "write-tree")
    result = commit(ready)
    assert result["status"] == "committed"
    assert git(ready, "rev-parse", "HEAD^{tree}") == tree
    assert git(ready, "rev-list", "--parents", "-1", "HEAD") == result["commit"]
    assert git(ready, "diff", "--cached") == ""


def test_linked_worktree_commits_only_its_own_branch_and_index(ready, tmp_path):
    main_head = git(ready, "rev-parse", "HEAD")
    linked = tmp_path / "linked"
    git(ready, "worktree", "add", "-b", "linked", str(linked))
    state.set_feature(linked, "reviewer")
    stage(linked, "linked reviewed\n")
    tree = git(linked, "write-tree")
    result = commit(linked)
    assert result["status"] == "committed"
    assert git(linked, "rev-parse", "HEAD^{tree}") == tree
    assert git(linked, "rev-parse", "HEAD^") == main_head
    assert git(ready, "rev-parse", "HEAD") == main_head
    assert git(ready, "show", ":change.txt") == "reviewed"


def test_cancellation_during_commit_hook_cannot_publish_later(ready):
    token = cancellation.Cancellation()
    before = git(ready, "rev-parse", "HEAD")
    marker, release = ready / "hook-started", ready / "hook-release"
    hook = ready / ".git" / "hooks" / "pre-commit"
    hook.write_text('#!/bin/sh\ntouch hook-started\n'
                    'while [ ! -f hook-release ]; do sleep 0.05; done\n')
    hook.chmod(0o755)
    results = []
    worker = threading.Thread(target=lambda: results.append(commit_tool.reviewed_commit(
        ready, "cancelled", state.create_commit_ticket(ready, "session"), cancel_event=token)))
    worker.start()
    canceller = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), results
        canceller = threading.Thread(target=token.cancel)
        canceller.start()
        canceller.join(2)
        assert not canceller.is_alive(), "cancellation must not wait for a blocked Git hook"
        worker.join(3)
        assert not worker.is_alive(), "cancelled hook process must be reaped"
    finally:
        release.touch()
        worker.join(5)
        if canceller:
            canceller.join(5)
    assert results[0]["status"] == "cancelled"
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "show", ":change.txt") == "reviewed"
    assert not (ready / ".git" / "index.lock").exists()
