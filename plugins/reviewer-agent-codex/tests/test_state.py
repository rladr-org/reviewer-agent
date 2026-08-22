# SPDX-License-Identifier: MPL-2.0
import hashlib
import subprocess

from reviewer_codex import prompts, state


def test_features_are_independent_and_default_off(git_repo, isolated_state):
    assert not state.feature_enabled(git_repo, "reviewer")
    assert not state.feature_enabled(git_repo, "stop")
    state.set_feature(git_repo, "reviewer")
    assert state.feature_enabled(git_repo, "reviewer")
    assert not state.feature_enabled(git_repo, "stop")
    state.set_feature(git_repo, "stop")
    state.clear_feature(git_repo, "reviewer")
    assert not state.feature_enabled(git_repo, "reviewer")
    assert state.feature_enabled(git_repo, "stop")


def test_prompt_ledger_is_session_scoped(git_repo, isolated_state):
    state.record_prompt(git_repo, "session-a", "first")
    state.record_prompt(git_repo, "session-a", "second")
    state.record_prompt(git_repo, "session-b", "other")
    assert state.recent_prompts(git_repo, "session-a") == ["first", "second"]
    assert state.recent_prompts(git_repo, "session-b") == ["other"]


def test_reviewer_roles_are_isolated(git_repo, isolated_state):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "review-thread")
    state.publish_reviewer_session(git_repo, "root", "stop", "stop-thread")
    assert state.read_reviewer_session(git_repo, "root", "reviewer") == "review-thread"
    assert state.read_reviewer_session(git_repo, "root", "stop") == "stop-thread"


def test_reviewer_session_retirement_is_compare_safe(git_repo, isolated_state):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "slow-thread")

    assert not state.retire_reviewer_session(git_repo,
        "root", "reviewer", expected_reviewer_id="replacement-thread"
    )
    assert state.read_reviewer_session(git_repo, "root", "reviewer") == "slow-thread"

    assert state.retire_reviewer_session(git_repo,
        "root", "reviewer", expected_reviewer_id="slow-thread"
    )
    assert state.read_reviewer_session(git_repo, "root", "reviewer") is None
    assert not state.retire_reviewer_session(git_repo,
        "root", "reviewer", expected_reviewer_id="slow-thread"
    )


def test_commit_cursor_clear_is_compare_safe(git_repo, isolated_state):
    first = {"reviewer_id": "slow-thread", "head": "abc", "files": {"a": "one"}}
    replacement = {
        "reviewer_id": "replacement-thread",
        "head": "def",
        "files": {"b": "two"},
    }
    state.write_commit_cursor(git_repo, "root", first)

    assert not state.clear_commit_cursor(
        git_repo, "root", expected_reviewer_id="replacement-thread"
    )
    assert state.read_commit_cursor(git_repo, "root") == first

    state.write_commit_cursor(git_repo, "root", replacement)
    assert not state.clear_commit_cursor(
        git_repo, "root", expected_reviewer_id="slow-thread"
    )
    assert state.read_commit_cursor(git_repo, "root") == replacement

    assert state.clear_commit_cursor(
        git_repo, "root", expected_reviewer_id="replacement-thread"
    )
    assert state.read_commit_cursor(git_repo, "root") is None


def test_commit_prompt_change_rotates_only_the_commit_reviewer_session(
    git_repo, isolated_state, monkeypatch,
):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "old-reviewer")
    state.publish_reviewer_session(git_repo, "root", "stop", "stop-thread")

    monkeypatch.setattr(
        prompts, "COMMIT_PROMPT", prompts.COMMIT_PROMPT + "\nPROMPT REVISION"
    )

    assert state.read_reviewer_session(git_repo, "root", "reviewer") is None
    assert state.read_reviewer_session(git_repo, "root", "stop") == "stop-thread"
    state.publish_reviewer_session(git_repo, "root", "reviewer", "new-reviewer")
    assert state.read_reviewer_session(git_repo, "root", "reviewer") == "new-reviewer"


def test_legacy_unversioned_reviewer_thread_is_not_resumed_after_upgrade(
    git_repo, isolated_state,
):
    token = hashlib.sha256(b"root").hexdigest()
    legacy = isolated_state / "sessions" / (token + ".reviewer.thread")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("legacy-reviewer\n", encoding="utf-8")

    assert state.read_reviewer_session(git_repo, "root", "reviewer") is None


def test_session_end_clears_legacy_and_superseded_prompt_revision_threads(
    git_repo, isolated_state, monkeypatch,
):
    token = hashlib.sha256(b"root").hexdigest()
    legacy = isolated_state / "sessions" / (token + ".reviewer.thread")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("legacy-reviewer\n", encoding="utf-8")
    state.publish_reviewer_session(git_repo, "root", "reviewer", "first-revision")
    monkeypatch.setattr(
        prompts, "COMMIT_PROMPT", prompts.COMMIT_PROMPT + "\nNEXT REVISION"
    )
    state.publish_reviewer_session(git_repo, "root", "reviewer", "second-revision")
    assert len(list((isolated_state / "sessions").glob(token + ".*.thread"))) == 3

    state.clear_session_artifacts("root")

    assert list((isolated_state / "sessions").glob(token + ".*.thread")) == []


def test_nudge_budget_round_trip(git_repo, isolated_state):
    assert state.read_nudges(git_repo, "root") == 0
    assert state.bump_nudges(git_repo, "root") == 1
    assert state.bump_nudges(git_repo, "root") == 2
    state.reset_nudges(git_repo, "root")
    assert state.read_nudges(git_repo, "root") == 0


def test_review_history_retention_is_operator_configurable(
    git_repo, isolated_state, monkeypatch
):
    monkeypatch.setenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", "2")

    for index in range(4):
        state.save_review(
            git_repo,
            "session-{}".format(index),
            "VERDICT {}".format(index),
            history=True,
        )

    assert [
        path.read_text(encoding="utf-8") for path in state.review_history(git_repo)
    ] == ["VERDICT 2", "VERDICT 3"]


def test_session_end_artifacts_can_be_retired(git_repo, isolated_state):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "review-thread")
    state.publish_reviewer_session(git_repo, "root", "stop", "stop-thread")
    state.record_prompt(git_repo, "root", "operator prompt")
    state.record_plan(git_repo,
        "root",
        {"plan": [{"step": "Finish cleanup", "status": "in_progress"}]},
    )
    state.bump_nudges(git_repo, "root")
    state.grant_stop_permit(git_repo, "root")
    state.grant_stop_abort_bypass(git_repo, "root", "turn-1")

    state.clear_session_artifacts("root")

    assert state.read_reviewer_session(git_repo, "root", "reviewer") is None
    assert state.read_reviewer_session(git_repo, "root", "stop") is None
    assert state.recent_prompts(git_repo, "root") == []
    assert state.current_plan(git_repo, "root") is None
    assert state.read_nudges(git_repo, "root") == 0
    assert not state.consume_stop_permit(git_repo, "root")
    assert not state.consume_stop_abort_bypass(git_repo, "root", "turn-1")


def test_feature_flags_follow_the_git_common_directory(git_repo, isolated_state, tmp_path):
    worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "-C", str(git_repo), "worktree", "add", "-q", "-b", "linked", str(worktree)],
        check=True,
    )
    state.set_feature(git_repo, "reviewer")
    assert state.project_key(git_repo) == state.project_key(worktree)
    assert state.feature_enabled(worktree, "reviewer")
