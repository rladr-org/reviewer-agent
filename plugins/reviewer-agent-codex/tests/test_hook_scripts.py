# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reviewer_codex import liveness, state, supervisor


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("raw", ["{", "[]", '"event"',
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":"bad"}',
    '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":42}}',
])
def test_pre_tool_malformed_input_explicitly_denies(raw, isolated_state):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/hook_pre_tool.py")],
        input=raw, text=True, capture_output=True,
        env=dict(os.environ, REVIEWER_CODEX_STATE_DIR=str(isolated_state)), timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("failure", ["bootstrap", "package"])
def test_pre_tool_import_failure_explicitly_denies(tmp_path, isolated_state, failure):
    script = tmp_path / "hook_pre_tool.py"
    script.write_bytes((ROOT / "scripts/hook_pre_tool.py").read_bytes())
    (tmp_path / "_bootstrap.py").write_text(
        "raise RuntimeError('private import detail')\n" if failure == "bootstrap" else ""
    )
    package = tmp_path / "reviewer_codex"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('private import detail')\n")
    result = subprocess.run(
        [sys.executable, str(script)], input="{}", text=True, capture_output=True,
        env=dict(os.environ, REVIEWER_CODEX_STATE_DIR=str(isolated_state)), timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not isolated_state.exists()
    assert "private import detail" not in result.stdout


@pytest.mark.parametrize("tool_name,tool_input", [("Bash", None), ("mcp__other__tool", "value")])
def test_pre_tool_preserves_optional_and_other_tool_input(git_repo, isolated_state, tool_name, tool_input):
    result = _run("hook_pre_tool.py", {
        "hook_event_name": "PreToolUse", "tool_name": tool_name,
        "cwd": str(git_repo), "tool_input": tool_input,
    }, isolated_state)
    assert result.returncode == 0
    assert result.stdout == ""


def test_shell_liveness_failure_does_not_block_non_commit(git_repo, tmp_path):
    obstructed_state = tmp_path / "obstructed"
    obstructed_state.write_text("not a directory")
    result = _run("hook_pre_tool.py", {
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "cwd": str(git_repo), "session_id": "root", "tool_use_id": "shell",
        "tool_input": {"command": "git status"},
    }, obstructed_state)
    assert result.returncode == 0
    assert result.stdout == ""


def test_pre_tool_ticket_failure_explicitly_denies(git_repo, isolated_state):
    state.set_feature(git_repo, "reviewer")
    (isolated_state / "commit-tickets").write_text("obstruction")
    payload = {
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(git_repo),
        "session_id": "root",
        "tool_input": {"command": "git -C {} commit -m private-command-detail".format(git_repo)},
    }
    result = _run("hook_pre_tool.py", payload, isolated_state)
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "review_token=" not in output["permissionDecisionReason"]
    assert "private-command-detail" not in result.stdout


@pytest.mark.parametrize("failure", ["python", "script", "crash"])
def test_pre_tool_launcher_failure_blocks(tmp_path, isolated_state, failure):
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    home = tmp_path / "home"
    source = home / "cache/reviewer-agent-codex/version"
    source.mkdir(parents=True)
    if failure == "crash":
        (source / "scripts").mkdir()
        (source / "scripts/hook_pre_tool.py").write_text("raise SystemExit(1)\n")
    env = dict(os.environ, HOME=str(home), PLUGIN_ROOT=str(source),
               REVIEWER_CODEX_STATE_DIR=str(isolated_state))
    if failure == "python":
        env["PATH"] = str(tmp_path / "empty-path")
    result = subprocess.run(
        command, shell=True, input="{}", text=True, capture_output=True, env=env, timeout=10,
    )
    assert result.returncode == 2
    assert "reviewer-agent" in result.stderr


def _run(script, payload, isolated_state, *args):
    env = dict(os.environ, REVIEWER_CODEX_STATE_DIR=str(isolated_state), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        input=json.dumps(payload), text=True, capture_output=True, env=env, check=False,
    )


def test_pre_and_post_hooks_track_shell_lifetime(git_repo, isolated_state):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": "tool-1",
        "tool_input": {"command": "pytest"},
    }
    assert _run("hook_pre_tool.py", payload, isolated_state).returncode == 0
    assert [item["label"] for item in liveness.outstanding(git_repo, "root")] == ["pytest"]
    payload["hook_event_name"] = "PostToolUse"
    assert _run("hook_post_tool.py", payload, isolated_state).returncode == 0
    assert liveness.outstanding(git_repo, "root") == []


