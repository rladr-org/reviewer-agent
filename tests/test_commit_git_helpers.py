# SPDX-License-Identifier: MPL-2.0
"""Unit tests for the (C) commit helpers in reviewer_git: staged_tree and run_commit.

These are the primitives the gate relies on for the "digest-compare + normal commit" design:
`staged_tree` is git's own write-tree digest of the staged set, and `run_commit` runs an ordinary
`git commit` (hooks and all) while resolving publication status safely -- a timeout or a nonzero
exit is ambiguous, so it returns an explicit committed / failed / unknown result, deciding by
whether HEAD advanced (and, when HEAD is unreadable, by whether git exited cleanly) rather than
ever assuming nothing was committed.
"""
import subprocess
import sys
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
sys.path.insert(0, str(ROOT))

import reviewer_git as rg  # noqa: E402


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "base.py").write_text("base = 0\n")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _hook(repo, name, body):
    hooks = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "hooks"
    path = hooks / name
    path.write_text(body)
    path.chmod(0o755)


def test_staged_tree_is_the_write_tree_digest(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    assert rg.staged_tree(str(repo)) == _git(repo, "write-tree")


def test_staged_tree_changes_with_the_staged_set(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    before = rg.staged_tree(str(repo))
    (repo / "f.py").write_text("x = 2\n")
    _git(repo, "add", "f.py")
    assert rg.staged_tree(str(repo)) != before


def test_run_commit_makes_a_normal_commit_and_runs_hooks(tmp_path):
    repo = _repo(tmp_path)
    marker = repo / "HOOK_RAN"
    _hook(repo, "pre-commit", "#!/bin/sh\ntouch '{}'\n".format(marker))
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    parent = _git(repo, "rev-parse", "HEAD")

    status, sha, note = rg.run_commit(str(repo), "feature", [])

    assert status == "committed"
    assert note == ""
    assert sha == _git(repo, "rev-parse", "HEAD")
    assert sha != parent
    assert marker.exists()  # the repository's own hook ran
    assert _git(repo, "log", "-1", "--format=%s") == "feature"


def test_run_commit_carries_options_like_signoff(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    status, sha, note = rg.run_commit(str(repo), "feature", ["--signoff"])
    assert status == "committed" and note == "" and sha
    assert "Signed-off-by:" in _git(repo, "log", "-1", "--format=%B")


def test_run_commit_reports_publication_when_a_post_commit_hook_hangs(tmp_path):
    # git moves the ref BEFORE running post-commit. If that hook hangs past the timeout, the commit
    # is already published, so run_commit must report it as committed -- never "failed" (which would
    # prompt a duplicate) -- while still tearing the hung hook down and surfacing a warning.
    repo = _repo(tmp_path)
    _hook(repo, "post-commit", "#!/bin/sh\nsleep 30\n")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    parent = _git(repo, "rev-parse", "HEAD")

    status, sha, note = rg.run_commit(str(repo), "feature", [], timeout=1)

    assert status == "committed"
    assert sha and sha != parent  # the ref moved -> published despite the timeout
    assert note  # a warning is carried (git did not exit cleanly)
    assert _git(repo, "log", "-1", "--format=%s") == "feature"


def test_run_commit_reports_failure_when_a_hook_blocks_the_commit(tmp_path):
    # A pre-commit hook that fails leaves nothing published -> ("failed", None, error), HEAD unmoved.
    repo = _repo(tmp_path)
    _hook(repo, "pre-commit", "#!/bin/sh\nexit 1\n")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    parent = _git(repo, "rev-parse", "HEAD")

    status, sha, error = rg.run_commit(str(repo), "feature", [])

    assert status == "failed"
    assert sha is None
    assert error
    assert _git(repo, "rev-parse", "HEAD") == parent  # HEAD did not move


def test_run_commit_reports_committed_when_head_is_unreadable_after_success(tmp_path, monkeypatch):
    # git exited cleanly, so a commit WAS made; if HEAD cannot be read back, that must still be
    # reported as committed (id unknown), never as "failed" -- reporting failure would invite a
    # duplicate.
    repo = _repo(tmp_path)
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    monkeypatch.setattr(rg, "git_head", lambda path: None)  # HEAD unreadable

    status, sha, detail = rg.run_commit(str(repo), "feature", [])

    assert status == "committed"
    assert sha == ""  # a commit was made but its id could not be read
    assert detail
    # a real commit did land (observed without the patched reader)
    assert _git(repo, "log", "-1", "--format=%s") == "feature"


def test_run_commit_reports_unknown_when_state_is_unreadable_after_failure(tmp_path, monkeypatch):
    # git did not exit cleanly AND HEAD is unreadable -> publication is genuinely undetermined.
    repo = _repo(tmp_path)
    _hook(repo, "pre-commit", "#!/bin/sh\nexit 1\n")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    monkeypatch.setattr(rg, "git_head", lambda path: None)  # HEAD unreadable

    status, sha, detail = rg.run_commit(str(repo), "feature", [])

    assert status == "unknown"
    assert sha is None
    assert "unknown" in detail.lower()


def test_run_commit_terminates_a_timed_out_hook_process_tree(tmp_path):
    # A pre-commit hook that outlives the timeout must be TORN DOWN, not left running to write
    # (mutate / re-stage) after the gate has already reported failure and the caller may retry.
    repo = _repo(tmp_path)
    started = repo / "HOOK_STARTED"
    done = repo / "HOOK_DONE"
    _hook(repo, "pre-commit",
          "#!/bin/sh\ntouch '{}'\nsleep 3\ntouch '{}'\n".format(started, done))
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    parent = _git(repo, "rev-parse", "HEAD")

    status, sha, error = rg.run_commit(str(repo), "feature", [], timeout=1)

    assert status == "failed"  # timed out during pre-commit -> nothing published
    assert sha is None
    assert "timed out" in error.lower()
    assert started.exists()  # the hook did start
    assert _git(repo, "rev-parse", "HEAD") == parent  # HEAD did not move
    time.sleep(4)  # past the hook's 3s sleep
    assert not done.exists()  # the hook tree was killed before its late side effect could land
