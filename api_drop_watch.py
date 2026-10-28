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


class _Tail:
    """Feeds the transcript's complete lines to the drop scan as they are appended."""

    def __init__(self, path, scan):
        self._path = path
        self._scan = scan
        self._offset = 0

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
            self._scan.feed(ev)


def _held_by_another(lease, me):
    pid = lease.get("pid")
    return (isinstance(pid, int) and pid != me and reviewer_state._api_drop_lease_fresh(lease)
            and reviewer_state._pid_running(pid))


def watch(cwd, sid, transcript):
    me = os.getpid()
    lease = rl.read_api_drop_lease(cwd, sid)
    if _held_by_another(lease, me):
        print("The API-drop watcher is already running for this session (pid {}); nothing to "
              "do.".format(lease["pid"]))
        return 0
    poll = _env_secs("REVIEWER_API_DROP_POLL_SECS", POLL_SECS)
    scan = rl.api_drop_scan()
    tail = _Tail(transcript, scan)
    first_seen = {}
    background_checked = {}
    while True:
        lease = rl.read_api_drop_lease(cwd, sid)
        if _held_by_another(lease, me):
            print("Another API-drop watcher took over for this session; this one stopped.")
            return 0
        lease.update(pid=me, ts=rl.api_drop_lease_now())
        rl.write_api_drop_lease(cwd, sid, lease)
        tail.poll()
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
