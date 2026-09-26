# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
HOOK = str(ROOT / "session_start_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def run_hook(stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


def test_inactive_project_captures_nothing(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict\n")
    p = run_hook(
        {"session_id": "claude-1", "cwd": str(repo)},
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE},
    )
    assert p.returncode == 0
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.read_session_id(str(repo), "claude-1") is None  # inactive -> nothing captured


def test_active_project_captures_and_persists_codex_session_id(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict\n")
    rl.set_active(str(repo))
    p = run_hook(
        {"session_id": "claude-1", "cwd": str(repo)},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_SESSION_ID": "codex-cap-1",
        },
    )
    assert p.returncode == 0, p.stderr
    assert rl.read_session_id(str(repo), "claude-1") == "codex-cap-1"


def test_active_pi_project_never_starts_codex_at_session_start(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = tmp_path / "repo-pi"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict\n")
    rl.set_active(str(repo))

    argv_file = tmp_path / "codex-argv.jsonl"
    p = run_hook(
        {"session_id": "claude-pi", "cwd": str(repo)},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_BACKEND": "pi",
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_ARGV_FILE": str(argv_file),
        },
    )

    assert p.returncode == 0, p.stderr
    assert not argv_file.exists(), "Pi selection unexpectedly launched Codex"
    assert rl.read_session_id(str(repo), "claude-pi") is None
    assert rl.read_pi_session(str(repo), "claude-pi") is None


def test_active_from_worktree_captures_for_this_claude_session(tmp_path, monkeypatch):
    # SessionStart fired inside a worktree: the project is active via the root flag, and a
    # session id is captured + persisted for this Claude session (resumable by id anywhere).
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)
    (main / "reviewerAgent.md").write_text("strict\n")
    (main / "base").write_text("0\n")
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-q", "-m", "i"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    rl.set_active(str(main))
    p = run_hook(
        {"session_id": "claude-wt", "cwd": str(wt)},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_SESSION_ID": "codex-wt",
        },
    )
    assert p.returncode == 0, p.stderr
    # Keyed to the canonical project root, so the reviewer captured from inside the
    # worktree is the same one the main checkout resolves.
    assert rl.read_session_id(str(wt), "claude-wt") == "codex-wt"
    assert rl.read_session_id(str(main), "claude-wt") == "codex-wt"


def test_active_brief_failure_persists_nothing_and_does_not_crash(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict\n")
    rl.set_active(str(repo))
    p = run_hook(
        {"session_id": "claude-x", "cwd": str(repo)},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_EXIT": "7",
            "FAKE_CODEX_STDERR": "brief boom",
        },
    )
    assert p.returncode == 0
    assert rl.read_session_id(str(repo), "claude-x") is None


def test_session_start_never_exits_nonzero_on_bad_input(tmp_path):
    p = subprocess.run([sys.executable, HOOK], input="{not json",
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0
