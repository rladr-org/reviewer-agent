# SPDX-License-Identifier: MPL-2.0
import shutil
import subprocess

import pytest

from reviewer_codex import state


@pytest.fixture(autouse=True)
def _no_reading_delay(monkeypatch):
    # Delay-specific tests opt in; ordinary reviews should not sleep in the suite.
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "0")


@pytest.fixture(autouse=True)
def _forget_project_lookups():
    # state remembers git's project-root answers briefly; no test may see another test's.
    state._project_lookups.clear()
    yield
    state._project_lookups.clear()


@pytest.fixture(scope="session")
def _template_repo(tmp_path_factory):
    # Built once per run; each test gets a copy (five git processes cost ~0.3 s per test).
    repo = tmp_path_factory.mktemp("template") / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    # No background housekeeping: a commit can start `git maintenance` detached, and its
    # `.git/objects/maintenance.lock` vanishing mid-copy failed the copy on CI (2026-10-01).
    subprocess.run(["git", "-C", str(repo), "config", "maintenance.auto", "false"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "gc.auto", "0"], check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


@pytest.fixture
def git_repo(tmp_path, _template_repo):
    repo = tmp_path / "repo"
    # Lock files are transient by nature; never copy one (belt and braces with the config above).
    shutil.copytree(str(_template_repo), str(repo), symlinks=True,
                    ignore=shutil.ignore_patterns("*.lock"))
    return repo


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    path = tmp_path / "state"
    monkeypatch.setenv("REVIEWER_CODEX_STATE_DIR", str(path))
    return path
