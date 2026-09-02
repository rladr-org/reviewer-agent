# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the Python package inside the Codex
plugin spells the same version in [PEP 440](https://peps.python.org/pep-0440/) form
(`1.2.1-beta.1` and `1.2.1b1` are the same release).

## [1.2.1-beta.4]

Opens the `1.2.1` pre-release line. Every declared version across both packages moves to
`1.2.1-beta.4` in lockstep (checked by `tools/release_versions.py`); the Codex plugin's
`+codex.<timestamp>` build metadata is refreshed so the Codex host notices the reinstall. The
version bump is what lets an installed copy pick the changes up: a local (directory-source) plugin
is compared by version *string*, so `claude plugin update` is a no-op until the version changes.

### Changed

- `stopContinue` supervisor prompt-hardening + a configurable pre-enactment delay. The stop
  supervisor now classifies a polite go-ahead request for named in-scope work ("say the word,"
  "shall I," "let me know," "I'll do that next," "ready when you are") as authorized remaining
  work rather than a request for operator input, and verifies a *claimed* approval requirement
  (genuinely needed, and not already granted in a message or plan) instead of taking it at face
  value — closing a case where a premature stop was approved. The same hardening is applied to the
  native Codex `STOP_BRIEF` (parity pinned by tests). A new `REVIEWER_STOPCONTINUE_DELAY_SECS`
  (or `stopContinueDelaySecs`, default 0, max 300) pauses before enacting EITHER verdict — a
  continue directive or a stop approval — announcing on stderr first so an operator can Escape; it
  is capped against the remaining Stop-hook budget (whose host timeout is raised to 630 s) so it
  can never overrun cancellation (which would fail the stop open). See
  `docs/plans/stopcontinue_delay_and_prompt_hardening.md`.
- `AskUserQuestion` answering moved off the host `askUserQuestionTimeout` auto-close onto a
  `stopContinue` reroute. The old `PostToolUse` answerer (`askq_answer_hook.py`) depended on the
  host auto-closing an unanswered question and returning an `afkTimeoutMs` tool response; that
  stopped firing (last observed 2026-08-21), so an unanswered `AskUserQuestion` blocked
  indefinitely and the reviewer was never invited. It is replaced by a `PreToolUse` hook
  (`askq_reroute_hook.py`) that -- only while `stopContinue` is active -- denies `AskUserQuestion`
  and directs the agent to re-ask it as a plain-prose message; the existing stop supervisor then
  answers it from established operator context or defers it to the operator. `/reviewerAgent on`
  no longer writes `askUserQuestionTimeout` to user settings (`setup_timeout.py` removed). The
  reroute is independent of `afkTimeoutMs`: if the host auto-close ever returns, restore
  `askq_answer_hook.py` and its `posttool_context_output` helper from git history to re-answer
  timed-out questions for `stopContinue`-off users. See `docs/plans/askq_stopcontinue_reroute.md`.
- `stopContinueWatchdog` tick prompt is smaller and Read-based. The tick now names the two
  per-project flag files by absolute path and directs the model to Read them, instead of
  shelling into `python3 stopcontinue_watchdog_toggle.py status`. Healthy ticks contract to a
  two-character `ok` reply; the "resume unfinished work" recovery branch is preserved. See
  `docs/plans/watchdog_tick_visibility.md`.
- `stopContinueWatchdog` tick prompt now directs a PER-PATH check (Read each path on its own, not
  one batched `ls`) and states that the session-scoped `teardown_ready` marker is normally absent,
  so a merely-missing path is not a read failure. A batched `ls` of all three paths exits non-zero
  on every healthy session -- a spurious error that also risked the fail-safe misreading an absent
  marker as an unreadable filesystem. See `docs/plans/watchdog_auto_shutoff.md`.
- `stopContinueWatchdog` now auto-shuts-off on idle and auto-rearms on the next prompt (Claude
  Code), so an idle session goes quiet instead of ticking every N minutes indefinitely. When a
  session genuinely stops (a STOP_OK verdict), the Stop hook records a session-scoped disarm
  timestamp; after a full interval of idle ticks it writes a `teardown_ready` marker and the tick
  — now a pure, fail-safe presence check that never does clock arithmetic — deletes its own cron.
  The next operator prompt re-arms via a new `UserPromptSubmit` hook. All timing is decided in
  Python (never by the tick model); disarm is written only on a genuine completion, so an
  incomplete or background-parked strand keeps the watchdog armed to recover it. The arm/reconcile
  is prompt-aware so a changed tick replaces a stale cron. See
  `docs/plans/watchdog_auto_shutoff.md`.
- Re-review prompts reframe the prior verdict's language across every re-review branch, so a
  reviewer that already ruled is less likely to anchor on its own previous wording on the next
  pass (measure 1 of the anti-stuck reviewer work).

### Added — reviewer skim mechanism

- `reviewerAgent-ignore.md` at a repository's root now names paths whose content is
  auto-generated data (dataset rows, generated manifests, captured HTML) so the reviewer
  skims their diffs rather than judging individual records. Optional — no file means today's
  baseline. Fenced blocks tagged ```reviewer-ignore``` carry the glob patterns; free-form
  prose around them explains the rationale to the reviewer. Two delivery channels: prompt
  guidance for all backends, and (codex only in v1) byte-level exclusion from the retry
  inline set. Loaded from git objects, never the working tree; delayed application means
  new ignore rules take effect on the next commit, not the one that introduces them; the
  root ignore file is always self-exempted from its own patterns; parse errors block the
  commit fail-closed. See `docs/plans/reviewer_ignore_guidance.md` and the "Skimmed paths"
  section of README.md.

### Fixed

- `stopContinue` no longer discards a `STOP_OK` verdict that is not the first line. A supervisor
  that explains itself and then answers puts the sentinel last; the first-line-only parser read
  that permit-the-stop verdict as a continue directive, nudged a finished task to the cap (six
  false rounds observed, ~45 s each), and relayed reasoning that said there was nothing to do as
  the "keep working" instruction — failing *closed* on the one path documented as fail-open. The
  sentinel now counts as the opening whole word of the first non-empty line (a trailing clause
  is still fine; `STOP_OKAY`, which the old prefix match accepted, is not) or as the bare token on
  the last non-empty line (markdown emphasis and trailing punctuation tolerated; quote wrappers
  and a sentence that merely mentions `STOP_OK` are not). A bare `STOP_OK` in the *middle* of a
  reply, followed by more directive, is contradictory and still resolves to continue — but the
  `[stop]` log line now records `stop_ok=first|last|mid|none` so that case is visible rather than
  silently inverted. The native Codex stop supervisor's ruling parser gets the same rule.
  See `docs/plans/stopcontinue_trailing_stop_ok.md`.
- Actionable model-rejection and usage-limit diagnostics now fire for the streaming `codex` commit
  audit, not only the brief. codex (0.149.x) exits nonzero **silently** under `--json` when the
  provider refuses the configured model or reasoning effort, or the account is out of quota —
  nothing on stdout or stderr — so the audit saw an empty error and reported a bare "review failed
  (exit N)" for the one case the diagnostic exists to name. On an opaque, non-timeout audit failure
  carrying no diagnosable stderr, the audit now re-probes the same turn **once without `--json`**
  (read-only, trivial prompt, short timeout) to recover the provider's own sentence, which the
  `/reviewerModel` rejection/usage-limit diagnostics then surface. Best-effort: the commit is
  already blocked fail-closed, and a probe that yields nothing leaves the previous generic message.
  Surfaced and verified by the live release matrix (`claude-codex`).
- The live release matrix (`tools/release_matrix.py`) exercises the Claude Code scenarios again.
  They enabled the reviewer with `toggle.py on` (which the acceptance gate refuses without a
  receipt) and drove the stubbed codex verdict through the pre-streaming `FAKE_CODEX_STDOUT`
  instead of `FAKE_CODEX_VERDICT`, so every Claude scenario failed at setup; the runner now enables
  with `acceptRisk`, derives the stub verdict knob, and reads the release identity from
  `release_versions.RELEASE` instead of a hard-coded literal.

## [1.2.0-beta.1]

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
- `/stopContinueWatchdog on [interval]|off|status` (Claude Code): a per-project recovery loop for
  the failure mode where the stop supervisor's API times out, the Stop hook fails open, and the
  agent is left idle with nothing to re-fire supervision. Arms a `CronCreate` cron (interval 1–59
  min, default 3) whose tick re-invokes the idle session so the existing Stop hook supervises it
  again; `CronList` is the source of truth, so arming is idempotent, liveness-correct and
  interval-correct, and a stale cron self-cancels on its next tick once the flag is off. Requires
  `stopContinue` active — the tick relies on the Stop hook, which is inert otherwise. The initial
  design ships the minimal feature (toggle, interval, mid-session and SessionStart arming); a
  block-until-armed cap, fail-open decision markers, and an opt-in alert are documented as deferred
  refinements in `docs/plans/stopcontinue_watchdog.md`.
- Streamed, schema-constrained commit audits (Claude Code, `codex` backend only — not the `pi` or
  `claude` backends, and not the separate native-Codex host plugin). The codex backend runs the
  commit audit as `codex exec … resume --json --output-schema`, so the reviewer returns a strict JSON verdict
  (`verdict`/`findings`/`suggestions`) that the hook reconstructs into the canonical
  `APPROVED`/`REJECTED` text (`is_approved` is unchanged). The `--json` event stream is watched
  against two idle thresholds — first-output (default 30 s) and between-output (default 600 s, which
  must exceed the longest legitimate silence such as a test run) — so a hung reviewer is killed and
  retried once instead of only hitting the total timeout, while a missing or malformed verdict fails
  closed (nonzero, empty), preserving any saved ruling. Set with
  `/reviewerModel firstOutputTimeout|idleTimeout <secs>|clear`,
  `REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS`/`REVIEWER_IDLE_TIMEOUT_SECS`, or `firstOutputTimeoutSecs`/
  `idleTimeoutSecs` in `config.json`.
- Session-scoped reviewer scratch with automatic cleanup (Claude Code, `codex` backend only). Each
  codex-backend audit runs with `TMPDIR` scoped to a per-reviewer-generation scratch dir under the
  system temp base (`REVIEWER_SCRATCH_ROOT`); the dir is removed when the reviewer session rotates,
  and an unconditional `SessionStart` sweep reclaims dirs whose mtime lease is older than
  `REVIEWER_SCRATCH_TTL_S` (default 12 h) for sessions that ended without rotating. This contains and
  reclaims scratch that honors `$TMPDIR` (e.g. `mktemp` with no explicit path). It does **not** reach
  scratch a reviewer or its tooling writes to a hardcoded absolute path outside `$TMPDIR` (for example
  under `/tmp` directly) — that is not under `REVIEWER_SCRATCH_ROOT` and is not reclaimed by the sweep.
  The commit-audit prompt asks the reviewer to keep its scratch under `$TMPDIR` to mitigate that, and
  the low-disk warning is the backstop for whatever still escapes.
- Warn-only low-disk reporting (Claude Code, `codex` backend only). Before each codex-backend audit
  the hook checks free space on the scratch volume and prints a `[reviewerAgent] low disk …` line
  below `REVIEWER_DISK_WARN_BYTES` (default
  2 GiB; `0` disables). It never blocks a commit or changes a verdict.
- Automatic reviewer-session rotation (Claude Code, all backends) after a configured number of
  completed non-retry audits, so a long-lived reviewer thread is periodically re-briefed with the
  current standing brief. Set with
  `/reviewerSession limit <N|off|clear>` or `REVIEWER_SESSION_AUDIT_LIMIT` (the stop-supervisor
  session is left untouched).

### Changed

- Reviewer session inspection and rotation act on the current project only and report the
  canonical project root.
- Unscoped legacy session files are retired rather than resumed. A thread whose originating
  repository cannot be established is not adopted; the next gated action briefs a fresh reviewer.
- The retention cap applies to all history entries, verdicts and outcomes alike.
