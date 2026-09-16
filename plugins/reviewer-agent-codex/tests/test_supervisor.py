# SPDX-License-Identifier: MPL-2.0
"""Stop behavior through the host handler, replacing legacy routing assertions."""
import pytest
from reviewer_codex import backend, cli, liveness, state, supervisor


def _payload(repo, active=False):
    return dict(session_id="root-session", turn_id="turn-1", cwd=str(repo),
                hook_event_name="Stop", stop_hook_active=active,
                last_assistant_message="I am done.")


@pytest.fixture
def reviews(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    seen = []
    def review(*args, **kwargs):
        seen.append(args[4])
        return 0, "Inspect the outstanding work and finish the task.", ""
    monkeypatch.setattr(backend, "review_turn", review)
    return seen


def test_disabled_stop_supervisor_is_inert(git_repo, isolated_state):
    assert supervisor.supervise_hook(_payload(git_repo)) == {}


def test_cli_cannot_record_operator_risk_acceptance(
    git_repo, isolated_state, monkeypatch, capsys
):
    monkeypatch.chdir(git_repo)
    assert cli.main(["stop", "on"]) != 0
    output = capsys.readouterr()
    assert "acceptRisk" in output.err
    assert not state.feature_enabled(git_repo, "stop")

    assert cli.main(["stop", "on", "acceptRisk"]) == 2
    assert not state.feature_enabled(git_repo, "stop")



@pytest.mark.parametrize("kinds", [("shell",), ("subagent",), ("shell", "subagent")])
def test_stop_reviews_marker_set_without_a_model_handoff(git_repo, reviews, kinds):
    for kind in kinds:
        liveness.mark(git_repo, "root-session", kind, kind + "-1", kind + " work")
    output = supervisor.supervise_hook(_payload(git_repo))
    assert output["decision"] == "block"
    assert len(reviews) == 1
    for kind in kinds:
        assert "- " + kind + ": " + kind + " work" in reviews[0]
    assert "review_token" not in output["reason"]
    assert "stop_continue" not in output["reason"]
    assert "write_stdin" not in output["reason"]
    assert "wait_agent" not in output["reason"]


@pytest.mark.parametrize("kind", ["shell", "subagent"])
def test_new_markers_reach_the_next_stop_review(git_repo, reviews, kind):
    liveness.mark(git_repo, "root-session", kind, "first", "first work")
    supervisor.supervise_hook(_payload(git_repo))
    liveness.mark(git_repo, "root-session", kind, "second", "new work")
    supervisor.supervise_hook(_payload(git_repo, active=True))
    assert len(reviews) == 2
    assert "first work" in reviews[0] and "new work" not in reviews[0]
    assert "first work" in reviews[1] and "new work" in reviews[1]


def test_stop_rechecks_shell_markers_before_declaring_work_active(git_repo, reviews, monkeypatch):
    shell = {"kind": "shell", "id": "tool-1", "label": "completed-test"}
    readings = iter([[shell], []])
    sleeps = []
    monkeypatch.setattr(liveness, "outstanding", lambda *_: next(readings))
    monkeypatch.setattr(supervisor, "_sleep", sleeps.append)
    output = supervisor.supervise_hook(_payload(git_repo))
    assert sleeps == [supervisor.SHELL_SETTLE_SECONDS]
    assert output["decision"] == "block" and len(reviews) == 1
    assert "completed-test" not in reviews[0]


def test_repeated_stop_reviews_consume_bounded_budget(git_repo, reviews, monkeypatch):
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "2")
    outputs = []
    for turn in ("turn-1", "turn-2", "turn-3"):
        payload = _payload(git_repo)
        payload["turn_id"] = turn
        outputs.append(supervisor.supervise_hook(payload))
    assert outputs[0]["decision"] == outputs[1]["decision"] == "block"
    assert "cap reached" in outputs[2]["systemMessage"].lower()
    assert len(reviews) == 2


def test_cancelled_stop_bypasses_immediate_host_generated_stop_with_new_turn_id(git_repo, reviews):
    state.grant_stop_abort_bypass(git_repo, "root-session", "turn-1")
    payload = _payload(git_repo)
    payload["turn_id"] = "host-reassigned-turn"
    assert supervisor.supervise_hook(payload) == {}
    assert reviews == []
    assert supervisor.supervise_hook(_payload(git_repo))["decision"] == "block"
    assert len(reviews) == 1


def test_expired_cancelled_stop_bypass_cannot_leak(git_repo, reviews, monkeypatch):
    monkeypatch.setattr(state.time, "time", lambda: 100.0)
    state.grant_stop_abort_bypass(git_repo, "root-session", "turn-1")
    monkeypatch.setattr(state.time, "time", lambda: 200.0)
    assert supervisor.supervise_hook(_payload(git_repo))["decision"] == "block"
    assert len(reviews) == 1
