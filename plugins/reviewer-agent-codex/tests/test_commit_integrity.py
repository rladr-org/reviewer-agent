# SPDX-License-Identifier: MPL-2.0
"""Real Git regressions for approval/content binding; only the model verdict is stubbed."""
import os
import re
import shlex
import signal
import subprocess
import sys
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


def test_staging_during_review_requires_another_review(ready, monkeypatch):
    before = git(ready, "rev-parse", "HEAD")
    environment_before = dict(os.environ)
    def review(cwd, session, role, *args, **kwargs):
        stage(ready, "changed during review\n")
        assert dict(os.environ) == environment_before
        assert backend.child_environment().get("GIT_INDEX_FILE") == environment_before.get("GIT_INDEX_FILE")
        state.publish_reviewer_session(cwd, session, role, "review-thread")
        return 0, "APPROVED", ""
    monkeypatch.setattr(backend, "review_turn", review)
    result = commit(ready)
    assert result["status"] == "error", result
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "show", ":change.txt") == "changed during review"


def test_ordinary_commit_uses_the_current_branch(ready, monkeypatch):
    branch = git(ready, "symbolic-ref", "HEAD")
    before = git(ready, "rev-parse", "HEAD")
    after_review(monkeypatch, lambda: git(ready, "checkout", "-qb", "other"))
    result = commit(ready)
    assert result["status"] == "committed", result
    assert git(ready, "rev-parse", branch) == before
    assert git(ready, "rev-parse", "HEAD") == result["commit"]
    assert git(ready, "symbolic-ref", "HEAD") == "refs/heads/other"


def test_commit_honors_content_restaged_by_repository_hook(ready):
    hooks = Path(git(ready, "rev-parse", "--absolute-git-dir")) / "hooks"
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'unreviewed\\n' > added-by-hook.txt\n"
                    "git add added-by-hook.txt\n")
    hook.chmod(0o755)
    before = git(ready, "rev-parse", "HEAD")

    result = commit(ready)

    assert result["status"] == "committed", result
    assert git(ready, "rev-parse", "HEAD") != before
    assert git(ready, "show", "HEAD:added-by-hook.txt") == "unreviewed"


def test_hooks_use_real_git_metadata_and_see_the_published_commit(ready):
    hooks = ready / ".git" / "hooks"
    pre = hooks / "pre-commit"
    pre.write_text("#!/bin/sh\ngit rev-parse --absolute-git-dir > hook-gitdir\n")
    pre.chmod(0o755)
    post = hooks / "post-commit"
    post.write_text("#!/bin/sh\ngit --git-dir=.git rev-parse HEAD > hook-public-head\n")
    post.chmod(0o755)

    result = commit(ready)

    assert result["status"] == "committed", result
    assert Path((ready / "hook-gitdir").read_text().strip()).resolve() == (ready / ".git").resolve()
    assert (ready / "hook-public-head").read_text().strip() == result["commit"]
    assert git(ready, "diff", "--cached", "--name-only") == ""
    assert not (ready / ".git" / "index.lock").exists()


def test_failing_hook_preserves_its_real_staging_changes(ready):
    before = git(ready, "rev-parse", "HEAD")
    hook = ready / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'formatted\\n' > change.txt\ngit add change.txt\nexit 1\n")
    hook.chmod(0o755)
    result = commit(ready)
    assert result["status"] == "error", result
    assert "Hooks may have modified or staged files" in result["message"]
    assert git(ready, "rev-parse", "HEAD") == before
    assert git(ready, "show", ":change.txt") == "formatted"


def test_cancellation_after_publication_does_not_claim_nothing_committed(ready):
    token = cancellation.Cancellation()
    before = git(ready, "rev-parse", "HEAD")
    marker, release = ready / "post-started", ready / "post-release"
    hook = ready / ".git" / "hooks" / "post-commit"
    hook.write_text('#!/bin/sh\ntouch post-started\n'
                    'while [ ! -f post-release ]; do sleep 0.05; done\n')
    hook.chmod(0o755)
    results = []
    worker = threading.Thread(target=lambda: results.append(commit_tool.reviewed_commit(
        ready, "reviewed change", state.create_commit_ticket(ready, "session"), cancel_event=token)))
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), results
        token.cancel()
        worker.join(3)
        assert not worker.is_alive()
        assert results[0]["status"] == "publication_unknown", results
        assert git(ready, "rev-parse", "HEAD") != before
        assert "Do not retry automatically" in results[0]["message"]
    finally:
        release.touch()
        worker.join(5)


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


