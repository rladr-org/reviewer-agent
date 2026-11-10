# Release notes for 1.4.8

Reviewer Agent provides separate commit review and stop supervision for Claude Code and Codex:
an independent reviewer audits each `git commit` before it lands, and a supervisor reviews each
stop so the agent does not quit before the request is done.

1.4.8 is a fix release. The commit gate now works in repositories with a long path, where it
used to block every commit.

## Fixed

- **The commit gate works in a repository with a long path (Claude Code).** State files are
  written through a temporary file beside the target. That file's name embedded the target's
  name plus 38 bytes. For a repository whose path exceeded about 139 bytes, the temporary name
  passed the 255-byte file name limit, the write failed with `File name too long`, and the gate
  blocked every commit with "commit gate crashed". Temporary files are now named
  `.<32 hex>.tmp` whatever the target is called. Existing state files keep their names.
- **The transcript fallback handles a long working directory (Claude Code).** When a hook
  payload carries no transcript path, the fallback lookup answers "no transcript" for a long
  working directory, where it used to raise the same error.
- **A too-long state file name is reported as such (Claude Code).** If a state file name or
  path does exceed the filesystem's limit, the gate's message says so and names the file. The
  gate still blocks the commit.
- **State names fit the limit for a long repository folder name (Codex).** The Codex plugin
  uses the same fixed-length temporary names, and the project part of a state file name is
  trimmed to 165 bytes of the repository folder name.

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
- One exception, in Codex only: a repository whose own folder name (the single directory name,
  apart from the path leading to it) is longer than 165 bytes gets new state file names. There,
  turn the reviewer and stop features on again and accept the risk notice again. Its earlier
  review history stays on disk under the old name and is left unread.

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
