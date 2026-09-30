# SPDX-License-Identifier: MPL-2.0
"""Loop recovery through native Stop, real state, and a stub Codex subprocess."""
import importlib
import io
import json
import runpy
import sys
from pathlib import Path

import pytest

from reviewer_codex import backend, liveness, state, supervisor
from reviewer_codex.cancellation import Cancellation

SESSION = "loop-session"
OLD_THREAD = "persistent-stop-reviewer"
DIRECTIVE = "OUTDATED SUPERVISOR DEMAND: perform more unrequested work."
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def event(repo, **changes):
    value = dict(cwd=str(repo), session_id=SESSION, turn_id="turn-1",
                 hook_event_name="Stop", stop_hook_active=True,
                 last_assistant_message="The requested change is complete and the tests passed.")
    value.update(changes)
    return value


@pytest.fixture
def reviews(git_repo, isolated_state, monkeypatch):
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "10")
    state.set_feature(git_repo, "stop")
    state.publish_reviewer_session(git_repo, SESSION, "stop", OLD_THREAD)
    state.publish_reviewer_session(git_repo, SESSION, "reviewer", "commit-thread")
    control = {"calls": [], "fresh": (0, "STOP_OK", ""),
               "resumed": (0, DIRECTIVE, ""), "callback": None}

    def run(argv, timeout, input_text=None, cancel_event=None):
        fresh = "resume" not in argv
        control["calls"].append(dict(argv=argv, prompt=input_text, timeout=timeout,
                                     cancel_event=cancel_event, fresh=fresh))
        if control["callback"]:
            control["callback"](fresh, cancel_event)
        result = control["fresh" if fresh else "resumed"]
        if isinstance(result, Exception):
            raise result
        code, output, error = result
        Path(argv[argv.index("--output-last-message") + 1]).write_text(output)
        return code, json.dumps({"type": "thread.started", "thread_id": "disposable-thread"}), error

    monkeypatch.setattr(backend, "_run", run)
    return control


def block_twice(repo, **changes):
    for _ in range(2):
        assert supervisor.supervise_hook(event(repo, **changes))["decision"] == "block"


def hook(monkeypatch, repo, script, kind, **changes):
    payload = event(repo, hook_event_name=kind, **changes)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", [script, "start"] if script == "hook_subagent.py" else [script])
    runpy.run_path(str(SCRIPTS / script))["main"]()


@pytest.mark.parametrize("answer", [
    "The requested change is complete and the tests passed.",
    "The local work is complete. Please supply the credentials only you can provide.",
])
def test_third_unchanged_stop_gets_fresh_completion_or_waiting_assessment(git_repo, reviews, answer):
    block_twice(git_repo, last_assistant_message=answer)
    assert len(reviews["calls"]) == 2
    result = supervisor.supervise_hook(event(git_repo, last_assistant_message=answer))
    assert result == {}
    assert [call["fresh"] for call in reviews["calls"]] == [False, False, True]
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert state.read_reviewer_session(git_repo, SESSION, "stop") == OLD_THREAD
    assert state.read_reviewer_session(git_repo, SESSION, "reviewer") == "commit-thread"


def test_fresh_review_has_scope_plan_and_reply_without_old_supervisor_history(git_repo, reviews):
    request = "Evaluate the parser and report; do not implement changes."
    state.record_prompt(git_repo, SESSION, request)
    state.record_plan(git_repo, SESSION, {"plan": [
        {"step": "Evaluate parser", "status": "completed"}]})
    block_twice(git_repo)
    supervisor.supervise_hook(event(git_repo))
    call = reviews["calls"][-1]
    assert call["fresh"]
    assert "FRESH COMPLETION REASSESSMENT" in call["prompt"]
    assert request in call["prompt"]
    assert "[completed] Evaluate parser" in call["prompt"]
    assert event(git_repo)["last_assistant_message"] in call["prompt"]
    assert DIRECTIVE not in call["prompt"]
    assert "not operator authorization" in call["prompt"]
    assert "lack of tool use" in call["prompt"]
    assert "STOP_OK" in call["prompt"]
    assert call["argv"][call["argv"].index("--sandbox") + 1] == "read-only"
    assert backend.HOOKS_DISABLED_CONFIG in call["argv"]
    assert backend.PLUGINS_DISABLED_CONFIG in call["argv"]
    assert call["timeout"] == backend.STOP_REVIEW_TIMEOUT


