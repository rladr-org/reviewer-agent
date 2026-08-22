# SPDX-License-Identifier: MPL-2.0
import re

from reviewer_codex import cli, liveness, state, supervisor


def _payload(repo, active=False):
    return {
        "session_id": "root-session",
        "turn_id": "turn-1",
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "stop_hook_active": active,
        "last_assistant_message": "I am done.",
    }


def test_disabled_stop_supervisor_is_inert(git_repo, isolated_state):
    assert supervisor.supervise(_payload(git_repo)) is None


def test_cli_enable_without_turn_id_does_not_skip_a_later_stop(
    git_repo, isolated_state, monkeypatch, capsys
):
    monkeypatch.chdir(git_repo)
    assert cli.main(["stop", "on"]) == 0
    capsys.readouterr()
    output = supervisor.supervise(_payload(git_repo))

    assert output["decision"] == "block"
    assert "stop_continue" in output["reason"]


def test_first_stop_with_shell_marker_routes_to_minimal_supervisor(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "shell", "tool-1", "long-test")
    output = supervisor.supervise(_payload(git_repo))
    assert output["decision"] == "block"
    assert "stop_continue" in output["reason"]
    assert "write_stdin" not in output["reason"]
    token = re.search(r"review_token=([a-f0-9]+)", output["reason"]).group(1)
    ticket = state.claim_stop_ticket(token, git_repo)
    assert ticket["background_markers"] == [
        {"kind": "shell", "id": "tool-1", "label": "long-test"}
    ]


def test_shell_marker_does_not_require_a_preliminary_polling_stop(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "shell", "tool-1", "lost unified exec")

    output = supervisor.supervise(_payload(git_repo))

    assert "stop_continue" in output["reason"]
    assert "write_stdin" not in output["reason"]
    token = re.search(r"review_token=([a-f0-9]+)", output["reason"]).group(1)
    ticket = state.claim_stop_ticket(token, git_repo)
    assert ticket["shell_markers"] == [
        {"id": "tool-1", "label": "lost unified exec"}
    ]


def test_new_shell_marker_is_snapshotted_in_a_new_stop_ticket(git_repo, isolated_state):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "shell", "tool-1", "first command")
    first = supervisor.supervise(_payload(git_repo))
    liveness.mark(git_repo, "root-session", "shell", "tool-2", "new command")

    second = supervisor.supervise(_payload(git_repo, active=True))

    first_token = re.search(r"review_token=([a-f0-9]+)", first["reason"]).group(1)
    second_token = re.search(r"review_token=([a-f0-9]+)", second["reason"]).group(1)
    assert state.claim_stop_ticket(first_token, git_repo)["background_markers"] == [
        {"kind": "shell", "id": "tool-1", "label": "first command"}
    ]
    assert {
        (item["kind"], item["id"])
        for item in state.claim_stop_ticket(second_token, git_repo)["background_markers"]
    } == {("shell", "tool-1"), ("shell", "tool-2")}


def test_first_stop_with_subagent_marker_routes_to_minimal_supervisor(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "subagent", "agent-1", "worker")

    output = supervisor.supervise(_payload(git_repo))

    assert "stop_continue" in output["reason"]
    assert "wait_agent" not in output["reason"]
    token = re.search(r"review_token=([a-f0-9]+)", output["reason"]).group(1)
    ticket = state.claim_stop_ticket(token, git_repo)
    assert ticket["background_markers"] == [
        {"kind": "subagent", "id": "agent-1", "label": "worker"}
    ]


def test_first_stop_with_mixed_markers_routes_one_exact_set(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "shell", "tool-1", "old validator")
    liveness.mark(git_repo, "root-session", "subagent", "agent-1", "default")

    output = supervisor.supervise(_payload(git_repo))

    assert "stop_continue" in output["reason"]
    assert "write_stdin" not in output["reason"]
    assert "wait_agent" not in output["reason"]
    token = re.search(r"review_token=([a-f0-9]+)", output["reason"]).group(1)
    ticket = state.claim_stop_ticket(token, git_repo)
    assert {(item["kind"], item["id"]) for item in ticket["background_markers"]} == {
        ("shell", "tool-1"),
        ("subagent", "agent-1"),
    }


