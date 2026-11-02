# Release notes for 1.4.6

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

1.4.6 fixes two Claude Code watchdog bugs that left work running after a session no longer
needed it. Codex is unchanged apart from its version label.

## Fixed

- **The watchdog stops ticking while a never-ending background shell runs.** When the agent
  ended its turn with a shell still running that never exits, such as a webhook forwarder or a log
  follower, every stopContinue watchdog tick replied `ok` but the pause was never recorded, so the
  tick repeated every interval until the cron expired after seven days. A tick's `ok` now pauses
  the watchdog and the idle teardown removes the cron as usual. The turn a background completion
  starts still clears the pause and re-arms the cron.
- **The API-drop watcher no longer outlives its session.** Claude Code does not kill background
  shells when it exits, and a conversation that continues under a new session id keeps its
  process, so the watcher added in 1.4.5 kept running in both cases. It now exits by itself. A
  leftover also held its session's lease, so a resumed session's watcher stopped at once, and it
  could resume an old dropped turn into the conversation that had taken over its process.

See the [CHANGELOG](https://github.com/rladr-org/reviewer-agent/blob/main/CHANGELOG.md) for
details.

## Install

Follow the [install instructions](https://github.com/rladr-org/reviewer-agent#documentation)
for your host. Claude Code and Codex are installed independently; their configuration files and
some key names differ.

## Upgrade and verify

From 1.4.3, 1.4.4 or 1.4.5 there is nothing to do beyond the normal
[update](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md). Hooks load per
session, so restart Claude Code sessions after updating. Watchers started by 1.4.5 keep running
until stopped; restarting Claude Code does not stop a leftover one, so end any you find
(`ps -ax | grep api_drop_watch.py`). From an earlier release, also note:

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

The API-drop watcher recognizes its Claude Code process by the `CLAUDE_PID` variable Claude Code
exports; without it, only the session-handoff exit applies.

macOS has daily use and automated coverage. On Ubuntu 20.04, 1.4.3 was verified with Claude Code
(`codex` backend) and the Codex CLI: commit review, Stop supervision, and update, uninstall and
reinstall. 1.4.5 and 1.4.6 change Claude Code stop-hook and watcher code that has not been
verified on Linux. Other distributions and WSL2 are unverified. No platform or model configuration
is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