def test_unfinished_work_keeps_getting_independent_rulings_until_cap(git_repo, reviews, monkeypatch):
    monkeypatch.setenv("REVIEWER_STOP_MAX_NUDGES", "4")
    reviews["fresh"] = (0, "Run the requested tests before stopping.", "")
    block_twice(git_repo)
    for _ in range(2):
        result = supervisor.supervise_hook(event(git_repo,
            last_assistant_message="I'll run the authorized tests next; anything else?"))
        assert result["decision"] == "block"
        assert "Run the requested tests" in result["reason"]
    result = supervisor.supervise_hook(event(git_repo))
    assert "cap reached" in result["systemMessage"].lower()
    assert [call["fresh"] for call in reviews["calls"]] == [False, False, True, True]


@pytest.mark.parametrize("activity", ["operator", "tool", "subagent"])
def test_real_activity_restarts_two_block_threshold(git_repo, reviews, monkeypatch, activity):
    block_twice(git_repo)
    if activity == "operator":
        hook(monkeypatch, git_repo, "hook_prompt.py", "UserPromptSubmit", prompt="New task: inspect this.")
    elif activity == "tool":
        hook(monkeypatch, git_repo, "hook_pre_tool.py", "PreToolUse", tool_name="Read", tool_use_id="read-1")
    else:
        hook(monkeypatch, git_repo, "hook_subagent.py", "SubagentStart", agent_id="child-1")
    block_twice(git_repo)
    assert all(not call["fresh"] for call in reviews["calls"])
    supervisor.supervise_hook(event(git_repo))
    assert reviews["calls"][-1]["fresh"]


def test_host_turn_changes_and_module_reload_do_not_erase_chain(git_repo, reviews):
    block_twice(git_repo)
    importlib.reload(supervisor)
    assert supervisor.supervise_hook(event(git_repo, turn_id="host-new-turn", stop_hook_active=False)) == {}
    assert reviews["calls"][-1]["fresh"]


def test_allowed_stop_ends_chain(git_repo, reviews):
    block_twice(git_repo)
    assert supervisor.supervise_hook(event(git_repo)) == {}
    block_twice(git_repo)
    assert [call["fresh"] for call in reviews["calls"]] == [False, False, True, False, False]


@pytest.mark.parametrize("scope", ["session", "project"])
def test_other_scope_does_not_inherit_loop(git_repo, reviews, tmp_path, scope):
    import shutil
    block_twice(git_repo)
    repo, session = git_repo, SESSION
    if scope == "session":
        session = "other-session"
    else:
        repo = tmp_path / "other-repo"
        shutil.copytree(git_repo, repo)
        state.set_feature(repo, "stop")
    state.publish_reviewer_session(repo, session, "stop", OLD_THREAD)
    for _ in range(2):
        assert supervisor.supervise_hook(event(repo, session_id=session))["decision"] == "block"
    assert all(not call["fresh"] for call in reviews["calls"])
    supervisor.supervise_hook(event(repo, session_id=session))
    assert reviews["calls"][-1]["fresh"]


def test_session_cleanup_removes_loop_state(git_repo, reviews):
    block_twice(git_repo)
    state.clear_session_artifacts(SESSION)
    state.publish_reviewer_session(git_repo, SESSION, "stop", OLD_THREAD)
    block_twice(git_repo)
    assert all(not call["fresh"] for call in reviews["calls"])
    supervisor.supervise_hook(event(git_repo))
    assert reviews["calls"][-1]["fresh"]


