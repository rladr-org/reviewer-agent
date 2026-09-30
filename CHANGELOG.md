# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); the Python package inside the Codex
plugin spells the same version in [PEP 440](https://peps.python.org/pep-0440/) form
(`1.2.1-beta.1` and `1.2.1b1` are the same release).

## [1.3.3]

### Changed

- Codex: one hook or tool call asks git for the project root and key once, not ~44 times
  (~35 ms each); the answers are kept for two seconds per process, and a failed lookup is never
  kept. A stop review's slowest test went from 1.9 s to under 0.3 s.
- Claude Code: the hooks likewise ask git for the canonical project key once per two-second
  window instead of ~44 times per hook run; the non-repo fallback is never kept. The root test
  suite went from ~9.5 to ~5.7 minutes.
- Inline-script and pipeline checks distinguish `merge-base` and `commit-graph` from
  commit-producing commands; the Claude inline-script check also covers `am` and `pull`.
- The Codex installer selects and remembers a compatible Python for both hooks and MCP, so
  Codex can start with a different PATH. It checks MCP startup before writing configuration.
- The Ubuntu 20.04 CI runner puts its Python 3.9 virtual environment on PATH for child commands.

- The commit gate finds a commit by reading the command the way a shell does: real command
  positions, `cd` it can be sure ran, wrappers (`env`, `sudo`, `nohup`, `timeout`, `xargs`), `bash
  -c`, `eval`, heredocs, aliases and variables assigned earlier in the same command. `merge`,
  `cherry-pick`, `revert`, `am`, `pull` and `commit-tree` are refused when they would commit. The
  README's new "What the gate cannot see" section lists what reading a command's text cannot catch.
- A command whose name is a variable the gate cannot resolve (`"$PYTHON" -m pytest`) is refused
  only when its visible text names a commit-producing subcommand or code that runs one.
- An inline `python -c`/`node -e`/perl/ruby script is refused only when it can run a command and
  mentions a commit; one that only prints or writes the words is not.
- The stop supervisor's session is retired every `stopRotateEvery` completed reviews (default 6,
  `REVIEWER_STOP_ROTATE_EVERY`), so its per-stop cost no longer grows through a long session.
- `/reviewerModel` changes (model or effort, set or clear) retire this project's reviewer sessions
  for the current Claude session, so codex does not resume a session begun on another model.
- `/stopContinueWatchdog status` prints `stopContinueWatchdog: ON|OFF for <root> ...`, like the
  other watchdog commands, instead of a bare `ACTIVE`/`INACTIVE`.
- `pytest -m smoke` runs a sub-minute install-time subset; `AGENT_INSTALL.md` step 3 uses it.
- `reviewerAgent.md.template` no longer starts with setup notes, which were sent to the reviewer
  as part of the brief; `/reviewerAgent on` warns about an untouched `(edit me)` placeholder or the
  old preamble.

### Fixed

- Codex blocks review responses admitting that staged files could not be inspected, including
  contradictory `APPROVED` suggestions. Reviewers now have a distinct `REVIEW_FAILED:` outcome;
  operational failure preserves the previous verdict and review cursor instead of committing.
- The Claude Code gate does the same on every backend: a `REVIEW_FAILED:` answer, or an `APPROVED`
  admitting the staged set went uninspected, blocks the commit as an operational failure and leaves
  the saved ruling and retry state untouched. The codex verdict schema gains `REVIEW_FAILED`.
- Codex native Interrupt now cancels pending commit reviews owned by the interrupted developer
  session, including reviews targeting another worktree, when the host omits MCP cancellation.
- The Codex gate now classifies executable commands using its independently packaged shell
  parser; quoted reports and data heredocs no longer trigger commit review. An earlier inactive
  target cannot hide a later protected target in a compound command.
- Both parsers detect commits in Bash's `function name { ...; }` form and honor backslash or
  mixed quoting in heredoc delimiters.
- `/reviewerDirective` works again: Claude Code passes the UserPromptSubmit hook the typed command
  (`/reviewer-agent:reviewerDirective list`), not the expanded skill text the hook looked for, so
  directives were never recorded or listed. The hook now recognizes the typed command.
- The stop hook honors a supervisor verdict that ends with STOP_OK as its own final sentence
  (`...nothing is left. STOP_OK`) on both hosts, instead of relaying it as a keep-working directive.
  The supervisor prompt now says any other reply keeps the agent working, so a verdict that stopping
  is appropriate must be STOP_OK.
