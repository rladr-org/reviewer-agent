# Remove the Codex Stop reading delay

The desktop incident captured on 2026-10-18 shows a 60.807-second gap between
the first internally completed answer and the supervisor review. Codex does not
provide a documented hook confirming that an answer was displayed. The reading
window therefore cannot reliably give the operator time to read or reply, and
submitted replies may remain queued until the synchronous hook finishes.

Remove the blocking reading window from the Codex integration. Native Stop
reviews start immediately, including with legacy delay environment/config values.
Retire the delay setting and command: old exact delay invocations must report
that the feature was removed, without changing configuration or reaching the
developer model. Preserve cancellation, completion supervision, and unrelated
settings. Scope this change to Codex; Claude's reading window is independent.

The missing/disappearing-text cause is not established. Removing the wait fixes
the confirmed extra minute, not proof of a GUI rendering fix. A background hook
is not an equivalent implementation: documented background hooks cannot control
continuation or start a new turn when they finish.

## Commit and TDD sequence

1. Commit this plan separately.
2. Replace reading-window tests with immediate-review regressions; cover legacy
   environment/config values, retired composer/CLI commands, preservation of
   unrelated settings, and cancellation before backend startup. Run the focused
   tests red and commit all test changes together.
3. Remove the wait and its configuration implementation, retain a clear legacy
   command error, and restore a Stop timeout sized for review without a reading
   window. Update active documentation, command help, release notes/checklist.
4. Run focused tests green, the full Codex suite, and affected documentation and
   packaging checks. Commit runtime and documentation together after review.

Use the development Python environment. Keep HANDOVER.md and the operator's Linux reports uncommitted.
After the reviewed source commit, refresh the local Codex installation using the
existing installer so the operator can test this candidate after hook trust and
restart. Preserve retained state and unrelated plugins/settings. Do not push.

## Acceptance

- No sleep or cancellation wait occurs before a native Stop review, with either
  default settings or obsolete delay overrides.
- Complete work is approved; incomplete work still produces continuation.
- Escape cancellation remains effective before/during review with no late ruling.
- Legacy delay commands clearly report removal and make no setting changes.
- After installing the candidate and restarting Codex, verify the extra minute is
  gone and observe answer/progress visibility. A clean turn cannot prove that an
  intermittent display issue is resolved; retain that issue until evidence does.

## Verification

- Plan commit: `ad3dee1`; combined test commit: `9508a50`.
- All 23 focused regressions failed on the old implementation before code edits.
- After removal, all 38 immediate-review, retired-command and submit-cancellation
  checks passed. The full Codex suite passed 901 tests in 160.16 seconds.
- The root suite passed 1,703 tests with 8 skips in 426.79 seconds. Layering,
  version parity, shipping-content scanning, and diff whitespace checks passed.
- Installed-candidate GUI startup, modified-hook trust and visibility acceptance
  remain separate from these source checks; no display fix is claimed.
