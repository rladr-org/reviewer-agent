# SPDX-License-Identifier: MPL-2.0
import subprocess

import pytest


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    path = tmp_path / "state"
    monkeypatch.setenv("REVIEWER_CODEX_STATE_DIR", str(path))
    return path
