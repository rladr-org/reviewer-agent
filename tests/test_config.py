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

import pytest

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
                "REVIEWER_CODEX_BIN", "REVIEWER_CODEX_MODEL", "REVIEWER_PI_BIN",
                "REVIEWER_SESSION_AUDIT_LIMIT",
                "REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS", "REVIEWER_IDLE_TIMEOUT_SECS"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_apply_with_no_config_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert rl._bg_shell_stale_secs() == 12 * 3600
    assert rl._monitor_stale_secs() == 3600
    assert rl._async_agent_stale_secs() == 4 * 3600
    assert rl.commit_review_timeout() == 1800
    assert rl.first_output_timeout() == 30
    assert rl.idle_timeout() == 900
    assert rl.max_nudges() == 8
    assert rl.stopcontinue_effort() == "low"
    assert rl.reviewer_session_audit_limit() == 3


def test_config_file_sets_every_tunable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {
        "bgShellStaleSecs": 30 * 3600,
        "monitorStaleSecs": 900,
        "asyncAgentStaleSecs": 8 * 3600,
        "commitReviewTimeoutSecs": 1800,
        "firstOutputTimeoutSecs": 45,
        "idleTimeoutSecs": 300,
        "maxNudges": 3,
        "stopContinueEffort": "medium",
        "codexBin": "/opt/codex",
        "piBin": "/opt/pi",
        "model": "gpt-5.4",
        "reviewerSessionAuditLimit": 6,
    })
    assert rl._bg_shell_stale_secs() == 30 * 3600
    assert rl._monitor_stale_secs() == 900
    assert rl._async_agent_stale_secs() == 8 * 3600
    assert rl.commit_review_timeout() == 1800
    assert rl.first_output_timeout() == 45
    assert rl.idle_timeout() == 300
    assert rl.max_nudges() == 3
    assert rl.stopcontinue_effort() == "medium"
    assert rl.codex_bin() == "/opt/codex"
    assert rl.pi_bin() == "/opt/pi"
    assert rl.codex_model() == "gpt-5.4"
    assert rl.reviewer_session_audit_limit() == 6


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
    assert rl.commit_review_timeout_detail() == (1800, "default")

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


# --------------------------------------------------------------------------
# The two codex-streaming idle thresholds resolve exactly like commit_review_timeout:
# env > state file (/reviewerModel) > config.json > default, each candidate clamped to its own
# floor and an unusable one skipped. Parametrised over the actual resolver FUNCTIONS (not name
# strings, which would be an unresolvable dynamic facade access) so both knobs are held to the
# identical contract. (docs/plans/codex_jsonl_streaming.md, Task 2.)
# --------------------------------------------------------------------------
# (value_fn, env var, config.json key, default, floor)
_STREAMING_KNOBS = [
    (rl.first_output_timeout, "REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS", "firstOutputTimeoutSecs",
     30, 5),
    (rl.idle_timeout, "REVIEWER_IDLE_TIMEOUT_SECS", "idleTimeoutSecs", 900, 60),
]


@pytest.mark.parametrize("value_fn,env_var,config_key,default,floor", _STREAMING_KNOBS)
def test_streaming_timeout_env_over_config_over_default(
        tmp_path, monkeypatch, value_fn, env_var, config_key, default, floor):
    _isolate(tmp_path, monkeypatch)
    assert value_fn() == default

    _cfg(tmp_path, monkeypatch, {config_key: default + 100})
    assert value_fn() == default + 100

    monkeypatch.setenv(env_var, str(default + 200))
    assert value_fn() == default + 200


@pytest.mark.parametrize("value_fn,env_var,config_key,default,floor", _STREAMING_KNOBS)
def test_streaming_timeout_junk_falls_through_and_small_is_floored(
        tmp_path, monkeypatch, value_fn, env_var, config_key, default, floor):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {config_key: default + 100})
    for junk in ("0", "-5", "abc", "90.5"):
        monkeypatch.setenv(env_var, junk)
        assert value_fn() == default + 100, junk
    monkeypatch.setenv(env_var, "1")  # meaningful but below the floor
    assert value_fn() == floor


@pytest.mark.parametrize("detail_fn,set_fn,clear_fn,env_var,default", [
    (rl.first_output_timeout_detail, rl.set_first_output_timeout,
     rl.clear_first_output_timeout, "REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS", 30),
    (rl.idle_timeout_detail, rl.set_idle_timeout,
     rl.clear_idle_timeout, "REVIEWER_IDLE_TIMEOUT_SECS", 900),
])
def test_streaming_timeout_detail_reports_the_source_that_won(
        tmp_path, monkeypatch, detail_fn, set_fn, clear_fn, env_var, default):
    _isolate(tmp_path, monkeypatch)
    assert detail_fn() == (default, "default")

    set_fn(default + 500)
    assert detail_fn()[0] == default + 500

    monkeypatch.setenv(env_var, str(default + 900))
    secs, source = detail_fn()
    assert (secs, env_var in source) == (default + 900, True), source

    # invalid env -> the state file wins, and the source must NOT name the env var
    monkeypatch.setenv(env_var, "abc")
    secs, source = detail_fn()
    assert secs == default + 500
    assert env_var not in source, source

    clear_fn()
    monkeypatch.delenv(env_var, raising=False)
    assert detail_fn() == (default, "default")


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
    assert rl.max_nudges() == 8
    assert rl._bg_shell_stale_secs() == 12 * 3600


def test_nonsense_values_fall_back_to_defaults(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"bgShellStaleSecs": "not-a-number", "maxNudges": None})
    assert rl._bg_shell_stale_secs() == 12 * 3600
    assert rl.max_nudges() == 8


def test_invalid_model_in_config_is_ignored(tmp_path, monkeypatch):
    # Same validation as every other model source: a malformed token must never reach
    # `-c model="..."`.
    _isolate(tmp_path, monkeypatch)
    _cfg(tmp_path, monkeypatch, {"model": "bad model with spaces"})
    assert rl.codex_model() == rl.DEFAULT_CODEX_MODEL


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


# #4 (Linux): an npm-global codex lives in /usr/local/bin, not ~/.local/bin, and was never found.
def test_codex_is_found_on_path_when_not_in_local_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_BIN", raising=False)
    monkeypatch.delenv("REVIEWER_CONFIG_FILE", raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no ~/.local/bin/codex
    bindir = tmp_path / "npm-global"
    bindir.mkdir()
    codex = bindir / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert rl.codex_bin() == str(codex)


def test_local_bin_still_wins_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_BIN", raising=False)
    monkeypatch.delenv("REVIEWER_CONFIG_FILE", raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    local = home / ".local" / "bin" / "codex"
    local.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(home))
    bindir = tmp_path / "npm-global"  # a second codex on PATH must not win
    bindir.mkdir()
    (bindir / "codex").write_text("#!/bin/sh\n")
    (bindir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert rl.codex_bin() == str(local)
