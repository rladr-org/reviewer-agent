# SPDX-License-Identifier: MPL-2.0
"""hooks/run_hook.sh: find a Python even without a login PATH; fail the commit gate CLOSED
(exit 2) when there is none. Claude Code launched by systemd/cron/an IDE is the real case."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke  # install-time subset (tests/test_smoke_subset.py)

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = str(ROOT / "hooks" / "run_hook.sh")
# /bin/sh is dash on Debian/Ubuntu and bash-as-sh on macOS; run under every one present.
SHELLS = [s for s in ("/bin/sh", "/bin/dash") if os.path.exists(s)]


@pytest.fixture(params=SHELLS)
def shell(request):
    return request.param


def _run(script, payload, pythons, shell):
    env = {"PATH": "/nonexistent", "REVIEWER_HOOK_PYTHONS": pythons,
           "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run([shell, LAUNCHER, str(ROOT / script)], input=payload,
                          capture_output=True, text=True, env=env)


def test_commit_gate_blocks_with_exit_2_when_no_python_exists(shell):
    p = _run("commit_hook.py", json.dumps({"tool_input": {"command": "git commit -m x"}}),
             "/nonexistent/python3", shell)
    assert p.returncode == 2
    assert "python3" in p.stderr


def test_commit_gate_stays_out_of_the_way_for_non_commits_without_python(shell):
    p = _run("commit_hook.py", json.dumps({"tool_input": {"command": "ls"}}),
             "/nonexistent/python3", shell)
    assert p.returncode == 0 and p.stdout == ""


def test_commit_gate_judges_the_command_not_the_cwd_without_python(shell):
    # "commit" in the cwd says nothing about the call.
    payload = json.dumps({"cwd": "/repos/commit-helper", "hook_event_name": "PreToolUse",
                          "tool_input": {"command": "git status"}})
    p = _run("commit_hook.py", payload, "/nonexistent/python3", shell)
    assert p.returncode == 0 and p.stdout == ""


def test_commit_gate_judges_only_the_command_value_without_python(shell):
    # tool_input also carries a `description`; "commit" there says nothing about the call.
    payload = json.dumps({"tool_input": {"command": "git status",
                                         "description": "check commit status"}})
    p = _run("commit_hook.py", payload, "/nonexistent/python3", shell)
    assert p.returncode == 0 and p.stdout == ""


def test_escaped_quotes_do_not_end_the_command_early_without_python(shell):
    payload = json.dumps({"tool_input": {"command": 'git log --grep "x\\\\" && git commit -m y',
                                         "description": "log"}})
    p = _run("commit_hook.py", payload, "/nonexistent/python3", shell)
    assert p.returncode == 2


def test_session_start_warns_loudly_without_python(shell):
    p = _run("session_start_hook.py", "{}", "/nonexistent/python3", shell)
    assert p.returncode == 0
    assert "no python3" in json.loads(p.stdout)["systemMessage"]


def test_stdin_reaches_python_byte_exact(tmp_path, shell):
    # Review Focus 3: quotes, newlines, $, backslashes, under dash on Linux.
    probe = tmp_path / "echo_stdin.py"
    probe.write_text("import sys; sys.stdout.write(sys.stdin.read())\n")
    payload = json.dumps({"tool_input": {"command": "printf '%s\\n' \"$HOME\" `id` \\\\ \n x"}})
    p = subprocess.run([shell, LAUNCHER, str(probe)], input=payload, capture_output=True,
                       text=True, env={"PATH": "/nonexistent",
                                       "REVIEWER_HOOK_PYTHONS": sys.executable})
    assert p.returncode == 0 and p.stdout == payload
