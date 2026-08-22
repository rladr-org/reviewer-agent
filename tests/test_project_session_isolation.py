# SPDX-License-Identifier: MPL-2.0
"""Reviewer sessions are keyed by PROJECT as well as by host session, role and backend.

The gap this closes: one Claude session that moves between two unrelated repositories used
to resume the reviewer thread -- and therefore the brief -- created for the first one. The
reviewer then audited repository B's staged diff while holding repository A's
`reviewerAgent.md`, its accumulated findings, and its operator directives.

Four dimensions, all of which must separate state, and one that deliberately must NOT:

  project     unrelated repositories never share a reviewer, a stop supervisor, an operator
              watermark, or a nudge budget, even under one host session id;
  session     unchanged from tests/test_session_isolation.py;
  role        unchanged from tests/test_session_roles.py;
  backend     codex and pi keep separate slots;
  worktree    linked worktrees of ONE repository share everything, because they are one
              project (`--git-common-dir`).

The project argument is REQUIRED and positional on every helper: a new call site that
forgets it raises TypeError rather than silently writing an unscoped file that some other
repository will later read.
"""
import subprocess

import pytest

import reviewer_lib as rl


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def state_dir():
    return rl.reviewer_home() / "state"


def make_repo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return str(repo)


def add_worktree(repo, path, branch):
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(path), "-b", branch], check=True
    )
    return str(path)


