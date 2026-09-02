# SPDX-License-Identifier: MPL-2.0
import threading

from reviewer_codex import liveness, state, stop_tool
from reviewer_codex.cancellation import Cancellation


def _ticket(
    repo,
    final="I am done.",
    session_id="root-session",
    turn_id="turn-1",
    shell_markers=None,
    background_markers=None,
):
    return state.create_stop_ticket(
        repo,
        session_id,
        final,
        turn_id=turn_id,
        shell_markers=shell_markers,
        background_markers=background_markers,
    )


def test_stop_ok_grants_one_use_permit(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, "STOP_OK\n", "")
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "stop_ok"
    assert state.consume_stop_permit(git_repo, "root-session")
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_incomplete_stop_returns_directive_without_permit(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "Run the targeted tests.", ""),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert "targeted tests" in result["directive"]
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_clears_only_the_persistently_challenged_shell_markers(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    liveness.mark(git_repo, "root-session", "shell", "stale-1", "old validator")
    liveness.challenge_shells(git_repo, "root-session", [marker])
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, "STOP_OK\n", "")
    )

    result = stop_tool.review_stop(
        git_repo,
        _ticket(git_repo, shell_markers=[marker]),
    )

    assert result["status"] == "stop_ok"
    assert liveness.outstanding(git_repo, "root-session") == []
    assert not liveness.shells_were_challenged(git_repo, "root-session", [marker])
    assert state.consume_stop_permit(git_repo, "root-session")


def test_incomplete_stop_keeps_persistent_shell_markers(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    liveness.mark(git_repo, "root-session", "shell", "stale-1", "old validator")
    liveness.challenge_shells(git_repo, "root-session", [marker])
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "Inspect the active session.", ""),
    )

    result = stop_tool.review_stop(
        git_repo,
        _ticket(git_repo, shell_markers=[marker]),
    )

    assert result["status"] == "incomplete"
    assert [item["id"] for item in liveness.outstanding(git_repo, "root-session")] == ["stale-1"]
    assert liveness.shells_were_challenged(git_repo, "root-session", [marker])
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_clears_exact_mixed_background_markers(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    markers = [
        {"kind": "shell", "id": "stale-shell", "label": "old validator"},
        {"kind": "subagent", "id": "stale-agent", "label": "default"},
    ]
    for marker in markers:
        liveness.mark(git_repo,
            "root-session", marker["kind"], marker["id"], marker["label"]
        )
    liveness.challenge_items(git_repo, "root-session", markers)
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, "STOP_OK\n", "")
    )

    result = stop_tool.review_stop(
        git_repo,
        _ticket(git_repo, background_markers=markers),
    )

    assert result["status"] == "stop_ok"
    assert liveness.outstanding(git_repo, "root-session") == []
    assert not liveness.items_were_challenged(git_repo, "root-session", markers)
    assert state.consume_stop_permit(git_repo, "root-session")


def test_incomplete_stop_keeps_mixed_background_markers(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    markers = [
        {"kind": "shell", "id": "stale-shell", "label": "old validator"},
        {"kind": "subagent", "id": "stale-agent", "label": "default"},
    ]
    for marker in markers:
        liveness.mark(git_repo,
            "root-session", marker["kind"], marker["id"], marker["label"]
        )
    liveness.challenge_items(git_repo, "root-session", markers)
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "A live subagent still needs to finish.", ""),
    )

    result = stop_tool.review_stop(
        git_repo,
        _ticket(git_repo, background_markers=markers),
    )

    assert result["status"] == "incomplete"
    assert {(item["kind"], item["id"]) for item in liveness.outstanding(git_repo, "root-session")} == {
        ("shell", "stale-shell"),
        ("subagent", "stale-agent"),
    }
    assert liveness.items_were_challenged(git_repo, "root-session", markers)
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_persistent_shell_context_is_sent_to_the_minimal_stop_reviewer(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    prompts = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, cancel_event=None):
        prompts.append(prompt)
        return 0, "Inspect it first.", ""

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    stop_tool.review_stop(git_repo, _ticket(git_repo, shell_markers=[marker]))

    assert "old validator" in prompts[0]
    assert "no process or unified-exec session remains" in prompts[0]
    assert "git diff" not in prompts[0]


