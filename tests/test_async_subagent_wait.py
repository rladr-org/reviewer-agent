# SPDX-License-Identifier: MPL-2.0
"""Async SUBAGENTS must suppress stopContinue for their whole lifetime (Amendment 7).

Observed failure (2026-07-29, session `792674b5`): the operator reported ~10 nudges while
subagents were working. Replaying that transcript against the reviewer log, 8 forced
continuations fired while 3 (twice: 2) async subagents were genuinely in flight -- every one
logging `bg_wait=False`. Two independent causes:

  1. The `Agent` tool now runs subagents in the background BY DEFAULT, so requiring a truthy
     `run_in_background` misclassifies every default launch as foreground. In that transcript
     23 `Agent` blocks carried the key only 11 times, yet 15 async launch acks were emitted.
  2. A fan-out's siblings leave the current-turn scan: a `<task-notification>` is a turn
     boundary, so when the FIRST of N subagents completes, the still-running N-1 -- launched
     before that boundary -- become invisible. Only the last-completing sibling was ever
     protected, and fan-out is the common case.

The opposite hazard is bounded as always: a subagent that dies without ever notifying must
not mute stopContinue for the rest of the session, so the wait expires after the staleness
window. That window is measured from the LAUNCH, because the ack tells the agent never to
quote the agentId -- so unlike a background shell, a healthy subagent never re-mentions it.
"""
import datetime

import reviewer_lib as rl
from test_reviewer_lib import _asst, _human, _task_notification, _transcript


def _iso(minutes_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return t.isoformat().replace("+00:00", "Z")


def _at(ev, minutes_ago):
    ev = dict(ev)
    ev["timestamp"] = _iso(minutes_ago)
    return ev


def _agent(tool_use_id, minutes_ago, run_in_background=None):
    """An `Agent` tool_use. `run_in_background=None` omits the key entirely -- the DEFAULT
    shape the harness emits now, and the one the old truthy test misread as foreground."""
    inp = {"description": "d", "subagent_type": "Explore", "prompt": "p"}
    if run_in_background is not None:
        inp["run_in_background"] = run_in_background
    return _at({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Agent", "id": tool_use_id, "input": inp}]}}, minutes_ago)


def _fg_bash(tool_use_id, minutes_ago):
    return _at({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "id": tool_use_id,
         "input": {"command": "pytest -q", "description": "run tests"}}]}}, minutes_ago)


def _async_ack(tool_use_id, agent_id, minutes_ago, stamped=True):
    """The harness's real acknowledgement for a background subagent -- the only place the
    AGENT id appears (the launch scan sees only tool_use ids). Trimmed to the load-bearing
    lines; the id is synthetic."""
    body = (
        "Async agent launched successfully. (This tool result is internal metadata - never "
        "quote or paste any part of it, including the agentId below, into a user-facing "
        "reply.)\nagentId: {} (internal ID - do not mention to user.)\nThe agent is working "
        "in the background. You will be notified automatically when it completes.".format(
            agent_id)
    )
    ev = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}]}}
    return _at(ev, minutes_ago) if stamped else ev


