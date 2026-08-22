# SPDX-License-Identifier: MPL-2.0
"""Whether the suite is running inside a Git working tree, and a marker for tests that need one.

A released source archive is not a repository. Someone who downloads the tarball, runs the
documented `python3 -m pytest -p no:cacheprovider`, and sees nine failures learns nothing about
their installation -- they learn that the project looks broken, on their first contact with it.

The tests in question are all release checks *about the repository*: which files are tracked,
whether the export excludes the right ones, whether any content quotes a private commit id.
Outside a repository those questions have no answer, so they skip with a reason rather than
failing. The release matrix runs them against an export that HAS been initialised as a
repository, which is the state the public repository is actually created in, so nothing is
skipped where it matters.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def in_git_repository(path=ROOT):
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


IS_REPOSITORY = in_git_repository()

requires_repository = pytest.mark.skipif(
    not IS_REPOSITORY,
    reason="needs a Git working tree; this is a released source archive, not a checkout",
)
