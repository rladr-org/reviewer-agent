# Release 1.4.3 readiness and public launch tasks

Finish the release fixes, complete the options reference, and collect the remaining host
acceptance evidence before publishing. Use `dev-env` for local validation. Keep one plan
commit, one verified red-test commit, one code commit, and one documentation commit where
practical. Do not stage the operator's existing handover edits or untracked Linux reports.

## Fixes and diagnosis

- [x] Add all regression tests together and confirm red before changing production code.
  Cover timeout-clear reporting with environment and config-file overrides, and the agreed
  spinner label. Commit the tests together with their observed failures.
- [x] Make timeout clear report the effective timeout and its source after removing the saved
  override. Preserve environment and config precedence and unrelated settings.
- [x] Change the Stop hook label to `stopContinue supervisor (reply to skip the review)`.
  This fixed label appears both in the spinner and in blocking feedback.
- [x] Diagnose the captured contradictory supervisor replies. Distinguish output-protocol
  failures from context selection, model judgment, and parser defects. Preserve the rule that
  mentioning a stop token or a conditional permission inside a directive does not approve it.
- [x] Record unresolved stale-request revival, demands for evidence only available after a stop,
  and reasoning emitted as a directive. Add a behavioral fix only with a reproducing regression.
- [x] Run the affected tests green, then commit the fixes together.

## Documentation

- [x] Document all 25 Claude JSON settings, their defaults, ranges, environment overrides,
  scope, precedence and zero/clear behavior. Include transcript and earlier-reply limits,
  optional context logging, liveness expiry and backend-specific model/effort behavior.
- [x] Complete the Claude command forms, including model plus timeout and all clear forms.
  Align skill hints and retry wording with the implementation.
- [x] Complete the Codex configuration reference: all six JSON settings and environment
  controls, including `REVIEWER_CODEX_SESSION_AUDIT_LIMIT`.
- [x] Replace claims that Linux has never been installed with the actual Ubuntu findings and
  remaining acceptance status. Keep WSL2 unverified.
- [x] Correct CONTRIBUTING's references to nonexistent issue templates and describe the actual
  bug-report route. Provide portable development setup without requiring a personal environment.
- [x] Update PRIVACY for earlier agent replies and optional operator-context logging.
- [x] Update CHANGELOG with the final fixes and document known supervisor limitations.
- [x] Verify local links, versions, layering, release-content scanning and export reproducibility.
  Commit the documentation together.

## Acceptance and release

- [ ] Retain the existing five-scenario live matrix result (53 checks passed on the candidate
  before the spinner change). Rerun affected scenarios when behavior changes; validate the
  final distribution from the release artifact.
- [x] Activate the current Codex candidate, compare installed bytes to source, restart, and
  verify commit redirection and reject/fix/approve in the desktop host. Verify commit-review
  Escape makes no commit, leaves the edit staged, and allows a successful gated retry.
- [ ] Finish desktop stop-supervision and cancellation checks, distinguishing reading-delay
  cancellation from cancellation during backend review. Check interactive `/hooks` trust when
  testing the CLI host.
- [ ] Resolve the desktop reply-cancellation acceptance failure: timely host input delivery is
  required, but the tested desktop delivered the reply after review finished. Decide whether
  to require a host fix or accept the documented Escape workaround before release.
- [ ] In the real hosts, verify Escape during a commit review and a successful retry,
  `AskUserQuestion` rerouting, and uninstall preservation of unrelated settings/plugins.
- [ ] On the operator's clean Linux server, exercise manual/agent installation, smoke tests
  under 60 seconds, full suites with NFS `TMPDIR`, Git 2.25, PATH/binary discovery, launcher
  failure, directives, watchdog status and history cleanup. Record results against the reports;
  do not create a second local profile or change the operator's running hosts for these tests.
- [ ] Verify command/config show, set, show and clear; override precedence; invalid input;
  global versus repository scope; delays 0 through 300; and the timeout reporting regression.
- [ ] Verify completed-work and compaction-handover stops, continuation of unfinished authorized
  work, and stop review with a background server. Preserve the operator's chosen delay afterward.
- [ ] After acceptance passes, cut 1.4.3 and refresh the Codex cachebuster; verify version parity,
  both full suites, packaging and an installed-artifact smoke check. Do not bump early.
