# SPDX-License-Identifier: MPL-2.0
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from reviewer_codex import install, uninstall

ROOT = Path(__file__).resolve().parents[1]


def old_python(directory):
    directory.mkdir()
    executable = directory / "python3"
    executable.write_text("#!/bin/sh\nprintf '3.8.10\\n'\n")
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize("default", ["old", "missing"])
def test_install_pins_python_for_hooks_and_mcp_despite_host_path(tmp_path, monkeypatch, default):
    home = tmp_path / "home with spaces"
    binaries = tmp_path / "bin"
    old_python(binaries)
    if default == "missing":
        (binaries / "python3").unlink()
    selected = tmp_path / "python with spaces and $literal"
    selected.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(selected))
    monkeypatch.setenv("PATH", str(binaries))
    install.install_local(ROOT, home=home, install=False)
    env = dict(os.environ, HOME=str(home), PLUGIN_ROOT=str(ROOT))
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    result = subprocess.run(command, shell=True, env=env, input="{", text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    server = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["reviewer-agent"]
    result = subprocess.run([server["command"], *server["args"]], cwd=ROOT, env=env,
                            input=json.dumps({"jsonrpc":"2.0", "id":1, "method":"initialize"})+"\n",
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["result"]["serverInfo"]["name"] == "reviewer-agent-codex"


def test_install_can_find_versioned_python_when_installer_and_default_are_old(tmp_path, monkeypatch):
    binaries = tmp_path / "bin"
    old = old_python(binaries)
    compatible = binaries / "python3.9"
    compatible.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(old))
    monkeypatch.setenv("PATH", str(binaries))
    assert install.verify_hook_python() == str(compatible)


def test_install_rejects_broken_server_before_writing_configuration(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT / ".codex-plugin", source / ".codex-plugin")
    (source / "scripts").mkdir()
    (source / "scripts/mcp_server.py").write_text("raise RuntimeError('broken server')\n")
    home = tmp_path / "home"
    with pytest.raises(install.InstallError, match="startup"):
        install.install_local(source, home=home, install=False)
    assert not home.exists()


def test_uninstall_removes_python_selection_and_preserves_other_configuration(tmp_path):
    home = tmp_path / "home"
    install.install_local(ROOT, home=home, install=False)
    config = home / ".codex/reviewer-agent-codex/python-path"
    assert config.read_text().strip() == sys.executable
    neighbour = config.parent / "unrelated.txt"
    neighbour.write_text("keep me")
    uninstall.uninstall_local(ROOT, home=home, run_command=lambda _: None)
    assert not config.exists()
    assert neighbour.read_text() == "keep me"


@pytest.mark.parametrize("failure", ["missing", "not_mcp"])
def test_install_requires_an_initializable_server(tmp_path, failure):
    source = tmp_path / "source"
    shutil.copytree(ROOT / ".codex-plugin", source / ".codex-plugin")
    if failure == "not_mcp":
        (source / "scripts").mkdir()
        (source / "scripts/mcp_server.py").write_text(
            "print('{\"result\":{\"serverInfo\":true}}')\n"
        )
    home = tmp_path / "home"
    with pytest.raises(install.InstallError, match="startup"):
        install.install_local(source, home=home, install=False)
    assert not home.exists()


@pytest.mark.parametrize("selection", ["", "/missing/python\n"])
def test_pretool_blocks_when_saved_python_is_broken(tmp_path, selection):
    home = tmp_path / "home"
    config = home / ".codex/reviewer-agent-codex/python-path"
    config.parent.mkdir(parents=True)
    config.write_text(selection)
    command = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    result = subprocess.run(
        command, shell=True, env=dict(os.environ, HOME=str(home), PLUGIN_ROOT=str(ROOT)),
        input="{}", text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 2