- Claude Code: the stop supervisor can no longer loop an agent that is waiting on an operator
  decision. It reworded each push (so the repeat check never matched) and cited its own earlier
  directive as the operator's, until Claude Code overrode the hook at 9 blocks. Now, once the agent
  has answered two consecutive directives without a tool call, a fresh reviewer session shown only
  the agent's reply decides whether it is waiting on the operator; if so the stop is allowed and
  starts the watchdog's idle countdown. The nudge cap default drops from 10 to 8, below the host's
  9-block limit, so the plugin's own cap ends a runaway loop first.
- Claude Code: a retry that stages only a file-mode fix (an executable bit) is reported to the
  reviewer as `MODE CHANGED` with the old and new mode, instead of "NOTHING MOVED ... reject
  again". A retry record written before modes were kept gets a full review.
- Codex: `$reviewer-agent-codex:reviewer-agent on` works in a linked worktree whose
  `reviewerAgent.md` exists only in the main checkout (an untracked brief). It checks the same
  brief every review from that worktree reads, instead of refusing with "brief not found".
- A heredoc delimiter with an unquoted `$` or backtick (`<<$'EOF'`, `<<$(echo EOF)`) no longer hides
  a later commit from either gate: Bash ends it at a different line than the parser did, so every
  line after it had been read as data.
- Codex Stop supervision treats a misplaced commit-gate `APPROVED` response as a failed review
  and allows the stop, instead of relaying the verdict as a continuation instruction.
- Codex reviewers in linked worktrees use the main checkout's project brief when the worktree
  has no brief of its own; a worktree's own brief still takes precedence.
- The stop supervisor no longer gets the commit reviewer's standing reminder. Framed as the commit
  auditor, it had answered `APPROVED`, and each answer was relayed as a keep-working directive. A
  commit-gate `APPROVED` reply now allows the stop and says why.
- Hooks start through a POSIX launcher that finds `python3`; without it the gate fails closed.
  A crash in the commit gate now denies the commit instead of letting it run.
- `git add … && git commit` is refused with the real reason (staging in the same call).
- Tests no longer need git newer than 2.25 or a host with less than 1 PB free; CI runs both suites
  on git 2.25.
- Codex plugin: fixes for the locally reproducible issues in the Codex Linux report.
- The stop supervisor judges the turn's real final message: a transcript that has not yet flushed
  the closing summary is re-read, and Claude Code's `last_assistant_message` is used when present.
- A final message that asks the operator to run a slash command, restart, or log in may end the
  turn, and a directive that repeats the one the agent just answered no longer loops.
- `/reviewerHistory clear` also removes the project's audit log and the stored prompt parts no
  other project uses; a prompt part can contain a diff of reviewed code.
- `codex` is found on `PATH` when `~/.local/bin/codex` does not exist (an npm-global install on
  Linux).
- `AGENT_INSTALL.md`: asks which repository to protect when started outside one, checks for a Git
  identity, persists `codexBin` when hooks would not find codex, lists the full suite's three
  expected skips, and moves the optional settings rules into a step 5b of their own.
- `AGENT_INSTALL.md`: the documented allow rule for the manual commands never matched. It mixed a
  mid-rule `*` (the version segment) with the legacy trailing `:*`, and Claude Code matches
  everything before a trailing `:*` literally, so the `*` was never expanded and the host warned at
  startup that the rule "mixes * with the trailing :* prefix syntax". The rule now ends in ` *`.
  The doc also wrongly said a `*` matches one path segment; it matches any text, including `/` and
  `..`, so the doc now pairs the rule with a `..` deny rule that keeps it from approving a
  same-named script outside the plugin cache. `tests/test_reviewer_cmd_allow_rule.py` guards both.

## [1.3.1]

### Fixed

- Re-arm the `stopContinueWatchdog` recovery cron from tick liveness, so a host restart
  cannot leave it silently absent. The cron lives only in the host session's memory and is
  destroyed by a restart; recreating it needs an instruction delivered to the agent, and on a
  resumed session the host does not deliver the `SessionStart` hook's `additionalContext` — so
  nothing re-armed it while every flag still reported healthy. The `UserPromptSubmit` hook now
  records a timestamp each time a tick actually arrives and asks for a re-arm when none has been
  seen for two intervals. Detecting the missing ticks rather than the specific host behaviour also
  covers a hand-deleted cron, the seven-day auto-expiry and a crashed scheduler.
- Clear the watchdog markers when *either* half of `stopContinue` + watchdog is disabled, and
  before the Stop hook's `stopContinue`-off early return rather than after it. With `stopContinue`
  off the tick tears its own cron down, so a marker left behind made a cron that no longer existed
  look alive and suppressed the re-arm for up to two intervals after re-enabling.