@pytest.mark.parametrize("failure", [(124, "", "timed out"), (0, "", ""), RuntimeError("offline")])
def test_fresh_failure_uses_existing_fail_open_policy(git_repo, reviews, failure):
    block_twice(git_repo)
    reviews["fresh"] = failure
    result = supervisor.supervise_hook(event(git_repo))
    assert reviews["calls"][-1]["fresh"]
    assert "decision" not in result
    assert "fail-open" in result["systemMessage"]
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert state.read_reviewer_session(git_repo, SESSION, "stop") == OLD_THREAD
    # Failure ended this chain, too.
    assert supervisor.supervise_hook(event(git_repo))["decision"] == "block"
    assert not reviews["calls"][-1]["fresh"]


def test_fresh_cancellation_keeps_one_use_bypass_and_resets_chain(git_repo, reviews):
    block_twice(git_repo)
    cancel = Cancellation()
    def interrupt(fresh, token):
        if fresh:
            assert token is cancel
            token.cancel()
    reviews["callback"] = interrupt
    result = supervisor.supervise_hook(event(git_repo), cancel_event=cancel)
    assert result == {}
    assert reviews["calls"][-1]["fresh"]
    assert not state.consume_stop_permit(git_repo, SESSION)
    reviews["callback"] = None
    assert supervisor.supervise_hook(event(git_repo, turn_id="interrupt-stop")) == {}
    assert len(reviews["calls"]) == 3
    assert supervisor.supervise_hook(event(git_repo))["decision"] == "block"
    assert not reviews["calls"][-1]["fresh"]


@pytest.mark.parametrize("work", [False, True], ids=["operator", "tool"])
def test_new_activity_during_fresh_review_invalidates_approval(git_repo, reviews, work):
    block_twice(git_repo)
    def activity(fresh, token):
        if fresh:
            state.invalidate_stop_permits(git_repo, SESSION, work=work)
    reviews["callback"] = activity
    result = supervisor.supervise_hook(event(git_repo))
    assert reviews["calls"][-1]["fresh"]
    assert result["decision"] == "block"
    assert not state.consume_stop_permit(git_repo, SESSION)
    reviews["callback"] = None
    block_twice(git_repo)
    assert all(not call["fresh"] for call in reviews["calls"][-2:])


def test_fresh_review_retains_background_checks_and_cannot_clear_new_work(git_repo, reviews):
    liveness.mark(git_repo, SESSION, "subagent", "old", "old worker")
    block_twice(git_repo)
    def activity(fresh, token):
        if fresh:
            liveness.mark(git_repo, SESSION, "subagent", "new", "new worker")
    reviews["callback"] = activity
    result = supervisor.supervise_hook(event(git_repo))
    call = reviews["calls"][-1]
    assert call["fresh"]
    assert "old worker" in call["prompt"]
    assert "collaboration registry" in call["prompt"]
    assert result["decision"] == "block"
    assert [item["id"] for item in liveness.outstanding(git_repo, SESSION)] == ["new"]
    assert not state.consume_stop_permit(git_repo, SESSION)


def test_supervisor_feedback_is_not_new_operator_input(git_repo, reviews, monkeypatch):
    block_twice(git_repo)
    hook(monkeypatch, git_repo, "hook_prompt.py", "UserPromptSubmit",
         prompt=supervisor.CONTINUATION_PREFIX + "\n" + DIRECTIVE)
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert reviews["calls"][-1]["fresh"]
    assert DIRECTIVE not in reviews["calls"][-1]["prompt"]


def test_outer_fail_open_ends_chain(git_repo, reviews, monkeypatch):
    block_twice(git_repo)
    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise OSError("liveness state unavailable")
        patch.setattr(supervisor, "_settled_outstanding", unavailable)
        result = supervisor.supervise_hook(event(git_repo))
        assert "fail-open" in result["systemMessage"]
    assert supervisor.supervise_hook(event(git_repo))["decision"] == "block"
    assert not reviews["calls"][-1]["fresh"]
