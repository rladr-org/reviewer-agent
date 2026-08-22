#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Minimum validated host versions, and the warning shown below them.

This package depends on host behaviour that has changed across releases -- plugin layout, hook
payload fields, MCP wiring, sandbox flags. Below the versions this release was actually
exercised on, the symptom is not "unsupported host": it is a reviewer that never fires, or one
that fires and fails, in a tool whose function is to block commits. Naming the mismatch once, at
the moment the reviewer is switched on, is far cheaper than diagnosing it later.

The floors are the OLDEST versions this release was exercised on, not the oldest that could
work. That distinction is why this warns rather than refuses: nobody has evidence about the
versions below, and blocking a probably-fine host on absent evidence is the wrong trade for a
beta. `python3 host_versions.py` prints what is detected here.

Node and Python floors are different in kind and stated for completeness: Node 22.19 is required
by Pi itself, and Python 3.9 is the macOS system interpreter, which the installation path
deliberately supports so no user-installed interpreter is needed.
"""
import os
import re
import shutil
import subprocess
import sys

# Exercised end to end on macOS during the 1.2.0-beta.1 release matrix.
CLAUDE_CODE_MIN = (2, 1, 238)
CODEX_MIN = (0, 147, 0)
# Required by Pi, not by this package.
NODE_MIN = (22, 19, 0)
# The macOS system interpreter (/usr/bin/python3). Past upstream end of life, and kept anyway so
# installation needs no user-installed Python; see the README.
PYTHON_MIN = (3, 9, 0)

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text):
    """The first dotted version in `text`, as a tuple, or None.

    Deliberately tolerant of surrounding words, because every host spells it differently:
    `2.1.238 (Claude Code)`, `codex-cli 0.147.0`, `v24.14.1`, `Python 3.9.6`. Deliberately
    intolerant of everything else -- a guess is worse than no answer here, since it either warns
    a supported host or silences a real warning.
    """
    match = _VERSION.search(str(text or ""))
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def format_version(version):
    return ".".join(str(part) for part in version) if version else "unknown"


def version_warning(host, detected, minimum):
    """A sentence naming the mismatch, or None when the host is new enough.

    An UNDETECTABLE version also warns, and says that is what happened: silence would be
    indistinguishable from a passing check.
    """
    if detected is None:
        return (
            "{} version could not be determined. This release was validated against {} or "
            "newer; if yours is older, expect the reviewer to fail in ways that look like "
            "reviewer bugs. Continuing.".format(host, format_version(minimum))
        )
    if tuple(detected) >= tuple(minimum):
        return None
    return (
        "{} {} is older than {}, the oldest version this release was validated against. "
        "Continuing anyway -- older hosts may work -- but if the reviewer misbehaves, update "
        "the host before reporting a bug.".format(
            host, format_version(detected), format_version(minimum)
        )
    )


def _probe(binary, *args):
    """`binary --version` output, or None. Never raises and never hangs a command."""
    if not binary:
        return None
    resolved = binary if os.path.isabs(binary) else shutil.which(binary)
    if not resolved or not os.path.exists(resolved):
        return None
    try:
        result = subprocess.run(
            [resolved] + list(args), capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr or "").strip()


def claude_code_version():
    binary = os.environ.get("REVIEWER_CLAUDE_BIN") or "claude"
    return parse_version(_probe(binary, "--version"))


def codex_version():
    binary = os.environ.get("REVIEWER_CODEX_BIN") or "codex"
    return parse_version(_probe(binary, "--version"))


def node_version():
    binary = os.environ.get("REVIEWER_NODE_BIN") or "node"
    return parse_version(_probe(binary, "--version"))


def claude_code_warning():
    """The warning to print when enabling the reviewer under Claude Code, or None.

    Silent when the binary is absent entirely: a hook-driven environment need not have `claude`
    on PATH, and warning about a host we cannot even find would fire for everyone using a
    non-standard install.
    """
    binary = os.environ.get("REVIEWER_CLAUDE_BIN") or "claude"
    if _probe(binary, "--version") is None:
        return None
    return version_warning("Claude Code", claude_code_version(), CLAUDE_CODE_MIN)


def main():
    rows = (
        ("Claude Code", claude_code_version(), CLAUDE_CODE_MIN),
        ("Codex", codex_version(), CODEX_MIN),
        ("Node", node_version(), NODE_MIN),
        ("Python", tuple(sys.version_info[:3]), PYTHON_MIN),
    )
    for host, detected, minimum in rows:
        warning = version_warning(host, detected, minimum)
        print("{:<12} detected {:<12} minimum {:<12} {}".format(
            host, format_version(detected), format_version(minimum),
            "OLD" if warning and detected else ("?" if warning else "ok"),
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