def test_stop_uses_one_minimal_turn_without_project_brief(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    state.record_prompt(git_repo, "root-session", "Implement the requested redesign.")
    (git_repo / "reviewerAgent.md").write_text("SECRET COMMIT POLICY\n", encoding="utf-8")
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, cancel_event=None):
        calls.append((cwd, session_id, role, sandbox, prompt, timeout, cancel_event))
        return 0, "STOP_OK\n", ""

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert len(calls) == 1
    assert calls[0][2:4] == ("stop", "read-only")
    assert "Implement the requested redesign." in calls[0][4]
    assert "I am done." in calls[0][4]
    assert "SECRET COMMIT POLICY" not in calls[0][4]


def test_stop_cancellation_is_forwarded_to_reviewer(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    cancelled = threading.Event()
    cancelled.set()

    def review(*args, **kwargs):
        assert kwargs["cancel_event"] is cancelled
        return 130, "", "reviewer cancelled"

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    result = stop_tool.review_stop(git_repo, _ticket(git_repo), cancelled)

    assert result["status"] == "cancelled"
    assert not state.consume_stop_permit(git_repo, "root-session")
    assert state.consume_stop_abort_bypass(git_repo, "root-session", "turn-1")
    assert not state.consume_stop_abort_bypass(git_repo, "root-session", "turn-1")


def test_abort_bypass_is_written_before_cancel_notification_returns(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    cancellation = Cancellation()
    reviewer_started = threading.Event()
    release_reviewer = threading.Event()

    def review(*args, **kwargs):
        reviewer_started.set()
        assert release_reviewer.wait(1)
        return 130, "", "reviewer cancelled"

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            stop_tool.review_stop(git_repo, _ticket(git_repo), cancellation)
        )
    )
    worker.start()
    assert reviewer_started.wait(1)

    try:
        assert cancellation.cancel()
        assert state.consume_stop_abort_bypass(git_repo, "root-session", "turn-1")
    finally:
        release_reviewer.set()
        worker.join(1)

    assert results[0]["status"] == "cancelled"


