# SPDX-License-Identifier: MPL-2.0
"""The Codex installer states its host floors and warns below them, without refusing.

Same reasoning as the Claude package: below the versions this release was exercised on, the
symptom is a plugin that misbehaves rather than one that says it is unsupported. The installer
is the one moment a person is watching, so that is where the sentence goes.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reviewer_codex import host_versions as hv

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_local.py"


@pytest.mark.parametrize("text,expected", [
    ("codex-cli 0.147.0", (0, 147, 0)),
    ("codex 0.150.2 (build abc)", (0, 150, 2)),
    ("0.147", (0, 147, 0)),
])
def test_codex_version_output_is_parsed(text, expected):
    assert hv.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "unknown", None])
def test_unparseable_output_is_not_guessed_at(text):
    assert hv.parse_version(text) is None


def test_a_current_host_produces_no_warning():
    assert hv.version_warning("Codex", (0, 147, 0), hv.CODEX_MIN) is None
    assert hv.version_warning("Codex", (1, 0, 0), hv.CODEX_MIN) is None


def test_an_older_host_is_named_and_still_allowed():
    warning = hv.version_warning("Codex", (0, 140, 0), hv.CODEX_MIN)

    assert warning
    assert "0.140.0" in warning and "0.147.0" in warning
    assert "continuing" in warning.lower()


def test_the_floors_match_the_versions_the_release_was_exercised_on():
    assert hv.CODEX_MIN == (0, 147, 0)
    assert hv.PYTHON_MIN == (3, 9, 0)


def test_an_old_codex_makes_the_installer_warn_without_failing(tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\necho 'codex-cli 0.100.0'\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        REVIEWER_CODEX_BIN=str(fake_codex),
        HOME=str(tmp_path),
        CODEX_HOME=str(tmp_path / ".codex"),
    )

    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--prepare-only"],
        capture_output=True, text=True, env=environment,
    )

    assert "0.100.0" in result.stderr
    assert "0.147.0" in result.stderr
    # Prepare-only still has to succeed: the warning is advisory.
    assert result.returncode == 0, result.stderr


def test_installer_handoff_distinguishes_cli_hook_trust_from_gui_usage(tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\necho 'codex-cli 0.147.0'\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        REVIEWER_CODEX_BIN=str(fake_codex),
        HOME=str(tmp_path),
        CODEX_HOME=str(tmp_path / ".codex"),
    )

    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--prepare-only"],
        capture_output=True, text=True, env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "interactive Codex CLI" in result.stdout
    assert "/hooks is not a Codex GUI command" in result.stdout
    assert "restart the Codex GUI" in result.stdout


def test_a_missing_codex_binary_is_not_warned_about(tmp_path):
    # The installer is routinely run before codex is on PATH in that shell. A warning nobody
    # can act on trains people to ignore warnings.
    environment = dict(os.environ)
    environment.update(
        REVIEWER_CODEX_BIN=str(tmp_path / "definitely-absent"),
        HOME=str(tmp_path),
        CODEX_HOME=str(tmp_path / ".codex"),
    )

    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--prepare-only"],
        capture_output=True, text=True, env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "Codex" not in result.stderr
