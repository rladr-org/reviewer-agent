# Keep completion retries inside the active Codex turn

## Finding and scope

The reported desktop symptom is resumed output briefly appearing and disappearing,
with no visible activity indicator after a Stop supervisor rejection. In the
affected chat, the documented thread API still reports an active turn and retains
commentary and work after the hook prompt. Desktop logs also record incoming
reasoning summaries. This establishes continued execution and receipt by the app,
not the exact renderer defect. The initial investigation did not include direct GUI inspection. Private incident captures and installed application assets stay under
ignored `logs/`; none belongs in the public change.

The documented Stop contract turns `decision: block` into a continuation prompt.
The plugin cannot control desktop rendering through that contract. The operator
has authorized an alternative implementation as a workaround. Prefer a normal,
blocking `review_completion` MCP call with the complete final draft, before the
agent publishes its final answer. Rejection returns ordinary tool output while
the turn remains active; the agent continues and submits its revised draft.

This deliberately revisits the removed pre-final path. Its historical circular
review failure must remain fixed: the supervisor receives factual invocation
context explaining that this call performs completion review and the supplied
draft is pending publication. Both routes retain the same canonical policy.

## Contract

- Enabled requests receive instructions to check the complete final draft. Request
  receipt and ordinary tool use never start a review themselves.
- PreToolUse replaces completion arguments with the host cwd and a one-use ticket
  bound to the actual session, turn, draft, operator generation and work generation.
  A missing hook, forged token, legacy ticket or changed draft cannot grant approval.
- A valid approval covers exactly one matching Stop. New work, new input, changed
  drafts, changed turns and expiration require review again. Legacy non-strict
  handoffs must not skip native review.
- Incomplete tool results contain an actionable directive, not hook decisions.
  Successful draft review followed by its exact final answer causes a silent native
  Stop without a duplicate review or synthetic continuation prompt.
- Both routes share the existing bounded budget, fresh reassessment, background
  liveness policy, cancellation and explicit fail-open reporting. Interrupt must
  cancel an in-flight draft review even if the host omits MCP cancellation.
- Native Stop stays the fallback if the model omits the draft check or its approval
  is invalidated. That fallback can still expose the desktop problem. This is a
  workaround for the triggering path, not a claim to repair the desktop renderer.
- Commit review and Claude Code behavior stay unchanged.

## Validation and delivery

Add and run failing behavioral tests before runtime edits. Exercise hook binding,
rejection/correction/approval, one-use and stale permits, budget, background waits,
failures and cancellation. Run the complete Codex suite and affected public-export
and shared-policy checks. Add an opt-in live backend check that requires an actual
rejection, an actual approval, only the approved final response, and a silent Stop;
record native fallback separately. Backend evidence does not establish GUI paint.

The operator's updated delivery protocol supersedes the earlier request to leave
this work uncommitted. Preserve the original working files in a local backup and
integrate on the 1.4.5 release baseline in an isolated worktree:

1. Commit this plan through the staged review gate.
2. Restore only the behavioral tests, run them against the release without the
   implementation, inspect the actual failures, and commit the red tests separately.
   Include the opt-in live-check driver as test infrastructure, so its safety guard
   tests an existing script rather than failing because a file is absent.
3. Restore the implementation and accompanying behavior documentation. Keep the
   release's 1.4.5 versions, assign a fresh Codex build label, and retain both the
   release changelog and the unreleased feature entry. Commit through review after
   the red tests pass. Any newly discovered behavior needs its own red/green cycle.
4. Run both complete suites, shared-policy/export/privacy/version checks, and a
   real rejection/correction/approval/silent-Stop check. Attempt a fresh-chat desktop
   visual check through available UI controls; report any unavailable or unobserved
   behavior explicitly. Do not restart an app with active user work merely to test.
5. Fast-forward the main checkout to the reviewed integration branch after verifying
   its original edits still match the backup. Preserve the two unrelated Linux issue
   reports as local files. Record validation separately after implementation.

This changes no Claude Code behavior beyond incorporating the existing release.
Do not publish or push without the operator's separate approval.

References: [Stop](https://learn.chatgpt.com/docs/hooks#stop),
[PreToolUse argument replacement](https://learn.chatgpt.com/docs/hooks#pretooluse),
[app-server reads](https://learn.chatgpt.com/docs/app-server#api-overview).

## Historical verification of the uncommitted prototype

- The initial 19 behavioral regressions failed before implementation. Three additional
  missing-binding cases demonstrated that errors must direct the agent to native Stop
  rather than inviting another unbound draft call. All 23 draft-path cases now pass,
  including cancellation before worker startup and fresh reassessment after unchanged retries.
- A real reviewer rejected the incomplete calculation draft, approved its corrected answer
  in the same reviewer session, and permitted a silent Stop with no third review.
- A separate test through the desktop-bundled Codex backend exercised host argument replacement,
  a controlled rejection, a real reviewer approval, and one empty native Stop decision. Only
  the corrected answer was emitted. The deliberate first rejection in this transport test
  was a fixture; the preceding real-reviewer check separately verifies actual judgment.
- The full Codex suite passed 923 tests. The root suite passed 1,703 tests with 8 skips.
  Shared-policy and affected public-export checks passed,
  and a candidate export including the new files passed the privacy scan. Runtime changes
  also parse with Python 3.9 grammar.
- Local installation preserved existing cache directory inodes, repository enablement,
  the selected interpreter and unrelated configuration. The installed server advertises
  the draft tool alongside commit review and the two lifecycle tools.

Desktop paint and activity indicators still require a fresh-chat visual check after restarting
the host. Existing chats were kept running. The implementation is a workaround for the triggering
transition; a native fallback rejection may still reproduce the desktop symptom. At that point, no commit or public push had been made. The protocol above now
authorizes local commits; these historical results must not substitute for testing
the integrated candidate.
