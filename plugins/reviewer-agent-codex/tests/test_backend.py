# SPDX-License-Identifier: MPL-2.0
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from reviewer_codex import backend, state


ROOT = Path(__file__).resolve().parents[1]


def _assert_heartbeat_stopped(path, label):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.02)
    assert path.exists(), label + " never produced a heartbeat"
    time.sleep(0.1)
    before = path.read_text(encoding="utf-8")
    time.sleep(0.2)
    after = path.read_text(encoding="utf-8")
    assert after == before, label + " survived cleanup"


def test_nested_reviewer_argv_disables_hooks_and_plugins_and_pins_reviewer_model(
    isolated_state, monkeypatch
):
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)
    fresh = backend.fresh_argv("/tmp/repo")
    resume = backend.resume_argv("thr_1", "/tmp/repo", "read-only")
    assert backend.HOOKS_DISABLED_CONFIG in fresh
    assert backend.HOOKS_DISABLED_CONFIG in resume
    assert backend.PLUGINS_DISABLED_CONFIG in fresh
    assert backend.PLUGINS_DISABLED_CONFIG in resume
    assert backend.APPROVAL_CONFIG in fresh
    assert backend.APPROVAL_CONFIG in resume
    assert ["--model", "gpt-5.6-luna"] == fresh[fresh.index("--model") : fresh.index("--model") + 2]
    assert ["--model", "gpt-5.6-luna"] == resume[resume.index("--model") : resume.index("--model") + 2]
    assert 'model_reasoning_effort="xhigh"' in fresh
    assert 'model_reasoning_effort="xhigh"' in resume
    assert backend.SERVICE_TIER_CONFIG in fresh
    assert backend.SERVICE_TIER_CONFIG in resume


def test_nested_reviewer_argv_reads_latest_model_settings(isolated_state):
    state_path = isolated_state / "config.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"model": "gpt-5.6-terra", "reasoningEffort": "max"}),
        encoding="utf-8",
    )

    resume = backend.resume_argv("thr_1", "/tmp/repo", "read-only", "review")

    assert ["--model", "gpt-5.6-terra"] == resume[resume.index("--model") : resume.index("--model") + 2]
    assert 'model_reasoning_effort="max"' in resume


def test_parse_thread_id_from_codex_jsonl():
    output = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "thr_123"}),
        json.dumps({"type": "item.completed"}),
    ])
    assert backend.parse_thread_id(output) == "thr_123"


def test_parse_thread_id_accepts_nested_shape():
    output = json.dumps({"type": "thread.started", "thread": {"id": "thr_nested"}})
    assert backend.parse_thread_id(output) == "thr_nested"


def test_first_review_turn_is_the_ruling_and_persists_its_thread(
    git_repo, isolated_state, monkeypatch,
):
    calls = []

    def fake_run(argv, timeout, input_text=None, cancel_event=None):
        calls.append((argv, timeout, input_text))
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text("STOP_OK\n", encoding="utf-8")
        return 0, json.dumps({"type": "thread.started", "thread_id": "stop-thread"}), ""

    monkeypatch.setattr(backend, "_run", fake_run)

    code, output, error = backend.review_turn(
        git_repo, "root-session", "stop", "read-only", "rule now", timeout=30
    )

    assert (code, output, error) == (0, "STOP_OK\n", "")
    assert len(calls) == 1
    assert calls[0][2] == "rule now"
    assert backend.SERVICE_TIER_CONFIG in calls[0][0]
    assert state.read_reviewer_session(git_repo, "root-session", "stop") == "stop-thread"


def test_existing_review_turn_resumes_without_starting_another_thread(
    git_repo, isolated_state, monkeypatch,
):
    state.publish_reviewer_session(git_repo, "root-session", "stop", "stop-thread")
    monkeypatch.setattr(
        backend, "resume", lambda *args, **kwargs: (0, "next ruling", "")
    )
    monkeypatch.setattr(
        backend, "start", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing thread must resume")
        )
    )

    assert backend.review_turn(
        git_repo, "root-session", "stop", "read-only", "rule again", timeout=30
    ) == (0, "next ruling", "")


