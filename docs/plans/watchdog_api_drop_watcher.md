# Watchdog: resume within seconds after an API drop

## Why

Claude Code ends a turn when its connection to the API fails partway through a response
("API Error: Connection lost mid-response", "The response stopped arriving", "No response from
API"). It deliberately does not retry these itself, because a re-sent request could repeat tool
calls. No Stop hook runs after such a turn, and `StopFailure` output cannot start a new one, so
the session sits idle until something prompts it. Today that something is the watchdog's cron
tick: up to one interval (7 minutes by default) plus the scheduler's jitter (up to half an
interval), so recovery takes up to about 10 minutes. Each tick is also a model turn, spent
every interval whether or not anything went wrong.

Measured on the operator's machine over three days (2026-10-24 to 26): 51 "Connection lost", 16
"stopped arriving" and 2 "No response" turns, spread through the day at 1-7 per hour.

## The mechanism

A background command started with the Bash tool's `run_in_background` is tracked by Claude Code,
and when it EXITS, Claude Code starts a new turn to report it -- including when the session is
idle (observed in this session: a background test run completing after the turn ended started
a new turn). That exit is the only hands-free way for a process to start a turn: a hook cannot
create a tracked task, a script cannot type into the prompt, and `StopFailure` output is not
delivered to the model.

So a small watcher script runs as one such background command per session. It reads the
session transcript and waits, using no tokens. When a turn ends on a dropped connection and
nothing has happened since, it prints its instruction and exits; the turn its exit starts
resumes the work, as typing `continue` would.

## When it fires

All of:

1. The latest main-session (non-sidechain) API-error entry has `isApiErrorMessage: true` and
   `error: "server_error"`. That covers connection lost, stopped arriving, no response, 500 and
   529. Login (`authentication_failed`), rate and usage limits (`rate_limit`) and safeguard
   refusals (`invalid_request`) are excluded: retrying does not help them. So are the two
   configuration failures Claude Code's connection-error path also tags `server_error`, told
   apart by `apiError`: `tls_untrusted_ca` (a proxy's certificate the client does not trust) and
   `gateway_content_type` (a gateway answering in the wrong format) fail the same way every time.
2. Nothing after it shows the session moved on: no main-session assistant event, no
   `api-request` record, no main-session user turn-start (operator prompt, tick, task
   notification), and no `queue-operation` enqueue. A tool-result carrier does not count.
   Sub-agent (sidechain) events neither count nor are ignored: they are not the main turn, so
   they do not cancel the drop, but something is still active, so the grace (condition 3) runs
   from the latest of them rather than from the error. This matters: when the auto-mode classifier's
   own API call fails, Claude Code records the same kind of error entry, writes a tool result,
   and carries on with a new API request within a second -- that turn is alive, and the
   `api-request` that follows cancels the drop.
3. The error -- or the latest sub-agent event after it -- is at least 20 seconds old (`GRACE`),
   so a turn that is carrying on has written
   its next record and the operator has a moment to type `continue` themselves.
4. The watchdog is effective for the project (stopContinue and the watchdog both on) and the
   session's disarm marker is absent. Disarm means the operator has the turn (an approved stop,
   an interrupt), exactly as for the tick; a dropped tick turn that started while disarm was
   set must not start work.
5. It has not already fired for this error (its `uuid` is recorded in the lease file).
6. No background work is outstanding: `background_wait_state` (the Stop hook's own check, which
   counts live background sub-agents, monitors and non-server background shells, and leaves the
   watcher itself out) reports no wait. While such work is outstanding Claude Code starts a turn
   when it completes, so there is nothing to resume -- and a background sub-agent can be inside
   a long tool call for minutes without writing anything, so quiet alone does not mean it ended.
   A synchronous sub-agent cannot outlive a main-session drop: it runs as a tool call, while no
   main-session request is open. The check runs only when a drop is otherwise due, so a large
   transcript is not re-parsed on every poll. The Stop hook's form of the check reads any
   internal error as "no wait" (fail-open toward supervision); that is the wrong direction for
   an automatic resume, so the watcher uses a strict form that raises instead, and an error
   means keep waiting -- the cron tick is still there to recover.

Backoff: if it has fired 3 times in the last 10 minutes, the grace becomes 5 minutes, so an API
outage produces a resume every few minutes rather than a burst.

