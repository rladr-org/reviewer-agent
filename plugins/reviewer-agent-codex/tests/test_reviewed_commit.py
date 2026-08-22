# SPDX-License-Identifier: MPL-2.0
import subprocess
import threading

from reviewer_codex import commit_tool, state


def _stage(repo, text="change\n"):
    (repo / "change.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "change.txt"], check=True)


def _enable(repo):
    (repo / "reviewerAgent.md").write_text(
        "Run tests and reject correctness bugs.\n", encoding="utf-8"
    )
    state.set_feature(repo, "reviewer")


def _ticket(repo, session_id="root-session"):
    return state.create_commit_ticket(repo, session_id)


def _review_result(monkeypatch, result):
    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        # `cwd` is the repository the tool resolved, which is what the real backend keys the
        # published thread on.
        state.publish_reviewer_session(cwd, session_id, role, "review-" + session_id)
        return result

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)


def test_first_commit_review_combines_brief_and_audit_in_one_turn(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append((cwd, session_id, role, sandbox, prompt, timeout))
        state.publish_reviewer_session(git_repo, session_id, role, "review-thread")
        return 0, "REJECTED\n- retry", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)

    result = commit_tool.reviewed_commit(git_repo, "first", _ticket(git_repo))

    assert result["status"] == "rejected"
    assert len(calls) == 1
    prompt = calls[0][4]
    assert "You are an independent reviewer" in " ".join(prompt.split())
    assert "PROJECT BRIEF:" in prompt
    assert "Run tests and reject correctness bugs." in prompt
    assert "Audit ONLY the staged changes" in prompt


def test_commit_review_uses_the_current_configured_timeout(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    monkeypatch.setenv("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS", "2700")
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append(timeout)
        state.publish_reviewer_session(cwd, session_id, role, "review-thread")
        return 0, "REJECTED\n- retry", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)

    result = commit_tool.reviewed_commit(git_repo, "first", _ticket(git_repo))

    assert result["status"] == "rejected"
    assert calls == [2700]


def test_fresh_reviewer_does_not_claim_an_old_saved_verdict_as_its_own(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    review_path = state.save_review(
        git_repo, "root-session", "REJECTED:\n1. Historical finding"
    )
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append(prompt)
        state.publish_reviewer_session(cwd, session_id, role, "fresh-reviewer")
        return 0, "REJECTED:\n1. Current finding", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "fresh", _ticket(git_repo))

    assert result["status"] == "rejected"
    assert str(review_path) not in calls[0]


def test_approved_review_commits_staged_index(git_repo, isolated_state, monkeypatch):
    _stage(git_repo)
    _enable(git_repo)
    _review_result(monkeypatch, (0, "APPROVED\n", ""))

    result = commit_tool.reviewed_commit(git_repo, "approved commit", _ticket(git_repo))

    assert result["status"] == "committed"
    assert result["review"].startswith("APPROVED")
    subject = subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == "approved commit"


def test_rejected_review_returns_verdict_without_committing(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    before = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _review_result(monkeypatch, (0, "REJECTED\n- fix change.txt:1", ""))

    result = commit_tool.reviewed_commit(git_repo, "rejected commit", _ticket(git_repo))

    assert result["status"] == "rejected"
    assert "fix change.txt:1" in result["review"]
    after = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert after == before


def test_each_completed_review_is_retained_in_immutable_history(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo, "first change\n")
    _enable(git_repo)
    verdicts = iter(
        (
            "REJECTED\n- change.txt:1 first finding",
            "REJECTED\n- change.txt:1 second finding",
        )
    )

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.publish_reviewer_session(git_repo, session_id, role, "review-thread")
        return 0, next(verdicts), ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)

    first = commit_tool.reviewed_commit(git_repo, "first", _ticket(git_repo))
    _stage(git_repo, "second change\n")
    second = commit_tool.reviewed_commit(git_repo, "second", _ticket(git_repo))

    assert first["status"] == second["status"] == "rejected"
    assert state.last_review_file(git_repo, "root-session").read_text(
        encoding="utf-8"
    ) == second["review"]
    history = sorted((isolated_state / "reviews").glob("*.history/*.txt"))
    assert [path.read_text(encoding="utf-8") for path in history] == [
        first["review"],
        second["review"],
    ]


def test_review_failure_is_fail_closed(git_repo, isolated_state, monkeypatch):
    _stage(git_repo)
    _enable(git_repo)
    _review_result(monkeypatch, (1, "", "boom"))

    result = commit_tool.reviewed_commit(git_repo, "failed review", _ticket(git_repo))

    assert result["status"] == "error"
    assert "boom" in result["message"]


