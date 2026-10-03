# Codex background waiting

## Problem

The Codex liveness prompt permits a stop only after every tracked process and subagent
has finished. It therefore blocks a legitimate handoff to an already-arranged monitor
or completion notification, including the Food52 scheduled wake-up test. Claude's
background-wait bypass does not exist in Codex. Durable Codex markers alone cannot
prove either live work or a guaranteed wake-up, so blindly bypassing review for any
marker would also allow abandoned work.

## Change

Add a Codex-specific STOP_WAIT ruling for a temporary yield while background work is
running and a concrete resumption mechanism is already arranged. The supervisor must
check that no other authorized work can proceed now, or that the operator explicitly
requested this pause. It must not require background completion before allowing that
wait, nor demand verification of a future wake-up before it occurs. An unfinished task
with no resumption mechanism or with actionable foreground work remains supervised.
Keep the shared Claude prompt unchanged; append the Codex-specific output contract to
both normal and fresh reassessments, including marker-free scheduled waits.

STOP_WAIT is distinct from STOP_OK. Consume the same single-use, generation-validated
permit but preserve all outstanding markers for later completion events and reviews.
New input or work during review still invalidates the ruling. Cancellation and fail-open
behavior remain intact. Completed-work STOP_OK retains exact stale-marker cleanup.
Recognize only an unambiguous standalone STOP_WAIT response; do not treat prose mentioning
that token or contradictory directives as permission.

## Protocol and verification

Commit this plan. Commit failing regressions for native wait approval, preserved shell
and subagent markers, legacy ticket handling, permit consumption, fresh reassessment,
generation races, cancellation, and prompt scope. Implement and run the entire Codex
suite; investigate every failure. Commit the green implementation. Use a real model
prompt evaluation (not a manually invoked lifecycle hook) to compare the supplied
Food52 wait with missing-wake and foreground-work controls. Keep test state isolated.
Do not modify concurrent release edits. Installation follows source integration with a
fresh build identifier; report any remaining restart or integration step explicitly.
