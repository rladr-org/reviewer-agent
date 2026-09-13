# SPDX-License-Identifier: MPL-2.0
"""Launch and resume isolated Codex reviewer threads."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import state
from .process_tree import terminate_process_tree

TIMEOUT_EXIT = 124
CANCELLED_EXIT = 130
APPROVAL_CONFIG = 'approval_policy="never"'
HOOKS_DISABLED_CONFIG = "features.hooks=false"
PLUGINS_DISABLED_CONFIG = "features.plugins=false"
SERVICE_TIER_CONFIG = 'service_tier="priority"'
COMMIT_REVIEW_TIMEOUT = state.DEFAULT_COMMIT_REVIEW_TIMEOUT
MAX_COMMIT_REVIEW_TIMEOUT = state.MAX_COMMIT_REVIEW_TIMEOUT
STOP_REVIEW_TIMEOUT = 300


def _snapshot_process_guard():
    """Keep this server's guard and dependency available after plugin cache eviction."""
    directory = Path(__file__).parent
    tree_source = (directory / "process_tree.py").read_text(encoding="utf-8")
    guard_source = (directory / "_process_guard.py").read_text(encoding="utf-8")
    return (
        "import sys, types\n"
        "tree = types.ModuleType('process_tree')\n"
        "exec(compile({!r}, '<reviewer-process-tree>', 'exec'), tree.__dict__)\n"
        "sys.modules['process_tree'] = tree\n"
        "exec(compile({!r}, '<reviewer-process-guard>', 'exec'))\n"
    ).format(tree_source, guard_source)


# Snapshot at import, while the installed package still exists. Later launches must not
# reopen its files or mix a running server's code with a replacement plugin version.
_PROCESS_GUARD_SOURCE = _snapshot_process_guard() if os.name == "posix" else ""


def codex_bin():
    configured = os.environ.get("REVIEWER_CODEX_BIN")
    return configured or shutil.which("codex") or str(Path.home() / ".local" / "bin" / "codex")


def stable_temp_root():
    if os.name == "posix" and Path("/tmp").is_dir():
        return "/tmp"
    for name in ("TEMP", "TMP", "TMPDIR"):
        candidate = os.environ.get(name)
        if candidate and Path(candidate).is_dir():
            return candidate
    candidate = tempfile.gettempdir()
    if Path(candidate).is_dir():
        return candidate
    raise FileNotFoundError("no stable temporary directory is available")


