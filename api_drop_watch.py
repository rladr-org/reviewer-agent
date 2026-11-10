#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""The API-drop watcher: run by the agent as a background command, one per Claude session.

Claude Code ends a turn when its API connection drops mid-response, runs no Stop hook after it,
and does not retry it; the session then idles until the next watchdog tick. Claude Code DOES
start a turn when a background command exits, so this script waits -- reading the transcript,
no model calls -- and exits with its instruction once a turn ended on a drop and nothing has
happened since. See docs/plans/watchdog_api_drop_watcher.md.

    api_drop_watch.py --cwd <project> --session <claude session id> --transcript <path>
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402
import reviewer_manifest  # noqa: E402
import reviewer_state  # noqa: E402
import reviewer_transcript  # noqa: E402

GRACE_SECS = 20.0
BACKOFF_FIRES = 3
BACKOFF_WINDOW_SECS = 600.0
BACKOFF_GRACE_SECS = 300.0
POLL_SECS = 2.0
# Re-check outstanding background work this often while it holds a due drop back: the check
# parses the whole transcript.
BACKGROUND_RECHECK_SECS = 30.0


def _env_secs(name, default):
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value >= 0 else default


def fire_due(quiet_since, now, fires, grace=None):
    """True once the drop (or the latest sub-agent event after it) is older than the grace. Three
    fires within ten minutes stretch the grace to five minutes, so an outage brings a resume every
    few minutes rather than a burst."""
    if grace is None:
        grace = _env_secs("REVIEWER_API_DROP_GRACE_SECS", GRACE_SECS)
    recent = [f for f in fires if now - f <= BACKOFF_WINDOW_SECS]
    if len(recent) >= BACKOFF_FIRES:
        grace = max(grace, BACKOFF_GRACE_SECS)
    return now - quiet_since >= grace


def background_work_outstanding(transcript_path):
    """Is the session parked on background work whose completion will start a turn by itself?
    An error reads as yes: an automatic resume on top of live work is the costly mistake, and
    the cron tick still recovers a session this leaves alone."""
    reviewer_transcript._reset_transcript_cache()
    try:
        return bool(reviewer_manifest.background_wait_state(transcript_path, strict=True)[0])
    except Exception:
        return True


def _launcher_pid():
    """The Claude Code process that started this watcher: it exports CLAUDE_PID into every shell
    it starts. None when absent (an older Claude Code, or a watcher started by hand)."""
    try:
        pid = int(os.environ.get("CLAUDE_PID", ""))
    except ValueError:
        return None
    return pid if pid > 1 else None


