# Release notes for 1.4.9

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

1.4.9 is a fix release for Claude Code. The stop supervisor could read the agent's own `ok`
answers to watchdog ticks as the operator's approval; it no longer sees them.

## Fixed

- **Watchdog-tick answers are no longer read as your approval (Claude Code).** The stop
  supervisor's context lists the agent's earlier replies. A bare `ok` answering a watchdog tick,
  listed right after the agent's request for approval, read like the operator's answer, and the
  supervisor then directed work the operator had not approved. Those acknowledgements are now
  left out. Any other reply to a tick is marked as a reply to an automatic check. The section
  also states that operator approval appears only under OPERATOR MESSAGES.

The Codex plugin has no watchdog tick and behaves as in 1.4.8.

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

- Settings, risk acceptance, reviewer sessions and review history carry over. `DISCLAIMER.md` is
  unchanged since 1.4.7.

Restart into the installed release and confirm its version and source. Make a gated commit and
confirm that its review ran.

## Known limitations

The hook launcher checks only that a `python3` exists. A Python older than 3.9 first on the PATH
Claude Code starts with is not rejected; the install and update guides check the version.

In Codex, interruption currently requires Escape. Typing or submitting a reply does not interrupt
the completion review. If the native fallback rejects an answer, that answer may already be
visible, and continuation can still collapse desktop history.

Stop supervision depends partly on the model and the prompt, and its judgments can be wrong.
Escape returns control, and `stopContinue off` turns supervision off without affecting commit
review.

macOS has daily use and automated coverage. On Ubuntu 20.04, 1.4.3 was verified with Claude Code
(`codex` backend) and the Codex CLI. Releases since then change Claude Code hook and watcher code
and Codex completion review; neither has been verified on Linux by hand. Other distributions and
WSL2 are unverified. No platform or model configuration is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