def test_new_subagent_marker_is_snapshotted_in_a_new_stop_ticket(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    liveness.mark(git_repo, "root-session", "subagent", "agent-1", "first worker")
    first = supervisor.supervise(_payload(git_repo))
    liveness.mark(git_repo, "root-session", "subagent", "agent-2", "new worker")

    second = supervisor.supervise(_payload(git_repo, active=True))

    first_token = re.search(r"review_token=([a-f0-9]+)", first["reason"]).group(1)
    second_token = re.search(r"review_token=([a-f0-9]+)", second["reason"]).group(1)
    assert state.claim_stop_ticket(first_token, git_repo)["background_markers"] == [
        {"kind": "subagent", "id": "agent-1", "label": "first worker"}
    ]
    assert {
        (item["kind"], item["id"])
        for item in state.claim_stop_ticket(second_token, git_repo)["background_markers"]
    } == {("subagent", "agent-1"), ("subagent", "agent-2")}


def test_stop_rechecks_shell_markers_before_declaring_work_active(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    shell = {"kind": "shell", "id": "tool-1", "label": "completed-test"}
    readings = iter([[shell], []])
    sleeps = []
    monkeypatch.setattr(
        liveness, "outstanding", lambda _path, _session_id: next(readings)
    )
    monkeypatch.setattr(supervisor.time, "sleep", sleeps.append)

    output = supervisor.supervise(_payload(git_repo))

    assert sleeps == [supervisor.SHELL_SETTLE_SECONDS]
    assert output["decision"] == "block"
    assert "stop_continue" in output["reason"]
    assert "completed-test" not in output["reason"]


def test_stop_hook_returns_immediately_with_session_bound_token(git_repo, isolated_state):
    state.set_feature(git_repo, "stop")
    output = supervisor.supervise(_payload(git_repo))
    assert output["decision"] == "block"
    assert output["reason"].startswith(supervisor.CONTINUATION_PREFIX)
    assert "stop_continue" in output["reason"]
    token = re.search(r"review_token=([a-f0-9]+)", output["reason"]).group(1)
    ticket = state.claim_stop_ticket(token, git_repo)
    assert ticket["session_id"] == "root-session"
    assert ticket["final_message"] == "I am done."
    assert ticket["turn_id"] == "turn-1"


def test_one_use_stop_permit_allows_next_stop(git_repo, isolated_state):
    state.set_feature(git_repo, "stop")
    state.grant_stop_permit(git_repo, "root-session")

    output = supervisor.supervise(_payload(git_repo))

    assert "approved" in output["systemMessage"]
    assert supervisor.supervise(_payload(git_repo))["decision"] == "block"


def test_repeated_stop_routing_consumes_bounded_nudge_budget(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "2")
    payload = _payload(git_repo, active=True)

    first = supervisor.supervise(payload)
    second = supervisor.supervise(payload)
    third = supervisor.supervise(payload)

    assert first["decision"] == second["decision"] == "block"
    assert "cap reached" in third["systemMessage"].lower()


def test_hook_generated_continuations_keep_budget_across_fresh_host_turns(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "2")

    outputs = []
    for turn_id in ("turn-1", "turn-2", "turn-3"):
        payload = _payload(git_repo, active=False)
        payload["turn_id"] = turn_id
        outputs.append(supervisor.supervise(payload))

    assert outputs[0]["decision"] == outputs[1]["decision"] == "block"
    assert "cap reached" in outputs[2]["systemMessage"].lower()


def test_cancelled_stop_bypasses_immediate_host_generated_stop_with_new_turn_id(
    git_repo, isolated_state
):
    state.set_feature(git_repo, "stop")
    state.grant_stop_abort_bypass(git_repo, "root-session", "turn-1")
    repeated_payload = _payload(git_repo)
    repeated_payload["turn_id"] = "host-reassigned-turn"

    immediate = supervisor.supervise(repeated_payload)
    repeated = supervisor.supervise(_payload(git_repo))

    assert "cancelled" in immediate["systemMessage"].lower()
    assert repeated["decision"] == "block"
    assert "stop_continue" in repeated["reason"]


def test_expired_cancelled_stop_bypass_cannot_leak(
    git_repo, isolated_state, monkeypatch
):
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(state.time, "time", lambda: 100.0)
    state.grant_stop_abort_bypass(git_repo, "root-session", "turn-1")
    monkeypatch.setattr(state.time, "time", lambda: 200.0)

    output = supervisor.supervise(_payload(git_repo))

    assert output["decision"] == "block"
    assert "stop_continue" in output["reason"]