def test_pre_tool_hook_records_latest_update_plan(git_repo, isolated_state):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "update_plan",
        "tool_use_id": "plan-1",
        "tool_input": {
            "explanation": "Current execution state",
            "plan": [
                {"step": "Implement redesign", "status": "completed"},
                {"step": "Run full suite", "status": "in_progress"},
            ],
        },
    }

    assert _run("hook_pre_tool.py", payload, isolated_state).returncode == 0
    assert state.current_plan(git_repo, "root") == payload["tool_input"]


def test_plan_capture_fails_open_when_state_is_unwritable(git_repo, tmp_path):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "update_plan",
        "tool_use_id": "plan-1",
        "tool_input": {
            "plan": [{"step": "Keep working", "status": "in_progress"}],
        },
    }
    env = dict(
        os.environ,
        REVIEWER_CODEX_STATE_DIR="/dev/null/reviewer-agent-codex",
        PYTHONDONTWRITEBYTECODE="1",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "hook_pre_tool.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_prompt_hook_excludes_hook_generated_continuations(git_repo, isolated_state):
    base = {"session_id": "root", "cwd": str(git_repo), "hook_event_name": "UserPromptSubmit"}
    _run("hook_prompt.py", dict(base, prompt="real operator prompt"), isolated_state)
    _run(
        "hook_prompt.py",
        dict(base, prompt=supervisor.CONTINUATION_PREFIX + "\nkeep working"),
        isolated_state,
    )
    assert state.recent_prompts(git_repo, "root") == ["real operator prompt"]


def test_real_operator_prompt_resets_stop_nudge_chain(git_repo, isolated_state):
    state.bump_nudges(git_repo, "root")
    state.bump_nudges(git_repo, "root")
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "begin a genuinely new task",
    }

    _run("hook_prompt.py", payload, isolated_state)

    assert state.read_nudges(git_repo, "root") == 0


def test_hook_continuation_does_not_reset_stop_nudge_chain(
    git_repo, isolated_state
):
    state.bump_nudges(git_repo, "root")
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": supervisor.CONTINUATION_PREFIX + "\nkeep working",
    }

    _run("hook_prompt.py", payload, isolated_state)

    assert state.read_nudges(git_repo, "root") == 1


def test_real_operator_prompt_clears_cancelled_stop_bypass(
    git_repo, isolated_state
):
    base = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
    }
    state.grant_stop_abort_bypass(git_repo, "root", "cancelled-turn")

    _run("hook_prompt.py", dict(base, prompt="start new work"), isolated_state)

    assert not state.consume_stop_abort_bypass(
        git_repo, "root", "host-reassigned-turn"
    )


def test_hook_continuation_does_not_clear_cancelled_stop_bypass(
    git_repo, isolated_state
):
    base = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
    }
    state.grant_stop_abort_bypass(git_repo, "root", "cancelled-turn")

    _run(
        "hook_prompt.py",
        dict(base, prompt=supervisor.CONTINUATION_PREFIX + "\nfinish stopping"),
        isolated_state,
    )

    assert state.consume_stop_abort_bypass(
        git_repo, "root", "host-reassigned-turn"
    )


def test_prompt_hook_requires_explicit_risk_acceptance_before_enabling(
    git_repo, isolated_state
):
    (git_repo / "reviewerAgent.md").write_text("Review the staged index.\n", encoding="utf-8")
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-agent on",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0
    assert not state.feature_enabled(git_repo, "reviewer")
    output = json.loads(result.stdout)
    assert (
        "Before enabling reviewerAgent you must accept the risk as described in "
        "DISCLAIMER.md by invoking:" in output["systemMessage"]
    )
    assert (
        "$reviewer-agent-codex:reviewer-agent on acceptRisk"
        in output["systemMessage"]
    )
    assert output["continue"] is False
    assert output["stopReason"] == output["systemMessage"]
    assert "hookSpecificOutput" not in output
    assert state.recent_prompts(git_repo, "root") == []


def test_prompt_hook_accepts_risk_and_enables_reviewer_in_one_turn(
    git_repo, isolated_state
):
    (git_repo / "reviewerAgent.md").write_text("Review the staged index.\n", encoding="utf-8")
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-agent on acceptRisk",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0
    assert state.feature_enabled(git_repo, "reviewer")
    output = json.loads(result.stdout)
    assert output["systemMessage"] == "reviewerAgent: ON for {}".format(git_repo)
    assert output["continue"] is False


