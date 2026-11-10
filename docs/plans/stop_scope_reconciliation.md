# Reconcile completion scope without reviving stale work

## Observed failures

The promise-follow-through test was rejected correctly, but the supervisor then
required the older incomplete-draft procedure, causing a redundant review. During
the explanation it revived an old desktop-visibility question. It subsequently
approved stopping after the agent identified a remaining defect in the behavior
it had been asked to implement and validate.

The shared policy currently treats questions/discussion too broadly as independent
deliverables. It does not clearly distinguish a regression report during assigned
implementation/validation from an unrelated defect mentioned in a standalone
discussion. Prior supervisor instructions are present as historical evidence, but
the prompt does not require tracing each proposed next step to a still-applicable
operator request. Codex also presents all recorded operator messages under one
heading, without a distinct latest-message boundary.

## Changes

- Reconcile the active outcome before issuing a directive: latest operator changes
  control the relevant earlier assignment; prior reviewer directives cannot create
  obligations. Completed or superseded procedures and optional observation requests
  must not be revived merely because a reviewer once requested them.
- Reporting a defect in assigned implementation or its validation keeps necessary
  investigation, repair and testing in scope. A prior completion claim does not
  discharge a demonstrated unresolved defect. Preserve explicit pauses, cancellations,
  operator-only blockers and standalone explanation/evaluation requests.
- When a new test changes the earlier procedure, follow its current steps. A failed
  attempt at stopping can already be the intended rejection; do not insert another
  incomplete submission from an older test. Do not hard-code a two-review limit:
  genuine remaining work still requires correction and review.
- Keep shared policy in the one resource consumed by both adapters. Label Codex's
  latest operator message separately from earlier context, retain prior messages
  needed to interpret short replies, and present that distinction near the final
  draft. Keep commit-review context unchanged.
- Do not add model calls, keyword-based approvals, automatic instruction execution,
  new authority, or changes to cancellation, fail-open behavior or approval permits.

## Test and delivery protocol

Commit this plan, then meaningful failing regressions, then implementation/docs,
then final testing. Hermetic tests must cover actual prompt dispatch on draft,
native and fresh paths, latest/earlier provenance, and shared-policy loading.
An opt-in isolated model replay must reproduce the stale-procedure and active-defect
errors with the configured model/effort, retaining actual previous rulings between
checks. Require a promise test to reject the premature draft and approve the
completed answer without requiring an extra incomplete submission. Include stale
reviewer-only desktop requests, unresolved in-scope defects, repaired defects,
standalone discussion and explicit cancellation controls. Normal pytest never
calls a real reviewer; use synthetic context only for model replays.

Run both suites, relevant export/privacy/version checks and the model replay after
the implementation commit. Install the reviewed Codex build with the existing Python
interpreter, preserving reviewer settings and recording configuration changes.
Keep unrelated local files. Do not push or publish. A host restart may be needed;
do not claim a newly installed server is loaded in an existing host session.
