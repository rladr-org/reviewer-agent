# Native Stop: reject an incomplete intermediate response

## Observed failure

The 2026-09-15 CLI audit ran Codex 0.154.0 against installed plugin build
`1.2.1-beta.6+codex.20260915215809`. The pre-final scenario passed. The first native
fallback scenario emitted `I have not calculated it yet.` and ended without calculating
the requested product. Its native hook returned `{}`. A repeat passed; passing repeats
do not clear the failed run.

Documented app-server task reads identify the failed reviewer as
`01a0a8c2-42c4-7951-9b92-1fa1ccb90756`. It received the complete operator prompt and
the incomplete response, then returned `STOP_OK`. This establishes an incorrect
reviewer approval, rather than absent hooks, missing operator context, or fail-open.
The evidence does not expose why the model made that decision. The existing prompt
does not identify native Stop as the active review invocation, unlike the pre-final
path. The operator's test instruction asks for an intentionally premature stop before
the calculation, which must not be confused with satisfying the substantive request.

Separately, `check_cli_stop.py` accepts fallback evidence in which the corrected answer
arrives before the blocking verdict. Its presence/count checks cannot establish that
rejection preceded continuation.

## Required behavior

- Identify the active native Stop review explicitly. Evaluate whether allowing this
  stop would leave the operator's substantive outcome unfinished. An instruction to
  attempt a stop or exercise the hook does not itself complete outstanding work.
- Ask the reviewer to check the requested outcome, evidence in the final response and
  active plan, and any genuine operator-input dependency before returning `STOP_OK`.
  The invocation itself supplies the Stop review: do not defer the decision to a
  later hook or demand that the developer manually invoke lifecycle tools.
- Preserve explicit operator requests to stop, pause, or cancel. Do not adopt quoted
  incomplete statements as live assignments. Preserve pre-final context, cancellation,
  authority boundaries, retry limits, and fail-open behavior.
- Include native invocation guidance in the stop-role fingerprint so old reviewer
  sessions cannot retain the earlier context after the update.
- Require observed fallback ordering: incomplete response, native Stop request and
  blocking response, corrected response, second Stop request and silent response.
  Cross-stream receipt timestamps are observations, not host emission timestamps;
  they provide regression evidence, not a universal publication guarantee.

## Implementation and verification sequence

1. Commit this specification before tests and runtime edits.
2. Commit red tests for native invocation wiring/fingerprint and fallback event order.
   Keep ordinary tests isolated from real reviewer calls. The recorded live false
   approval above is the behavioral red evidence; prompt-string assertions alone
   cannot establish that a model makes the right decision.
3. Implement native context and the ordering checks, then commit after green tests.
4. Exercise real reviewer cases outside pytest: the original incomplete request,
   its completed answer, explicit operator cancellation, and quoted incomplete text.
   Exercise the CLI fallback repeatedly and the desktop pre-final path. Record all
   results, including failures; do not retry until green and discard earlier failures.

The new CLI smoke harness and documentation from the preceding task are uncommitted
inputs to this work. Preserve them; do not mix unrelated existing work into these
commits. This fix does not claim infallible model judgments or control over desktop
history rendering. A new live failure requires diagnosis rather than dismissal as
variation.

## Red checkpoint

The protocol source is an earlier user message in desktop task
`01a0a4b0-e341-7003-8fcb-04dd267b8e1c` ("Improve desktop stop-continue UX"), turn
`01a0a4b2-d01f-7221-ac46-e63272ee35df`, item
`01a0a4b5-2234-7842-a579-944a9078aa0c`, retrieved through the documented `read_thread`
API during this fix. It is older than the current shortened operator-prompt ledger.
That message explicitly required: "documentation update -> commit", then "write red
TDD tests that actually test the proper working of the functionality -> commit", then
"write code that passes the tests and properly implements the specifications -> commit".
The current test commit is that deliberately failing checkpoint, not the implementation
or a release. Running `tests/test_native_review_context.py tests/test_cli_stop_check.py`
before implementation produced eight intended assertion failures and seventeen passes
(including the subsequently added live-check opt-in guard):
native invocation context, reviewer-session invalidation, and six ordering cases.

