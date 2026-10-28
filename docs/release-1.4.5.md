# Release notes for 1.4.5

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

1.4.5 resumes Claude Code sessions within seconds after a dropped API connection and fixes two
Claude Code stop-supervision bugs. Codex is unchanged apart from its version label.

## Added

- **Resume within seconds after a dropped API connection.** Claude Code ends a turn on
  `Connection lost mid-response`, `The response stopped arriving` or `No response from API`, runs
  no Stop hook after it, and does not retry it, so until now the session waited for the next
  watchdog tick (up to about ten minutes). While the watchdog is on, the first prompt in a
  session now asks the agent to start an API-drop watcher: a background command that reads the
  session transcript without model calls and exits about 20 seconds after such a turn, which
  starts the resume turn. It also covers 500 and 529 errors, leaves login, limit and safeguard
  errors alone, waits while background work is outstanding or you have the turn, and backs off
  to one resume per five minutes during an outage. Outside auto mode Claude Code may ask you to
  approve the command once. `REVIEWER_API_DROP_WATCH=0` turns it off.

## Fixed

- **The stopContinue watchdog no longer ticks forever.** When a background task finished while
  the agent was still working, its completion was queued into the running turn and was not
  recognized, so the task still counted as running. Every watchdog tick then skipped the stop
  review and the idle teardown never came; the tick repeated every seven minutes for as long as
  the session stayed open.
- **A reply typed while the agent is working is no longer held back.** A reply submitted before
  the agent's final message waited in the input queue unseen, so the supervisor reviewed the
  stop and could push the agent on; the reply was only delivered after pressing Escape. The
  stop hook now sees the queued reply and ends the stop at once.

See the [CHANGELOG](https://github.com/rladr-org/reviewer-agent/blob/main/CHANGELOG.md) for
details.

## Install

Follow the [install instructions](https://github.com/rladr-org/reviewer-agent#documentation)
for your host. Claude Code and Codex are installed independently; their configuration files and
some key names differ.

## Upgrade and verify

From 1.4.3 or 1.4.4 there is nothing to do beyond the normal
[update](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md). Hooks load per
session, so restart Claude Code sessions after updating. From an earlier release, also note:

- `DISCLAIMER.md` changed in 1.4.3. Accepting the risk is tied to its text, so re-accept it
  (`on acceptRisk`) or turn a feature off.
- The Codex Stop hook changed in 1.4.3. Use `/hooks` in the interactive CLI to trust it, then
  start a fresh host session.

Restart into the installed release and confirm its version and source. Check an actual gated
commit; the absence of an error does not prove a review ran.

## Known limitations

In Codex, interruption currently requires Escape. Typing or submitting a reply does not interrupt
the completion review.

Intermittent missing or disappearing desktop text in Codex remains under investigation.

Stop supervision can misjudge scope, revive completed requests, or return prose instead of its
required stop token, producing an unwanted continuation. Escape returns control; feature toggles
let you disable supervision separately from commit review.

The hook launcher checks that a `python3` exists, not its version; a Python older than 3.9 first
on the PATH Claude Code starts with is not rejected. The install and update guides check it.

macOS has daily use and automated coverage. On Ubuntu 20.04, 1.4.3 was verified with Claude Code
(`codex` backend) and the Codex CLI: commit review, Stop supervision, and update, uninstall and
reinstall. 1.4.5 changes Claude Code stop-hook code and adds the watcher; neither has been verified on
Linux. Other
distributions and WSL2 are unverified. No platform or model configuration is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