# --------------------------------------------------------------------------
# Root cause 1: the launch-flag default flipped.
# --------------------------------------------------------------------------
def test_agent_launch_without_run_in_background_is_outstanding(tmp_path):
    # No ack in this transcript, so ONLY the launch scan can catch it -- isolating the
    # default. Background is now the Agent tool's default, so an absent key means background.
    path = _transcript(
        tmp_path,
        _at(_human("fan out the review lenses"), 6),
        _agent("toolu_A", 5),
        _at(_asst("Dispatched the lens; it will notify me when it lands."), 4),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_explicitly_synchronous_agent_is_not_outstanding(tmp_path):
    # `run_in_background: false` returns inline -- it never ends the turn to wait, and the
    # harness emits no async ack for it.
    path = _transcript(
        tmp_path,
        _at(_human("check this one thing"), 6),
        _agent("toolu_A", 5, run_in_background=False),
        _at(_asst("The check came back clean; everything is committed."), 4),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_an_inline_result_resolves_the_launch_whatever_the_flags_said(tmp_path):
    # The launch flag is no longer the discriminator, so the EVIDENCE has to be: a tool_use
    # whose result came back inline demonstrably did not outlive the turn. This keeps a
    # subagent that runs inline (however the harness decides that) from muting stopContinue
    # for the whole turn.
    path = _transcript(
        tmp_path,
        _at(_human("summarize the module"), 6),
        _agent("toolu_F", 5),
        _at({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_F", "content": "the summary"}]}}, 4),
        _at(_asst("Here is the summary; nothing further to do."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_a_launch_ack_is_not_an_inline_result(tmp_path):
    # The inverse: a background ack IS an inline tool_result for the launching tool_use id,
    # but it announces work that is still RUNNING ("You will be notified automatically when it
    # completes"), so it must never read as a returned result.
    #
    # The unit assertion is the load-bearing one -- verified by removing the ack exclusion,
    # which makes `_inline_resolved_tool_use_ids` claim toolu_A. The end-to-end assertion
    # below does NOT pin the exclusion (the whole-transcript async layer answers True either
    # way); it is here to state the behaviour the pair is protecting.
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the lens"), 6),
        _agent("toolu_A", 5),
        _async_ack("toolu_A", "astillrunning", 4),
        _at(_asst("Lens dispatched; I will report when it returns."), 3),
    )
    events = rl._transcript_events(path)
    assert all("toolu_A" not in rl._inline_resolved_tool_use_ids(ev) for ev in events)
    assert rl.has_outstanding_background_task(path) is True


def test_bash_without_the_flag_stays_foreground(tmp_path):
    # The widened default is scoped to the SUBAGENT tools. For Bash, foreground is still the
    # default, so an absent flag must not suppress -- otherwise every ordinary shell command
    # would mute stopContinue.
    path = _transcript(
        tmp_path,
        _at(_human("run the suite"), 6),
        _fg_bash("toolu_B", 5),
        _at(_asst("Suite is green; nothing further to do."), 4),
    )
    assert rl.has_outstanding_background_task(path) is False


# --------------------------------------------------------------------------
# Root cause 2: fan-out siblings must survive the boundary the first completion creates.
# --------------------------------------------------------------------------
def test_fanout_with_one_completion_keeps_the_siblings_outstanding(tmp_path):
    # The exact reported shape: three lenses launched together, ONE returns. Its
    # <task-notification> becomes the turn boundary, so the current-turn scan sees no
    # launches at all -- yet two lenses are still running.
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the three review lenses"), 20),
        _agent("toolu_1", 19),
        _async_ack("toolu_1", "a19a89fd7fba3f18a", 19),
        _agent("toolu_2", 18),
        _async_ack("toolu_2", "afa56622b4bf7f4a5", 18),
        _agent("toolu_3", 17),
        _async_ack("toolu_3", "a279acc7dea7c67f5", 17),
        _at(_task_notification("toolu_2", task_id="afa56622b4bf7f4a5"), 3),
        _at(_asst("Lens 2 returned. The other two lenses have not."), 2),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_fanout_fully_completed_allows_the_stop(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the three review lenses"), 20),
        _agent("toolu_1", 19),
        _async_ack("toolu_1", "aone", 19),
        _agent("toolu_2", 18),
        _async_ack("toolu_2", "atwo", 18),
        _agent("toolu_3", 17),
        _async_ack("toolu_3", "athree", 17),
        _at(_task_notification("toolu_1", task_id="aone"), 6),
        _at(_task_notification("toolu_2", task_id="atwo"), 5),
        _at(_task_notification("toolu_3", task_id="athree"), 4),
        _at(_asst("All three lenses returned and I have applied their findings."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_a_subagent_launched_in_an_earlier_turn_is_still_tracked(tmp_path):
    # Several turns of unrelated work after the launch: the current-turn scan cannot see it,
    # so only whole-transcript tracking keeps the wait open.
    path = _transcript(
        tmp_path,
        _at(_human("start the deep audit"), 40),
        _agent("toolu_A", 39),
        _async_ack("toolu_A", "adeepaudit", 39),
        _at(_asst("Audit dispatched."), 38),
        _at(_human("meanwhile, what does the config say?"), 10),
        _at(_asst("It pins torch 2.4."), 9),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_terminal_evidence_wins_over_a_fresh_launch(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the lens"), 10),
        _agent("toolu_A", 9),
        _async_ack("toolu_A", "afinished", 9),
        _at(_task_notification("toolu_A", task_id="afinished"), 3),
        _at(_asst("The lens returned; its findings are applied and committed."), 2),
    )
    assert rl.has_outstanding_background_task(path) is False


# --------------------------------------------------------------------------
# Bounding: a subagent that dies silently must not mute stopContinue forever.
# --------------------------------------------------------------------------
def test_launch_older_than_the_window_stops_suppressing(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the lens"), 9 * 60),
        _agent("toolu_A", 9 * 60 - 1),
        _async_ack("toolu_A", "adeadlens", 9 * 60 - 1),
        _at(_human("anything left?"), 4),
        _at(_asst("Everything is committed and pushed; nothing further to do."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_async_window_is_env_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_ASYNC_AGENT_STALE_SECS", str(30 * 24 * 3600))
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the very long lens"), 9 * 60),
        _agent("toolu_A", 9 * 60 - 1),
        _async_ack("toolu_A", "alonglens", 9 * 60 - 1),
        _at(_human("anything left?"), 4),
        _at(_asst("Still waiting on the lens."), 3),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_launch_without_a_timestamp_never_suppresses(tmp_path):
    # Unified fail-open no-timestamp rule (Amendment 3): an undateable launch cannot be
    # bounded, so the CROSS-TURN layer must not suppress on it. A later turn boundary is
    # required to isolate that layer -- within its own turn the launch scan reports the
    # launch outstanding regardless of the ack's timestamp, which is correct and is bounded
    # by the turn itself.
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the lens"), 40),
        _agent("toolu_A", 39),
        _async_ack("toolu_A", "aundateable", 39, stamped=False),
        _at(_asst("Lens dispatched."), 38),
        _at(_human("anything left?"), 4),
        _at(_asst("Everything is committed and pushed; nothing further to do."), 3),
    )
    assert rl.has_outstanding_background_task(path) is False
    assert rl._async_agent_launch_times(rl._transcript_events(path)) == {}


def test_live_ids_are_evaluated_against_a_supplied_clock(tmp_path):
    # Historical replay needs "was it live AT THE STOP", not at wall clock -- the same
    # `now=` convention Amendment 3 established for monitors.
    path = _transcript(
        tmp_path,
        _at(_human("dispatch the lens"), 9 * 60),
        _agent("toolu_A", 9 * 60 - 1),
        _async_ack("toolu_A", "areplay", 9 * 60 - 1),
    )
    events = rl._transcript_events(path)
    stop_ts = datetime.datetime.now(datetime.timezone.utc).timestamp() - (9 * 60 - 5) * 60
    assert rl._live_async_agent_ids(events, now=stop_ts) == {"areplay"}
    assert rl._live_async_agent_ids(events) == set()  # wall clock: long past the window
