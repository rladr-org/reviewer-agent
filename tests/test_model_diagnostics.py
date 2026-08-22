# SPDX-License-Identifier: MPL-2.0
"""A rejected model or effort must say so, and say what to type.

The failure mode this closes: `/reviewerModel` accepts any well-formed token, and the reviewer
then fails on every call because the account cannot reach that model, or because the level is
not one that model accepts. The gate is fail-closed, so the symptom is "every commit is
blocked" with a provider error buried in a stderr tail -- and the remedy (`/reviewerModel`) is
never named. Local validation cannot prevent this: whether a model is reachable is a property of
the account, not of the string.

So the diagnosis happens where the evidence is: the reviewer's own failure output.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
COMMIT_HOOK = str(ROOT / "commit_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    return str(repo)


# --------------------------------------------------------------------------
# recognising a provider rejection
# --------------------------------------------------------------------------
@pytest.mark.parametrize("stderr", [
    "stream error: unexpected status 400 Bad Request: "
    '{"error":{"message":"Invalid value: \'ultra\'","code":"invalid_enum_value",'
    '"param":"reasoning.effort"}}',
    "The model `gpt-5.6-luna` does not exist or you do not have access to it.",
    'unexpected status 404 Not Found: {"error":{"code":"model_not_found"}}',
    "unexpected status 403 Forbidden: your account does not have access to this model",
    "error: unknown model: gpt-5.6-luna",
    # Verbatim from codex-cli 0.147.0 with an unusable model. The release matrix caught that
    # nothing here matched it, which is the whole reason this line exists.
    'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
    '"message":"The \'gpt-5.6-luna\' model is not supported when using Codex with a ChatGPT '
    'account."}}',
])
def test_provider_rejections_are_recognised(stderr):
    assert rl.model_rejection_detail("codex", stderr)


@pytest.mark.parametrize("stderr", [
    "",
    "connection reset by peer",
    "error: repository not found",
    "panic: something unrelated went wrong",
    "rate limit exceeded, please retry",
    # Access refusals that are NOT about a model. Sending an operator to change model settings
    # for a repository or organization permission problem is worse than saying nothing.
    "you do not have access to this repository",
    "your account does not have access to this organization",
    "403 Forbidden: the installation does not have access to the requested resource",
    # The benign companion of the real refusal: codex warns about the service tier for any
    # model whose catalogue entry advertises none, and then runs perfectly well. Treating this
    # as a configuration failure would send people to change a working selection.
    "warning: Configured service tier `priority` is not advertised as supported for model "
    "`gpt-5.6-luna` and will be omitted from requests.",
    "warning: Model metadata for `gpt-5.6-luna` not found. Defaulting to fallback metadata;",
])
def test_ordinary_failures_are_not_misreported_as_rejections(stderr):
    # A false positive is its own bug: sending an operator to `/reviewerModel` for a network
    # blip wastes the one place the message had to be right.
    assert rl.model_rejection_detail("codex", stderr) is None


def test_the_diagnostic_names_the_command_the_model_and_where_it_came_from(home, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("REVIEWER_CODEX_EFFORT", "ultra")

    message = rl.model_rejection_message("codex", "unknown model: gpt-5.6-luna")

    assert "/reviewerModel" in message
    assert "gpt-5.6-luna" in message
    assert "ultra" in message
    assert "REVIEWER_CODEX_MODEL" in message  # the SOURCE, so the right place gets edited


def test_the_pi_diagnostic_names_pis_own_settings(home, monkeypatch):
    monkeypatch.setenv("REVIEWER_PI_MODEL", "some-pi-model")

    message = rl.model_rejection_message("pi", "unknown model: some-pi-model")

    assert "/reviewerModel" in message
    assert "some-pi-model" in message
    assert "REVIEWER_PI_MODEL" in message


# --------------------------------------------------------------------------
# the gate surfaces it
# --------------------------------------------------------------------------
def test_a_rejected_model_blocks_with_an_actionable_reason_not_a_bare_exit_code(
    home, tmp_path, monkeypatch
):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-m", "sid-1")
    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": str(rl.reviewer_home()),
        "REVIEWER_CODEX_BIN": FAKE,
        "REVIEWER_CODEX_MODEL": "gpt-5.6-luna",
        "FAKE_CODEX_EXIT": "1",
        "FAKE_CODEX_STDERR": "The model `gpt-5.6-luna` does not exist or you do not have "
                             "access to it.",
    })
    payload = {
        "cwd": repo,
        "session_id": "claude-m",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }

    result = subprocess.run(
        [sys.executable, COMMIT_HOOK], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "/reviewerModel" in reason
    assert "gpt-5.6-luna" in reason
    assert out["systemMessage"].count("/reviewerModel") >= 1


def test_an_ordinary_reviewer_failure_keeps_its_plain_message(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-m", "sid-1")
    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": str(rl.reviewer_home()),
        "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_EXIT": "1",
        "FAKE_CODEX_STDERR": "connection reset by peer",
    })
    payload = {
        "cwd": repo,
        "session_id": "claude-m",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }

    result = subprocess.run(
        [sys.executable, COMMIT_HOOK], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )

    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "connection reset" in reason
    assert "/reviewerModel" not in reason


def test_a_rejection_recorded_in_history_carries_the_diagnosis(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-m", "sid-1")
    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": str(rl.reviewer_home()),
        "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_EXIT": "1",
        "FAKE_CODEX_STDERR": "unexpected status 404 Not Found: model_not_found",
    })
    payload = {
        "cwd": repo,
        "session_id": "claude-m",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }

    subprocess.run(
        [sys.executable, COMMIT_HOOK], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )

    texts = [path.read_text(encoding="utf-8") for path in rl.review_history(repo)]
    assert any("/reviewerModel" in text for text in texts), texts


def test_a_briefing_refusal_is_diagnosed_before_any_session_exists(home, tmp_path):
    # The hardest case to see and the most important to name: a refused model fails at the
    # BRIEF, so no session is ever persisted and every retry fails identically. This path used
    # to report only "could not start the reviewer".
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": str(rl.reviewer_home()),
        "REVIEWER_CODEX_BIN": FAKE,
        "REVIEWER_CODEX_MODEL": "gpt-5.6-luna",
        "FAKE_CODEX_EXIT": "1",
        "FAKE_CODEX_STDERR": "unexpected status 404 Not Found: model_not_found",
    })
    payload = {
        "cwd": repo,
        "session_id": "claude-brief-refused",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }

    result = subprocess.run(
        [sys.executable, COMMIT_HOOK], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "/reviewerModel" in reason
    assert "gpt-5.6-luna" in reason
    assert rl.read_session_id(repo, "claude-brief-refused") is None
    texts = [path.read_text(encoding="utf-8") for path in rl.review_history(repo)]
    assert any("/reviewerModel" in text for text in texts), texts
