# SPDX-License-Identifier: MPL-2.0
"""Best-effort termination for a process and all of its POSIX descendants."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def _process_table():
    executable = shutil.which("ps")
    if not executable:
        return []
    try:
        result = subprocess.run(
            [executable, "-axo", "pid=,ppid=,pgid="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines():
        try:
            pid, parent_pid, group_id = (int(value) for value in line.split())
        except (TypeError, ValueError):
            continue
        rows.append((pid, parent_pid, group_id))
    return rows


def _darwin_children(parent_pid):
    try:
        import ctypes

        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_listchildpids
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        function.restype = ctypes.c_int
        values = (ctypes.c_int * 16384)()
        count = function(int(parent_pid), values, ctypes.sizeof(values))
    except (AttributeError, OSError, ValueError):
        return None
    if count < 0:
        return None
    return [pid for pid in values[:count] if pid > 1]


def _proc_children(parent_pid):
    task_root = Path("/proc") / str(parent_pid) / "task"
    if not task_root.is_dir():
        return None
    children = set()
    try:
        task_paths = list(task_root.iterdir())
    except OSError:
        return None
    read_any = False
    for task_path in task_paths:
        try:
            text = (task_path / "children").read_text(encoding="ascii")
        except OSError:
            continue
        read_any = True
        for value in text.split():
            try:
                pid = int(value)
            except ValueError:
                continue
            if pid > 1:
                children.add(pid)
    return sorted(children) if read_any else None


def _fallback_children(parent_pid, rows):
    return [pid for pid, candidate_parent, _group in rows if candidate_parent == parent_pid]


def _kernel_children(parent_pid):
    if sys.platform == "darwin":
        return _darwin_children(parent_pid)
    return _proc_children(parent_pid)


def _descendants(root_pid):
    fallback_rows = None
    found = []
    pending = [int(root_pid)]
    seen = {int(root_pid)}
    while pending:
        parent = pending.pop()
        children = _kernel_children(parent)
        if children is None:
            if fallback_rows is None:
                fallback_rows = _process_table()
            children = _fallback_children(parent, fallback_rows)
        for pid in children:
            if pid in seen:
                continue
            seen.add(pid)
            try:
                group_id = os.getpgid(pid)
            except OSError:
                group_id = None
            found.append((pid, group_id))
            pending.append(pid)
    return found


def _signal_pid(pid, signal_number):
    try:
        os.kill(pid, signal_number)
    except OSError:
        pass


def _signal_group(group_id, signal_number):
    try:
        os.killpg(group_id, signal_number)
    except OSError:
        pass


def _is_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process_tree(root_pid, include_root=True, grace=0.5):
    """Terminate descendants even when they created independent process groups.

    Descendants are captured before any parent is killed so session-creating
    children cannot be reparented out of reach. Kernel child enumeration is used
    on macOS and Linux; ``ps`` and the root process group are fallbacks.
    """
    if os.name != "posix":
        return
    root_pid = int(root_pid)
    targets = _descendants(root_pid)
    if include_root:
        try:
            root_group = os.getpgid(root_pid)
        except OSError:
            root_group = root_pid
        targets.append((root_pid, root_group))
    if not targets:
        return

    own_pid = os.getpid()
    own_group = os.getpgrp()
    pids = {pid for pid, _group in targets if pid > 1 and pid != own_pid}
    groups = {
        group
        for _pid, group in targets
        if group is not None and group > 1 and group != own_group
    }

    for group in groups:
        _signal_group(group, signal.SIGTERM)
    for pid in pids:
        _signal_pid(pid, signal.SIGTERM)

    deadline = time.monotonic() + max(0.0, float(grace))
    while time.monotonic() < deadline and any(_is_alive(pid) for pid in pids):
        time.sleep(0.02)

    surviving = {pid for pid in pids if _is_alive(pid)}
    if surviving:
        for group in groups:
            _signal_group(group, signal.SIGKILL)
        for pid in surviving:
            _signal_pid(pid, signal.SIGKILL)
