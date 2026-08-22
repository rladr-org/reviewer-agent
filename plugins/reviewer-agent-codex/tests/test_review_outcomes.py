# SPDX-License-Identifier: MPL-2.0
"""Terminal outcomes are retained, latest rulings are not forged, and history can be purged.

Same two rules as the Claude package: a timeout, a cancellation and an operational failure are
real events in a repository's review record and must survive, while none of them may overwrite
the saved latest ruling -- that file is what the next reviewer is shown as its own previous
verdict.

The purge is separate from uninstalling: retained verdicts quote the staged diffs they judged,
so a project accumulates plaintext copies of its own code under review.
"""
import subprocess

from reviewer_codex import commit_tool, controls, state


def _enable(repo):
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    state.set_feature(repo, "reviewer")


def _stage(repo):
    (repo / "change.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "change.txt"], check=True)


def _ticket(repo, session_id="root-session"):
    return state.create_commit_ticket(repo, session_id)


def _history_texts(repo):
    return [path.read_text(encoding="utf-8") for path in state.review_history(repo)]


def test_a_timeout_is_recorded_and_leaves_the_last_ruling_alone(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)
    state.save_review(git_repo, "root-session", "APPROVED\nearlier good verdict")
    state.publish_reviewer_session(git_repo, "root-session", "reviewer", "thread-1")

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return commit_tool.backend.TIMEOUT_EXIT, "", "reviewer timed out"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "timed out", _ticket(git_repo))

    assert result["status"] == "error"
    texts = _history_texts(git_repo)
    assert any("OUTCOME: TIMEOUT" in text for text in texts), texts
    assert "earlier good verdict" in state.last_review_file(
        git_repo, "root-session"
    ).read_text(encoding="utf-8")


def test_a_failed_review_is_recorded(git_repo, isolated_state, monkeypatch):
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return 3, "", "boom"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "failed", _ticket(git_repo))

    assert result["status"] == "error"
    texts = _history_texts(git_repo)
    assert any("OUTCOME: FAILED" in text for text in texts), texts
    assert any("boom" in text for text in texts), texts


def test_a_cancelled_review_is_recorded(git_repo, isolated_state, monkeypatch):
    _stage(git_repo)
    _enable(git_repo)

    class _Cancelled:
        def is_set(self):
            return True

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return commit_tool.backend.CANCELLED_EXIT, "", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(
        git_repo, "cancelled", _ticket(git_repo), cancel_event=_Cancelled()
    )

    assert result["status"] == "cancelled"
    texts = _history_texts(git_repo)
    assert any("OUTCOME: CANCELLED" in text for text in texts), texts


def test_an_approved_review_records_the_verdict_not_an_outcome(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.publish_reviewer_session(cwd, session_id, role, "thread-1")
        return 0, "APPROVED\n", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "approved", _ticket(git_repo))

    assert result["status"] == "committed"
    texts = _history_texts(git_repo)
    assert any("APPROVED" in text for text in texts), texts
    assert not any("OUTCOME:" in text for text in texts), texts


def test_history_status_reports_what_is_retained(git_repo, isolated_state):
    state.save_review(git_repo, "root-session", "one", history=True)
    state.save_review(git_repo, "root-session", "two", history=True)

    code, message = controls.apply_history_action(git_repo, "status")

    assert code == 0
    assert "2" in message
    assert str(state.project_root(git_repo)) in message


def test_history_clear_purges_only_this_project(git_repo, tmp_path, isolated_state):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    state.save_review(git_repo, "root-session", "purge me", history=True)
    state.save_review(other, "root-session", "keep me", history=True)

    code, message = controls.apply_history_action(git_repo, "clear")

    assert code == 0, message
    assert state.review_history(git_repo) == []
    assert not state.last_review_file(git_repo, "root-session").exists()
    assert len(state.review_history(other)) == 1
    assert state.last_review_file(other, "root-session").exists()


def test_history_clear_reports_when_there_is_nothing_to_purge(git_repo, isolated_state):
    code, message = controls.apply_history_action(git_repo, "clear")

    assert code == 0
    assert "nothing" in message.lower()


def test_history_rejects_an_unknown_action(git_repo, isolated_state):
    state.save_review(git_repo, "root-session", "one", history=True)

    code, message = controls.apply_history_action(git_repo, "nuke")

    assert code == 1
    assert "ERROR" in message
    assert len(state.review_history(git_repo)) == 1

def test_a_verdict_discarded_by_a_changed_stage_is_kept_not_called_missing(
    git_repo, isolated_state, monkeypatch
):
    # The review RAN and ruled; the staged set moved underneath it, so the ruling judged a
    # different set and cannot be applied. Recording that as "no verdict was produced" would be
    # false, and throwing the text away would lose the only copy of what the reviewer said.
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        state.publish_reviewer_session(cwd, session_id, role, "thread-1")
        (git_repo / "late.txt").write_text("staged mid-review\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(git_repo), "add", "late.txt"], check=True)
        return 0, "REJECTED:\n1. fix change.txt:1", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "raced", _ticket(git_repo))

    assert result["status"] == "error"
    assert "staged set changed" in result["message"]
    texts = _history_texts(git_repo)
    assert any("OUTCOME: DISCARDED" in text for text in texts), texts
    assert any("fix change.txt:1" in text for text in texts), texts
    assert not any("No verdict was produced" in text for text in texts), texts


