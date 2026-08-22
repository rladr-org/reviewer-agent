# SPDX-License-Identifier: MPL-2.0
"""Native-Codex reviewer state is keyed by PROJECT as well as by host session and role.

A Codex session is not bound to one repository: the operator can `cd` anywhere in the
conversation. Keying the reviewer thread, the operator-prompt ledger, the plan, the nudge
budget and the liveness markers by session alone therefore let repository B's stop supervisor
resume repository A's thread and read A's prompts and plan.

Linked worktrees of one repository are deliberately the same project -- `project_key` resolves
through `--git-common-dir` -- so they share every one of these.
"""
import subprocess

import pytest

from reviewer_codex import liveness, state


@pytest.fixture
def two_repos(tmp_path):
    made = []
    for name in ("alpha", "beta"):
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
        made.append(repo)
    return made


def test_reviewer_threads_are_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos

    state.publish_reviewer_session(alpha, "session-1", "reviewer", "thread-alpha")
    state.publish_reviewer_session(beta, "session-1", "reviewer", "thread-beta")

    assert state.read_reviewer_session(alpha, "session-1", "reviewer") == "thread-alpha"
    assert state.read_reviewer_session(beta, "session-1", "reviewer") == "thread-beta"


def test_a_second_repository_starts_with_no_thread(two_repos, isolated_state):
    alpha, beta = two_repos

    state.publish_reviewer_session(alpha, "session-1", "reviewer", "thread-alpha")

    assert state.read_reviewer_session(beta, "session-1", "reviewer") is None


def test_roles_stay_isolated_inside_one_project(two_repos, isolated_state):
    alpha, _beta = two_repos

    state.publish_reviewer_session(alpha, "session-1", "reviewer", "review-thread")
    state.publish_reviewer_session(alpha, "session-1", "stop", "stop-thread")

    assert state.read_reviewer_session(alpha, "session-1", "reviewer") == "review-thread"
    assert state.read_reviewer_session(alpha, "session-1", "stop") == "stop-thread"


def test_retirement_is_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos
    state.publish_reviewer_session(alpha, "session-1", "reviewer", "thread-alpha")
    state.publish_reviewer_session(beta, "session-1", "reviewer", "thread-beta")

    assert state.retire_reviewer_session(
        alpha, "session-1", "reviewer", expected_reviewer_id="thread-alpha"
    )

    assert state.read_reviewer_session(alpha, "session-1", "reviewer") is None
    assert state.read_reviewer_session(beta, "session-1", "reviewer") == "thread-beta"


def test_linked_worktrees_share_one_thread(git_repo, tmp_path, isolated_state):
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(git_repo), "worktree", "add", "-q", str(worktree), "-b", "b"],
        check=True,
    )

    state.publish_reviewer_session(git_repo, "session-1", "reviewer", "thread-1")

    assert state.read_reviewer_session(worktree, "session-1", "reviewer") == "thread-1"


def test_prompt_ledger_is_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos

    state.record_prompt(alpha, "session-1", "alpha prompt")
    state.record_prompt(beta, "session-1", "beta prompt")

    assert state.recent_prompts(alpha, "session-1") == ["alpha prompt"]
    assert state.recent_prompts(beta, "session-1") == ["beta prompt"]


def test_plan_is_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos

    state.record_plan(alpha, "session-1", {"plan": [{"step": "alpha work"}]})

    assert state.current_plan(alpha, "session-1")["plan"][0]["step"] == "alpha work"
    assert state.current_plan(beta, "session-1") is None


def test_nudge_budget_is_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos

    state.bump_nudges(alpha, "session-1")
    state.bump_nudges(alpha, "session-1")

    assert state.read_nudges(alpha, "session-1") == 2
    assert state.read_nudges(beta, "session-1") == 0
    state.reset_nudges(alpha, "session-1")
    assert state.read_nudges(alpha, "session-1") == 0


def test_liveness_markers_are_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos

    liveness.mark(alpha, "session-1", "shell", "call-1", "sleep 100")

    assert [item["id"] for item in liveness.outstanding(alpha, "session-1")] == ["call-1"]
    assert liveness.outstanding(beta, "session-1") == []


def test_liveness_challenges_are_project_scoped(two_repos, isolated_state):
    alpha, beta = two_repos
    items = [{"kind": "shell", "id": "call-1"}]

    liveness.challenge_items(alpha, "session-1", items)

    assert liveness.items_were_challenged(alpha, "session-1", items)
    assert not liveness.items_were_challenged(beta, "session-1", items)


def test_ending_a_session_clears_its_artifacts_in_every_project(two_repos, isolated_state):
    alpha, beta = two_repos
    for repo in (alpha, beta):
        state.publish_reviewer_session(repo, "session-1", "reviewer", "thread")
        state.record_prompt(repo, "session-1", "prompt")
        state.record_plan(repo, "session-1", {"plan": [{"step": "x"}]})
        state.bump_nudges(repo, "session-1")
        liveness.mark(repo, "session-1", "shell", "call-1", "sleep 100")
    state.publish_reviewer_session(alpha, "session-2", "reviewer", "other-thread")

    state.clear_session_artifacts("session-1")
    liveness.clear_session("session-1")

    for repo in (alpha, beta):
        assert state.read_reviewer_session(repo, "session-1", "reviewer") is None
        assert state.recent_prompts(repo, "session-1") == []
        assert state.current_plan(repo, "session-1") is None
        assert state.read_nudges(repo, "session-1") == 0
        assert liveness.outstanding(repo, "session-1") == []
    assert state.read_reviewer_session(alpha, "session-2", "reviewer") == "other-thread"


def test_state_outside_a_repository_is_not_shared(tmp_path, isolated_state):
    # `project_key` is None outside a Git repository. That must mean "no state", exactly as an
    # unidentifiable session does -- never one shared slot that two unrelated directories would
    # both resume.
    loose = tmp_path / "loose"
    loose.mkdir()

    assert state.reviewer_session_file(loose, "session-1", "reviewer") is None
    state.publish_reviewer_session(loose, "session-1", "reviewer", "thread")
    assert state.read_reviewer_session(loose, "session-1", "reviewer") is None
    assert state.recent_prompts(loose, "session-1") == []
    assert liveness.outstanding(loose, "session-1") == []
