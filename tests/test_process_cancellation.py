# SPDX-License-Identifier: MPL-2.0
"""A cancelled or timed-out Claude-side review must leave nothing running.

Three ways a reviewer call ends early, and each used to leak:

  TIMEOUT   `subprocess.run(timeout=...)` kills the direct child only. A codex reviewer runs
            the project's tests under `workspace-write`, so the processes that matter are its
            GRANDCHILDREN -- and a test runner that calls `setsid` (or any tool that starts its
            own process group) survived the reviewer it belonged to.
  ESCAPE    the host signals the hook. Without a handler the hook dies and the reviewer it
            started is reparented to init and keeps spending tokens.
  SIGKILL   nothing in the hook can run at all, so the reviewer has to notice its parent is
            gone by itself. The guard process holds one end of a pipe for exactly that.

Every wait here is bounded and polls, so a pass is fast and a failure cannot hang the suite.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import reviewer_backends
import reviewer_lib as rl

ROOT = Path(__file__).resolve().parent.parent


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _wait_gone(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return not _alive(pid)


def _spawn_tree(marker):
    """A child that starts a grandchild in ITS OWN process group, then reports both pids."""
    script = (
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'], start_new_session=True)\n"
        "sys.stdout.write('%d %d\\n' % (os.getpid(), child.pid))\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    line = process.stdout.readline().split()
    assert len(line) == 2, marker
    return process, int(line[0]), int(line[1])


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_terminate_process_tree_reaches_a_child_in_its_own_group():
    process, parent_pid, child_pid = _spawn_tree("tree")
    try:
        assert _alive(child_pid)

        rl.terminate_process_tree(parent_pid, include_root=True)

        assert _wait_gone(child_pid), "grandchild in its own process group survived"
        # `wait`, not a liveness poll: the parent is this test's own child, so once it dies it
        # stays a reapable zombie and `kill(pid, 0)` keeps succeeding.
        assert process.wait(timeout=5) != 0
    finally:
        for pid in (child_pid, parent_pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_a_timed_out_review_kills_its_whole_process_tree(tmp_path):
    # The reviewer stand-in prints the pid of a grandchild it puts in a separate session, then
    # hangs past the timeout. Both must be gone when _run returns.
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "slow_reviewer.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(120)'], start_new_session=True)\n"
        "open({!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n".format(str(pid_file)),
        encoding="utf-8",
    )

    code, _out, err = reviewer_backends._run(
        [sys.executable, str(script)], timeout=1.5
    )

    assert code == rl.TIMEOUT_EXIT, err
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.02)
    assert pid_file.exists(), "the stand-in never started its grandchild"
    assert _wait_gone(int(pid_file.read_text().strip()))


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT, signal.SIGKILL])
def test_killing_the_hook_process_kills_the_review_it_started(tmp_path, stop_signal):
    # SIGTERM/SIGINT are what a host sends on Escape; SIGKILL cannot be handled at all, which
    # is why the guard watches the lifecycle pipe rather than relying on a handler.
    pid_file = tmp_path / "reviewer.pid"
    reviewer = tmp_path / "reviewer.py"
    reviewer.write_text(
        "import os, time\n"
        "open({!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n".format(str(pid_file)),
        encoding="utf-8",
    )
    caller = tmp_path / "caller.py"
    caller.write_text(
        "import sys\n"
        "sys.path.insert(0, {!r})\n"
        "import reviewer_backends\n"
        "reviewer_backends._run([sys.executable, {!r}], timeout=120)\n".format(
            str(ROOT), str(reviewer)
        ),
        encoding="utf-8",
    )

    hook = subprocess.Popen([sys.executable, str(caller)], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.02)
        assert pid_file.exists(), "the review never started"
        reviewer_pid = int(pid_file.read_text().strip())

        hook.send_signal(stop_signal)

        assert _wait_gone(reviewer_pid, timeout=10), "the review outlived the hook"
    finally:
        try:
            os.killpg(os.getpgid(hook.pid), signal.SIGKILL)
        except OSError:
            pass
        hook.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_a_normal_review_still_returns_its_output():
    # The guard must be transparent on the happy path: same exit code, stdout and stderr.
    code, out, err = reviewer_backends._run(
        [sys.executable, "-c", "import sys; sys.stdout.write('ok'); sys.stderr.write('warn')"],
        timeout=30,
    )

    assert (code, out.strip(), err.strip()) == (0, "ok", "warn")


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_stdin_still_reaches_the_reviewer():
    code, out, _err = reviewer_backends._run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        input_bytes=b"brief text",
        timeout=30,
    )

    assert (code, out.strip()) == (0, "BRIEF TEXT")


def test_a_missing_binary_is_still_reported_as_127():
    code, _out, err = reviewer_backends._run(
        [str(ROOT / "definitely-not-a-real-binary")], timeout=30
    )

    assert code == 127
    assert "not found" in err.lower() or "no such file" in err.lower()
