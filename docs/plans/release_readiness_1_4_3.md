# Release 1.4.3 readiness and public launch tasks

Finish the release fixes, complete the options reference, and collect the remaining host
acceptance evidence before publishing. Use `dev-env` for local validation. Keep one plan
commit, one verified red-test commit, one code commit, and one documentation commit where
practical. Do not stage the operator's existing handover edits or untracked Linux reports.

## Fixes and diagnosis

- [ ] Add all regression tests together and confirm red before changing production code.
  Cover timeout-clear reporting with environment and config-file overrides, and the agreed
  spinner label. Commit the tests together with their observed failures.
- [ ] Make timeout clear report the effective timeout and its source after removing the saved
  override. Preserve environment and config precedence and unrelated settings.
- [ ] Change the Stop hook label to `stopContinue supervisor (reply to skip the review)`.
  This fixed label appears both in the spinner and in blocking feedback.
- [ ] Diagnose the captured contradictory supervisor replies. Distinguish output-protocol
  failures from context selection, model judgment, and parser defects. Preserve the rule that
  mentioning a stop token or a conditional permission inside a directive does not approve it.
- [ ] Record unresolved stale-request revival, demands for evidence only available after a stop,
  and reasoning emitted as a directive. Add a behavioral fix only with a reproducing regression.
- [ ] Run the affected tests green, then commit the fixes together.

## Documentation

- [ ] Document all 25 Claude JSON settings, their defaults, ranges, environment overrides,
  scope, precedence and zero/clear behavior. Include transcript and earlier-reply limits,
  optional context logging, liveness expiry and backend-specific model/effort behavior.
- [ ] Complete the Claude command forms, including model plus timeout and all clear forms.
  Align skill hints and retry wording with the implementation.
- [ ] Complete the Codex configuration reference: all six JSON settings and environment
  controls, including `REVIEWER_CODEX_SESSION_AUDIT_LIMIT`.
- [ ] Replace claims that Linux has never been installed with the actual Ubuntu findings and
  remaining acceptance status. Keep WSL2 unverified.
- [ ] Correct CONTRIBUTING's references to nonexistent issue templates and describe the actual
  bug-report route. Provide portable development setup without requiring a personal environment.
- [ ] Update PRIVACY for earlier agent replies and optional operator-context logging.
- [ ] Update CHANGELOG with the final fixes and document known supervisor limitations.
- [ ] Verify local links, versions, layering, release-content scanning and export reproducibility.
  Commit the documentation together.

## Acceptance and release

- [ ] Retain the existing five-scenario live matrix result (53 checks passed on the candidate
  before the spinner change). Rerun affected scenarios when behavior changes; validate the
  final distribution from the release artifact.
- [ ] Activate the current Codex candidate, compare installed bytes to source, restart, trust
  `/hooks`, and verify bare-commit redirection, reject/fix/approve, and cancellation without a
  stale continuation. A host restart and interactive hook trust require the operator.
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
- [ ] Review what will become public, including history if changing repository visibility.
  The existing content scanner checks the shipping tree, not every historical revision.
- [ ] Prepare current GitHub release notes and the public install/reporting instructions. Keep
  actual publication and visibility changes pending final acceptance and operator approval.
- [ ] After publication, verify unauthenticated clone/install and private vulnerability reporting.

## Evidence and remaining gates

Before this work, the local root suite passed 1,701 tests with 8 skipped and the Codex suite
passed. CI on the pushed runtime passed the macOS/Python matrix, Ubuntu/Python 3.9 through 3.14,
Git 2.25 and Pi checks. Local layering, version parity and release-content scanning passed.
Version identity is still 1.4.2. Keep the human acceptance gates pending until actually exercised.
