# SPDX-License-Identifier: MPL-2.0
"""The opt-in CLI smoke check must not mistake missing hooks or fail-open for approval."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_cli_stop.py"


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("cli_stop_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def exchange(name, draft, result, number):
    return [
        {"kind": "mcp_request", "at": number, "message": {
            "id": number, "method": "tools/call", "params": {
                "name": name, "arguments": {"proposed_response": draft,
                    "last_assistant_message": draft, "session_id": "thread"}}}},
        {"kind": "mcp_response", "at": number + 1, "message": {
            "id": number, "result": {"structuredContent": result}}},
    ]


def output(text, at=10):
    return {"kind": "cli", "at": at, "message": {"type": "item.completed",
            "item": {"type": "agent_message", "text": text}}}


def evidence(check, scenario):
    records = [{"kind": "cli", "at": 0, "message": {
        "type": "thread.started", "thread_id": "thread"}}]
    if scenario == "prefinal":
        records += exchange("review_completion", check.INCOMPLETE, {"status": "incomplete"}, 1)
        records += exchange("review_completion", check.FINAL, {"status": "stop_ok"}, 3)
    else:
        records += [output(check.INCOMPLETE, at=1)]
        records += exchange("stop_hook", check.INCOMPLETE, {"decision": "block", "reason": "Calculate."}, 2)
    records += [output(check.FINAL)]
    records += exchange("stop_hook", check.FINAL, {}, 11)
    records += [{"kind": "cli", "at": 13, "message": {"type": "turn.completed"}}]
    return records


@pytest.mark.parametrize("scenario", ["native", "fallback"])
def test_correlated_real_hook_round_trip(check, scenario):
    result = check.assess(evidence(check, scenario), scenario, 0)
    assert result["scenario"] == scenario
    assert result["rejected_response_emitted"]


@pytest.mark.parametrize("fault", ["no-hooks", "fail-open", "no-final", "no-turn-end",
                                  "wrong-draft", "wrong-session", "no-response", "failed-process"])
def test_incomplete_evidence_never_passes(check, fault):
    records = evidence(check, "fallback")
    if fault == "no-hooks":
        records = [r for r in records if r["kind"] == "cli"]
    elif fault == "fail-open":
        records[-2]["message"]["result"]["structuredContent"] = {"systemMessage": "review failed"}
    elif fault == "no-final":
        records = [r for r in records if r != output(check.FINAL)]
    elif fault == "no-turn-end":
        records.pop()
    elif fault == "wrong-draft":
        records[-3]["message"]["params"]["arguments"]["last_assistant_message"] = "unreviewed"
    elif fault == "wrong-session":
        records[-3]["message"]["params"]["arguments"]["session_id"] = "another-thread"
    elif fault == "no-response":
        records.pop(-2)
    with pytest.raises(ValueError):
        check.assess(records, "fallback", 1 if fault == "failed-process" else 0)


def test_removed_prefinal_scenario_is_rejected(check):
    records = evidence(check, "prefinal")
    with pytest.raises(ValueError, match="unsupported"):
        check.assess(records, "prefinal", 0)


def test_default_live_check_runs_only_automatic_stop(check, git_repo, isolated_state, monkeypatch):
    from reviewer_codex import state
    state.set_feature(git_repo, "stop")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--run", "--cwd", str(git_repo),
                                     "--plugin-root", str(SCRIPT.parents[1])])
    original_run = check.subprocess.run
    def run_command(args, **kwargs):
        if args[-1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="codex test\n")
        return original_run(args, **kwargs)
    monkeypatch.setattr(check.subprocess, "run", run_command)
    calls = []
    def run(args, scenario, directory):
        calls.append(scenario)
        return {"scenario": scenario}
    monkeypatch.setattr(check, "run_scenario", run)
    assert check.main() == 0
    assert calls == ["native"]


@pytest.mark.parametrize("fault", ["missing-incomplete", "answer-before-rejection",
                                  "first-stop-before-output", "reply-before-request",
                                  "second-stop-before-answer", "approval-before-request"])
def test_fallback_requires_rejection_before_continuation(check, fault):
    records = evidence(check, "fallback")
    if fault == "missing-incomplete":
        records.pop(1)
    elif fault == "answer-before-rejection":
        records[4]["at"] = 2.5
    elif fault == "first-stop-before-output":
        records[2]["at"] = 0.5
    elif fault == "reply-before-request":
        records[3]["at"] = 1.5
    elif fault == "second-stop-before-answer":
        records[5]["at"] = 9
    elif fault == "approval-before-request":
        records[6]["at"] = 10.5
    with pytest.raises(ValueError, match="fallback.*order"):
        check.assess(records, "fallback", 0)


def test_duplicate_response_is_not_a_clean_round_trip(check):
    records = evidence(check, "fallback")
    records.append(records[-2])
    with pytest.raises(ValueError):
        check.assess(records, "fallback", 0)


def test_observer_forwards_and_correlates_split_writes(check):
    import io
    source = json.dumps({"id": 8, "method": "tools/call", "params": {"name": "stop_hook"}}) + "\n"
    records, destination = [], io.StringIO()
    observer = check.Observer(io.StringIO(source), destination, records.append)
    assert list(observer.input()) == [source]
    reply = json.dumps({"id": 8, "result": {"structuredContent": {}}}) + "\n"
    observer.write(reply[:7])
    observer.write(reply[7:])
    observer.flush()
    assert destination.getvalue() == reply
    assert [r["kind"] for r in records] == ["mcp_request", "mcp_response"]


def test_missing_opt_in_never_launches_cli(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "--run" in result.stderr
