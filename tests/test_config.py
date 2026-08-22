# SPDX-License-Identifier: MPL-2.0
"""One settings file for the reviewer's tunables.

Before this, every knob was an environment variable (plus state files for model/backend),
which meant a hook subprocess could only be configured by exporting vars before launching
Claude Code -- impractical. Config lives at <state dir>/config.json.

Precedence is env > state file (set by the slash commands) > config.json > built-in default:
an env var stays a one-off override, an explicit runtime toggle beats a static default, and
config.json supplies the defaults.
"""
import json

import reviewer_lib as rl


def _cfg(tmp_path, monkeypatch, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(p))
    return p


def _isolate(tmp_path, monkeypatch):
    """No config file, no stray env from the developer's own shell."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    for var in ("REVIEWER_BG_SHELL_STALE_SECS", "REVIEWER_MONITOR_STALE_SECS",
                "REVIEWER_ASYNC_AGENT_STALE_SECS", "REVIEWER_COMMIT_TIMEOUT_SECS",
                "REVIEWER_STOPCONTINUE_MAX_NUDGES", "REVIEWER_STOPCONTINUE_EFFORT",
                "REVIEWER_CODEX_BIN", "REVIEWER_CODEX_MODEL", "REVIEWER_PI_BIN"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_apply_with_no_config_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert rl._bg_shell_stale_secs() == 12 * 3600
    assert rl._monitor_stale_secs() == 3600
    assert rl._async_agent_stale_secs() == 4 * 3600
    assert rl.commit_review_timeout() == 900
    assert rl.max_nudges() == 10
    assert rl.stopcontinue_effort() == "low"


def test_config_file_sets_every_tunable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {
        "bgShellStaleSecs": 30 * 3600,
        "monitorStaleSecs": 900,
        "asyncAgentStaleSecs": 8 * 3600,
        "commitReviewTimeoutSecs": 1800,
        "maxNudges": 3,
        "stopContinueEffort": "medium",
        "codexBin": "/opt/codex",
        "piBin": "/opt/pi",
        "model": "gpt-5.4",
    })
    assert rl._bg_shell_stale_secs() == 30 * 3600
    assert rl._monitor_stale_secs() == 900
    assert rl._async_agent_stale_secs() == 8 * 3600
    assert rl.commit_review_timeout() == 1800
    assert rl.max_nudges() == 3
    assert rl.stopcontinue_effort() == "medium"
    assert rl.codex_bin() == "/opt/codex"
    assert rl.pi_bin() == "/opt/pi"
    assert rl.codex_model() == "gpt-5.4"


def test_commit_timeout_env_overrides_the_config_file(tmp_path, monkeypatch):
    # Raising it for a genuinely large audit is the whole point of the knob.
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"commitReviewTimeoutSecs": 1200})
    monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", "3600")
    assert rl.commit_review_timeout() == 3600


def test_commit_timeout_junk_falls_through_but_a_small_value_is_floored(tmp_path, monkeypatch):
    # Two DIFFERENT protections, and the distinction matters because the commit gate is
    # fail-CLOSED -- a bad timeout blocks every commit rather than merely degrading:
    #   * a MEANINGLESS value (0, negative, non-numeric) is skipped entirely, falling through
    #     to the next configured source, exactly as an invalid model name does. Clamping it to
    #     the 60s floor would be worse than honouring the operator's real 1200s setting.
    #   * a value that is meaningful but too SMALL is raised to the floor, because there the
    #     operator did express a number and the floor is the only thing standing between a
    #     typo and a gate that times out before any audit can finish.
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"commitReviewTimeoutSecs": 1200})
    for junk in ("0", "-5", "abc", "90.5"):
        monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", junk)
        assert rl.commit_review_timeout() == 1200, junk
    monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", "30")
    assert rl.commit_review_timeout() == 60


def test_commit_timeout_detail_reports_the_source_that_actually_won(tmp_path, monkeypatch):
    # One resolver, so the value and its reported source can never disagree -- the display
    # duplicating the precedence rules is exactly how it drifted.
    _isolate(tmp_path, monkeypatch)
    assert rl.commit_review_timeout_detail() == (900, "default")

    _cfg(tmp_path, monkeypatch, {"commitReviewTimeoutSecs": 1200})
    secs, source = rl.commit_review_timeout_detail()
    assert (secs, "config" in source) == (1200, True), source

    rl.set_commit_timeout(1500)
    assert rl.commit_review_timeout_detail()[0] == 1500

    monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", "2400")
    secs, source = rl.commit_review_timeout_detail()
    assert (secs, "REVIEWER_COMMIT_TIMEOUT_SECS" in source) == (2400, True), source

    # Invalid env -> the state file wins, and the source must say so.
    monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", "abc")
    secs, source = rl.commit_review_timeout_detail()
    assert secs == 1500
    assert "REVIEWER_COMMIT_TIMEOUT_SECS" not in source, source


def test_env_overrides_the_config_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"bgShellStaleSecs": 100, "codexBin": "/opt/codex"})
    monkeypatch.setenv("REVIEWER_BG_SHELL_STALE_SECS", "7200")
    monkeypatch.setenv("REVIEWER_CODEX_BIN", "/env/codex")
    assert rl._bg_shell_stale_secs() == 7200
    assert rl.codex_bin() == "/env/codex"


def test_slash_command_state_file_beats_config_for_model(tmp_path, monkeypatch):
    # /reviewerModel is an explicit runtime choice; config.json only supplies the default.
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"model": "config-model"})
    assert rl.codex_model() == "config-model"
    rl.set_codex_model("runtime-model")
    assert rl.codex_model() == "runtime-model"
    monkeypatch.setenv("REVIEWER_CODEX_MODEL", "env-model")
    assert rl.codex_model() == "env-model"


def test_malformed_config_falls_back_to_defaults(tmp_path, monkeypatch):
    # A hand-edited config with a syntax error must never wedge the hooks.
    _isolate(tmp_path, monkeypatch)
    p = tmp_path / "config.json"
    p.write_text('{"maxNudges": 3,}', encoding="utf-8")
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(p))
    assert rl.max_nudges() == 10
    assert rl._bg_shell_stale_secs() == 12 * 3600


def test_nonsense_values_fall_back_to_defaults(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"bgShellStaleSecs": "not-a-number", "maxNudges": None})
    assert rl._bg_shell_stale_secs() == 12 * 3600
    assert rl.max_nudges() == 10


def test_invalid_model_in_config_is_ignored(tmp_path, monkeypatch):
    # Same validation as every other model source: a malformed token must never reach
    # `-c model="..."`.
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"model": "bad model with spaces"})
    assert rl.codex_model() is None


def test_config_selects_the_backend(tmp_path, monkeypatch):
    # Until now `pi` could only be selected by an env var or a hand-written state file --
    # there is no slash command for it.
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    assert rl.reviewer_backend("/Users/x/proj") == "codex"
    _cfg(tmp_path, monkeypatch, {"backend": "pi"})
    assert rl.reviewer_backend("/Users/x/proj") == "pi"


def test_invalid_backend_in_config_is_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    _cfg(tmp_path, monkeypatch, {"backend": "not-a-backend"})
    assert rl.reviewer_backend("/Users/x/proj") == "codex"


def test_config_lives_in_the_state_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CONFIG_FILE", raising=False)
    assert rl.config_file() == rl.reviewer_state_home() / "config.json"
