# SPDX-License-Identifier: MPL-2.0
"""Guards for the reproducible development baseline.

Three separate traps, each of which cost real time before this file existed:

1. The root `.gitignore` is ignored by the operator's *global* excludes file, so a plain
   `git add .gitignore` silently does nothing and the release export ships without it.
2. A bare `pytest` at the repository root used to also collect
   `plugins/reviewer-agent-codex/tests/`, which imports `reviewer_codex` relative to the
   plugin directory -- 15 collection errors and no way to run the root suite by habit.
3. The runtime is standard-library-only; development dependencies belong in
   `requirements-dev.txt` with an upper bound, so a pytest major release cannot break
   collection on the supported Python floor without a deliberate bump.
"""

import configparser
import subprocess
import sys
from pathlib import Path
from repo_context import requires_repository  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


@requires_repository
def test_root_gitignore_is_tracked_despite_the_global_excludes_file():
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".gitignore"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    assert tracked.returncode == 0, (
        "root .gitignore is not tracked; it is matched by the global core.excludesFile, "
        "so it must be added with `git add -f .gitignore`"
    )


def test_root_pytest_config_scopes_collection_to_the_root_suite():
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")

    assert config.get("pytest", "testpaths").split() == ["tests"]
    assert "plugins" in config.get("pytest", "norecursedirs").split()


def test_bare_root_collection_excludes_the_nested_codex_suite():
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    assert collected.returncode == 0, collected.stdout + collected.stderr
    # Checked on the collected PATHS, not on the raw output: a parametrized test id can quote
    # a path under plugins/ (tests/test_license_metadata.py does), and a substring search over
    # the whole listing would read that as a collected nested test.
    collected_files = {
        line.split("::", 1)[0] for line in collected.stdout.splitlines() if "::" in line
    }
    assert collected_files, collected.stdout
    assert not [name for name in collected_files if not name.startswith("tests/")], (
        sorted(collected_files)
    )


def test_dev_requirements_bound_pytest_at_both_ends():
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    pins = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert any(pin.startswith("pytest>=") and "<" in pin for pin in pins), pins
