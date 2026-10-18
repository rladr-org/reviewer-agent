# Draft release notes for 1.4.3

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex.
This update gives you time to read an agent's response before its stop review starts and fixes
large staged-index handling and several command and supervision details.

## Changes

- Both hosts wait 60 seconds before reviewing a stop. In Claude Code, replying during the
  window skips the review. In Codex, you must currently press Escape to interrupt the reading
  window or running completion review; typing or submitting a reply does not interrupt it.
  Set a delay from 0 through 300 seconds with the host's stop-continue command; 0 turns the wait
  off. Existing explicit settings remain in effect.
- Claude Code labels both the spinner and blocking feedback
  `stopContinue supervisor (reply to skip the review)`.
- The commit gate reads the staged index without putting every path on a command line, avoiding
  argument-list failures on large stages.
- A Claude background shell listening on a port no longer suppresses stop review indefinitely;
  the supervisor receives information about running servers.
- Stop-supervisor context and shared instructions clarify replies to earlier proposals,
  discussion requests and compaction handovers.
- `/reviewerDirective` leads with SUCCEEDED or FAILED, and `/reviewerModel timeout clear`
  reports the effective timeout and the source that still applies.
- Session start warns when Claude's plugin source is under a temporary directory. Install the
  plugin into a permanent directory: OS cleanup can remove temporary files and disable its hooks.
- Both configuration references list JSON settings and environment controls, and the command
  reference includes supported clear forms.

## Upgrade and verify

Follow the [update instructions](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md)
for your host. Restart into the installed candidate and confirm its version and source. For
Codex, use `/hooks` in the interactive CLI to trust modified hooks, then start a fresh host
session. Check an actual gated commit; the absence of an error does not prove a review ran.

The hosts are installed independently. Their configuration files and some key names differ;
use the [README](https://github.com/rladr-org/reviewer-agent#documentation) for the correct host.

## Known limitations

In Codex, interruption currently requires Escape. Typing or submitting a reply does not interrupt
the reading window or completion review; the desktop host delivered submitted replies only after
the review finished during acceptance testing.

Stop supervision can misjudge scope, revive completed requests, or return prose instead of its
required stop token, producing an unwanted continuation. Captured incidents remain under
investigation. Escape returns control; feature toggles let you disable supervision separately
from commit review.

macOS has daily use and automated coverage. Ubuntu installations produced findings, and
clean-host acceptance of the current Linux fixes is still pending. WSL2 is unverified. No platform
or model configuration is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.

The release remains a draft until host acceptance, version metadata and artifact checks are complete.