def test_stop_failure_remains_fail_open(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (1, "", "failed")
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "failed_open"
    assert state.consume_stop_permit(git_repo, "root-session")


def test_stop_failure_clears_adjudicated_persistent_markers_when_failing_open(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    liveness.mark(git_repo, "root-session", "shell", "stale-1", "old validator")
    liveness.challenge_shells(git_repo, "root-session", [marker])
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (1, "", "failed")
    )

    result = stop_tool.review_stop(
        git_repo, _ticket(git_repo, shell_markers=[marker])
    )

    assert result["status"] == "failed_open"
    assert liveness.outstanding(git_repo, "root-session") == []
    assert state.consume_stop_permit(git_repo, "root-session")


def test_marker_cleanup_error_cannot_defeat_stop_failure_fail_open(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (1, "", "failed")
    )

    def cleanup_fails(*args, **kwargs):
        raise OSError("state directory became unavailable")

    monkeypatch.setattr(stop_tool.liveness, "clear_items", cleanup_fails)

    result = stop_tool.review_stop(
        git_repo, _ticket(git_repo, shell_markers=[marker])
    )

    assert result["status"] == "failed_open"
    assert state.consume_stop_permit(git_repo, "root-session")


def test_new_work_during_persistent_marker_review_prevents_stop_permit(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    marker = {"kind": "shell", "id": "stale-1", "label": "old validator"}
    liveness.mark(git_repo, "root-session", "shell", "stale-1", "old validator")
    liveness.challenge_shells(git_repo, "root-session", [marker])

    def review(*args, **kwargs):
        liveness.mark(git_repo, "root-session", "shell", "new-1", "new command")
        return 0, "STOP_OK\n", ""

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    result = stop_tool.review_stop(
        git_repo, _ticket(git_repo, shell_markers=[marker])
    )

    assert result["status"] == "incomplete"
    assert [item["id"] for item in liveness.outstanding(git_repo, "root-session")] == ["new-1"]
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_new_subagent_during_mixed_marker_review_prevents_stop_permit(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    markers = [
        {"kind": "shell", "id": "stale-shell", "label": "old validator"},
        {"kind": "subagent", "id": "stale-agent", "label": "old worker"},
    ]
    for marker in markers:
        liveness.mark(git_repo,
            "root-session", marker["kind"], marker["id"], marker["label"]
        )
    liveness.challenge_items(git_repo, "root-session", markers)

    def review(*args, **kwargs):
        liveness.mark(git_repo, "root-session", "subagent", "new-agent", "new worker")
        return 0, "STOP_OK\n", ""

    monkeypatch.setattr(stop_tool.backend, "review_turn", review)
    result = stop_tool.review_stop(
        git_repo, _ticket(git_repo, background_markers=markers)
    )

    assert result["status"] == "incomplete"
    assert [(item["kind"], item["id"]) for item in liveness.outstanding(git_repo, "root-session")] == [
        ("subagent", "new-agent")
    ]
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_requires_exact_whole_token(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, "STOP_OKAY\n", "")
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_exception_fails_open(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")

    def explode(*args, **kwargs):
        raise RuntimeError("reviewer crashed")

    monkeypatch.setattr(stop_tool.backend, "review_turn", explode)
    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "failed_open"
    assert "crashed" in result["detail"]
    assert state.consume_stop_permit(git_repo, "root-session")


def test_stop_permit_is_repository_scoped(
    git_repo, tmp_path, isolated_state, monkeypatch
):
    other = tmp_path / "other"
    other.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(other)], check=True)
    state.grant_stop_permit(git_repo, "root-session")

    assert not state.consume_stop_permit(other, "root-session")
    assert state.consume_stop_permit(git_repo, "root-session")


def test_cancellation_wins_immediately_before_stop_permit(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, "STOP_OK\n", "")
    )

    class CancelAtPermit:
        def is_set(self):
            return False

        def complete(self, action=None):
            return False, None

    result = stop_tool.review_stop(
        git_repo, _ticket(git_repo), cancel_event=CancelAtPermit()
    )

    assert result["status"] == "cancelled"
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_incomplete_ruling_retires_abort_callback(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    token = Cancellation()
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "Keep working.", ""),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo), token)

    assert result["status"] == "incomplete"
    assert not token.cancel()
    assert not state.consume_stop_abort_bypass(
        git_repo, "root-session", "turn-1"
    )


# Trailing STOP_OK parity with the Claude Code hook (docs/plans/stopcontinue_trailing_stop_ok.md):
# the sentinel counts on the first OR the last non-empty line; a mid-reply sentinel followed by
# more directive is contradictory and resolves to incomplete.
def test_stop_ok_on_the_last_line_after_reasoning_grants_permit(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    ruling = "There is no authorized work left incomplete.\n\nSTOP_OK\n"
    monkeypatch.setattr(
        stop_tool.backend, "review_turn", lambda *args, **kwargs: (0, ruling, "")
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "stop_ok"
    assert state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_trailing_line_tolerates_emphasis_only(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "Nothing remains.\n\n**STOP_OK**\n", ""),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "stop_ok"


def test_stop_ok_first_line_stays_exact_token(git_repo, isolated_state, monkeypatch):
    # The trailing-line tolerance must not widen the first-line rule: `STOP_OK.` followed by a
    # directive is not a permit.
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, "STOP_OK.\n\nRun the targeted tests first.\n", ""),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_trailing_line_rejects_quote_wrappers(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (0, 'Nothing remains.\n\n"STOP_OK"\n', ""),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_mentioned_in_a_trailing_sentence_is_incomplete(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (
            0, "Fix the failing test.\n\nSTOP_OK is not warranted until it passes.\n", ""
        ),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert not state.consume_stop_permit(git_repo, "root-session")


def test_stop_ok_mid_reply_is_contradictory_and_incomplete(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(
        stop_tool.backend,
        "review_turn",
        lambda *args, **kwargs: (
            0, "The list is complete.\n\nSTOP_OK\n\nHowever, run the linter first.\n", ""
        ),
    )

    result = stop_tool.review_stop(git_repo, _ticket(git_repo))

    assert result["status"] == "incomplete"
    assert not state.consume_stop_permit(git_repo, "root-session")
