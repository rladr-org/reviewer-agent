# Release notes for 1.4.4

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

This is the first public release. It is functionally identical to 1.4.3; the release export
now also excludes any working handover log. For everything that changed in 1.4.3, see the
[1.4.3 release notes](https://github.com/rladr-org/reviewer-agent/blob/main/docs/release-1.4.3.md) and the
[CHANGELOG](https://github.com/rladr-org/reviewer-agent/blob/main/CHANGELOG.md).

## Install

Follow the [install instructions](https://github.com/rladr-org/reviewer-agent#documentation)
for your host. Claude Code and Codex are installed independently; their configuration files and
some key names differ.

## Upgrade and verify

From 1.4.3 there is nothing to do beyond the normal
[update](https://github.com/rladr-org/reviewer-agent/blob/main/AGENT_UPDATE.md). From an
earlier release, also note:

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
reinstall. 1.4.4 changes no runtime code beyond its version labels. Other distributions and WSL2 are unverified. No platform or
model configuration is guaranteed to work.

Reviewer calls send project context to the selected provider and may consume quota. Read
[SECURITY](https://github.com/rladr-org/reviewer-agent/blob/main/SECURITY.md),
[PRIVACY](https://github.com/rladr-org/reviewer-agent/blob/main/PRIVACY.md) and
[DISCLAIMER](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) before enabling.
