# A tick's `ok` pauses the watchdog during a background wait

## Problem

A session whose agent ended its turn with a never-ending background shell still running (a
webhook forwarder, a log follower) ticks every seven minutes until the cron's seven-day expiry.
Seen live on 2026-11-02: a Stripe webhook listener and a long-running Python job kept a
session in a background wait, and every tick replied `ok`.

The stop hook allows a background-wait stop before anything else (`stop_hook.py`, the `bg_wait`
branch), with no supervisor call and no watchdog bookkeeping. That is right for the agent's own
park: the watchdog stays armed to recover the strand. But the tick's own `ok` stop takes the same
branch, so the step that writes the pause marker (`_watchdog_on_stop_ok`) never runs, the idle
teardown never starts, and the cron never goes away.

## Why the ticks stay armed during a wait

One case needs them: a turn that ended on a dropped API connection while background work was
outstanding. The API-drop watcher holds off then, because the completion will start a turn, and a
job that never completes never starts one. The tick is what resumes that dropped work: with no
pause marker it says "if you have unfinished work, resume it; otherwise reply `ok`".

So a tick that replies `ok` has already answered the question the ticks exist to ask. It found
nothing unfinished.

## Change

In the background-wait branch, a tick turn whose whole reply is `ok` runs `_watchdog_on_stop_ok`
before allowing the stop, exactly as a paused tick's `ok` does outside a wait:

- no pause marker yet: it is written, and the idle countdown starts;
- a marker older than one interval: `teardown_ready` is armed, and the next tick deletes the cron.

A tick that did anything more than reply `ok` (it resumed work) is unchanged: the stop is still
allowed with no bookkeeping, and the watchdog stays armed. The agent's own park (not a tick) is
unchanged.

## The turn a background completion starts

A completion starts a turn that runs the UserPromptSubmit hook, with a prompt that begins
`<task-notification>` (checked live with a headless probe on 2026-11-02, Claude Code 2.1.312). The
prompt hook treats every non-tick prompt the same way: it clears the pause marker and
`teardown_ready`, and re-arms the cron when it was torn down or its ticks are presumed dead. So the
work a completion wakes is protected again without new code; a test pins that.

## Tests

- A tick's `ok` during a background wait writes the pause marker and calls no supervisor.
- With the marker a full interval old, it arms `teardown_ready` and keeps the marker's timestamp.
- A tick that resumed work during a background wait writes no marker.
- A `<task-notification>` prompt clears the pause and re-arms the cron after a teardown.