def test_stage_snapshot_keys_a_rename_by_its_new_name(ready):
    git(ready, "commit", "-qm", "base")
    git(ready, "mv", "change.txt", "moved.txt")
    assert commit_tool._stage_snapshot(ready) == {
        "moved.txt": "100644 " + git(ready, "rev-parse", ":moved.txt")}


def test_stage_snapshot_survives_more_paths_than_one_command_line_holds(git_repo):
    # Passing every staged path to one git invocation died with E2BIG ("Argument list too
    # long") past ~8800 files on macOS. Index entries need no working-tree files, so ~4 MB
    # of names -- over ARG_MAX on macOS and Linux alike -- stages in well under a second.
    blob = git(git_repo, "hash-object", "-w", "--stdin", input="x\n")
    names = ["many/{:04d}/".format(i) + "/".join(["p" * 200] * 7) for i in range(3000)]
    git(git_repo, "update-index", "--index-info",
        input="".join("100644 {}\t{}\n".format(blob, n) for n in names))
    snapshot = commit_tool._stage_snapshot(git_repo)
    assert snapshot == {n: "100644 " + blob for n in names}


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


def test_ordinary_commit_uses_the_current_parent(ready, monkeypatch):
    parent = git(ready, "rev-parse", "HEAD")
    tree = git(ready, "rev-parse", "HEAD^{tree}")
    competing = git(ready, "commit-tree", tree, "-p", parent, "-m", "competing")
    after_review(monkeypatch, lambda: git(ready, "update-ref", "HEAD", competing, parent))
    result = commit(ready)
    assert result["status"] == "committed", result
    assert git(ready, "rev-parse", "HEAD^") == competing
    assert git(ready, "show", "HEAD:change.txt") == "reviewed"


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


def test_reviewer_launch_environment_remains_stable(ready, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(ready / "temporary-parent-turn"))
    seen = []

    def review(*args, **kwargs):
        seen.append(backend.child_environment())
        state.publish_reviewer_session(ready, "session", "reviewer", "review-thread")
        return 0, "APPROVED", ""

    monkeypatch.setattr(backend, "review_turn", review)
    result = commit(ready)
    assert result["status"] == "committed", result
    assert seen[0]["TMPDIR"] == backend.stable_temp_root()
    assert seen[0]["REVIEWER_CODEX_CHILD"] == "1"


def test_split_index_retains_exact_tree(ready):
    git(ready, "update-index", "--split-index")
    tree = git(ready, "write-tree")
    result = commit(ready)
    assert result["status"] == "committed", result
    assert git(ready, "rev-parse", "HEAD^{tree}") == tree




def test_existing_index_lock_is_preserved(ready):
    lock = ready / ".git" / "index.lock"
    lock.write_text("owned by another process")
    before = git(ready, "rev-parse", "HEAD")
    result = commit(ready)
    assert result["status"] == "error", result
    assert lock.read_text() == "owned by another process"
    assert git(ready, "rev-parse", "HEAD") == before


def test_commit_launches_only_synchronous_git_processes(ready, monkeypatch):
    launches = []
    original = subprocess.Popen

    def launch(argv, *args, **kwargs):
        process = original(argv, *args, **kwargs)
        launches.append((argv, kwargs, process))
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    result = commit(ready)

    assert result["status"] == "committed", result
    assert launches
    assert all(Path(argv[0]).name == "git" for argv, _, _ in launches)
    assert all(not kwargs.get("start_new_session") for _, kwargs, _ in launches)
    assert all(process.poll() is not None for _, _, process in launches)


def test_commit_hook_timeout_is_bounded_and_cannot_publish(ready, monkeypatch):
    from reviewer_codex import git as git_ops
    monkeypatch.setattr(git_ops, "COMMIT_TIMEOUT", 0.2)
    durations = []
    original = commit_tool.run_commit
    def timed_commit(*args, **kwargs):
        started = time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            durations.append(time.monotonic() - started)
    monkeypatch.setattr(commit_tool, "run_commit", timed_commit)
    before = git(ready, "rev-parse", "HEAD")
    hook = ready / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexec sleep 3\n")
    hook.chmod(0o755)
    result = commit(ready)
    assert result["status"] == "error", result
    assert "budget" in result["message"]
    assert len(durations) == 1 and durations[0] < 2
    assert git(ready, "rev-parse", "HEAD") == before
    assert not (ready / ".git" / "index.lock").exists()