def test_a_verdict_with_no_reviewer_thread_is_kept_as_discarded(
    git_repo, isolated_state, monkeypatch
):
    # Same shape, different cause: a ruling came back but nothing published a thread to
    # attribute it to, so it cannot be applied or resumed from.
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return 0, "APPROVED\n", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "unattributed", _ticket(git_repo))

    assert result["status"] == "error"
    assert "no reviewer thread" in result["message"]
    texts = _history_texts(git_repo)
    assert any("OUTCOME: DISCARDED" in text for text in texts), texts
    assert any("APPROVED" in text for text in texts), texts


def test_history_status_names_the_directory_even_when_empty(git_repo, isolated_state):
    # The skill promises status reports where entries live; an operator checking an empty
    # project needs the path most, since there is nothing else to go on.
    code, message = controls.apply_history_action(git_repo, "status")

    assert code == 0
    assert "nothing retained" in message
    assert str(state.review_history_dir(git_repo)) in message
    assert str(state.review_history_keep()) in message


def test_a_failed_review_during_a_stage_change_is_a_failure_not_a_discard(
    git_repo, isolated_state, monkeypatch
):
    # Both conditions at once, which is the ordering trap: the reviewer failed AND the staged
    # set moved. There is no ruling to discard, so recording DISCARDED would claim a verdict
    # that never existed -- and would hide the reviewer error the operator has to act on.
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        (git_repo / "late.txt").write_text("staged mid-review\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(git_repo), "add", "late.txt"], check=True)
        return 1, "", "boom"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "failed and raced", _ticket(git_repo))

    assert result["status"] == "error"
    assert "boom" in result["message"]
    texts = _history_texts(git_repo)
    assert any("OUTCOME: FAILED" in text for text in texts), texts
    assert not any("OUTCOME: DISCARDED" in text for text in texts), texts


def test_an_empty_review_during_a_stage_change_is_a_failure_not_a_discard(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        (git_repo / "late.txt").write_text("staged mid-review\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(git_repo), "add", "late.txt"], check=True)
        return 0, "   \n", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "empty and raced", _ticket(git_repo))

    assert result["status"] == "error"
    texts = _history_texts(git_repo)
    assert any("OUTCOME: FAILED" in text for text in texts), texts
    assert not any("OUTCOME: DISCARDED" in text for text in texts), texts


def test_a_refused_model_is_diagnosed_rather_than_reported_as_a_bare_exit(
    git_repo, isolated_state, monkeypatch
):
    # The gate is fail-closed, so an unreachable model blocks every commit in the repository.
    # Reporting "review failed (exit 1)" leaves the operator with no indication that the remedy
    # is a settings change rather than a retry.
    _stage(git_repo)
    _enable(git_repo)
    monkeypatch.setenv("REVIEWER_CODEX_REVIEWER_MODEL", "gpt-5.6-luna")

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return 1, "", "The model `gpt-5.6-luna` does not exist or you do not have access to it."

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "refused", _ticket(git_repo))

    assert result["status"] == "error"
    assert "reviewer-model" in result["message"]
    assert "gpt-5.6-luna" in result["message"]
    texts = _history_texts(git_repo)
    assert any("reviewer-model" in text for text in texts), texts


def test_an_ordinary_failure_is_not_diagnosed_as_a_model_problem(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return 1, "", "connection reset by peer"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "flaky", _ticket(git_repo))

    assert result["status"] == "error"
    assert "connection reset" in result["message"]
    assert "reviewer-model" not in result["message"]


def test_an_access_error_about_something_other_than_a_model_is_not_diagnosed(
    git_repo, isolated_state, monkeypatch
):
    _stage(git_repo)
    _enable(git_repo)

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        return 1, "", "403 Forbidden: your account does not have access to this organization"

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    result = commit_tool.reviewed_commit(git_repo, "org problem", _ticket(git_repo))

    assert result["status"] == "error"
    assert "organization" in result["message"]
    assert "reviewer-model" not in result["message"]


def test_the_real_provider_refusal_text_is_recognised():
    """Verbatim from codex-cli 0.147.0, and its benign lookalike alongside it.

    Captured from a live run during the release matrix: none of the original patterns matched
    the actual 400, so a repository with an unusable model was blocked with a bare exit code.
    The service-tier warning is included because it fires for perfectly usable models, and a
    diagnostic that cried wolf there would be worse than none.
    """
    refusal = (
        'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'gpt-5.6-luna\' model is not supported when using Codex with a '
        'ChatGPT account."}}'
    )
    benign = (
        "warning: Configured service tier `priority` is not advertised as supported for model "
        "`gpt-5.6-luna` and will be omitted from requests."
    )

    assert state.model_rejection_detail(refusal)
    assert state.model_rejection_detail(benign) is None