@pytest.mark.parametrize("kind", ["template", "empty", "unreadable"])
def test_prompt_hook_refuses_unedited_brief_template(git_repo, isolated_state, kind):
    brief = {"empty": b"\n", "unreadable": b"\xff"}.get(
        kind, (ROOT / "reviewerAgent.md.template").read_bytes()
    )
    (git_repo / "reviewerAgent.md").write_bytes(brief)
    payload = {
        "session_id": "root", "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-agent on acceptRisk",
    }
    result = _run("hook_prompt.py", payload, isolated_state)
    assert result.returncode == 0
    assert not state.feature_enabled(git_repo, "reviewer")
    message = json.loads(result.stdout)["systemMessage"]
    assert "reviewerAgent.md" in message
    disclaimer = state.disclaimer_sha256(ROOT / "DISCLAIMER.md")
    assert not state.risk_accepted(git_repo, disclaimer)


def test_prompt_hook_requires_explicit_risk_acceptance_before_enabling_stop(
    git_repo, isolated_state
):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:stop-continue on",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0
    assert not state.feature_enabled(git_repo, "stop")
    output = json.loads(result.stdout)
    assert (
        "Before enabling stopContinue you must accept the risk as described in "
        "DISCLAIMER.md by invoking:" in output["systemMessage"]
    )
    assert (
        "$reviewer-agent-codex:stop-continue on acceptRisk"
        in output["systemMessage"]
    )
    assert output["continue"] is False


def test_reviewer_acceptance_is_shared_with_stopcontinue(git_repo, isolated_state):
    (git_repo / "reviewerAgent.md").write_text(
        "Review the staged index.\n", encoding="utf-8"
    )
    base = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
    }
    accepted = _run(
        "hook_prompt.py",
        dict(
            base,
            prompt="$reviewer-agent-codex:reviewer-agent on acceptRisk",
        ),
        isolated_state,
    )
    assert accepted.returncode == 0

    enabled = _run(
        "hook_prompt.py",
        dict(base, prompt="$reviewer-agent-codex:stop-continue on"),
        isolated_state,
    )

    assert enabled.returncode == 0
    assert state.feature_enabled(git_repo, "stop")
    assert json.loads(enabled.stdout)["systemMessage"] == (
        "stopContinue: ON for {}".format(git_repo)
    )


def test_prompt_hook_rotates_only_the_current_commit_reviewer_session(
    git_repo, isolated_state
):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "slow-thread")
    state.publish_reviewer_session(git_repo, "root", "stop", "stop-thread")
    state.write_commit_cursor(
        git_repo,
        "root",
        {
            "reviewer_id": "slow-thread",
            "head": "abc",
            "files": {"change.txt": "blob"},
        },
    )
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-session rotate",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False
    assert "rotated slow-thread" in output["systemMessage"]
    assert "fresh reviewer" in output["systemMessage"]
    assert state.read_reviewer_session(git_repo, "root", "reviewer") is None
    assert state.read_reviewer_session(git_repo, "root", "stop") == "stop-thread"
    assert state.read_commit_cursor(git_repo, "root") is None
    assert state.recent_prompts(git_repo, "root") == []


def test_prompt_hook_reports_reviewer_session_without_mutating_it(
    git_repo, isolated_state
):
    state.publish_reviewer_session(git_repo, "root", "reviewer", "current-thread")
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-session status",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    output = json.loads(result.stdout)
    assert output["systemMessage"].startswith(
        "reviewerSession: current-thread for {};".format(git_repo)
    )
    assert "after 3 completed non-retry audit(s)" in output["systemMessage"]
    assert state.read_reviewer_session(git_repo, "root", "reviewer") == "current-thread"


def test_prompt_hook_sets_disables_and_clears_session_audit_limit(
    git_repo, isolated_state
):
    base = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
    }
    configured = _run(
        "hook_prompt.py",
        dict(base, prompt="$reviewer-agent-codex:reviewer-session limit 5"),
        isolated_state,
    )
    assert configured.returncode == 0
    assert "after 5 completed non-retry audit(s)" in json.loads(
        configured.stdout
    )["systemMessage"]

    disabled = _run(
        "hook_prompt.py",
        dict(base, prompt="$reviewer-agent-codex:reviewer-session limit off"),
        isolated_state,
    )
    assert "automatic rotation disabled" in json.loads(
        disabled.stdout
    )["systemMessage"]

    cleared = _run(
        "hook_prompt.py",
        dict(base, prompt="$reviewer-agent-codex:reviewer-session limit clear"),
        isolated_state,
    )
    assert "after 3 completed non-retry audit(s)" in json.loads(
        cleared.stdout
    )["systemMessage"]


