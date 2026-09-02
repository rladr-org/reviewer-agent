# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "toggle.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


def run_toggle(action, proj, home, codex_env=None):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    env["REVIEWER_CODEX_BIN"] = FAKE
    if codex_env:
        env.update(codex_env)
    args = [action] if isinstance(action, str) else list(action)
    return subprocess.run(
        [sys.executable, TOGGLE, *args], capture_output=True, text=True, env=env
    )


def make_repo(tmp_path, with_brief=True):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    if with_brief:
        (proj / "reviewerAgent.md").write_text("You are a strict reviewer.\n")
    return str(proj)


def flag_exists(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.is_active(proj)


def test_first_on_requires_explicit_risk_acceptance_without_mutating_state(
    tmp_path, monkeypatch
):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    settings = tmp_path / "user_settings.json"
    p = run_toggle("on", proj, home, {"REVIEWER_SETTINGS_FILE": str(settings)})

    assert p.returncode != 0
    assert not flag_exists(home, proj, monkeypatch)
    assert not settings.exists()
    assert (
        "Before enabling reviewerAgent you must accept the risk as described in "
        "DISCLAIMER.md by invoking:" in (p.stdout + p.stderr)
    )
    assert "/reviewerAgent on acceptRisk" in (p.stdout + p.stderr)


def test_on_accept_risk_creates_flag_in_git_repo_with_brief(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run_toggle(
        ["on", "acceptRisk"],
        proj,
        home,
        {"FAKE_CODEX_STDOUT": "ack", "FAKE_CODEX_EXIT": "0"},
    )
    assert p.returncode == 0, p.stderr
    assert flag_exists(home, proj, monkeypatch)
    assert "ON" in p.stdout
    assert "USE AT YOUR OWN RISK" in p.stdout
    assert "Full terms:" in p.stdout


def test_off_removes_flag(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    run_toggle(["on", "acceptRisk"], proj, home, {"FAKE_CODEX_STDOUT": "ack"})
    assert flag_exists(home, proj, monkeypatch)
    p = run_toggle("off", proj, home)
    assert p.returncode == 0
    assert not flag_exists(home, proj, monkeypatch)
    assert "OFF" in p.stdout


def test_on_fails_when_not_a_git_repo(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = tmp_path / "plain"
    proj.mkdir()
    (proj / "reviewerAgent.md").write_text("x")
    p = run_toggle("on", str(proj), home)
    assert p.returncode != 0
    assert not flag_exists(home, str(proj), monkeypatch)
    assert "ERROR" in (p.stdout + p.stderr)


def test_on_fails_when_brief_missing(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path, with_brief=False)
    p = run_toggle("on", proj, home, {"FAKE_CODEX_STDOUT": "ack"})
    assert p.returncode != 0
    assert not flag_exists(home, proj, monkeypatch)
    assert "ERROR" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------
# `on` is INSTANT: it performs only local validation and never runs the codex brief.
# Briefing is lazy -- the first hook that needs the reviewer calls ensure_session(), and
# SessionStart briefs for already-active projects. Previously `on` ran a full brief turn,
# which made enabling the reviewer on a large repo take minutes.
# --------------------------------------------------------------------------
def test_on_does_not_run_the_codex_brief(tmp_path, monkeypatch):
    # A codex that would FAIL LOUDLY if invoked (nonexistent binary -> exit 127, plus an
    # exit-5 stub env). `on` must still succeed: that is only possible if no brief ran.
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run_toggle(
        ["on", "acceptRisk"],
        proj,
        home,
        {
            "REVIEWER_CODEX_BIN": str(tmp_path / "no-such-codex-binary"),
            "FAKE_CODEX_EXIT": "5",
            "FAKE_CODEX_STDERR": "brief boom",
        },
    )
    assert p.returncode == 0, p.stderr
    assert flag_exists(home, proj, monkeypatch)
    assert "ON" in p.stdout
    assert "boom" not in (p.stdout + p.stderr)


def test_on_tells_the_operator_briefing_is_lazy(tmp_path):
    # The operator must know the reviewer is not briefed yet, so the first commit it gates is
    # expected to take a moment. reviewerAgent gates commits; it must NOT claim to gate questions
    # -- AskUserQuestion rerouting is a separately-enabled stopContinue feature, not reviewerAgent.
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run_toggle(["on", "acceptRisk"], proj, home, {"FAKE_CODEX_STDOUT": "ack"})
    assert p.returncode == 0, p.stderr
    assert "first" in p.stdout.lower()
    assert "commit" in p.stdout.lower()
    assert "question" not in p.stdout.lower()  # reviewerAgent no longer advertises question answering


# --------------------------------------------------------------------------
# `on` no longer touches the user's settings file. AskUserQuestion answering moved off the
# host `askUserQuestionTimeout` auto-close (which stopped firing) onto the stopContinue
# PreToolUse reroute, so enabling the reviewer arms nothing in ~/.claude/settings.json.
# --------------------------------------------------------------------------
def test_on_does_not_touch_user_settings(tmp_path):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    settings = tmp_path / "user_settings.json"
    p = run_toggle(
        ["on", "acceptRisk"], proj, home, {"REVIEWER_SETTINGS_FILE": str(settings)}
    )
    assert p.returncode == 0, p.stderr
    assert not settings.exists()  # enabling wrote no settings file
    assert "askUserQuestionTimeout" not in p.stdout + p.stderr


def test_on_does_not_persist_a_session_id(tmp_path, monkeypatch):
    # No brief at toggle time => no captured codex session id. ensure_session() creates it
    # lazily later, keyed to the Claude session that needs it.
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    run_toggle(
        ["on", "acceptRisk"], proj, home,
        {"FAKE_CODEX_STDOUT": "ack", "FAKE_CODEX_SESSION_ID": "codex-X"}
    )
    monkeypatch.setenv("REVIEWER_HOME", home)
    state = Path(home) / "state"
    ids = list(state.glob("session-*")) if state.exists() else []
    assert ids == [], "toggle must not capture/persist a reviewer session id: {}".format(ids)
