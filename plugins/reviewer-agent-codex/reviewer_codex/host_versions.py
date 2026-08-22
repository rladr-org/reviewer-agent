# SPDX-License-Identifier: MPL-2.0
"""Minimum validated host versions for the Codex package, and the warning below them.

Duplicated in shape from the Claude package's `host_versions.py` for the same reason the
process-tree helper is: the two packages install independently, and neither may import the
other. Both state the same floors, and a change to one belongs in the other.

The floors are the OLDEST versions this release was exercised on, not the oldest that could
work -- which is why this warns rather than refuses. An older Codex may be fine; nobody has
evidence either way, and refusing to install on absent evidence is the wrong trade for a beta.
"""
import os
import re
import shutil
import subprocess
import sys

CODEX_MIN = (0, 147, 0)
PYTHON_MIN = (3, 9, 0)

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text):
    """The first dotted version in `text` (`codex-cli 0.147.0`), as a tuple, or None."""
    match = _VERSION.search(str(text or ""))
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def format_version(version):
    return ".".join(str(part) for part in version) if version else "unknown"


def version_warning(host, detected, minimum):
    """A sentence naming the mismatch, or None when the host is new enough."""
    if detected is None:
        return (
            "{} version could not be determined. This release was validated against {} or "
            "newer; if yours is older, expect failures that look like plugin bugs. "
            "Continuing.".format(host, format_version(minimum))
        )
    if tuple(detected) >= tuple(minimum):
        return None
    return (
        "{} {} is older than {}, the oldest version this release was validated against. "
        "Continuing anyway -- older hosts may work -- but update the host before reporting "
        "a bug.".format(host, format_version(detected), format_version(minimum))
    )


def codex_version():
    binary = os.environ.get("REVIEWER_CODEX_BIN") or "codex"
    resolved = binary if os.path.isabs(binary) else shutil.which(binary)
    if not resolved or not os.path.exists(resolved):
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_version((result.stdout or result.stderr or "").strip())


def install_warnings():
    """Every host-version warning worth printing during installation, newest concern first.

    Silent about a Codex binary that cannot be found at all: the installer is often run before
    Codex is on PATH in this shell, and a warning nobody can act on trains people to ignore
    warnings.
    """
    warnings = []
    binary = os.environ.get("REVIEWER_CODEX_BIN") or "codex"
    resolved = binary if os.path.isabs(binary) else shutil.which(binary)
    if resolved and os.path.exists(resolved):
        warning = version_warning("Codex", codex_version(), CODEX_MIN)
        if warning:
            warnings.append(warning)
    python_warning = version_warning("Python", tuple(sys.version_info[:3]), PYTHON_MIN)
    if python_warning:
        warnings.append(python_warning)
    return warnings