def test_resume_captures_only_last_agent_message(monkeypatch):
    calls = []

    def fake_run(argv, timeout, input_text=None, cancel_event=None):
        calls.append((argv, timeout, input_text))
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text("APPROVED\n", encoding="utf-8")
        return 0, '{"type":"turn.completed"}\n', ""

    monkeypatch.setattr(backend, "_run", fake_run)

    result = backend.resume(
        "review-thread", "/tmp/repo", "workspace-write", "audit now", timeout=30
    )

    assert result == (0, "APPROVED\n", "")
    assert calls[0][2] == "audit now"
    assert calls[0][0][-1] == "-"


def test_child_environment_replaces_inherited_temporary_roots(
    tmp_path, monkeypatch
):
    missing = tmp_path / "deleted-turn-temp"
    monkeypatch.setenv("TMPDIR", str(missing))
    monkeypatch.setenv("TMP", str(missing))
    monkeypatch.setenv("TEMP", str(missing))

    environment = backend.child_environment()

    stable = Path(environment["TMPDIR"])
    assert stable.is_dir()
    assert stable != missing
    assert environment["TMP"] == str(stable)
    assert environment["TEMP"] == str(stable)


def test_fresh_and_resumed_capture_ignore_deleted_tempfile_cache(
    isolated_state, tmp_path, monkeypatch
):
    missing = tmp_path / "deleted-cached-temp"
    monkeypatch.setattr(backend.tempfile, "tempdir", str(missing))
    answers = iter(("FIRST\n", "SECOND\n"))

    def fake_run(argv, timeout, input_text=None, cancel_event=None):
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text(next(answers), encoding="utf-8")
        events = json.dumps(
            {"type": "thread.started", "thread_id": "review-thread"}
        )
        return 0, events, ""

    monkeypatch.setattr(backend, "_run", fake_run)

    assert backend.start(
        "/tmp/repo", "root-session", "reviewer", "workspace-write", "first", 30
    ) == (0, "FIRST\n", "")
    assert backend.resume(
        "review-thread", "/tmp/repo", "workspace-write", "second", 30
    ) == (0, "SECOND\n", "")


def test_concurrent_first_turn_resumes_the_published_winner(
    isolated_state, monkeypatch
):
    def fake_run(argv, timeout, input_text=None, cancel_event=None):
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text("LOSING RULING\n", encoding="utf-8")
        return 0, json.dumps({"type": "thread.started", "thread_id": "losing-thread"}), ""

    resumed = []
    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(
        backend.state,
        "publish_reviewer_session",
        lambda *args, **kwargs: "winning-thread",
    )
    monkeypatch.setattr(
        backend,
        "resume",
        lambda thread_id, cwd, sandbox, prompt, timeout: (
            resumed.append((thread_id, prompt)) or (0, "WINNING RULING\n", "")
        ),
    )

    result = backend.start(
        "/tmp/repo", "root-session", "stop", "read-only", "rule now", timeout=30
    )

    assert result == (0, "WINNING RULING\n", "")
    assert resumed == [("winning-thread", "rule now")]


def test_run_cancellation_terminates_nested_process_promptly():
    cancel = threading.Event()
    result = []

    thread = threading.Thread(
        target=lambda: result.append(
            backend._run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=60,
                cancel_event=cancel,
            )
        )
    )
    started = time.monotonic()
    thread.start()
    time.sleep(0.1)
    cancel.set()
    thread.join(2)

    assert not thread.is_alive()
    assert result[0][0] == backend.CANCELLED_EXIT
    assert "cancel" in result[0][2].lower()
    assert time.monotonic() - started < 2


