# SPDX-License-Identifier: MPL-2.0
import json

import pytest

from reviewer_codex import cli, state
from test_hook_scripts import _run


def command(repo, isolated_state, value=""):
    return _run("hook_prompt.py", dict(cwd=str(repo), session_id="delay-settings",
                hook_event_name="UserPromptSubmit",
                prompt="$reviewer-agent-codex:stop-continue delay" + value), isolated_state)


def test_delay_command_persists_config_without_enabling_supervision(git_repo, isolated_state, monkeypatch):
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS")
    isolated_state.mkdir()
    state.config_file().write_text(json.dumps({"model": "keep-this", "other": {"x": 1}}))
    result = command(git_repo, isolated_state, " 30")
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["continue"] is False
    assert "30" in output["stopReason"]
    assert state.load_config() == {"model": "keep-this", "other": {"x": 1}, "stopContinueDelaySecs": 30}
    assert state.stopcontinue_delay_secs() == 30
    assert not state.feature_enabled(git_repo, "stop")
    assert "30" in json.loads(command(git_repo, isolated_state).stdout)["stopReason"]
    assert "30" in json.loads(command(git_repo, isolated_state, " status").stdout)["stopReason"]
    command(git_repo, isolated_state, " clear")
    assert state.load_config() == {"model": "keep-this", "other": {"x": 1}}
    assert state.stopcontinue_delay_secs() == 15


@pytest.mark.parametrize("value", ["-1", "301", "1.5", "oops", "30 extra"])
def test_invalid_delay_command_does_not_write_config(git_repo, isolated_state, value):
    result = command(git_repo, isolated_state, " " + value)
    assert "ERROR" in json.loads(result.stdout)["stopReason"]
    assert not state.config_file().exists()


def test_delay_command_reports_environment_override(git_repo, isolated_state, monkeypatch):
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "5")
    result = command(git_repo, isolated_state, " 30")
    message = json.loads(result.stdout)["stopReason"]
    assert "30" in message and "5" in message and "REVIEWER_STOPCONTINUE_DELAY_SECS" in message
    assert state.load_config()["stopContinueDelaySecs"] == 30


@pytest.mark.parametrize("config", ['{"broken":', '[1,2]'])
def test_delay_command_does_not_replace_invalid_config(git_repo, isolated_state, config):
    isolated_state.mkdir()
    state.config_file().write_text(config)
    assert "ERROR" in json.loads(command(git_repo, isolated_state, " 30").stdout)["stopReason"]
    assert state.config_file().read_text() == config


def test_delay_cli_uses_same_setting(git_repo, isolated_state, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS")
    assert cli.main(["stop", "delay", "0"]) == 0
    assert state.stopcontinue_delay_secs() == 0
    assert cli.main(["stop", "delay"]) == 0
    assert "0" in capsys.readouterr().out