The unchanged, previously untracked CLI helper is included solely so a clean checkout
can reproduce those assertion failures instead of failing to import the helper. Its
known ordering defect is intentionally retained until the next implementation commit,
as required by the operator's red-before-green sequence. Fixing it in this checkpoint
would erase the requested red state. The opt-in native reviewer script is a test driver,
not a runtime change, and ordinary pytest never invokes real reviewer calls.

## Implementation and live evidence

The native prompt now identifies the active review, distinguishes an intermediate stop
attempt from the substantive requested outcome, preserves explicit cancellation and
quoted-evidence boundaries, and requires the reviewer to decide now. Its context is
included in the stop-role fingerprint (`ef06bf03cd641b89`). Fallback smoke assessment
now requires the observed six-event sequence in order.

Initial implementation build: `1.2.1-beta.6+codex.20260916062300`. The installed prompt, smoke harness,
and manifest bytes match the checkout. The following checks used real reviewer calls:

- `scripts/check_native_stop_reviewer.py --run --attempts 5`: all twelve cases passed
  (five original incomplete/completed pairs, explicit cancellation, and quoted evidence).
  Artifact: `native-stop-reviewer-check-kl_9213g/results.json` in the host temporary directory.
- CLI pre-final and native fallback both passed, including strict observed event order.
  Artifact: `cli-stop-check-_g0r_1z0/summary.json` in the host temporary directory.
  Two additional fresh fallback sessions also passed (`cli-stop-check-zplm6qej` and
  `cli-stop-check-dyiwjfkk`), giving three passing installed-host fallback runs.
- A fresh desktop app-server exercised the installed plugin: two completion reviews
  (`incomplete`, then `stop_ok`), one approved final answer, no hookPrompt, and silent
  native Stop in one turn. Artifact: `reviewer-prefinal-live-z5n7cw_l/host-events.json`.
  This is backend-event evidence, not a new visual inspection of the desktop window.

The original failed CLI run is retained as `cli-stop-check-wi6w7x4o`; its passing retry
is `cli-stop-check-9eameue0`. Four further diagnostic runs of the old build also passed,
so this was one observed failure in six old-build fallback runs. Those passes did not
erase the confirmed erroneous approval. Prompt clarification and finite live regressions
reduce this observed failure mode; they do not establish infallible model decisions.

The targeted suite first reported 106 passes and one existing cancellation-test startup
timeout (`reviewer_started.wait(1)`) while tests ran concurrently. A targeted rerun of
that test and all new regressions passed all 26 cases. Release metadata/export checks
passed all 50 tests and the plugin validator passed. The complete canonical plugin
suite then passed all 387 tests in 472.35 seconds, including the cancellation test.
Changed Python sources also parse with the Python 3.9 grammar; `git diff --check` passed.

### Commit-review correction: legacy call distinction

The implementation reviewer found that `stop_prompt(prefinal=False)` is also used by
legacy `stop_continue`, so selecting native guidance for every non-prefinal call was
incorrect. The added MCP-level legacy regression failed on that implementation (one
failure, four passes). `supervisor._review_in_hook` now explicitly passes `native=True`
through `stop_tool.review_stop` to the prompt builder. Legacy calls default to false;
pre-final calls keep their existing context. This selects the invocation context without
changing review-result processing, budgets, or permit handling in either path.

The new regression and all other new tests then passed (26 cases). Installed build
`1.2.1-beta.6+codex.20260916064352` includes the correction. The full suite is rerun for
this build, and the final host smoke tests will be rerun after its implementation commit.

The full rerun reported 387 passes and the same one-second cancellation-test startup
timeout. That recurrence is not dismissed. The test created its Git-backed ticket inside
the worker's one-second startup window, conflating setup/scheduling latency with the
property under test. Ticket creation is now outside the worker, both event waits and
the join remain bounded at ten seconds, and cleanup also runs on startup failure.
The cancellation assertion remains before releasing the reviewer: the abort bypass must
already exist when `cancel()` returns. No runtime cancellation behavior is changed.
The corrected test and new regressions passed together (27 cases), and a separate
fault-injection run adding 1.5 seconds to ticket creation also passed. The final installed
package is `1.2.1-beta.6+codex.20260916065354`; its runtime, changed tests, harness, and
manifest match the checkout. The 50 release checks passed again for that package.
The final canonical plugin run passed all 388 tests in 467.85 seconds, including the
corrected cancellation test and the native/legacy routing regression.