def test_run_timeout_terminates_descendant_that_started_new_session(tmp_path):
    if os.name != "posix":
        return

    descendant_pid_path = tmp_path / "detached-descendant.pid"
    heartbeat_path = tmp_path / "detached-descendant.heartbeat"
    descendant_script = "\n".join(
        [
            "import time",
            "from pathlib import Path",
            "path = Path({!r})".format(str(heartbeat_path)),
            "value = 0",
            "while True:",
            "    value += 1",
            "    path.write_text(str(value), encoding='utf-8')",
            "    time.sleep(0.05)",
        ]
    )
    reviewer_script = "\n".join(
        [
            "import subprocess, sys, time",
            "from pathlib import Path",
            "descendant = subprocess.Popen(",
            "    [sys.executable, '-c', {!r}],".format(descendant_script),
            "    start_new_session=True,",
            "    stdout=subprocess.DEVNULL,",
            "    stderr=subprocess.DEVNULL,",
            ")",
            "Path({!r}).write_text(str(descendant.pid), encoding='utf-8')".format(
                str(descendant_pid_path)
            ),
            "time.sleep(30)",
        ]
    )
    descendant_pid = None
    try:
        code, _output, error = backend._run(
            [sys.executable, "-c", reviewer_script], timeout=0.5
        )
        assert code == backend.TIMEOUT_EXIT
        assert "timed out" in error
        assert descendant_pid_path.exists(), "detached descendant did not start"
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        _assert_heartbeat_stopped(heartbeat_path, "detached descendant")
    finally:
        if descendant_pid is not None:
            try:
                os.killpg(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_terminates_nested_process_when_mcp_parent_dies(tmp_path):
    if os.name != "posix":
        return

    child_pid_path = tmp_path / "reviewer-child.pid"
    descendant_pid_path = tmp_path / "detached-descendant.pid"
    heartbeat_path = tmp_path / "detached-descendant.heartbeat"
    descendant_script = "\n".join(
        [
            "import time",
            "from pathlib import Path",
            "path = Path({!r})".format(str(heartbeat_path)),
            "value = 0",
            "while True:",
            "    value += 1",
            "    path.write_text(str(value), encoding='utf-8')",
            "    time.sleep(0.05)",
        ]
    )
    child_script = "; ".join(
        [
            "import os, subprocess, sys, time",
            "from pathlib import Path",
            "Path({!r}).write_text(str(os.getpid()), encoding='utf-8')".format(
                str(child_pid_path)
            ),
            "descendant = subprocess.Popen([sys.executable, '-c', {!r}], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)".format(descendant_script),
            "Path({!r}).write_text(str(descendant.pid), encoding='utf-8')".format(
                str(descendant_pid_path)
            ),
            "time.sleep(30)",
        ]
    )
    server_script = "\n".join(
        [
            "import sys",
            "sys.path.insert(0, {!r})".format(str(ROOT)),
            "from reviewer_codex import backend",
            "result = backend._run(",
            "    [sys.executable, '-c', {!r}], timeout=60".format(child_script),
            ")",
            "raise SystemExit(result[0])",
        ]
    )
    server = subprocess.Popen(
        [sys.executable, "-c", server_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    descendant_pid = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (
            child_pid_path.exists()
            and descendant_pid_path.exists()
            and heartbeat_path.exists()
        ):
            time.sleep(0.02)
        assert child_pid_path.exists(), "nested reviewer did not start"
        assert descendant_pid_path.exists(), "detached descendant did not start"
        assert heartbeat_path.exists(), "detached descendant did not begin running"
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))

        server.kill()
        server.wait(timeout=2)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("nested reviewer survived MCP parent death")
        _assert_heartbeat_stopped(heartbeat_path, "detached descendant")
    finally:
        if server.poll() is None:
            server.kill()
            server.wait(timeout=2)
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if descendant_pid is not None:
            try:
                os.killpg(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_preserves_stdin_across_communication_polling():
    code, output, error = backend._run(
        [
            sys.executable,
            "-c",
            "import sys,time; data=sys.stdin.read(); time.sleep(.25); print(data.upper())",
        ],
        timeout=2,
        input_text="briefing",
    )

    assert (code, output.strip(), error) == (0, "BRIEFING", "")


def test_run_closes_stdin_after_large_prompt_crosses_pipe_buffer():
    prompt = "x" * 16_701

    code, output, error = backend._run(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; time.sleep(.25); "
                "data=sys.stdin.read(); print(len(data))"
            ),
        ],
        timeout=1,
        input_text=prompt,
    )

    assert (code, output.strip(), error) == (0, str(len(prompt)), "")


def test_run_survives_deleted_server_working_directory(tmp_path):
    orphaned = tmp_path / "deleted-mcp-cwd"
    orphaned.mkdir()
    script = "\n".join(
        [
            "import json, sys",
            "sys.path.insert(0, {!r})".format(str(ROOT)),
            "from reviewer_codex import backend",
            "print('READY', flush=True)",
            "sys.stdin.readline()",
            "result = backend._run(",
            "    [sys.executable, '-c', \"import os; print(os.getcwd())\"], timeout=2",
            ")",
            "print(json.dumps(result), flush=True)",
        ]
    )
    server = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(orphaned),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout.readline().strip() == "READY"
        orphaned.rmdir()
        server.stdin.write("\n")
        server.stdin.flush()
        code, output, error = json.loads(server.stdout.readline())
        assert code == 0, error
        assert Path(output.strip()).is_dir()
        assert error == ""
    finally:
        server.kill()
        server.communicate()