- [x] Review what will become public, including history if changing repository visibility.
  The existing content scanner checks the shipping tree, not every historical revision.
- [x] Prepare current GitHub release notes and the public install/reporting instructions. Keep
  actual publication and visibility changes pending final acceptance and operator approval.
- [ ] After publication, verify unauthenticated clone/install and private vulnerability reporting.

## Evidence and remaining gates

Before this work, the local root suite passed 1,701 tests with 8 skipped and the Codex suite
passed. CI on the pushed runtime passed the macOS/Python matrix, Ubuntu/Python 3.9 through 3.14,
Git 2.25 and Pi checks. Local layering, version parity and release-content scanning passed.
Version identity is still 1.4.2. Keep the human acceptance gates pending until actually exercised.

### Current verification

- Plan committed as `9a8f639`; the combined red tests as `f3bf887`; code as `e099bb2`.
  The three selected tests failed before the fixes. Both affected files then passed all 97
  tests. Local tests use the operator's selected conda environment.
- Documentation/distribution/release/dispatcher/shared-prompt checks passed 89 tests. The source
  inventory accounts for all 25 Claude JSON keys and six Codex keys, with no missing literal
  environment controls in either host's README. The shell-launcher override and dynamically
  constructed Pi/Claude model and effort controls are documented too.
- Final local-link checks passed five tests. Version parity, layering and the shipping-tree scan
  passed. Two independent 295-file exports of the staged source produced byte-identical archives
  and passed required-file/content checks. Unrelated unstaged handover edits were excluded from
  those snapshots; this is a candidate check, not the future versioned release artifact.
- Pre-change CI evidence: [Python suites](https://github.com/rladr-org/reviewer-agent/actions/runs/39022822433)
  and [Pi extension](https://github.com/rladr-org/reviewer-agent/actions/runs/39022822410).
  The earlier five-scenario live result is recorded in the existing handover.
- A pattern scan of all 1,353 text blobs reachable from local refs found no credential-pattern
  matches. One historical planning revision matched account-path rules: generic test fixtures
  and default Ubuntu account paths, also reachable from remote refs/tags. Those do not appear
  in the current shipping tree. This pattern scan is limited to the existing scanner's rules;
  publishing an export remains distinct from exposing all repository history.
- [Draft release notes](../release-1.4.3.md) are prepared locally. No version bump or publication
  has occurred.
- Desktop acceptance on 2026-10-17 passed restart/persistence, commit rejection and correction,
  and commit-review Escape/retry in a detached disposable worktree. The release branch was not
  changed by these fixture commits. Reply cancellation failed: the timed observer saw the
  reviewer exit at +79.299 seconds and the operator input hook arrive at +79.504 seconds.
  The 16 focused submit-cancellation tests pass when that hook is delivered. This establishes
  a desktop delivery limitation rather than proving cancellation during the actual GUI check.
  Both READMEs and the draft release notes now qualify the behavior. The local timing report
  and remaining operator checks are recorded in the uncommitted handover.

### Supervisor diagnosis

The local hook log records two relevant replies on 2026-10-17 at 02:44:01 and 02:45:55. Both
describe an operator-only next step and conclude that stopping is permitted; neither emits
`STOP_OK`. The second complete reply is:

> The update is installed, but the remaining checks require restarting Claude Code and operator
> interaction. The proposed message names those steps, so stopping is permitted.

Replaying that string through the current classifier returns no stop token, so the hook relays
it as a directive. A synthetic control ending in its own final `STOP_OK` sentence is accepted.
The older reasoning-dump form ending `So STOP_OK. exact.` also fails the terminal-token contract.
These are reproduced output-protocol failures, rather than evidence of a lost token or a parser
inverting a valid approval. Inferring approval from arbitrary prose could approve a conditional
directive and is outside this fix.

Stale requests, post-stop evidence demands and model judgments require their captured input
context to reproduce. Earlier replay results already exist in [supervisor reply context](supervisor_reply_context.md);
most historical compaction incidents did not reproduce there. Keep those incidents documented
as unresolved and collect the next actual failing prompt/output before adding another behavior
change. No broad prompt change or new parsing heuristic was made for these observations.
