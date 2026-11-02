# The API-drop watcher exits once its session is gone

## Problem

The watcher (docs/plans/watchdog_api_drop_watcher.md) leaves its loop only to fire a resume, or
when another watcher takes over the same session's lease. Two ways of losing its session give it
neither, so it polls a transcript that never changes again, indefinitely. Both were seen live:

- **The Claude Code process exited.** Claude Code does not kill a background shell when it exits:
  the shell is reparented to launchd and keeps running. On 2026-11-02 two watchers had been
  polling for 8.7 hours after their sessions (b0bb9074, 1460d5a1) ended, each transcript ending in
  the `cost-state` record Claude Code writes on exit.
- **The conversation moved to a new session id.** The process carries on, and so do its
  background shells, but the old transcript ends with
  `{"type":"continued-in","continuedInSessionId":"<new id>"}` and is never written again. The new
  session's first prompt starts its own watcher under the new id and its own lease. On
  2026-10-29 the watcher for 29311a44 kept running after the session continued as 1460d5a1.

A leftover is not just a leaked process. It keeps its session's lease fresh, so a resumed
session's watcher sees "already running for this session" and stops, leaving the resumed session
unprotected. And a leftover can still fire: if the old transcript ended on a drop, it would resume
into whatever conversation now owns the process, citing the old transcript.

## Change

Each poll, before anything else, the watcher stops quietly when:

1. **Its launcher has exited.** Claude Code exports `CLAUDE_PID` (the `claude` process) into every
   shell it starts, and the watcher inherits it. When that pid is no longer running, the watcher
   returns. Nobody is listening for its output any more, so it says one line and exits. Without
   `CLAUDE_PID` (an older Claude Code, or a hand-started watcher) this check is skipped.
2. **Its transcript records a handoff.** A `continued-in` record with no `user` or `assistant`
   record after it means the conversation now lives under another id. That is the rule Claude
   Code itself applies when deciding whether a session was superseded: it walks back from the end
   and stops at the first message. The tail already reads the transcript from the start, so a
   handoff that was already there when the watcher started is caught on its first poll. The exit
   starts a turn in the session that now owns the process, so the line says no action is needed.

Neither exit writes the lease: the pid stops running, so the lease reads as dead at once and a
watcher for a resumed session can take over.

A reused pid would keep a leftover running, as today; that is rare, and the handoff check does
not depend on it.

## Tests

- A watcher whose `CLAUDE_PID` is not running exits without a resume message.
- A watcher whose `CLAUDE_PID` is running keeps waiting.
- A handoff appended while the watcher runs makes it exit, and its output names the new session.
- A handoff already present at startup makes it exit on the first poll.
- A handoff followed by more messages (the session was resumed under its old id) does not.
