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