def child_environment():
    """Build a stable environment for a reviewer that may outlive its parent turn."""
    environment = dict(os.environ)
    stable_temp = stable_temp_root()
    environment.update(
        REVIEWER_CODEX_CHILD="1",
        TMPDIR=stable_temp,
        TMP=stable_temp,
        TEMP=stable_temp,
    )
    return environment


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            terminate_process_tree(process.pid, include_root=True)
        else:
            process.terminate()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _run(argv, timeout, input_text=None, cancel_event=None):
    lifecycle_read = None
    lifecycle_write = None
    guarded_argv = argv
    popen_options = {}
    if os.name == "posix":
        lifecycle_read, lifecycle_write = os.pipe()
        guarded_argv = [
            sys.executable,
            "-c",
            _PROCESS_GUARD_SOURCE,
            str(lifecycle_read),
        ] + list(argv)
        popen_options["pass_fds"] = (lifecycle_read,)
    try:
        process = subprocess.Popen(
            guarded_argv,
            cwd=stable_temp_root(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_environment(),
            start_new_session=(os.name == "posix"),
            **popen_options,
        )
    except OSError as error:
        for descriptor in (lifecycle_read, lifecycle_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return 127, "", str(error)
    if lifecycle_read is not None:
        os.close(lifecycle_read)
    communication_finished = threading.Event()

    def cancel_when_requested():
        while not communication_finished.wait(0.05):
            if cancel_event.is_set():
                _terminate(process)
                return

    cancel_watcher = None
    if cancel_event is not None:
        cancel_watcher = threading.Thread(target=cancel_when_requested, daemon=True)
        cancel_watcher.start()
    try:
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process)
            stdout, stderr = process.communicate()
            if cancel_event is not None and cancel_event.is_set():
                return CANCELLED_EXIT, stdout or "", stderr or "reviewer cancelled"
            return TIMEOUT_EXIT, stdout or "", stderr or "reviewer timed out"
        if cancel_event is not None and cancel_event.is_set():
            return CANCELLED_EXIT, stdout or "", stderr or "reviewer cancelled"
        return process.returncode, stdout or "", stderr or ""
    finally:
        communication_finished.set()
        if cancel_watcher is not None:
            cancel_watcher.join(timeout=0.2)
        if lifecycle_write is not None:
            try:
                os.close(lifecycle_write)
            except OSError:
                pass


def parse_thread_id(output):
    for line in (output or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("thread") if isinstance(event.get("thread"), dict) else event
        candidate = (
            event.get("thread_id") or event.get("session_id") or event.get("id")
            if event.get("type") in ("thread.started", "session_meta", "session")
            else None
        )
        candidate = candidate or payload.get("id") or payload.get("thread_id")
        if candidate and event.get("type") in ("thread.started", "session_meta", "session"):
            return str(candidate)
    return None


def fresh_argv(cwd, sandbox="read-only", output_path=None):
    argv = [
        codex_bin(), "exec", "-C", str(cwd), "--json", "--sandbox", sandbox,
        "-c", APPROVAL_CONFIG, "-c", HOOKS_DISABLED_CONFIG,
        "-c", PLUGINS_DISABLED_CONFIG, "-c", SERVICE_TIER_CONFIG,
        "--model", state.reviewer_model(),
        "-c", 'model_reasoning_effort="{}"'.format(state.reviewer_effort()),
    ]
    if output_path is not None:
        argv += ["--output-last-message", str(output_path)]
    return argv + ["-"]


def resume_argv(thread_id, cwd, sandbox, output_path=None):
    argv = [
        codex_bin(), "exec", "-C", str(cwd), "--json", "--sandbox", sandbox,
        "-c", APPROVAL_CONFIG, "-c", HOOKS_DISABLED_CONFIG,
        "-c", PLUGINS_DISABLED_CONFIG, "-c", SERVICE_TIER_CONFIG,
        "--model", state.reviewer_model(),
        "-c", 'model_reasoning_effort="{}"'.format(state.reviewer_effort()),
    ]
    if output_path is not None:
        argv += ["--output-last-message", str(output_path)]
    return argv + ["resume", str(thread_id), "-"]


def start(cwd, root_session_id, role, sandbox, prompt, timeout, cancel_event=None):
    """Create a role thread and use its first turn as the first real ruling."""
    with tempfile.TemporaryDirectory(
        prefix="reviewer-agent-codex-", dir=stable_temp_root()
    ) as directory:
        output_path = Path(directory) / "last-message.txt"
        code, events, error = _run(
            fresh_argv(cwd, sandbox=sandbox, output_path=output_path),
            timeout=timeout,
            input_text=prompt,
            cancel_event=cancel_event,
        )
        try:
            output = output_path.read_text(encoding="utf-8")
        except OSError:
            output = ""
    thread_id = parse_thread_id(events)
    if not thread_id:
        detail = error.strip() or "no thread id captured (exit {})".format(code)
        return code or 1, "", detail
    published = state.publish_reviewer_session(cwd, root_session_id, role, thread_id)
    if published != thread_id:
        if cancel_event is None:
            return resume(published, cwd, sandbox, prompt, timeout)
        return resume(published, cwd, sandbox, prompt, timeout, cancel_event=cancel_event)
    return code, output, error


def resume(thread_id, cwd, sandbox, prompt, timeout, cancel_event=None):
    with tempfile.TemporaryDirectory(
        prefix="reviewer-agent-codex-", dir=stable_temp_root()
    ) as directory:
        output_path = Path(directory) / "last-message.txt"
        code, _events, error = _run(
            resume_argv(thread_id, cwd, sandbox, output_path=output_path),
            timeout=timeout,
            input_text=prompt,
            cancel_event=cancel_event,
        )
        try:
            output = output_path.read_text(encoding="utf-8")
        except OSError:
            output = ""
    return code, output, error


def review_turn(cwd, root_session_id, role, sandbox, prompt, timeout, cancel_event=None):
    thread_id = state.read_reviewer_session(cwd, root_session_id, role)
    if thread_id:
        if cancel_event is None:
            return resume(thread_id, cwd, sandbox, prompt, timeout)
        return resume(thread_id, cwd, sandbox, prompt, timeout, cancel_event=cancel_event)
    if cancel_event is None:
        return start(cwd, root_session_id, role, sandbox, prompt, timeout)
    return start(
        cwd, root_session_id, role, sandbox, prompt, timeout, cancel_event=cancel_event
    )
