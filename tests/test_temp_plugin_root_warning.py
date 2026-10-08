# SPDX-License-Identifier: MPL-2.0
"""Session start warns when the plugin runs from a temporary directory.

2026-10-07: the installed build ran from a Directory marketplace under /private/tmp. Claude Code
runs such a plugin's hooks from that directory; macOS's cleanup deleted the files, every hook
exited 127 (non-blocking), and the commit gate failed open. See
docs/plans/temp_plugin_root_warning.md."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import session_start_hook as ssh

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE


def test_a_root_under_a_temp_dir_is_flagged(tmp_path):
    temp = tmp_path / "t"
    root = temp / "scratchpad" / "export"
    root.mkdir(parents=True)
    assert ssh.temp_plugin_root(str(root), temp_dirs=[str(temp)]) is not None


def test_a_sibling_sharing_a_prefix_is_not_flagged(tmp_path):
    (tmp_path / "tmp").mkdir()
    root = tmp_path / "tmpfoo" / "plugin"
    root.mkdir(parents=True)
    assert ssh.temp_plugin_root(str(root), temp_dirs=[str(tmp_path / "tmp")]) is None


def test_a_permanent_root_is_not_flagged(tmp_path):
    root = tmp_path / "home" / "plugins" / "reviewer-agent"
    root.mkdir(parents=True)
    assert ssh.temp_plugin_root(str(root), temp_dirs=[str(tmp_path / "elsewhere")]) is None


@pytest.mark.skipif(os.path.realpath("/tmp") == "/tmp", reason="/tmp is not a symlink here")
def test_tmp_and_its_resolved_target_match_alike(tmp_path):
    # On macOS /tmp -> /private/tmp: a root spelled either way is under the default temp dirs.
    d = tempfile.mkdtemp(dir="/tmp")
    try:
        resolved = os.path.realpath(d)
        assert ssh.temp_plugin_root(d) is not None
        assert ssh.temp_plugin_root(resolved) is not None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_defaults_cover_tmp_var_tmp_and_the_system_temp_dir():
    defaults = {os.path.realpath(p) for p in ssh.default_temp_dirs()}
    for p in ("/tmp", "/var/tmp", tempfile.gettempdir()):
        assert os.path.realpath(p) in defaults


def _run(hook, home):
    env = dict(os.environ, REVIEWER_HOME=home)
    return subprocess.run([sys.executable, hook], input=json.dumps({"session_id": "c1"}),
                          capture_output=True, text=True, env=env)


def test_the_hook_warns_when_its_copy_runs_from_a_temp_dir(tmp_path):
    if ssh.temp_plugin_root(str(tmp_path)) is None:
        pytest.skip("pytest's tmp_path is not under a temp directory here")
    copy = tmp_path / "plugin"
    copy.mkdir()
    for source in ROOT.glob("*.py"):
        shutil.copy2(source, copy / source.name)
    prompts = Path("plugins") / "reviewer-agent-codex" / "prompts"  # read at import
    shutil.copytree(ROOT / prompts, copy / prompts)
    p = _run(str(copy / "session_start_hook.py"), str(tmp_path / "rh"))
    assert p.returncode == 0
    out = json.loads(p.stdout)
    message = out["systemMessage"]
    assert os.path.realpath(str(copy)) in message or str(copy) in message
    assert "fails open" in message
    assert "AGENT_UPDATE.md" in message


def test_the_hook_is_silent_from_a_permanent_checkout(tmp_path):
    if ssh.temp_plugin_root(str(ROOT)) is not None:
        pytest.skip("this checkout itself sits in a temp directory")
    p = _run(str(ROOT / "session_start_hook.py"), str(tmp_path / "rh"))
    assert p.returncode == 0
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    assert "systemMessage" not in out
