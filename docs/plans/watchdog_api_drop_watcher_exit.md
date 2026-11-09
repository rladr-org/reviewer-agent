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

## Addendum (2026-11-08): the process moved to another session without a handoff

Watchers still accumulated after 1.4.6. On 2026-11-08 there were 13 running. Five were orphans
from before the launcher check (1.4.5). Four belonged to Claude Code processes that were alive and
had moved on to a different session: interactive pid 92723 ran watchers for 27062789, d0670610 and
f65989f9, and bg host pid 549 ran them for 89fbbd1f and e41487a4. None of the abandoned
transcripts contains a `continued-in` record. `/clear`, `/resume`, and a bg host taking a new job
switch the session inside the same process without writing one. So neither check above fires,
and each switch leaves one more watcher polling forever.

Claude Code keeps a registry entry per live process at `<config dir>/sessions/<pid>.json`. The
config dir is `$CLAUDE_CONFIG_DIR`, defaulting to `~/.claude`. The entry's `sessionId` is the
session the process runs now. It is rewritten on a switch: 549's entry lists e41487a4 under
`formerNames`.

### Change

3. **Its launcher now runs another session.** Each poll, once the launcher is known to be running,
   the watcher reads the launcher's registry entry. If the entry's `sessionId` names a different
   session, the watcher stops quietly and says which session the process runs now. Like the
   handoff exit, this starts a turn in that session, so the line says no action is needed. The
   check fails open. If `CLAUDE_PID` is unset, or the entry is missing, unreadable, not JSON, or
   has no `sessionId`, the watcher keeps waiting as before. The registry is Claude Code's internal
   file, not a documented interface, so the check may only ever end a watch, never start or fire
   one.

A `/resume` back to the old id starts a new watcher from that session's next prompt, the same
way a resumed handoff does.

### Tests

- The registry names another session: the watcher exits without a resume message, even with a
  drop pending, and its output names the session the process runs now.
- The registry switches while the watcher runs: it exits.
- The registry names this session: it keeps waiting.
- No entry, an unparseable entry, or an entry without `sessionId`: it keeps waiting.
