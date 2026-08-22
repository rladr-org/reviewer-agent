# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the Python package inside the Codex
plugin spells the same version in [PEP 440](https://peps.python.org/pep-0440/) form
(`1.2.0-beta.1` and `1.2.0b1` are the same release).

## [1.2.0-beta.1] — unreleased

**First public release.** Earlier versions were developed privately, so this tag points at a
repository with no public ancestry: there are no `1.0.x` or `1.1.x` tags to compare against, and
none are missing.

It also unifies two version lines that were previously independent. The Claude Code plugin was at
`1.1.3` and the Codex plugin at `0.1.0`; both are now `1.2.0-beta.1`, and every declared version
across both packages is checked against that one identity by `tools/release_versions.py`. The
Codex plugin additionally carries `+codex.<timestamp>` build metadata, which is a cachebuster the
Codex host needs in order to notice a reinstall — it is not part of the release identity.

### Added

- Project-scoped reviewer state on both hosts. Reviewer sessions, stop-supervisor sessions,
  operator-message watermarks and nudge counters are keyed by canonical project, host session,
  role and backend, so one developer session that moves between repositories no longer reuses the
  first repository's reviewer, brief or accumulated findings. Linked worktrees of one repository
  still share state, because they are one project.
- Process-tree termination for Claude Code reviews. A timeout, an Escape cancellation, or the
  death of the hook process now terminates the reviewer and every process it started, including
  test runners that created their own process groups.
- Retention of every terminal review outcome. Timeouts, cancellations, operational failures and
  discarded rulings are recorded in the bounded per-project history as labelled `OUTCOME:`
  entries. None of them overwrites the saved latest ruling, which the next reviewer is shown as
  its own previous findings.
- `/reviewerHistory` (Claude Code) and `$reviewer-agent-codex:reviewer-history` (Codex): report
  or purge one project's retained reviews and saved rulings without uninstalling the plugin.
- Actionable diagnostics when a provider or account refuses a configured model or reasoning
  effort. The failure now names the selection, where it came from, and the command that changes
  it, instead of surfacing as a bare exit code on a permanently blocked repository.
- A reproducible development baseline: a tracked root `.gitignore`, root pytest configuration so
  a bare `pytest` runs only the root suite, and `requirements-dev.txt`.
- A third Claude Code reviewer backend, `claude`, so a Claude Code user can run an independent
  reviewer without installing a second vendor's CLI. **Beta**, like the `pi` backend: both pass
  the automated live matrix, but the combinations with months of daily use behind them are Claude
  Code with the `codex` backend and native Codex. It is opt-in and never the default: the
  review runs on the operator's own Claude subscription quota. The reviewer is a `claude` child
  restricted to `Read`, `Grep` and `Glob` under `--permission-mode dontAsk`, run with
  `--safe-mode` so the operator's hooks, plugins, MCP servers and `CLAUDE.md` cannot load into
  it. It has no git access, so the staged patch is included in its prompt and a patch too large
  to include blocks the commit rather than being truncated. It carries its own model and effort
  slots, its own per-project session slots for the reviewer and stop-supervisor roles, and an
  exhausted-quota diagnostic that names what actually helps instead of reporting a bare exit
  code.

### Changed

- Reviewer session inspection and rotation act on the current project only and report the
  canonical project root.
- Unscoped legacy session files are retired rather than resumed. A thread whose originating
  repository cannot be established is not adopted; the next gated action briefs a fresh reviewer.
- The retention cap applies to all history entries, verdicts and outcomes alike.