@pytest.mark.parametrize("detached", [False, True])
def test_external_hook_background_output_does_not_hold_commit_open(ready, detached):
    pid_file = ready / "hook-child.pid"
    hook = ready / ".git" / "hooks" / "post-commit"
    # The child publishes its pid by rename: write_text creates the file before filling it, and
    # the cleanup below read it empty in that gap on a CI runner (int('') -> ValueError).
    script = ("import os, time; from pathlib import Path; "
              + ("os.setsid(); " if detached else "") +
              "Path('hook-child.pid.tmp').write_text(str(os.getpid())); "
              "os.replace('hook-child.pid.tmp', 'hook-child.pid'); time.sleep(60)")
    hook.write_text("#!/bin/sh\n{} -c {} &\n".format(
        shlex.quote(sys.executable), shlex.quote(script)))
    hook.chmod(0o755)
    results = []
    worker = threading.Thread(target=lambda: results.append(commit(ready)))
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), results
        worker.join(5)
        assert not worker.is_alive(), "external hook output must not hold the commit open"
        assert results[0]["status"] == "committed", results[0]["message"]
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        worker.join(5)


def test_repository_conditional_configuration_applies_to_commit(ready, tmp_path):
    included = tmp_path / "identity.config"
    included.write_text("[user]\nemail = conditional@example.com\n")
    git(ready, "config", "includeIf.gitdir:{}/.git.path".format(ready), str(included))
    assert git(ready, "config", "user.email") == "conditional@example.com"
    result = commit(ready)
    assert result["status"] == "committed", result
    assert git(ready, "log", "-1", "--format=%ae") == "conditional@example.com"


@pytest.mark.parametrize("phase", ["pre-commit", "post-commit"])
def test_hook_timeout_reports_actual_publication(ready, monkeypatch, phase):
    from reviewer_codex import git as git_ops
    monkeypatch.setattr(git_ops, "COMMIT_TIMEOUT", 0.2)
    before = git(ready, "rev-parse", "HEAD")
    hook = ready / ".git" / "hooks" / phase
    hook.write_text("#!/bin/sh\nexec sleep 3\n")
    hook.chmod(0o755)
    token = cancellation.Cancellation()
    result = commit_tool.reviewed_commit(
        ready, "reviewed change", state.create_commit_ticket(ready, "session"), cancel_event=token)
    if phase == "post-commit":
        assert result["status"] == "publication_unknown", result
        assert git(ready, "rev-parse", "HEAD") != before
        assert "Do not retry automatically" in result["message"]
        assert not token.cancel()
    else:
        assert result["status"] == "error", result
        assert git(ready, "rev-parse", "HEAD") == before
    assert not (ready / ".git" / "index.lock").exists()


def test_unconfirmed_publication_does_not_report_an_ordinary_failure(ready, monkeypatch):
    from reviewer_codex import git as git_ops
    before = git(ready, "rev-parse", "HEAD")
    hook = ready / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    monkeypatch.setattr(git_ops, "git_head", lambda *args: None)
    token = cancellation.Cancellation()
    result = commit_tool.reviewed_commit(
        ready, "reviewed change", state.create_commit_ticket(ready, "session"), cancel_event=token)
    assert result["status"] == "publication_unknown", result
    assert "Do not retry automatically" in result["message"]
    assert git(ready, "rev-parse", "HEAD") == before
    assert not token.cancel()


def test_failing_hook_moving_head_cannot_claim_reviewed_commit_published(ready):
    before = git(ready, "rev-parse", "HEAD")
    unrelated = git(ready, "commit-tree", "HEAD^{tree}", "-p", before,
                    "-m", "unrelated commit")
    hook = ready / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ngit update-ref HEAD " + unrelated + "\nexit 1\n")
    hook.chmod(0o755)

    result = commit(ready)

    assert git(ready, "rev-parse", "HEAD") == unrelated
    assert result["status"] == "publication_unknown", result
    assert "Do not retry automatically" in result["message"]
    assert not result.get("commit")
