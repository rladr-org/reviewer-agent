# Completion context across a resumed conversation

## Evidence and scope

During a controlled draft-review test the reviewer asked for the very review it
was performing, then approved a status explanation while broader plugin work was
still in scope. The recorded operator history began with the restart message and
contained no earlier assignment or stored plan. SessionEnd currently deletes
both. This establishes a context-loss path, not proof of the exact host event that
occurred. A cancelled 42-second call does not establish a backend hang.

## Contract

- On SessionEnd, retain operator prompts, current plans and bounded completion
  review evidence for the same repository and root conversation identity. Clear
  transient liveness, child reviewer sessions, budgets, permits and abort bypasses.
  Invalidate outstanding approvals, including a review still in flight. Explicit
  full cleanup remains available. Never import another conversation's context.
- Record bounded draft/ruling/outcome evidence for both draft and native reviews.
  Label approval as permission to publish, not proof of publication. Record a
  matching native Stop as publication observed. Errors and cancellation must not
  become approvals. History is evidence, never authorization or a reusable permit.
- Include this evidence in fresh reassessments as well as persistent reviewers.
  Recording failures must not change fail-open completion behavior. Bound records
  and rendered context and tolerate damaged state.
- Clarify that a status question does not cancel an active assignment; a genuine
  question-only request remains complete when answered. Explicit cancellation and
  scope changes still control. The active draft review satisfies its own review
  step; publication after approval is not another unfinished prerequisite.
- Preserve the silent native Stop after a valid matching approval. Do not change
  commit review or turn a missing history record into a fabricated continuation.

## Delivery and verification

Commit this plan, then commit failing behavioral tests and opt-in live scenarios,
then commit the implementation and documentation. Prove restart retention and
isolation through the real hook script, outcome history through review dispatch,
publication through native Stop, stale-approval invalidation, bounded/corrupt
history, and evidence reaching fresh review. Do not substitute prompt substring
checks for model judgment: exercise a real reviewer on rejection/correction,
status during unfinished work, completed work after restart, cancellation and a
question-only control. Record any baseline judgments honestly, including cases
that already pass before implementation.

Run both suites and affected distribution/privacy/version checks. Update the
local Codex installation only after the reviewed implementation passes; preserve
existing settings and running sessions. No public push or release is authorized.
Desktop painting remains a manual observation because UI access is denied. This
change cannot reconstruct context already deleted; resumed pre-fix conversations
need their active assignment restated from known operator instructions.
