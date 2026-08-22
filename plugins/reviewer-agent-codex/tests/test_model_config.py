# SPDX-License-Identifier: MPL-2.0
import json

from reviewer_codex import cli, state


def _write_config(isolated_state, **values):
    isolated_state.mkdir(parents=True, exist_ok=True)
    (isolated_state / "config.json").write_text(json.dumps(values), encoding="utf-8")


def test_reviewer_model_defaults_to_luna_xhigh(isolated_state, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)

    assert state.reviewer_model_detail() == ("gpt-5.6-luna", "default")
    assert state.reviewer_effort_detail() == ("xhigh", "default")


def test_commit_review_timeout_defaults_to_one_hour(isolated_state, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS", raising=False)

    assert state.commit_review_timeout_detail() == (3600, "default")


def test_commit_review_timeout_can_be_configured_and_environment_wins(
    isolated_state, monkeypatch
):
    monkeypatch.delenv("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS", raising=False)
    _write_config(isolated_state, commitReviewTimeoutSeconds=2700)

    assert state.commit_review_timeout_detail() == (
        2700,
        "config.json commitReviewTimeoutSeconds",
    )

    monkeypatch.setenv("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS", "4200")
    assert state.commit_review_timeout_detail() == (
        4200,
        "env REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS",
    )


def test_invalid_commit_review_timeouts_fall_back_safely(
    isolated_state, monkeypatch
):
    _write_config(isolated_state, commitReviewTimeoutSeconds=7201)
    monkeypatch.setenv("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS", "not-a-number")

    assert state.commit_review_timeout_detail() == (3600, "default")


def test_review_history_retention_defaults_and_refuses_a_zero_cap(
    isolated_state, monkeypatch
):
    monkeypatch.delenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", raising=False)

    assert state.review_history_keep() == state.REVIEW_HISTORY_KEEP

    monkeypatch.setenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", "0")
    assert state.review_history_keep() == 1

    monkeypatch.setenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", "not-a-number")
    assert state.review_history_keep() == state.REVIEW_HISTORY_KEEP


def test_review_history_retention_can_be_configured_and_environment_wins(
    isolated_state, monkeypatch
):
    monkeypatch.delenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", raising=False)
    _write_config(isolated_state, reviewHistoryKeep=250)

    assert state.review_history_keep() == 250

    monkeypatch.setenv("REVIEWER_CODEX_REVIEW_HISTORY_KEEP", "12")
    assert state.review_history_keep() == 12


def test_config_json_overrides_model_and_effort(isolated_state, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)
    _write_config(isolated_state, model="gpt-5.6-sol", reasoningEffort="max")

    assert state.reviewer_model_detail() == ("gpt-5.6-sol", "config.json model")
    assert state.reviewer_effort_detail() == ("max", "config.json reasoningEffort")


def test_command_overrides_config_and_environment_overrides_command(isolated_state, monkeypatch):
    _write_config(isolated_state, model="config-model", reasoningEffort="medium")
    state.set_reviewer_model("command-model")
    state.set_reviewer_effort("high")

    assert state.reviewer_model() == "command-model"
    assert state.reviewer_effort() == "high"

    monkeypatch.setenv("REVIEWER_CODEX_MODEL", "environment-model")
    monkeypatch.setenv("REVIEWER_CODEX_EFFORT", "ultra")
    assert state.reviewer_model_detail() == (
        "environment-model",
        "env REVIEWER_CODEX_MODEL",
    )
    assert state.reviewer_effort_detail() == (
        "ultra",
        "env REVIEWER_CODEX_EFFORT",
    )


def test_invalid_candidates_fall_through_without_reaching_codex(isolated_state, monkeypatch):
    _write_config(isolated_state, model="bad model", reasoningEffort="impossible")
    monkeypatch.setenv("REVIEWER_CODEX_MODEL", 'bad"model')
    monkeypatch.setenv("REVIEWER_CODEX_EFFORT", "maximum-est")

    assert state.reviewer_model_detail() == ("gpt-5.6-luna", "default")
    assert state.reviewer_effort_detail() == ("xhigh", "default")


def test_reviewer_model_cli_sets_shows_and_clears_overrides(
    isolated_state, monkeypatch, capsys
):
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)

    assert cli.main(["model", "gpt-5.6-sol", "max"]) == 0
    assert capsys.readouterr().out.strip() == (
        "reviewerModel: gpt-5.6-sol max (model from command override; "
        "effort from command override)"
    )

    assert cli.main(["model"]) == 0
    assert "reviewerModel: gpt-5.6-sol max" in capsys.readouterr().out

    assert cli.main(["model", "effort", "xhigh"]) == 0
    assert state.reviewer_effort() == "xhigh"
    capsys.readouterr()

    assert cli.main(["model", "clear"]) == 0
    assert state.reviewer_model() == "gpt-5.6-luna"
    assert state.reviewer_effort() == "xhigh"


def test_reviewer_model_cli_rejects_invalid_values_without_partial_write(
    isolated_state, capsys
):
    assert cli.main(["model", "bad model", "max"]) == 2
    assert "invalid model" in capsys.readouterr().err
    assert state.reviewer_model() == "gpt-5.6-luna"
    assert state.reviewer_effort() == "xhigh"

    assert cli.main(["model", "gpt-5.6-sol", "impossible"]) == 2
    assert "invalid reasoning effort" in capsys.readouterr().err
    assert state.reviewer_model() == "gpt-5.6-luna"
    assert state.reviewer_effort() == "xhigh"
