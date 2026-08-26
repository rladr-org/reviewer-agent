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


# --------------------------------------------------------------------------
# _run_streaming: the codex --json audit path. It watches the event pipe and kills a HUNG
# reviewer via two idle thresholds (N1 first-output, N2 between-output), with the total cap
# checked first so a genuinely slow audit is never misread as idle.
# (docs/plans/codex_jsonl_streaming.md, Task 2.)
# --------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_returns_stdout_and_stderr_on_a_fast_process():
    code, out, err = reviewer_backends._run_streaming(
        [sys.executable, "-c",
         "import sys; sys.stdout.write('hi'); sys.stderr.write('warn')"],
        timeout=30, first_output_timeout=5, idle_timeout=10)
    assert (code, out.strip(), err.strip()) == (0, "hi", "warn")


@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_kills_on_first_output_timeout():
    # Silent for 300s; N1=1 must kill in ~1s. The reason carries "idle" (so it is retried) and
    # "no output" (so an operator can see which threshold fired).
    code, _out, err = reviewer_backends._run_streaming(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        timeout=120, first_output_timeout=1, idle_timeout=60)
    assert code == rl.TIMEOUT_EXIT
    assert "idle" in err and "no output" in err


@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_kills_on_between_output_timeout():
    # Emit one line immediately, then go silent; the first byte flips N1->N2, and N2=1 kills.
    code, _out, err = reviewer_backends._run_streaming(
        [sys.executable, "-c",
         "import sys, time; sys.stdout.write('started\\n'); sys.stdout.flush(); "
         "time.sleep(300)"],
        timeout=120, first_output_timeout=30, idle_timeout=1)
    assert code == rl.TIMEOUT_EXIT
    assert "idle" in err and "no output" in err


@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_first_byte_flips_the_threshold_from_n1_to_n2():
    # Prints immediately (beats N1=1), then works 3s (< N2=10) and exits 0. Neither fires: with a
    # single threshold this same run would have to trip one of them.
    code, out, _err = reviewer_backends._run_streaming(
        [sys.executable, "-c",
         "import sys, time; sys.stdout.write('go\\n'); sys.stdout.flush(); "
         "time.sleep(3); sys.stdout.write('done\\n')"],
        timeout=120, first_output_timeout=1, idle_timeout=10)
    assert code == 0
    assert "go" in out and "done" in out


@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_records_first_event_and_max_gap():
    reviewer_backends.reset_last_invocation()
    code, _out, _err = reviewer_backends._run_streaming(
        [sys.executable, "-c",
         "import sys, time; sys.stdout.write('a\\n'); sys.stdout.flush(); "
         "time.sleep(2); sys.stdout.write('b\\n'); sys.stdout.flush()"],
        timeout=120, first_output_timeout=10, idle_timeout=30)
    assert code == 0
    rec = reviewer_backends.last_invocation()
    assert rec["t_first_event_s"] is not None and rec["t_first_event_s"] >= 0
    # the ~2s gap between 'a' and 'b' is the largest inter-event gap
    assert rec["max_gap_s"] >= 1.5


@pytest.mark.skipif(os.name != "posix", reason="select() on pipes is POSIX-only")
def test_streaming_total_timeout_fires_even_while_output_streams():
    # Emits a line every 0.1s forever, so N2 never fires (output keeps resetting it); the total
    # cap (2s) must still kill it, checked FIRST so it is NOT misclassified as an idle kill.
    code, _out, err = reviewer_backends._run_streaming(
        [sys.executable, "-c",
         "import sys, time\n"
         "while True:\n"
         "    sys.stdout.write('tick\\n'); sys.stdout.flush(); time.sleep(0.1)\n"],
        timeout=2, first_output_timeout=5, idle_timeout=30)
    assert code == rl.TIMEOUT_EXIT
    assert "timed out after" in err
    assert "idle" not in err
