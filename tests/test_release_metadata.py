# SPDX-License-Identifier: MPL-2.0
"""One release identity, spelled four ways, verified in every file that declares it.

A release where the Claude manifest says one thing, the Codex manifest another, and the MCP
server a third is not a version -- it is three, and the one a user reads during troubleshooting
is whichever surface they happened to look at. Eight declarations exist across the two packages,
in four different notations, so the check normalizes before comparing rather than string-matching
and hoping.

The exemption is deliberate and recorded rather than silent: `clientInfo.version` in
`host_config.py` identifies the installer client in an app-server handshake, not the plugin.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_versions as rv  # noqa: E402


def test_every_declared_version_normalizes_to_the_release():
    assert rv.mismatches() == {}


def test_all_eight_declarations_are_covered():
    # A declaration the checker does not know about is a declaration that can drift.
    assert len(rv.declared_versions()) == 7  # plus the recorded host_config exemption


@pytest.mark.parametrize("raw,expected", [
    ("1.2.0-beta.1", "1.2.0-beta.1"),
    ("v1.2.0-beta.1", "1.2.0-beta.1"),
    ("1.2.0-beta.1+codex.20260101010101", "1.2.0-beta.1"),
    ("v1.2.0-beta.1+codex.1", "1.2.0-beta.1"),
    ("1.2.0b1", "1.2.0-beta.1"),
    ("1.2.0a3", "1.2.0-alpha.3"),
    ("1.2.0rc2", "1.2.0-rc.2"),
    ("1.2.0", "1.2.0"),
])
def test_normalization_covers_every_spelling(raw, expected):
    assert rv.normalize(raw) == expected


def test_normalization_does_not_invent_equivalences():
    # `1.2.0-beta.1` and `1.2.0` are different releases; a normalizer that flattened the
    # prerelease away would make the parity check pass on a mismatched tree.
    assert rv.normalize("1.2.0") != rv.normalize("1.2.0-beta.1")
    assert rv.normalize("1.2.0b2") != rv.normalize("1.2.0b1")
    assert rv.normalize("1.3.0b1") != rv.normalize("1.2.0b1")


def test_the_tag_and_the_python_version_agree_with_the_release():
    assert rv.normalize(rv.TAG) == rv.RELEASE
    assert rv.normalize(rv.PEP440_RELEASE) == rv.RELEASE


def test_the_codex_manifest_keeps_a_cachebuster():
    # Codex resolves an installed local plugin by version string, so a reinstall of an
    # unchanged version can serve the cached copy. The build metadata is what prevents that,
    # and it must survive every version bump.
    version = json.loads(
        (ROOT / "plugins" / "reviewer-agent-codex" / ".codex-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )["version"]

    assert "+codex." in version
    assert version.split("+", 1)[1].startswith("codex.")


def test_the_release_version_tool_runs_clean():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "release_versions.py")],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    # The exemption is stated in the output, not just in a comment, so a reader of the check
    # sees which declaration is deliberately excluded.
    assert "host_config.py" in result.stdout


def test_the_release_wrappers_exist_and_are_self_describing():
    # The plan requires repository-owned wrappers for the two plugin-creator steps, because CI
    # and outside contributors do not have that locally installed skill.
    for name in ("release_cachebuster.py", "read_marketplace_name.py", "release_versions.py"):
        path = ROOT / "tools" / name
        assert path.is_file(), name
        result = subprocess.run(
            [sys.executable, str(path), "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0, (name, result.stderr)


def test_the_cachebuster_wrapper_can_report_without_mutating(tmp_path):
    before = (ROOT / "plugins" / "reviewer-agent-codex" / ".codex-plugin" / "plugin.json")
    original = before.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "release_cachebuster.py"), "--print"],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith(rv.RELEASE)
    assert before.read_text(encoding="utf-8") == original
