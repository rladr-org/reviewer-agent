# SPDX-License-Identifier: MPL-2.0
"""Retired delay commands must not silently continue changing settings."""

import json

import pytest

from reviewer_codex import cli, state
from test_hook_scripts import _run


@pytest.mark.parametrize("value", ["", " status", " 0", " 30", " 300", " clear", " -1", " oops"])
def test_retired_delay_prompt_reports_removal_without_mutating_state(
    git_repo, isolated_state, monkeypatch, value
):
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "300")
    isolated_state.mkdir()
    config = json.dumps({"model": "keep-this", "other": {"x": 1}, "stopContinueDelaySecs": 30})
    state.config_file().write_text(config)
    result = _run("hook_prompt.py", dict(
        cwd=str(git_repo), session_id="retired-delay", hook_event_name="UserPromptSubmit",
        prompt="$reviewer-agent-codex:stop-continue delay" + value,
    ), isolated_state)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False
    assert "removed" in output["stopReason"].lower()
    assert "immediately" in output["stopReason"].lower()
    assert state.config_file().read_text() == config
    assert not state.feature_enabled(git_repo, "stop")


@pytest.mark.parametrize("config", [None, '{"broken":', '[1,2]'])
def test_retired_delay_prompt_does_not_create_or_repair_config(git_repo, isolated_state, config):
    if config is not None:
        isolated_state.mkdir()
        state.config_file().write_text(config)
    result = _run("hook_prompt.py", dict(
        cwd=str(git_repo), session_id="retired-delay", hook_event_name="UserPromptSubmit",
        prompt="$reviewer-agent-codex:stop-continue delay 30",
    ), isolated_state)
    assert "removed" in json.loads(result.stdout)["stopReason"].lower()
    if config is None:
        assert not state.config_file().exists()
    else:
        assert state.config_file().read_text() == config


@pytest.mark.parametrize("args", [[], ["0"], ["status"], ["clear"]])
def test_retired_delay_cli_reports_removal(git_repo, isolated_state, monkeypatch, capsys, args):
    monkeypatch.chdir(git_repo)
    assert cli.main(["stop", "delay"] + args) == 1
    message = capsys.readouterr().err.lower()
    assert "removed" in message and "immediately" in message
    assert not state.config_file().exists()


def test_cli_usage_does_not_advertise_retired_delay(capsys):
    assert cli.main([]) == 2
    assert "stop delay" not in capsys.readouterr().err