# --------------------------------------------------------------------------
# codex reviewer ids
# --------------------------------------------------------------------------
def test_one_host_session_in_two_repositories_gets_two_reviewers(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.write_session_id(a, "claude-1", "codex-a")
    rl.write_session_id(b, "claude-1", "codex-b")

    assert rl.read_session_id(a, "claude-1") == "codex-a"
    assert rl.read_session_id(b, "claude-1") == "codex-b"


def test_a_repository_with_no_reviewer_yet_does_not_inherit_anothers(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.write_session_id(a, "claude-1", "codex-a")

    assert rl.read_session_id(b, "claude-1") is None


def test_linked_worktrees_share_one_reviewer(home, tmp_path):
    repo = make_repo(tmp_path, "repo")
    worktree = add_worktree(repo, tmp_path / "wt", "b")

    rl.write_session_id(repo, "claude-1", "codex-1")

    assert rl.read_session_id(worktree, "claude-1") == "codex-1"
    assert rl.session_id_file(worktree, "claude-1") == rl.session_id_file(repo, "claude-1")


def test_roles_stay_separate_within_one_project(home, tmp_path):
    repo = make_repo(tmp_path, "repo")

    rl.write_session_id(repo, "claude-1", "reviewer-1")
    rl.write_session_id(repo, "claude-1", "stop-1", role=rl.ROLE_STOP)

    assert rl.read_session_id(repo, "claude-1") == "reviewer-1"
    assert rl.read_session_id(repo, "claude-1", role=rl.ROLE_STOP) == "stop-1"


def test_compare_and_set_converges_per_project(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    assert rl.write_session_id_if_absent(a, "claude-1", "first") == "first"
    assert rl.write_session_id_if_absent(a, "claude-1", "second") == "first"
    # The loser in project A must not have published anything in project B.
    assert rl.write_session_id_if_absent(b, "claude-1", "second") == "second"


def test_an_unidentifiable_session_still_persists_nothing(home, tmp_path):
    repo = make_repo(tmp_path, "repo")

    assert rl.session_id_file(repo, None) is None
    rl.write_session_id(repo, None, "codex-X")
    assert rl.read_session_id(repo, None) is None


def test_a_missing_project_persists_nothing_rather_than_sharing_a_slot(home):
    assert rl.session_id_file("", "claude-1") is None
    rl.write_session_id("", "claude-1", "codex-X")
    assert rl.read_session_id("", "claude-1") is None


def test_session_helpers_require_the_project_argument(home):
    for call in (
        lambda: rl.session_id_file("claude-1"),
        lambda: rl.read_session_id("claude-1"),
        lambda: rl.pi_session_file("claude-1"),
        lambda: rl.read_pi_session("claude-1"),
    ):
        with pytest.raises(TypeError):
            call()


# --------------------------------------------------------------------------
# pi reviewer ids
# --------------------------------------------------------------------------
def test_pi_sessions_are_project_scoped_and_separate_from_codex(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.write_pi_session(a, "claude-1", "pi-a")
    rl.write_pi_session(b, "claude-1", "pi-b")
    rl.write_session_id(a, "claude-1", "codex-a")

    assert rl.read_pi_session(a, "claude-1") == "pi-a"
    assert rl.read_pi_session(b, "claude-1") == "pi-b"
    assert rl.read_session_id(a, "claude-1") == "codex-a"
    assert rl.pi_session_file(a, "claude-1") != rl.session_id_file(a, "claude-1")


def test_pi_worktrees_share_and_roles_do_not(home, tmp_path):
    repo = make_repo(tmp_path, "repo")
    worktree = add_worktree(repo, tmp_path / "wt", "b")

    rl.write_pi_session(repo, "claude-1", "pi-reviewer")
    rl.write_pi_session(repo, "claude-1", "pi-stop", role=rl.ROLE_STOP)

    assert rl.read_pi_session(worktree, "claude-1") == "pi-reviewer"
    assert rl.read_pi_session(worktree, "claude-1", role=rl.ROLE_STOP) == "pi-stop"


# --------------------------------------------------------------------------
# operator watermarks and nudge counters
# --------------------------------------------------------------------------
def test_operator_watermarks_are_project_scoped(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.write_operator_watermark(a, "claude-1", "uuid-a")

    assert rl.read_operator_watermark(a, "claude-1") == "uuid-a"
    assert rl.read_operator_watermark(b, "claude-1") is None


def test_nudge_counters_are_project_scoped(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.bump_nudge_count(a, "claude-1")
    rl.bump_nudge_count(a, "claude-1")

    assert rl.read_nudge_count(a, "claude-1") == 2
    assert rl.read_nudge_count(b, "claude-1") == 0
    rl.reset_nudge_count(a, "claude-1")
    assert rl.read_nudge_count(a, "claude-1") == 0


def test_an_unidentifiable_session_keeps_one_shared_nudge_budget_per_project(home, tmp_path):
    # Deliberate exception to the no-shared-slot rule: no counter at all would mean an
    # unbounded nudge budget, and sharing one only makes it stricter. It is still scoped to
    # the project, so a runaway in one repository cannot mute nudges in another.
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")

    rl.bump_nudge_count(a, None)

    assert rl.read_nudge_count(a, None) == 1
    assert rl.read_nudge_count(b, None) == 0


# --------------------------------------------------------------------------
# legacy unscoped files
# --------------------------------------------------------------------------
def test_legacy_unscoped_session_files_are_never_resumed(home, tmp_path):
    repo = make_repo(tmp_path, "repo")
    state_dir().mkdir(parents=True, exist_ok=True)
    for name in ("sess_claude-1.codex", "sess_claude-1.reviewer.codex",
                 "sess_claude-1.stop.codex", "sess_claude-1.pi", "sess_claude-1.stop.pi"):
        (state_dir() / name).write_text("legacy-thread\n", encoding="utf-8")

    assert rl.read_session_id(repo, "claude-1") is None
    assert rl.read_session_id(repo, "claude-1", role=rl.ROLE_STOP) is None
    assert rl.read_pi_session(repo, "claude-1") is None
    assert rl.read_pi_session(repo, "claude-1", role=rl.ROLE_STOP) is None


def test_legacy_unscoped_session_files_are_retired_on_use(home, tmp_path):
    repo = make_repo(tmp_path, "repo")
    state_dir().mkdir(parents=True, exist_ok=True)
    legacy = [
        state_dir() / name
        for name in ("sess_claude-1.codex", "sess_claude-1.reviewer.codex",
                     "sess_claude-1.stop.codex", "sess_claude-1.pi", "sess_claude-1.stop.pi")
    ]
    for path in legacy:
        path.write_text("legacy-thread\n", encoding="utf-8")

    rl.retire_legacy_sessions("claude-1")

    assert [path for path in legacy if path.exists()] == []


# --------------------------------------------------------------------------
# rotation and inspection
# --------------------------------------------------------------------------
def test_rotation_clears_only_the_current_project(home, tmp_path):
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    rl.write_session_id(a, "claude-1", "codex-a")
    rl.write_session_id(b, "claude-1", "codex-b")
    rl.write_pi_session(a, "claude-1", "pi-a")
    rl.write_pi_session(b, "claude-1", "pi-b")
    rl.save_review("verdict-a", proj=a, claude_session_id="claude-1")
    rl.save_review("verdict-b", proj=b, claude_session_id="claude-1")

    cleared = rl.rotate_session(a, "claude-1")

    assert cleared
    assert rl.read_session_id(a, "claude-1") is None
    assert rl.read_pi_session(a, "claude-1") is None
    assert rl.read_session_id(b, "claude-1") == "codex-b"
    assert rl.read_pi_session(b, "claude-1") == "pi-b"
    assert rl.last_review_file(b, "claude-1").exists()
    assert not rl.last_review_file(a, "claude-1").exists()


def test_rotation_reports_the_canonical_project_root(home, tmp_path):
    repo = make_repo(tmp_path, "repo")
    worktree = add_worktree(repo, tmp_path / "wt", "b")
    rl.write_session_id(repo, "claude-1", "codex-1")

    cleared = rl.rotate_session(worktree, "claude-1")

    assert cleared
    assert rl.read_session_id(repo, "claude-1") is None
