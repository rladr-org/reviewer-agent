# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "stopcontinue_toggle.py")


def run_toggle(action, proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    return subprocess.run(
        [sys.executable, TOGGLE, action], capture_output=True, text=True, env=env
    )


def make_repo(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return str(proj)


def active(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.is_stopcontinue_active(proj)


def test_on_sets_stopcontinue_flag(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run_toggle("on", proj, home)
    assert p.returncode == 0, p.stderr
    assert active(home, proj, monkeypatch)
    assert "ON" in p.stdout


def test_off_clears_flag(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    run_toggle("on", proj, home)
    assert active(home, proj, monkeypatch)
    p = run_toggle("off", proj, home)
    assert p.returncode == 0
    assert not active(home, proj, monkeypatch)
    assert "OFF" in p.stdout


def test_on_fails_when_not_a_git_repo(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = tmp_path / "plain"
    proj.mkdir()
    p = run_toggle("on", str(proj), home)
    assert p.returncode != 0
    assert not active(home, str(proj), monkeypatch)
    assert "ERROR" in (p.stdout + p.stderr)
