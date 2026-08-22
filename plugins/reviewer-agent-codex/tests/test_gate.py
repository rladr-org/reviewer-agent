# SPDX-License-Identifier: MPL-2.0
import re
import subprocess

import pytest

from reviewer_codex import gate, state


def _payload(repo, command="git commit -m test"):
    return {
        "session_id": "root-session",
        "cwd": str(repo),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": "tool-1",
        "tool_input": {"command": command},
    }


def _enable(repo):
    (repo / "reviewerAgent.md").write_text(
        "Run tests and reject correctness bugs.\n", encoding="utf-8"
    )
    state.set_feature(repo, "reviewer")


def test_non_commit_is_not_intercepted(git_repo, isolated_state):
    assert gate.review_commit(_payload(git_repo, "git status")) is None


def test_disabled_gate_is_inert(git_repo, isolated_state):
    assert gate.review_commit(_payload(git_repo)) is None


def test_plain_commit_requires_an_explicit_repository_before_minting_token(
    git_repo, isolated_state
):
    _enable(git_repo)

    output = gate.review_commit(_payload(git_repo))

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "git -C {} commit".format(git_repo.resolve()) in reason
    assert "review_token=" not in reason


def test_plain_commit_cannot_bypass_enabled_gate_via_hidden_shell_workdir(
    git_repo, tmp_path, isolated_state
):
    _enable(git_repo)
    outer = tmp_path / "disabled-outer-repo"
    subprocess.run(["git", "init", str(outer)], check=True, capture_output=True)

    output = gate.review_commit(_payload(outer))

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "explicit absolute repository target" in reason
    assert "review_token=" not in reason


@pytest.mark.parametrize("target", [".", "relative", "~/repo", "$REPO"])
def test_relative_git_c_cannot_mint_a_repository_token(
    git_repo, isolated_state, target
):
    _enable(git_repo)

    output = gate.review_commit(
        _payload(git_repo, "git -C {} commit -m test".format(target))
    )

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "explicit absolute repository target" in reason
    assert "review_token=" not in reason


@pytest.mark.parametrize("later_target", ["$REPO", "~/repo", "$(pwd)"])
def test_shell_dependent_later_git_c_cannot_mint_a_token(
    git_repo, isolated_state, later_target
):
    _enable(git_repo)
    command = "git -C {} -C {} commit -m test".format(git_repo, later_target)

    output = gate.review_commit(_payload(git_repo, command))

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "explicit absolute repository target" in reason
    assert "review_token=" not in reason


def test_explicit_repository_commit_is_immediately_redirected_to_blocking_tool(
    git_repo, isolated_state
):
    _enable(git_repo)

    output = gate.review_commit(
        _payload(git_repo, "git -C {} commit -m test".format(git_repo))
    )

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert "review_commit" in reason
    assert "cwd={}".format(git_repo.resolve()) in reason
    assert "poll" not in reason.lower()
    token = re.search(r"review_token=([a-f0-9]+)", reason).group(1)
    ticket = state.claim_commit_ticket(token, git_repo)
    assert ticket["session_id"] == "root-session"
    assert ticket["root"] == str(git_repo.resolve())


def test_git_c_commit_is_redirected_for_its_effective_repository(
    git_repo, tmp_path, isolated_state
):
    _enable(git_repo)
    outside = tmp_path / "outside"
    outside.mkdir()

    output = gate.review_commit(
        _payload(outside, "git -C {} commit -m test".format(git_repo))
    )

    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
