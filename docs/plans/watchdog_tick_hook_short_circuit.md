# The prompt hook answers a watchdog tick that has nothing to do

## Problem

A CronCreate job can only enqueue a prompt into its session, so every watchdog tick is a full
model turn. Every request in that turn re-sends the whole conversation. On 2026-11-09 an idle
tick in a long session cost two requests of about 150K tokens each (99% cache reads): one to
Read the marker files, one to reply `ok`. The operator's loop panel showed 778.5K tokens per run
over 11 runs, because ticks that found finished background work went on to handle it.

Most ticks have nothing to do, and that is decidable without a model:

- **The operator has the turn.** The disarm marker is set (an approved stop, an interrupt, or a
  paused background wait), so the tick's own instruction is to reply `ok` and do nothing else.
  After an approved stop the cron keeps ticking until the idle teardown: the first tick after the
  disarm is an interval old writes `teardown_ready` from its Stop hook, and the tick after that
  deletes the cron. So every approved stop cost two or three idle turns.
- **The session is parked on background work.** The harness starts a turn when that work ends.
  Since 1.4.6, a tick that replies a bare `ok` there sets the pause
  (docs/plans/watchdog_tick_pause_during_background_wait.md). But the model often added a
  sentence ("ok, still waiting on X"), which is not a bare `ok`, so the pause was not set and the
  ticks kept running.

## Change

Every tick already passes through the UserPromptSubmit hook before the model sees it. The hook
stamps tick liveness there and marks the pause after an interrupt. It now also decides the
tick's outcome. While the watchdog is effective, after those two steps:

1. **`teardown_ready` exists:** the tick goes through to the model, which deletes the cron. Only
   the model can call CronDelete.
2. **The disarm marker is at least one interval old:** the hook writes `teardown_ready`, as the
   Stop hook does for an aged tick's `ok`, and lets the tick through to delete the cron. This
   makes the teardown tick the first tick after the interval, one turn per idle period instead of
   two or three.
3. **The disarm marker is set and younger than that:** the hook blocks the prompt
   (`decision: block`). No model request is made. The marker is not refreshed, so it ages toward
   the teardown in step 2.
4. **No disarm marker, and the transcript shows a background wait** (the same
   `background_wait_state` the Stop hook uses; a session kept open only by a listening server is
   not a wait): the hook sets the disarm marker, as the Stop hook does for a bare `ok` in a wait,
   and blocks. A background completion clears the pause through the existing paths.
5. **Otherwise** the session is idle with no stop approved: the tick goes through, so the model
   resumes the unfinished work. This is the case the watchdog exists for.

With the watchdog not effective (a flag file missing), the hook returns early as today, and the
tick goes through and deletes the cron.

A tick is exactly the prompt this session's cron is armed with (`watchdog_tick_prompt` for this
project and session). Every place the hook asks "is this a tick?" uses that test: the liveness
stamp, never arming on a tick, the interrupt pause, skipping the API-drop watcher request, and the
block. The hook used to match the label anywhere in the prompt, so an operator's message that
mentioned or quoted the tick stamped liveness and held off a dead cron's re-arm for two
intervals. With a block, it would also have been swallowed.

No cron can carry another wording. The wording last changed on 2026-09-30, crons expire after
seven days, and a session's hooks and its cron come from the same install. **When the wording
changes again, the previous wording must stay recognized for at least seven days.** An
unrecognized tick counts as an operator prompt, which clears the pause, so the model could resume
work the operator had paused.

A block shows its reason to the operator. It happens at most once per idle period after an
approved stop, since the next tick is the teardown. Claude Code also appends the blocked prompt
("Original prompt: ..."), which for a tick is a paragraph of instructions the operator never
typed. The block therefore sets `hookSpecificOutput.suppressOriginalPrompt: true`, which Claude
Code's hook schema describes as "When decision is "block", omit the original prompt from the block
message". The operator then sees one line: `UserPromptSubmit operation blocked by hook:` and
the block's reason, `Watchdog: no API failure detected` (the operator's wording, 2026-11-10). The reason
stays: without one, Claude Code 2.1.322 prints its default `Blocked by hook`. Which case blocked
the tick goes to `reviewer.log` only. No hook output hides the notice entirely: in Claude
Code 2.1.322, every way a UserPromptSubmit hook can stop a prompt (a block, `continue: false`,
exit code 2) shows a warning, and `suppressOutput` is accepted but has no effect. A host that does
not know the field ignores it or treats the output as invalid. Either way the tick is not lost: it
shows the full notice, or reaches the model as before.

Fail-safe direction: any error in the hook lets the tick through, which is today's behaviour. If
the host did not honour a block for a cron-enqueued prompt, the model would get the tick and
answer it exactly as now. No marker the hook writes differs from what the Stop hook would have
written for the same tick.

## Live check

After install, in a session with the watchdog armed: approve a stop, wait for one tick, and
confirm the transcript has no assistant turn for it and `reviewer.log` has the block line. Then
the next tick after one interval tears the cron down.

Done 2026-11-09 with Claude Code 2.1.322: the tick was blocked with no model call, and the log had
`watchdog tick blocked, no model call (you have the turn)`. The notice echoed the whole tick
prompt, which led to `suppressOriginalPrompt`. Repeat the check after that change and confirm the
notice is the reason line only.

## Tests

- Disarm fresh: a tick is blocked, the disarm `ts` is unchanged, and no `teardown_ready` exists.
- Every block sets `hookSpecificOutput.suppressOriginalPrompt` for `UserPromptSubmit`, and its
  reason is `Watchdog: no API failure detected`.
- Disarm aged past the interval: a tick is not blocked and `teardown_ready` is written.
- `teardown_ready` present: a tick is not blocked.
- No disarm, transcript parked on a background sub-agent: a tick is blocked and sets the disarm.
- No disarm, transcript parked only on a listening server: a tick is not blocked.
- No disarm, no background wait: a tick is not blocked and writes no marker beyond tick liveness.
- Watchdog not effective: a tick is not blocked.
- An interrupt before the tick (which sets the disarm) blocks that same tick.
- A prompt that is not the armed tick (one that mentions the label, quotes the whole tick and adds
  a question, or carries another wording) is not blocked and stamps no liveness: with no tick
  seen, it still gets the re-arm.
