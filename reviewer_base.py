#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Layer 0 of the reviewer package: primitives every other layer uses.

Step 1 of LIB_SPLIT_PLAN.md. This module imports NOTHING from the package -- that is what makes
it layer 0, and `tools/check_split_layering.py` fails if that ever stops being true.

What belongs here: filesystem-safe naming, atomic and compare-and-set publishing, truncation
helpers, the log, the writable state root, and process-tree termination. What does not:
anything that knows about projects, sessions, git, prompts or backends.

`terminate_process_tree` is deliberately duplicated from
`plugins/reviewer-agent-codex/reviewer_codex/process_tree.py` rather than shared. The two
packages install independently -- the Codex plugin is not on the Claude hooks' import path and
must not become a dependency of them -- so a shared copy would have to be vendored into one of
them anyway. Both copies implement the same algorithm; a fix to one belongs in the other.

Everything here is re-exported by `reviewer_lib` for back-compat, so existing `rl.short(...)`
call sites are unchanged.
"""
import datetime
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Hard cap on a single reviewer.log entry. Codex's `exec` transcript (everything
# it reads and reasons through) can be hundreds of KB; the log records metadata
# plus a short tail, never the full transcript.
LOG_ENTRY_MAX = 4000


# --------------------------------------------------------------------------
# environment / paths
# --------------------------------------------------------------------------
def reviewer_state_home():
    """Writable dir for flags / per-session reviewer ids / logs / reviews. In a plugin
    install the CODE lives under ${CLAUDE_PLUGIN_ROOT} (managed by the plugin system and
    overwritten on update), so writable state must live elsewhere. Precedence:
    REVIEWER_STATE_DIR, then the legacy REVIEWER_HOME (back-compat with the pre-plugin
    single-dir install), then ~/.claude/reviewer-agent."""
    env = os.environ.get("REVIEWER_STATE_DIR") or os.environ.get("REVIEWER_HOME")
    return Path(env) if env else Path.home() / ".claude" / "reviewer-agent"


def reviewer_home():
    # Back-compat alias: every historical reviewer_home() call site wanted the STATE dir.
    return reviewer_state_home()


# --------------------------------------------------------------------------
# filesystem-safe naming
# --------------------------------------------------------------------------
def _sanitize(path):
    s = str(path).strip("/").replace("/", "_").replace(" ", "_")
    return s or "root"


# --------------------------------------------------------------------------
# publishing: atomic replace, and compare-and-set
# --------------------------------------------------------------------------
def _atomic_write_text(path, text, encoding="utf-8"):
    """Publish `text` at `path` by writing a sibling temp file and renaming it into place.
    Truncate-then-write leaves a window in which a concurrent reader observes an EMPTY file;
    for the reviewer session id that read returns None and the reader briefs a SECOND
    reviewer, orphaning the first. os.replace within one directory is atomic, so a reader
    sees either the old content or the new, never a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ".{}.{}.tmp".format(path.name, uuid.uuid4().hex)
    try:
        with open(str(tmp), "w", encoding=encoding) as fh:
            fh.write(text)
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def _publish_if_absent(path, text, encoding="utf-8"):
    """Create `path` holding `text` ONLY if it does not exist. Returns the content that won:
    ours when we created it, the existing content when someone else got there first, or None
    when the file exists but is empty/unreadable (a caller may then take it over).

    os.link is the compare-and-set: it fails atomically if the destination exists, and it
    publishes a fully-written file, so a loser never reads a half-written winner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ".{}.{}.tmp".format(path.name, uuid.uuid4().hex)
    try:
        with open(str(tmp), "w", encoding=encoding) as fh:
            fh.write(text)
        try:
            os.link(str(tmp), str(path))
            return text
        except FileExistsError:
            pass
        except OSError:
            # Filesystem without hardlinks: degrade to last-writer-wins rather than crash
            # the hook. The window this reopens is the pre-existing one, not a new hazard.
            os.replace(str(tmp), str(path))
            return text
        try:
            existing = path.read_text(encoding=encoding)
        except OSError:
            return None
        return existing if existing.strip() else None
    finally:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------
def log(message):
    if len(message) > LOG_ENTRY_MAX:
        message = message[:LOG_ENTRY_MAX] + " ...[entry truncated]"
    p = reviewer_home() / "logs" / "reviewer.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with p.open("a", encoding="utf-8") as fh:
        fh.write("[{}] {}\n".format(ts, message))


# --------------------------------------------------------------------------
# truncation for log/report text
# --------------------------------------------------------------------------
def short(text, n=600):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " ...[truncated]"


def tail(text, n=600):
    text = (text or "").strip()
    return text if len(text) <= n else "...[head truncated] " + text[-n:]


# --------------------------------------------------------------------------
# process-tree termination
#
# A reviewer call is not one process. A codex commit audit runs under `workspace-write`
# precisely so it can run the project's tests, so the processes that outlive a cancelled review
# are its GRANDCHILDREN -- and a test runner that calls setsid, or any tool that starts its own
# process group, is out of reach of a plain group kill. Descendants are therefore enumerated
# from the kernel BEFORE anything is signalled, so a process cannot be reparented out of view
# while the sweep runs.
# --------------------------------------------------------------------------
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
            children = [
                pid for pid, candidate_parent, _group in fallback_rows
                if candidate_parent == parent
            ]
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
    """Terminate `root_pid` and every descendant, TERM first and KILL for survivors.

    Never signals this process or its own group: the hook has to survive long enough to report
    the outcome, and a fail-closed gate that killed itself would report nothing at all."""
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