Claude Code's own pre-output retry loop ("Waiting for API response · will retry in 2m 1s ·
check your network") writes no error entry while it runs -- the spinner is the only sign of it;
the transcript gains one `api-request` record per attempt -- so the watcher leaves it alone:
nothing is pending, and a request record would cancel an earlier drop anyway. When the loop
gives up it writes an ordinary error entry ("Unable to connect to API (...) ... switch networks,
then try again", or "Connection to the API was lost (ECONNRESET) ..."), both `server_error`
(read from Claude Code 2.1.311), so condition 1 covers the final failure.

## Starting it, and keeping exactly one

The lease file `wd_<stem>.dropwatch` (same session-scoped stem as the other watchdog markers)
holds JSON `{"pid", "ts", "fired_uuid", "fires"}`. The watcher rewrites `ts` every poll (2s).
A watcher is ALIVE when its `pid` is running and `ts` is under 60 seconds old; both are needed
because pids are reused.

- **UserPromptSubmit** (non-tick prompts, watchdog effective, no live watcher): appends a
  launch instruction to its additionalContext -- run this exact command with
  `run_in_background`, with a fixed description that says to Read its output when it
  completes. It writes a provisional lease (`pid: null`, fresh `ts`) so the next prompt within a
  minute does not ask again while the agent is still launching it. Ticks never carry the launch
  instruction: their own instruction is to reply `ok` and do nothing else when disarmed.
- **The watcher on start**: if another live watcher holds the lease, it exits at once saying so
  (one redundant report, not a second watcher). Otherwise it takes the lease.
- **The fire message** tells the agent to relaunch the watcher (same command) BEFORE resuming,
  so a drop during the resumed work is also caught.
- **A crash** exits with the error and the relaunch command; the next prompt also relaunches it
  once its lease goes stale.

The command goes through `hooks/run_hook.sh`, the same python3 locator the hooks use.
`REVIEWER_API_DROP_WATCH=0` turns the launch instruction off.

## The watcher is not background work

The Stop hook skips supervision while the agent is parked on an outstanding background command
(`background_wait_state`), and a session's ticks only reach the idle teardown through a
supervised stop. The watcher never exits on its own, so counted as background work it would
switch stop supervision off for the whole session and keep the cron alive forever. So
`background_wait_state` drops the watcher on every path that can count a shell: the current
turn's launches, a shell from an earlier turn that is still signalling, and the job-state list.
A shell is the watcher only when its command is EXACTLY the launch the plugin generates --
`/bin/sh <root>/hooks/run_hook.sh <root>/api_drop_watch.py --cwd X --session Y --transcript Z`,
nine shell words -- so a command that merely mentions the script (`rg api_drop_watch.py`) or
runs something more after it still counts as work. It is matched on the FULL command, not the
200-character copy kept for display, because the plugin path can push the script name past that
cut. In the job state the same match runs on a shell entry's `label`, which is its command
(observed, up to 193 characters, uncut), so the watcher is recognized even when compaction or a
respawn has removed its launch acknowledgement from the transcript. With only
the watcher outstanding, a stop is supervised as if nothing were running; with the watcher and a
real job, the real job still parks the stop.

## Evidence for the transcript shape

Checked against every saved transcript on the operator's machine (2026-10-27): every dropped
turn is an assistant entry with `isApiErrorMessage: true` and `error: "server_error"` (98
connection lost or stopped arriving, 2 no response, one 500, one 529); login, limit and safeguard
errors carry `authentication_failed`, `rate_limit` and `invalid_request`. A drop that ended the
turn is followed only by bookkeeping (`system` turn_duration, `last-prompt`, `ai-title`,
attachments) until the next prompt; a classifier failure inside a live turn is followed within a
second by a tool result and an `api-request`. The pre-output retry loop wrote no error entry in
any transcript, and none of them shows it ending in an error.

## What the woken turn does

The exit message reads, in substance: "the last turn ended on <error text> at <time> and nothing
has happened since. First relaunch the watcher with <command> (run_in_background, description
<…>). Then Read <disarm>: if it exists, the operator has the turn -- reply `ok` and stop.
Otherwise resume the unfinished work." The disarm check repeats the watcher's own, because the
operator may have been given the turn between the fire and the turn starting.

## Not changed

- The cron tick stays as the backstop (watcher not running, killed, or a stall the watcher
  cannot see). Its interval and prompt are unchanged.
- Codex is unaffected; this is Claude Code only.

## Tests

- Detection (pure, over event lists): each server-error kind fires; auth, rate-limit and
  safeguard errors do not; an api-request, assistant event, human prompt, tick or enqueue after
  the error cancels it; a tool result alone does not; sidechain errors are ignored, and
  sidechain activity after a drop moves the start of the grace to its latest event; the grace
  and the backoff grace; an already-fired uuid does not refire.
- Lease: alive needs a running pid AND a fresh ts; a provisional lease suppresses a second
  launch instruction for a minute; a second watcher exits when one is alive.
- Background wait: a turn whose only outstanding shell is the watcher is not a wait (current
  turn, earlier turn, and a command long enough that the script name falls past 200 characters);
  the watcher beside a real running job still is; the Stop hook supervises a stop with only the
  watcher running.
- UserPromptSubmit: launch instruction present when effective and no live watcher; absent on a
  tick, when the watchdog is off, when `REVIEWER_API_DROP_WATCH=0`, and when a watcher is alive.
- The script does not fire while a background sub-agent launched in the dropped turn is still
  outstanding, and does fire when the only background shell is the watcher itself. A failure
  inside the background check reads as outstanding work for the watcher, while the Stop hook's
  form still reads it as no wait.
- The script end to end (subprocess, short grace via an environment override): exits with the
  instruction after a dropped turn is appended; keeps running while the turn carries on; exits
  immediately when another watcher is alive.