def _launcher_session(pid):
    """The session the launcher runs now, from Claude Code's per-process registry entry
    (<config dir>/sessions/<pid>.json). It changes on an in-process session switch even when the
    old transcript records no handoff. None when it cannot tell: the file is Claude Code's
    internal state, so anything unexpected leaves the watch running."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    try:
        with open(os.path.join(base, "sessions", "{}.json".format(pid))) as fh:
            entry = json.load(fh)
    except (OSError, ValueError):
        return None
    sid = entry.get("sessionId") if isinstance(entry, dict) else None
    return sid if isinstance(sid, str) and sid else None


# A watcher's command line as `ps` prints it: the script, then (after any options) --session.
_WATCHER_ARGS = re.compile(r"(?:^|[\s/])api_drop_watch\.py\s(?:.*\s)?--session\s")
_CLAUDE_PID_IN_ENV = re.compile(r"(?:^|\s)CLAUDE_PID=(\d+)(?=\s|$)")


def _is_watcher(command):
    """A python process running api_drop_watch.py with --session: never the shell wrapper around
    one, a grep, or an editor showing the file."""
    parts = command.split(None, 1)
    return (len(parts) == 2 and os.path.basename(parts[0]).lower().startswith("python")
            and bool(_WATCHER_ARGS.search(parts[1])))


def orphaned_watchers(rows, me, claude_pid_of, running):
    """The watcher pids in `rows` ((pid, ppid, command) per process) whose Claude Code process has
    exited: the CLAUDE_PID in their environment no longer runs, or, without one, their shell (or
    the watcher itself) was reparented to pid 1. Never `me`. A watcher inside a running Claude
    Code process is not selected even if that process moved to another session: ending it would
    start a failed-task turn there (docs/plans/watchdog_api_drop_watcher_exit.md)."""
    parent = {pid: ppid for pid, ppid, _ in rows}
    found = []
    for pid, ppid, command in rows:
        if pid == me or not _is_watcher(command):
            continue
        launcher = claude_pid_of(pid)
        if launcher is not None:
            orphaned = not running(launcher)
        else:
            orphaned = ppid == 1 or parent.get(ppid) == 1
        if orphaned:
            found.append(pid)
    return found


def _claude_pid_of(pid):
    """The CLAUDE_PID another process started with: /proc/<pid>/environ where there is one, else
    `ps eww`, which prints the environment after the command. None when it cannot tell."""
    try:
        with open("/proc/{}/environ".format(pid), "rb") as fh:
            env = fh.read().decode("utf-8", "replace").split("\0")
    except OSError:
        out = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout
        found = _CLAUDE_PID_IN_ENV.findall(out)
        value = found[-1] if found else ""  # the environment follows the arguments
    else:
        value = next((e[len("CLAUDE_PID="):] for e in env if e.startswith("CLAUDE_PID=")), "")
    return int(value) if value.isdigit() and int(value) > 1 else None


def _own_processes():
    """(pid, ppid, command) for every process this user owns."""
    out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,uid=,command="],
                         capture_output=True, text=True, timeout=10, check=True).stdout
    uid = os.getuid()
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and all(p.isdigit() for p in parts[:3]) and int(parts[2]) == uid:
            rows.append((int(parts[0]), int(parts[1]), parts[3]))
    return rows


def _end_orphaned_watchers(me):
    """End the watchers whose Claude Code process has exited. Watchers from 1.4.5 have no exit of
    their own and would otherwise poll until someone kills them. Nothing listens to an orphan, so
    ending one starts no turn anywhere. Any error leaves the other processes alone.
    REVIEWER_API_DROP_REAP=0 turns this off."""
    if os.name != "posix" or os.environ.get("REVIEWER_API_DROP_REAP") == "0":
        return
    try:
        rows = _own_processes()
        commands = {pid: command for pid, _, command in rows}
        for pid in orphaned_watchers(rows, me, _claude_pid_of, reviewer_state._pid_running):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
            rl.log("[api-drop-watch] ended orphaned watcher pid {}: {}".format(
                pid, commands[pid][:300]))
    except Exception as e:
        try:
            rl.log("[api-drop-watch] orphaned-watcher cleanup skipped ({}: {})".format(
                type(e).__name__, e))
        except Exception:
            pass


class _Tail:
    """Feeds the transcript's complete lines to the drop scan as they are appended, and notes a
    handoff: a `continued-in` record with no message after it means the conversation now lives
    under another session id, the rule Claude Code itself applies to supersede a session."""

    def __init__(self, path, scan):
        self._path = path
        self._scan = scan
        self._offset = 0
        self.continued_in = None

    def poll(self):
        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return
        end = data.rfind(b"\n") + 1  # a line still being written is read on the next poll
        self._offset += end
        for line in data[:end].splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type") if isinstance(ev, dict) else None
            if kind == "continued-in":
                self.continued_in = str(ev.get("continuedInSessionId") or "another session")
            elif kind in ("user", "assistant"):
                self.continued_in = None
            self._scan.feed(ev)


def _held_by_another(lease, me):
    pid = lease.get("pid")
    return (isinstance(pid, int) and pid != me and reviewer_state._api_drop_lease_fresh(lease)
            and reviewer_state._pid_running(pid))


def watch(cwd, sid, transcript):
    me = os.getpid()
    _end_orphaned_watchers(me)
    lease = rl.read_api_drop_lease(cwd, sid)
    if _held_by_another(lease, me):
        print("The API-drop watcher is already running for this session (pid {}); nothing to "
              "do.".format(lease["pid"]))
        return 0
    poll = _env_secs("REVIEWER_API_DROP_POLL_SECS", POLL_SECS)
    scan = rl.api_drop_scan()
    tail = _Tail(transcript, scan)
    launcher = _launcher_pid()
    first_seen = {}
    background_checked = {}
    while True:
        # A lost session ends the watch before anything else, without touching the lease: this
        # pid stops running, so the lease reads as dead at once. Claude Code does not kill a
        # background shell when it exits, and a session that continues under a new id keeps its
        # process; either way a leftover polled for hours, and could have resumed a drop into a
        # conversation it no longer belongs to (docs/plans/watchdog_api_drop_watcher_exit.md).
        if launcher is not None and not reviewer_state._pid_running(launcher):
            print("The Claude Code process that started this API-drop watcher (pid {}) has "
                  "exited; the watcher stopped.".format(launcher))
            return 0
        current = _launcher_session(launcher) if launcher is not None else None
        if current is not None and current != sid:
            print("The Claude Code process that started this API-drop watcher (pid {}) now runs "
                  "session {}, which starts its own API-drop watcher; this one (for session {}) "
                  "stopped. No action needed.".format(launcher, current, sid))
            return 0
        tail.poll()
        if tail.continued_in is not None:
            print("This conversation continued as session {}, which starts its own API-drop "
                  "watcher; this one stopped. No action needed.".format(tail.continued_in))
            return 0
        lease = rl.read_api_drop_lease(cwd, sid)
        if _held_by_another(lease, me):
            print("Another API-drop watcher took over for this session; this one stopped.")
            return 0
        lease.update(pid=me, ts=rl.api_drop_lease_now())
        rl.write_api_drop_lease(cwd, sid, lease)
        drop = scan.pending(fired_uuid=lease.get("fired_uuid"))
        now = time.time()
        if drop is not None:
            key = drop.get("uuid") or drop.get("ts") or ""
            quiet = drop.get("quiet_since") or first_seen.setdefault(key, now)
            fires = [f for f in lease.get("fires") or [] if isinstance(f, (int, float))]
            if (fire_due(quiet, now, fires)
                    and rl.is_stopcontinue_watchdog_effective(cwd)
                    and not rl.is_watchdog_disarm_set(cwd, sid)
                    and now - background_checked.get(key, 0) >= BACKGROUND_RECHECK_SECS):
                background_checked[key] = now
                if not background_work_outstanding(transcript):
                    lease.update(fired_uuid=drop.get("uuid"), ts=rl.api_drop_lease_now(),
                                 fires=[f for f in fires if now - f <= BACKOFF_WINDOW_SECS]
                                 + [now])
                    rl.write_api_drop_lease(cwd, sid, lease)
                    print(rl.api_drop_resume_message(cwd, sid, transcript, drop))
                    return 0
        time.sleep(poll)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--transcript", required=True)
    args = parser.parse_args(argv)
    try:
        return watch(args.cwd, args.session, args.transcript)
    except Exception as e:
        print("The API-drop watcher stopped on an error ({}: {}). Relaunch it: {}".format(
            type(e).__name__, e, rl.api_drop_watcher_launch_instruction(
                args.cwd, args.session, args.transcript)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
