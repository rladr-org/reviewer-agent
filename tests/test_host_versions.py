# SPDX-License-Identifier: MPL-2.0
"""Minimum host versions: stated, detected, and warned about -- never silently assumed.

This package rides on host behaviour that has changed before: plugin layout, hook payload
fields, MCP wiring. A user on an older Claude Code or Codex than the release was validated
against does not see "unsupported host"; they see a reviewer that never fires, or fires and
fails, in a tool whose whole job is to block their commits. That is the second most likely
first-run failure after an unreachable model, and the cheapest to name.

Warn, do not fail. The floors are the oldest versions this release was actually exercised on,
not the oldest that can work -- refusing to run below them would block users whose host is
probably fine, on evidence nobody has.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import host_versions as hv  # noqa: E402

pytestmark = pytest.mark.smoke  # install-time subset (tests/test_smoke_subset.py)


# --------------------------------------------------------------------------
# parsing real version output
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("2.1.238 (Claude Code)", (2, 1, 238)),
    ("claude 2.1.238", (2, 1, 238)),
    ("codex-cli 0.147.0", (0, 147, 0)),
    ("v24.14.1", (24, 14, 1)),
    ("Python 3.9.6", (3, 9, 6)),
    ("1.2.0-beta.1", (1, 2, 0)),
    ("  2.0.1  \n", (2, 0, 1)),
])
def test_versions_are_parsed_out_of_real_command_output(text, expected):
    assert hv.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "not a version", "unknown", None, "v"])
def test_unparseable_output_yields_no_version_rather_than_a_guess(text):
    # A wrong guess is worse than no answer: it either warns a supported host or silences a
    # real warning. No version means the check says it could not tell.
    assert hv.parse_version(text) is None


# --------------------------------------------------------------------------
# comparing against the floor
# --------------------------------------------------------------------------
def test_a_host_at_or_above_the_floor_produces_no_warning():
    assert hv.version_warning("Claude Code", (2, 1, 238), (2, 1, 238)) is None
    assert hv.version_warning("Claude Code", (2, 2, 0), (2, 1, 238)) is None
    assert hv.version_warning("Claude Code", (3, 0, 0), (2, 1, 238)) is None


def test_an_older_host_is_named_with_both_versions_and_what_it_means():
    warning = hv.version_warning("Claude Code", (2, 0, 9), (2, 1, 238))

    assert warning
    assert "2.0.9" in warning
    assert "2.1.238" in warning
    assert "Claude Code" in warning
    # It must say the install continues, or a warning reads as a refusal.
    assert "continu" in warning.lower()


def test_an_undetectable_version_says_so_without_claiming_a_failure():
    warning = hv.version_warning("Codex", None, (0, 147, 0))

    assert warning
    assert "could not" in warning.lower()
    assert "0.147.0" in warning


# --------------------------------------------------------------------------
# the declared floors
# --------------------------------------------------------------------------
def test_the_declared_floors_are_the_versions_the_release_was_exercised_on():
    assert hv.CLAUDE_CODE_MIN == (2, 1, 238)
    assert hv.CODEX_MIN == (0, 147, 0)
    assert hv.NODE_MIN == (22, 19, 0)
    assert hv.PYTHON_MIN == (3, 9, 0)


def test_the_running_interpreter_satisfies_the_python_floor():
    assert sys.version_info[:2] >= hv.PYTHON_MIN[:2]


# --------------------------------------------------------------------------
# the toggle surfaces it
# --------------------------------------------------------------------------
def test_reviewer_agent_on_warns_about_an_old_host_but_still_enables(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '1.9.0 (Claude Code)'\n", encoding="utf-8")
    fake_claude.chmod(0o755)

    result = subprocess.run(
        [sys.executable, str(ROOT / "toggle.py"), "on", "acceptRisk"],
        capture_output=True, text=True, cwd=str(repo),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "REVIEWER_HOME": str(tmp_path / "rh"),
            "CLAUDE_PROJECT_DIR": str(repo),
            "REVIEWER_CLAUDE_BIN": str(fake_claude),
        },
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "1.9.0" in combined
    assert "2.1.238" in combined
    assert (tmp_path / "rh" / "state").is_dir(), "the reviewer must still be enabled"


def test_reviewer_agent_on_is_quiet_when_the_host_is_current(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n", encoding="utf-8")
    fake_claude.chmod(0o755)

    result = subprocess.run(
        [sys.executable, str(ROOT / "toggle.py"), "on", "acceptRisk"],
        capture_output=True, text=True, cwd=str(repo),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "REVIEWER_HOME": str(tmp_path / "rh"),
            "CLAUDE_PROJECT_DIR": str(repo),
            "REVIEWER_CLAUDE_BIN": str(fake_claude),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "older than" not in (result.stdout + result.stderr)


def test_an_absent_host_binary_does_not_break_the_toggle(tmp_path):
    # The check probes an external binary. If it is missing, renamed, or slow, enabling the
    # reviewer must still work -- a version notice is not worth a broken command.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "toggle.py"), "on", "acceptRisk"],
        capture_output=True, text=True, cwd=str(repo),
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "REVIEWER_HOME": str(tmp_path / "rh"),
            "CLAUDE_PROJECT_DIR": str(repo),
            "REVIEWER_CLAUDE_BIN": str(tmp_path / "definitely-absent"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "rh" / "state").is_dir()
