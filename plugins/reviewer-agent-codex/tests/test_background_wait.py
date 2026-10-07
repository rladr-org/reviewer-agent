# SPDX-License-Identifier: MPL-2.0
"""A background wait ends the turn without declaring tracked work complete."""
import pytest

from reviewer_codex import backend, liveness, prompts, state, stop_tool, supervisor
from reviewer_codex.cancellation import Cancellation

SESSION = "background-wait"
FINAL = ("The monitor will wake me at 4:53 PM Pacific and verify a new turn starts. "
         "Downloads and processing continue; I am yielding until that wake-up.")
MARKERS = [
    {"kind": "shell", "id": "food52-1", "label": "Food52 download"},
    {"kind": "shell", "id": "food52-2", "label": "Food52 processing"},
    {"kind": "subagent", "id": "monitor", "label": "scheduled wake-up monitor"},
]


def event(repo):
    return dict(cwd=str(repo), session_id=SESSION, turn_id="turn-1",
                hook_event_name="Stop", last_assistant_message=FINAL)


@pytest.fixture
def waiting(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    for item in MARKERS:
        liveness.mark(git_repo, SESSION, item["kind"], item["id"], item["label"])
    state.record_prompt(git_repo, SESSION,
                        "Let the downloads continue and test the scheduled wake-up.")
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "STOP_WAIT", ""))
    return liveness.outstanding(git_repo, SESSION)


def test_native_wait_allows_stop_and_preserves_all_markers(git_repo, waiting):
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert liveness.outstanding(git_repo, SESSION) == waiting
    assert state.consume_stop_permit(git_repo, SESSION) is None
    assert state.read_nudges(git_repo, SESSION) == 1
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert state.read_nudges(git_repo, SESSION) == 2


def test_wait_has_distinct_single_use_permit_for_legacy_shell_ticket(git_repo, waiting):
    token = state.create_stop_ticket(git_repo, SESSION, FINAL,
                                     shell_markers=MARKERS[:2])
    liveness.clear_items(git_repo, SESSION, [MARKERS[2]])
    result = stop_tool.review_stop(git_repo, token)
    assert result["status"] == "stop_wait"
    assert state.consume_stop_permit(git_repo, SESSION) == "stop_wait"
    assert state.consume_stop_permit(git_repo, SESSION) is None
    assert len(liveness.outstanding(git_repo, SESSION)) == 2


def test_marker_free_scheduled_wait_is_not_failed_open(git_repo, waiting):
    liveness.clear_items(git_repo, SESSION, waiting)
    assert supervisor.supervise_hook(event(git_repo)) == {}


@pytest.mark.parametrize("work", [False, True], ids=["operator", "tool"])
def test_wait_cannot_hide_new_input_or_work(git_repo, waiting, monkeypatch, work):
    def ruling(*args, **kwargs):
        state.invalidate_stop_permits(git_repo, SESSION, work=work)
        return 0, "STOP_WAIT", ""
    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    if work:
        assert result["decision"] == "block"
        assert "changed" in result["reason"]
    else:
        # A new operator message supersedes the old wait ruling without a continuation.
        assert result == {}
    assert liveness.outstanding(git_repo, SESSION) == waiting


def test_wait_rejects_new_marker_even_without_generation_event(git_repo, waiting, monkeypatch):
    def ruling(*args, **kwargs):
        liveness.mark(git_repo, SESSION, "shell", "new", "new foreground command")
        return 0, "STOP_WAIT", ""
    monkeypatch.setattr(backend, "review_turn", ruling)
    result = supervisor.supervise_hook(event(git_repo))
    assert result["decision"] == "block"
    assert "newly started background work" in result["reason"]
    assert len(liveness.outstanding(git_repo, SESSION)) == 4


def test_fresh_reassessment_can_allow_wait_without_clearing_work(git_repo, waiting, monkeypatch):
    generations = state.stop_loop_generations(git_repo, SESSION)
    state.record_stop_loop(git_repo, SESSION, generations, 2)
    calls = []
    def fresh(*args, **kwargs):
        calls.append(args[2])
        return 0, "STOP_WAIT", ""
    monkeypatch.setattr(backend, "review_once", fresh)
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert len(calls) == 1
    assert "STOP_WAIT" in calls[0]
    assert liveness.outstanding(git_repo, SESSION) == waiting
    assert state.read_stop_loop(git_repo, SESSION, generations) == 0


def test_cancelled_wait_does_not_grant_permission_or_clear_work(git_repo, waiting, monkeypatch):
    cancel = Cancellation()
    def ruling(*args, **kwargs):
        cancel.set()
        return 0, "STOP_WAIT", ""
    monkeypatch.setattr(backend, "review_turn", ruling)
    assert supervisor.supervise_hook(event(git_repo), cancel_event=cancel) == {}
    assert state.consume_stop_permit(git_repo, SESSION) is None
    assert liveness.outstanding(git_repo, SESSION) == waiting


@pytest.mark.parametrize("ruling", ["STOP_WAITING", "Then reply STOP_WAIT", "STOP_WAIT\nRun tests."])
def test_mentions_or_contradictory_wait_do_not_allow_stop(git_repo, waiting, monkeypatch, ruling):
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, ruling, ""))
    assert supervisor.supervise_hook(event(git_repo))["decision"] == "block"
    assert liveness.outstanding(git_repo, SESSION) == waiting


def test_genuine_completion_still_reconciles_stale_markers(git_repo, waiting, monkeypatch):
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "STOP_OK", ""))
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert liveness.outstanding(git_repo, SESSION) == []


@pytest.mark.parametrize("context", [{}, {"shell_markers": MARKERS[:2]},
                                     {"background_markers": MARKERS}])
def test_wait_contract_overrides_completion_only_rule_on_all_routes(git_repo, waiting, context):
    prompt = prompts.stop_prompt(git_repo, SESSION, FINAL, **context)
    norm = " ".join(prompt.split())
    assert "STOP_WAIT" in norm
    assert "resumption mechanism" in norm
    assert "no other authorized work can proceed now" in norm
    assert "Do not require background work to finish" in norm
    assert "Do not require verification of a future wake-up before it occurs" in norm
    assert "Return STOP_OK, STOP_WAIT, or the next-step directive now." in norm
