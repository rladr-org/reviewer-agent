# SPDX-License-Identifier: MPL-2.0
"""A background SHELL launched in an EARLIER turn must keep suppressing stopContinue --
but only while the agent is demonstrably still parked on it.

Observed failure (2026-07-24): an agent launched a multi-hour background watcher, then had
several more turns (probes, status reports). At the next turn end it was still parked on that
watcher, yet stopContinue fired and the reviewer told it to "keep going", because:

  * the job-state layer only applies to background-JOB sessions (CLAUDE_JOB_DIR unset here),
  * the watcher is a background shell, not a Monitor, and
  * the launch scan only looks at events AFTER the last turn boundary, so a launch from an
    earlier turn is invisible.

The opposite failure matters just as much: that same session held a SUPERSEDED watcher that
died without ever emitting a terminal notification. Suppressing on it forever would mute
stopContinue permanently -- exactly when the nudge is most needed. So the wait is bounded by
the staleness window, measured from the last time anything referenced the task.
"""
import datetime

import reviewer_lib as rl
from test_reviewer_lib import _asst, _bg_bash, _human, _task_notification, _transcript


def _iso(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.isoformat().replace("+00:00", "Z")


def _at(ev, minutes_ago):
    ev = dict(ev)
    ev["timestamp"] = _iso(minutes_ago)
    return ev


def _bg_shell_ack(tool_use_id, task_id, minutes_ago):
    """The harness's real acknowledgement for `run_in_background` Bash -- the only place the
    background TASK id (as opposed to the tool_use id) appears."""
    body = (
        "Command running in background with ID: {}. Output is being written to: "
        "/tmp/tasks/{}.output. You will be notified when it completes.".format(
            task_id, task_id
        )
    )
    return _at(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}
                ],
            },
        },
        minutes_ago,
    )


def test_background_shell_from_an_earlier_turn_is_still_outstanding(tmp_path):
    # The launch is 90 minutes old -- older than the staleness window -- but the agent is
    # still reporting on that watcher by name, which is the signal that it is still parked.
    path = _transcript(
        tmp_path,
        _at(_human("kick off the long sweep"), 95),
        _at(_bg_bash("toolu_B"), 91),
        _bg_shell_ack("toolu_B", "b9omfzynx", 90),
        _at(_asst("Sweep running in the background."), 89),
        _at(_human("how is it going?"), 6),  # a LATER turn boundary
        _at(_asst("Arm 3 still generating; watcher b9omfzynx will re-invoke me."), 5),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_a_long_gpu_run_keeps_suppressing_for_many_hours(tmp_path):
    # The point of the background-shell window: an 8-hour GPU run is SILENT by design, and
    # the agent must not be nudged into spending tokens while it waits. A monitor-length
    # window (60 min) would have fired here; the shell window must not.
    path = _transcript(
        tmp_path,
        _at(_human("launch the 8 hour sweep"), 8 * 60 + 10),
        _at(_bg_bash("toolu_B"), 8 * 60 + 6),
        _bg_shell_ack("toolu_B", "bgpurun", 8 * 60 + 5),
        _at(_asst("Sweep running; the watcher will re-invoke me when it lands."), 8 * 60),
        _at(_human("still going?"), 4),
        _at(_asst("Yes -- nothing to do until the sentinel lands."), 3),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_shell_silent_past_the_window_stops_suppressing(tmp_path):
    # The b6x4rvxz4 case: a watcher relaunched under a new id, the old one never reported a
    # terminal status. Past the (long) shell window it must stop muting stopContinue, or a
    # silently dead watcher disables the nudge for the rest of the session.
    path = _transcript(
        tmp_path,
        _at(_human("start the run"), 20 * 60),
        _at(_bg_bash("toolu_OLD"), 20 * 60 - 1),
        _bg_shell_ack("toolu_OLD", "b6x4rvxz4", 20 * 60 - 2),
        _at(_asst("Watcher armed."), 20 * 60 - 3),
        _at(_human("anything left?"), 4),
        _at(_asst("Everything is committed and pushed; nothing further to do."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_shell_window_is_env_overridable(tmp_path, monkeypatch):
    # Operators running multi-day jobs can widen it (or narrow it) without a code change.
    monkeypatch.setenv("REVIEWER_BG_SHELL_STALE_SECS", str(30 * 24 * 3600))
    path = _transcript(
        tmp_path,
        _at(_human("start the very long run"), 20 * 60),
        _at(_bg_bash("toolu_OLD"), 20 * 60 - 1),
        _bg_shell_ack("toolu_OLD", "blongrun", 20 * 60 - 2),
        _at(_asst("Watcher armed."), 20 * 60 - 3),
        _at(_human("anything left?"), 4),
        _at(_asst("Still waiting on it."), 3),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_terminal_notification_ends_the_wait_even_when_fresh(tmp_path):
    # Terminal evidence WINS over recency, or the agent could never legitimately stop.
    path = _transcript(
        tmp_path,
        _at(_human("kick off the long sweep"), 30),
        _at(_bg_bash("toolu_B"), 29),
        _bg_shell_ack("toolu_B", "b9omfzynx", 28),
        _at(_task_notification("toolu_B", task_id="b9omfzynx", status="completed"), 5),
        _at(_asst("Watcher b9omfzynx finished; here are the results."), 4),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_only_the_unfinished_shell_keeps_the_wait_open(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("run the batch"), 40),
        _at(_bg_bash("toolu_1"), 39),
        _bg_shell_ack("toolu_1", "bfinished", 38),
        _at(_task_notification("toolu_1", task_id="bfinished", status="completed"), 30),
        _at(_asst("First batch done."), 29),
        _at(_human("now the long one"), 20),
        _at(_bg_bash("toolu_2"), 19),
        _bg_shell_ack("toolu_2", "brunning", 18),
        _at(_human("status?"), 4),
        _at(_asst("brunning is still going."), 3),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_all_shells_finished_allows_the_stop(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("run the batch"), 20),
        _at(_bg_bash("toolu_1"), 19),
        _bg_shell_ack("toolu_1", "bfinished", 18),
        _at(_task_notification("toolu_1", task_id="bfinished", status="completed"), 10),
        _at(_human("anything else?"), 4),
        _at(_asst("No -- everything finished and is committed."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False