def test_prompt_hook_reports_non_repository_toggle_failure(tmp_path, isolated_state):
    outside_repo = tmp_path / "not-a-repo"
    outside_repo.mkdir()
    payload = {
        "session_id": "root",
        "cwd": str(outside_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-agent on",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "ERROR" in output["systemMessage"]
    assert output["continue"] is False
    assert output["stopReason"] == output["systemMessage"]


def test_prompt_hook_reports_state_write_failure(git_repo, tmp_path):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:stop-continue on acceptRisk",
    }
    env = dict(
        os.environ,
        REVIEWER_CODEX_STATE_DIR="/dev/null/reviewer-agent-codex",
        PYTHONDONTWRITEBYTECODE="1",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "hook_prompt.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["systemMessage"].startswith("stopContinue: ERROR")
    assert output["continue"] is False
    assert output["stopReason"] == output["systemMessage"]


def test_prompt_hook_arms_stop_after_the_enabling_turn(
    git_repo, isolated_state, monkeypatch
):
    payload = {
        "session_id": "root-session",
        "turn_id": "enable-turn",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:stop-continue on acceptRisk",
    }
    result = _run("hook_prompt.py", payload, isolated_state)
    assert result.returncode == 0
    assert state.feature_enabled(git_repo, "stop")

    stop_payload = {
        "session_id": "root-session",
        "turn_id": "enable-turn",
        "cwd": str(git_repo),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "stopContinue: ON",
    }
    assert supervisor.supervise_hook(stop_payload) == {}


def test_stop_activation_marker_does_not_skip_a_later_turn(
    git_repo, isolated_state, monkeypatch
):
    enable_payload = {
        "session_id": "root-session",
        "turn_id": "enable-turn",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:stop-continue on acceptRisk",
    }
    assert _run("hook_prompt.py", enable_payload, isolated_state).returncode == 0
    later_stop = {
        "session_id": "root-session",
        "turn_id": "later-turn",
        "cwd": str(git_repo),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Finished later work.",
    }

    from reviewer_codex import backend
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: (0, "Finish later work.", ""))
    output = supervisor.supervise_hook(later_stop)

    assert output["decision"] == "block"
    assert "Finish later work." in output["reason"]
    assert "stop_continue" not in output["reason"]


def test_subagent_hooks_track_agent_lifetime(git_repo, isolated_state):
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "agent_id": "agent-1",
        "agent_type": "reviewer",
    }
    _run("hook_subagent.py", payload, isolated_state, "start")
    assert [item["kind"] for item in liveness.outstanding(git_repo, "root")] == ["subagent"]
    _run("hook_subagent.py", payload, isolated_state, "stop")
    assert liveness.outstanding(git_repo, "root") == []


def test_prompt_hook_routes_the_history_status_control(git_repo, isolated_state):
    # The namespaced invocation is the ONLY way an operator reaches this action, so exercising
    # controls.apply_history_action directly would leave the route itself untested.
    state.save_review(git_repo, "root", "one", history=True)
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-history status",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["continue"] is False
    assert "reviewerHistory: 1 retained review" in output["stopReason"]
    # A status action must not be a mutation.
    assert len(state.review_history(git_repo)) == 1


def test_prompt_hook_routes_the_history_clear_control(git_repo, isolated_state):
    state.save_review(git_repo, "root", "purge me", history=True)
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-history clear",
    }

    result = _run("hook_prompt.py", payload, isolated_state)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "reviewerHistory: cleared" in output["stopReason"]
    assert state.review_history(git_repo) == []
    assert not state.last_review_file(git_repo, "root").exists()


def test_prompt_hook_does_not_treat_a_history_control_as_operator_context(
    git_repo, isolated_state
):
    # Control invocations end the turn without a model request; recording them as operator
    # prompts would replay plugin syntax into every later reviewer turn.
    payload = {
        "session_id": "root",
        "cwd": str(git_repo),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "$reviewer-agent-codex:reviewer-history status",
    }

    _run("hook_prompt.py", payload, isolated_state)

    assert state.recent_prompts(git_repo, "root") == []
