# Release notes for 1.4.7

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

1.4.7 adds standing stop rules for Claude Code and makes its watchdog much cheaper to run. It
also changes how Codex checks completion: the agent submits its final draft for review before
publishing it.

## Added

- **`/reviewer-agent:stopContinueDirective <text>` (Claude Code).** Records a standing rule for
  the stop supervisor alone, about when the agent may stop or must keep working, for example
  "items on HANDOVER.md's approved list are approved; do not stop between them". It reaches every
  stop review and never a commit audit. It supports `list`, `remove <n>` and `clear`. Like
  `/reviewerDirective`, the `UserPromptSubmit` hook handles it, so its text never reaches a shell.

## Changed

- **Codex checks the final draft before publishing it.** With stopContinue on, the agent calls
  a blocking `review_completion` tool with its complete final draft. A rejection comes back as an
  ordinary tool result, so the agent keeps working inside the same turn, and the desktop's
  Stop-continuation path, which can hide resumed output, is not used. An approval covers one exact
  draft in one turn; new work or new input invalidates it. The native Stop review remains the
  fallback for a skipped or changed draft. This is a workaround, not a fix for desktop rendering.
- **Codex completion reviews see more of the turn.** They read the visible commentary of the
  current turn, so the reviewer can weigh promises and progress against the proposed answer. They
  also keep task context across a session end and resume.
- **Completion scope rules for both hosts.** The supervisor keeps investigation, repair and
  testing going while a defect remains in assigned work; reporting a diagnosis does not finish it.
  Standalone discussion, diagnosis-only requests and cancellations stay distinct. Each
  continuation must serve a request that still applies. Older reviewer directives cannot create
  new tasks or revive a superseded test procedure, and Codex drops completed directives from
  later reviews.

## Fixed

- **Idle watchdog ticks no longer cost a model call (Claude Code).** Every tick was a full turn
  that re-sent the whole conversation, about 300K tokens in a long session just to reply `ok`. The
  prompt hook now answers the tick itself while you have the turn or the session waits on
  background work. You see a one-line notice instead (`UserPromptSubmit operation blocked by
  hook: [stopContinueWatchdog] tick answered without a model call ...`), at most about once per
  idle period. A tick reaches the model only to resume unfinished work, to delete the cron, or
  when the hook cannot decide.
- **A message that mentions the watchdog tick is no longer mistaken for one (Claude Code).**
  Pasting or quoting the tick text used to make a dead cron look alive, which delayed its re-arm.
- **The API-drop watcher stops when its process moves to another session (Claude Code).**
  `/clear` and `/resume` switch sessions without the handoff record 1.4.6 watched for, so each
  switch left one more watcher running. A stale watcher could also resume an old dropped turn into
  the session that replaced it.
- **The disclaimer's note on directive commands now says it applies to Claude Code.** The Codex
  plugin has no directive commands.

See the [CHANGELOG](https://github.com/rladr-org/reviewer-agent/blob/main/CHANGELOG.md) for
details.

## Install

Follow the [install instructions](https://github.com/rladr-org/reviewer-agent#documentation)
for your host. Claude Code and Codex are installed independently; their configuration files and
some key names differ.

## Upgrade and verify

Update as usual ([AGENT_UPDATE](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md)).
Hooks load per session, so restart Claude Code sessions and start fresh Codex sessions after
updating.

- `DISCLAIMER.md` changed. Features that are on stay on. Turning one on again asks you to
  re-accept the risk (`on acceptRisk`), because acceptance is tied to the disclaimer's text.
- Watchers already running keep their old code until they exit. Find leftovers with
  `ps -ax | grep api_drop_watch.py`. A watcher whose Claude Code process is gone is safe to end.
  Ending one whose process is still running starts a turn in that process's current session.

Restart into the installed release and confirm its version and source. Check an actual gated
commit; the absence of an error does not prove a review ran.

## Known limitations

The watchdog's tick answer relies on Claude Code honouring a `UserPromptSubmit` block for a
prompt fired by a cron, verified with Claude Code 2.1.322. If a version does not, ticks reach the
model as they did before. Claude Code offers no way for a hook to block a prompt silently, so the
one-line notice stays.

The watcher's session-switch check reads Claude Code's internal session registry. If the format
changes, the watcher keeps waiting as it did in 1.4.6.

In Codex, interruption currently requires Escape. Typing or submitting a reply does not interrupt
the completion review. If the native fallback rejects an answer, that answer may already be
visible, and continuation can still collapse desktop history.

Stop supervision can misjudge scope, revive completed requests, or return prose instead of its
required stop token, producing an unwanted continuation. Escape returns control; feature toggles
let you disable supervision separately from commit review.

macOS has daily use and automated coverage. On Ubuntu 20.04, 1.4.3 was verified with Claude Code
(`codex` backend) and the Codex CLI. Releases since then change Claude Code hook and watcher code,
and this release changes Codex completion review; neither has been verified on Linux. Other
distributions and WSL2 are unverified. No platform or model configuration is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
