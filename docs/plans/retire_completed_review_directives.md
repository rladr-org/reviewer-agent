# Retire completed review directives from completion input

## Remaining failure

The scope-policy change passed its short replay but a closer synthetic replay of
the conversation still reproduced the redundant incomplete-draft instruction.
Three fixed trials against the old version passed 5 of 9 decisions; the installed
candidate passed 8 of 9. Its one failure was a directive to repeat the old procedure
after the premature stop already supplied the rejection the new scenario required.
The later corrected answer was approved. Diagnosis-only stops were rejected in all
trials on both versions, so those trials do not establish a before/after repair for
that separate historical error.

This is incomplete acceptance evidence, not grounds to call every original failure
fixed. Keep the exact stronger fixtures and the failed result as regressions.

## Bounded correction

Completion history currently sends old reviewer commands verbatim even after a
later successful completion review. Project that history for model input: retain
the submitted drafts, statuses and turn identities, but omit directive/detail text
at or before the latest stop_ok or published record. Keep the unresolved suffix's
details for ongoing corrections and fresh reassessment. stop_wait, cancellation,
errors and exhausted budgets do not retire directives. Preserve the full stored
history for audit; this is not deleting task requests or asserting that historical
approval proves every task complete.

Also clarify the developer-facing completion guidance: a continuation directive is
not new authorization. Reconcile it with the current user request and observed work;
do not replay an already-satisfied test step merely because the reviewer repeats it.
Perform the remaining substantive work and submit the completed draft. Do not add
automatic approval, a fixed two-review cap, extra model calls, or keyword-based
classification of tasks.

## Protocol and acceptance

Commit this plan. Commit failing tests for prompt projection, preservation of stored
evidence, unresolved suffixes and non-approval outcomes, plus delivery of the updated
guidance through a real hook fixture. Preserve the stronger synthetic replay as an
opt-in regression check with the configured model/effort and isolated state.
Then implement and commit the change, and test it.

Run the same three trials without editing their inputs between versions. Include
the existing standalone-discussion, cancellation and repaired-defect controls.
Run the relevant suites and package checks, install after approval, and preserve
settings. Verify the current host version and finish with the actual promise test
when other implementation and validation work is complete. If it fails, continue
investigation rather than treating a favorable subset as completion.
