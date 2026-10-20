# Release notes for 1.4.3

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex.
This update adds a reading window in Claude Code, removes Codex's blocking reading delay,
raises Claude Code's codex-backend defaults, fixes large staged-index handling and several
command and supervision details, and rewrites the install, update and uninstall guides.

## Changes

- Claude Code's codex backend now defaults to `gpt-6-luna` at `xhigh` effort with an
  1800-second commit review budget (was 900). Previously an unset model or effort inherited
  `~/.codex/config.toml`. Saved choices, `config.json` and environment overrides still win.
- Claude Code waits 60 seconds before reviewing a stop; replying during the window skips the
  review. Its stop-continue command sets a delay from 0 through 300 seconds; 0 disables the wait.
- Codex starts completion review immediately. Its blocking reading window is removed because
  the hook cannot confirm that an answer reached the screen. Old delay configuration and
  environment values are ignored; legacy delay commands report removal without changing settings.
  The Stop-hook timeout returns to 360 seconds. Escape interrupts a running review; typing or
  submitting a reply does not interrupt it.
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
- The install, update and uninstall guides walk through settings and enablement one screen at a
  time after the restart, check the hooks' `python3`, and verify the installed files against the
  release commit.
- The test suite no longer leaves folders in the real reviewer scratch directory
  (`$TMPDIR/reviewer-agent-scratch`) when it runs during installation or update.

## Upgrade and verify

Follow the [update instructions](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md)
for your host. Restart into the installed release and confirm its version and source. For
Codex, use `/hooks` in the interactive CLI to trust modified hooks, then start a fresh host
session. Check an actual gated commit; the absence of an error does not prove a review ran.

`DISCLAIMER.md` changed: it now says the software can run commands in your repository, or have
your development agent run them, with any backend. Accepting the risk is tied to the disclaimer's
text, so after updating, re-accept it (`on acceptRisk`) or turn a feature off.

The hosts are installed independently. Their configuration files and some key names differ;
use the [README](https://github.com/rladr-org/reviewer-agent#documentation) for the correct host.

## Known limitations

In Codex, interruption currently requires Escape. Typing or submitting a reply does not interrupt
the completion review; the desktop host delivered submitted replies only after
the review finished during acceptance testing.

Removing the blocking wait does not establish that intermittent missing or disappearing
desktop text is fixed. That display issue remains under investigation.

Stop supervision can misjudge scope, revive completed requests, or return prose instead of its
required stop token, producing an unwanted continuation. Captured incidents remain under
investigation. Escape returns control; feature toggles let you disable supervision separately
from commit review.

The hook launcher checks that a `python3` exists, not its version; a Python older than 3.9 first
on the PATH Claude Code starts with is not rejected. The install and update guides check it.

macOS has daily use and automated coverage. On Ubuntu 20.04, 1.4.3 was verified with Claude Code
(`codex` backend) and the Codex CLI: commit review, Stop supervision, and update, uninstall and
reinstall. Other distributions and WSL2 are unverified. No platform or model configuration is
guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