## [1.3.0]

First release without a prerelease label. It carries the Codex stopContinue work developed across
the `1.2.1` betas, the last of which (`1.2.1-beta.7`) was never published. Every declared version
across both packages moves to `1.3.0` in lockstep (checked by `tools/release_versions.py`); the
Codex plugin's `+codex.<timestamp>` build metadata is refreshed so the Codex host notices the
reinstall. As with every local (directory-source) release, the version bump is what lets an
installed copy pick the changes up: the plugin is compared by version *string*, so
`claude plugin update` is a no-op until the version changes.

### Changed

- Run Codex completion supervision from its automatic Stop event; remove the early
  review tool, request-time instructions, and pre-final binding.
- Use one canonical stop-supervisor prompt in both Codex and Claude Code, with no
  route-specific instruction variants.
- Check automatic rejection and continuation with the live CLI harness. Visible
  rejection feedback and desktop history expansion remain controlled by Codex.

### Added

- An opt-in live CLI completion check covering automatic Stop rejection and continuation. It records actual MCP hook verdicts and CLI
  output, rejects missing hooks and fail-open results, and leaves saved configuration unchanged.

### Documentation

- Clarify the Codex stopContinue release behavior: automatic continuation after premature stops,
  with possible visible rejected responses. Record the limited macOS CLI smoke coverage and its
  repeatable procedure. The early-review path is removed in 1.3.0; rejection can still produce visible feedback.

## [1.2.1-beta.6]

Ships the Codex reviewer-launch fix committed after `1.2.1-beta.5`. Every declared version across
both packages moves to `1.2.1-beta.6` in lockstep (checked by `tools/release_versions.py`); the
Codex plugin's `+codex.<timestamp>` build metadata is refreshed so the Codex host notices the
reinstall.

### Fixed

- Codex review launches retain the process guard and its process-tree helper in memory when the
  MCP backend starts. A later plugin update can park or remove the original cache without causing
  a missing `_process_guard.py` failure. This covers the documented installer's temporary parking
  window as well as cache eviction; cancellation and parent-death cleanup remain covered by
  subprocess tests. Already-running older servers require a restart to load the fix.

## [1.2.1-beta.5]

Ships the commit-gate and stopContinue-supervisor hardening committed after `1.2.1-beta.4`. Every
declared version across both packages moves to `1.2.1-beta.5` in lockstep (checked by
`tools/release_versions.py`); the Codex plugin's `+codex.<timestamp>` build metadata is refreshed so
the Codex host notices the reinstall. As with every local (directory-source) release, the version
bump is what lets an installed copy pick the changes up: the plugin is compared by version *string*,
so `claude plugin update` is a no-op until the version changes.

### Changed

- Commit gate: a `git -C "$VAR" commit` whose repository path is a shell variable no longer slips
  the gate. The `-C` target is parsed lexically, so an unexpandable `$VAR`/`$(...)`/backtick was
  joined onto the cwd as a fabricated path (`cwd/$VAR`), making `is_active` false and silently
  skipping the fail-closed review (a fail-OPEN). Such a target now falls back to the session cwd.
  The Codex host already fail-closed on this case. See `tests/test_commit_hook.py`.
- `AGENT_INSTALL.md`: the single allow rule for the manual commands is now version-independent —
  wildcard the plugin cache's version segment (`.../reviewer-agent/*/reviewer_cmd.py`) so it
  survives `claude plugin update`, plus an `autoMode.allow` exception (also version-independent)
  that clears the state-changing `on` the classifier flags regardless of the permission rule. New
  regression guard `tests/test_reviewer_cmd_allow_rule.py`.
- `stopContinue` (Codex host): the stop-permit fast-path is now revocation-safe under concurrency.
  `consume_stop_permit` and `invalidate_stop_permits` serialize their compare-and-mutate under the
  shared mutation lock, so an invalidation from new operator input, a tool call, or a subagent start
  can no longer interleave with a permit consumption and let a stale approval through. A failed
  generation-file write during invalidation now deletes the generation instead of leaving it stale,
  so a review already running cannot regrant a permit from its pre-review snapshot (a missing
  generation reads as empty, which no snapshot matches, so the stop is re-reviewed). Generation
  reads and claim cleanup fail open to the supervisor's failure path rather than escaping as a bare
  exception. See `plugins/reviewer-agent-codex/tests/test_stop_permit_regressions.py`.

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