def test_timeout_retires_reviewer_and_next_retry_starts_fresh(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    state.publish_reviewer_session(git_repo, "root-session", "reviewer", "slow-thread")
    state.write_commit_cursor(
        git_repo,
        "root-session",
        {
            "reviewer_id": "slow-thread",
            "head": subprocess.run(
                ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "files": {"change.txt": "old-blob"},
        },
    )
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append((state.read_reviewer_session(git_repo, session_id, role), prompt))
        if len(calls) == 1:
            return commit_tool.backend.TIMEOUT_EXIT, "", "reviewer timed out"
        state.publish_reviewer_session(git_repo, session_id, role, "fresh-thread")
        return 0, "REJECTED:\n1. Fresh finding", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)

    timed_out = commit_tool.reviewed_commit(
        git_repo, "slow", _ticket(git_repo)
    )
    assert timed_out["status"] == "error"
    assert "timed out" in timed_out["message"]
    assert "retired" in timed_out["message"]
    assert "retry" in timed_out["message"]
    assert state.read_reviewer_session(git_repo, "root-session", "reviewer") is None
    assert state.read_commit_cursor(git_repo, "root-session") is None

    retried = commit_tool.reviewed_commit(
        git_repo, "fresh retry", _ticket(git_repo)
    )
    assert retried["status"] == "rejected"
    assert calls[0][0] == "slow-thread"
    assert calls[1][0] is None
    assert "PROJECT BRIEF:" in calls[1][1]
    assert "STAGED SET vs YOUR LAST AUDIT (same reviewer session):" not in calls[1][1]
    assert "CHANGED: change.txt" not in calls[1][1]


def test_timeout_does_not_retire_a_concurrent_replacement_reviewer(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    state.publish_reviewer_session(git_repo, "root-session", "reviewer", "slow-thread")

    def replace_during_review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.reviewer_session_file(git_repo, session_id, role).unlink()
        state.publish_reviewer_session(git_repo, session_id, role, "replacement-thread")
        return commit_tool.backend.TIMEOUT_EXIT, "", "reviewer timed out"

    monkeypatch.setattr(commit_tool.backend, "review_turn", replace_during_review)

    result = commit_tool.reviewed_commit(git_repo, "slow", _ticket(git_repo))

    assert result["status"] == "error"
    assert state.read_reviewer_session(git_repo, "root-session", "reviewer") == (
        "replacement-thread"
    )


def test_first_turn_timeout_retires_the_thread_published_during_startup(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def time_out_after_publish(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        assert state.read_reviewer_session(git_repo, session_id, role) is None
        state.publish_reviewer_session(git_repo, session_id, role, "first-slow-thread")
        return commit_tool.backend.TIMEOUT_EXIT, "", "reviewer timed out"

    monkeypatch.setattr(commit_tool.backend, "review_turn", time_out_after_publish)

    result = commit_tool.reviewed_commit(git_repo, "slow", _ticket(git_repo))

    assert result["status"] == "error"
    assert "retired" in result["message"]
    assert state.read_reviewer_session(git_repo, "root-session", "reviewer") is None


def test_fresh_reviewer_startup_failure_preserves_backend_error(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    monkeypatch.setattr(
        commit_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (127, "", "codex executable was not found"),
    )

    result = commit_tool.reviewed_commit(git_repo, "failed start", _ticket(git_repo))

    assert result["status"] == "error"
    assert "codex executable was not found" in result["message"]


def test_stage_change_during_review_invalidates_approval(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def mutate(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.publish_reviewer_session(git_repo, session_id, role, "review-" + session_id)
        _stage(git_repo, "changed again\n")
        return 0, "APPROVED\n", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", mutate)

    result = commit_tool.reviewed_commit(git_repo, "stale approval", _ticket(git_repo))

    assert result["status"] == "error"
    assert "changed while" in result["message"]


def test_disabled_reviewer_tool_is_inert(git_repo, isolated_state):
    _stage(git_repo)
    result = commit_tool.reviewed_commit(git_repo, "not enabled", _ticket(git_repo))
    assert result["status"] == "disabled"


def test_review_requires_one_use_token_from_shell_attempt(git_repo, isolated_state):
    _stage(git_repo)
    _enable(git_repo)

    missing = commit_tool.reviewed_commit(git_repo, "missing token", "")
    invalid = commit_tool.reviewed_commit(git_repo, "invalid token", "deadbeef")

    assert missing["status"] == "error"
    assert "shell git commit" in missing["message"]
    assert invalid["status"] == "error"


def test_token_preserves_developer_session_identity(git_repo, isolated_state, monkeypatch):
    _stage(git_repo)
    _enable(git_repo)
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append(
            (session_id, state.read_reviewer_session(git_repo, session_id, role), prompt)
        )
        state.publish_reviewer_session(git_repo, session_id, role, "review-" + session_id)
        return 0, "REJECTED\n- retry", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)

    commit_tool.reviewed_commit(git_repo, "one", _ticket(git_repo, "developer-one"))
    commit_tool.reviewed_commit(git_repo, "two", _ticket(git_repo, "developer-one"))
    commit_tool.reviewed_commit(git_repo, "three", _ticket(git_repo, "developer-two"))

    assert [call[0] for call in calls] == [
        "developer-one",
        "developer-one",
        "developer-two",
    ]
    assert [call[1] for call in calls] == [None, "review-developer-one", None]
    assert "PROJECT BRIEF:" in calls[0][2]
    assert "PROJECT BRIEF:" not in calls[1][2]
    assert "PROJECT BRIEF:" in calls[2][2]


def test_repeat_review_gets_exact_staged_delta_and_path_classes(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo, "first\n")
    _enable(git_repo)
    prompts = []

    def reject(cwd, session_id, role, sandbox, prompt, timeout):
        state.publish_reviewer_session(git_repo, session_id, role, "review-thread")
        prompts.append(prompt)
        return 0, "REJECTED\n- fix change.txt", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", reject)
    commit_tool.reviewed_commit(git_repo, "first", _ticket(git_repo))

    (git_repo / "change.txt").write_text("second\n", encoding="utf-8")
    (git_repo / "added.txt").write_text("added\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "change.txt", "added.txt"], check=True
    )
    commit_tool.reviewed_commit(git_repo, "second", _ticket(git_repo))

    assert "STAGED SET vs YOUR LAST AUDIT" in prompts[1]
    assert "CHANGED: change.txt" in prompts[1]
    assert "-first" in prompts[1]
    assert "+second" in prompts[1]
    assert "ADDED: added.txt" in prompts[1]
    assert "explicit disposition" in prompts[1].lower()
    assert "SUGGESTIONS:" in prompts[1]


def test_unchanged_retry_tells_same_reviewer_not_to_redo_audit(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    prompts = []

    def reject(cwd, session_id, role, sandbox, prompt, timeout):
        state.publish_reviewer_session(git_repo, session_id, role, "review-thread")
        prompts.append(prompt)
        return 0, "REJECTED\n- still broken", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", reject)
    commit_tool.reviewed_commit(git_repo, "first", _ticket(git_repo))
    commit_tool.reviewed_commit(git_repo, "retry", _ticket(git_repo))

    assert "NOTHING MOVED" in prompts[1]
    assert "do not redo the audit" in prompts[1].lower()


def test_approval_token_may_be_followed_by_punctuation():
    assert commit_tool.approved("APPROVED: clean staged set")
    assert commit_tool.approved("APPROVED — tests passed")


def test_approval_suggestions_may_discuss_rejected_alternatives():
    assert commit_tool.approved(
        "APPROVED\nSUGGESTIONS:\n"
        "1. Document why the rejected alternative was not selected."
    )


def test_suggestions_split_is_fail_closed_on_ambiguous_markers():
    verdict = "REJECTED:\n1. Fix the bug\nSUGGESTIONS:\n1. Rename the helper"
    assert commit_tool.split_suggestions(verdict) == (
        "REJECTED:\n1. Fix the bug",
        "SUGGESTIONS:\n1. Rename the helper",
    )

    repeated = verdict + "\nSUGGESTIONS:\n2. Add a comment"
    assert commit_tool.split_suggestions(repeated) == (repeated, None)

    empty = "REJECTED:\n1. Fix the bug\nSUGGESTIONS:"
    assert commit_tool.split_suggestions(empty) == (empty, None)


def test_approval_rejects_malformed_suggestions_sections():
    assert not commit_tool.approved("APPROVED\nSUGGESTIONS:")
    assert not commit_tool.approved(
        "APPROVED\nSUGGESTIONS:\n1. First\nSUGGESTIONS:\n2. Second"
    )


def test_self_contradictory_approval_is_rejected():
    assert not commit_tool.approved("APPROVED but also REJECTED due to a race")
    assert not commit_tool.approved("APPROVED\nREJECTED\n- late finding")


def test_approval_requires_a_whole_word():
    assert not commit_tool.approved("APPROVEDX")


def test_commit_cancellation_is_forwarded_and_never_commits(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    cancelled = threading.Event()

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        assert kwargs["cancel_event"] is cancelled
        return commit_tool.backend.CANCELLED_EXIT, "", "reviewer cancelled"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(
        git_repo, "cancelled", _ticket(git_repo), cancel_event=cancelled
    )

    assert result["status"] == "cancelled"
    subject = subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == "initial"


def test_cancellation_wins_immediately_before_commit_side_effect(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    _review_result(monkeypatch, (0, "APPROVED\n", ""))

    class CancelAtCommit:
        def is_set(self):
            return False

        def complete(self, action=None):
            return False, None

    result = commit_tool.reviewed_commit(
        git_repo, "must not land", _ticket(git_repo), cancel_event=CancelAtCommit()
    )

    assert result["status"] == "cancelled"
    assert subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "initial"
