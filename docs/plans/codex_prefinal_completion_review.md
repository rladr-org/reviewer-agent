# Review completion before publishing the final response

## Problem and API boundary

The desktop live test of the native Stop hook verified that rejection continues work, but the
operator observed both the supervisor prompt and collapsed earlier messages. Silent approval
alone does not meet the requested UX. The original desktop task also failed to invoke the native
reviewer after restart; its cause remains unresolved, so a fresh-task pass is not proof of a fix.

The [documented hook API](https://learn.chatgpt.com/docs/hooks) creates a user-like continuation
prompt whenever Stop returns `decision: block`. `suppressOutput` is not implemented. Plugins
cannot guarantee hidden rejection or control desktop history expansion with that API.

## Proposed behavior

- Enabled sessions receive developer context asking them to call `review_completion` with the
  complete proposed response before publishing a final response. This is a regular, cancellable
  MCP call, like commit review. It may have the desktop's normal tool presentation.
- PreToolUse binds that call to the actual host repository, session, turn, and proposed response
  using a fresh one-use ticket and documented `updatedInput`. Model-provided session identities
  and tokens cannot override that binding. Missing hook context must never fabricate approval.
- An incomplete ruling is an ordinary tool result. The agent performs the remaining work and
  resubmits its draft in the same turn. Neither rejection nor approval emits a hook continuation.
- Approval permits one Stop for the reviewed draft and host turn, with existing operator/work
  generation checks and five-minute expiry. Any changed draft, input, work, or turn requires a
  new review. Legacy Stop handoffs retain their existing continuation-compatible semantics.
- The native Stop handler remains the fallback for absent or invalid approval. It can still
  produce visible feedback and collapse history when it must reject. This is a limitation, not
  a claim of complete control over the desktop UI.
- Pre-final and fallback review share the existing bounded review budget. Reviewer failure is
  reported as failure and permits stopping under the fail-open policy; budget exhaustion is
  reported distinctly. Cancellation retains the existing short-lived interrupt bypass.
- Commit gating, reviewer isolation, minimal review context, and liveness checks stay intact.

## Required development and validation

1. Commit this specification and corresponding user documentation.
2. Commit failing behavioral tests for the hook-to-tool-to-Stop round trip: rejection followed
   by approval without a continuation, trusted scope binding, changed draft/turn/work/input,
   disabled/missing-hook behavior, one-use tickets, budget, failure, and cancellation.
3. Implement and commit after focused and full plugin checks pass.
4. Install through the cache-preserving local installer. Test with the real desktop backend and
   in an authorized desktop task. Inspect reviewer verdicts and documented lifecycle events to
   prove review happened and normal retries did not generate a hook prompt. Ask the operator to
   confirm visible history behavior; backend events alone cannot prove the UI stayed expanded.

## Validation history

- Documentation committed as `68349d5`; 22 failing behavioral cases were run and committed as
  `a3e308d` before implementation.
- The initial candidate passed all 360 then-existing plugin tests, 50 release/export tests,
  and plugin validation. Installation metadata was updated through the cachebuster helper.
- Implementation review found that budget exhaustion accepted non-strict legacy permits.
  Regression commit `36d9e29` covers matching/changed drafts, changed turns, and legacy rejection.
  It failed against the candidate; the fix requires `strict_completion` to be exactly true.
  All 61 affected completion/state/native Stop checks passed after that restriction.
- The next independent commit review ran the full suite: 361 tests passed in 437.65 seconds.
  It requested consistent surrounding-whitespace normalization at the ticket-to-tool boundary.
  Regression commit `a529ae0` reproduces that rejection and also rejects internal-whitespace
  changes. The comparison now normalizes only surrounding whitespace, matching permit validation.
- All 24 completion tests pass after normalization (58.41 seconds). Live verification is pending.

## Live finding: review requests become circular

The installed desktop backend test invoked the real completion reviewer, but failed: it made
10 incomplete rulings followed by the bounded budget-exhaustion result. After calculating the
answer, the developer repeatedly submitted `17 × 19 = 323.`; the reviewer told it to call
`review_completion` again or publish the approved draft. The user test had explicitly requested
those steps, while the reviewer lacked context that its current invocation performs that review.
This is a failed functional test, not approval. Artifact directory:
`/var/folders/84/ks9457dn2gg0sqww5cdrn0cw0000gn/T/reviewer-prefinal-live-5l2tbni2`.

The next correction will supply trusted pre-final review context: this invocation fulfills the
completion-review step, and the proposed draft is the response that will be published if approved.
The reviewer must evaluate whether that response completes the substantive request, without
demanding another invocation of itself or prior publication of the draft. This does not fulfill
other requested actions (such as tests, commits, external publication, or a distinct review).
Native Stop review must retain its current context. A real-reviewer regression will require an
incomplete calculation draft to be rejected and its completed answer to be approved in the same
reviewer session. It will be opt-in because it invokes the real Codex model and needs host access.
