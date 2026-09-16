# Supervise only attempted stops

**Historical plan.** The operator has now confirmed review at every Stop,
including completed question answers, and removal of the legacy agent-directed
fallback. The [approved sprint scope](codex_stop_hook_parity.md) supersedes the
unresolved question-classification discussion and compatibility allowance below.

## Requirement

Run completion supervision only when Codex emits its automatic `Stop` event.
Receiving a user request and performing ordinary work must not invoke completion
supervision or instruct the agent to invoke it early. Ending an answer to a
question is also a turn ending and therefore still emits `Stop`.

The operator also objects to supervision of each ordinary question answer.
Codex does not expose a separate event distinguishing completion of a question
answer from abandonment of an active work assignment. It is impossible to
guarantee that distinction using the exposed Stop event alone. A plugin would
need additional task-state tracking and a policy for classifying work; that
would be an inference, not detection supplied by Codex.

This specification covers only removal of the early invocation. It is a partial
change and MUST NOT be reported as satisfying the full requested behavior or
preventing reviews after ordinary question answers. The operator has been asked
to resolve the intended treatment of those turn endings before task-state
classification is designed. No question exemption is implemented by this change.

This supersedes the pre-final invocation design. The user prioritizes correct
invocation timing over the desktop presentation workaround. Codex's rejection
continuation can still be visible and affect history expansion; this plugin
cannot promise to suppress that desktop behavior.

## Implementation contract

- Retain request/plan/work tracking without injecting pre-final instructions.
- Do not advertise or execute the agent-initiated `review_completion` path.
- Do not bind pre-final review tokens in `PreToolUse`.
- Preserve automatic `Stop` supervision, cancellation, bounded retries, and
  commit review. Keep legacy hook-directed continuation compatibility separate
  from an early completion review.
- Do not invent a question classifier while that requirement is pending.
  Removing early review is independent of any subsequent selection of which
  Stop events warrant substantive review.
- Use one canonical stop-supervisor prompt shared by Codex and Claude Code.
  Remove pre-final and native-only instruction variants. Application-specific
  code may supply task context, but must not change the review instructions.

## Verification and delivery

Follow documentation commit, failing behavioral test commit, implementation
commit. Tests must exercise request and ordinary tool lifecycle events without
completion reviews, reject obsolete early-review calls without invoking the
reviewer, and demonstrate automatic Stop review and continuation on unfinished
work. Run the relevant suite and a live automatic-Stop smoke test.

An already running desktop process retains its loaded plugin instructions until
the updated plugin is loaded. Report that distinction explicitly when delivering.

## Verification record

- Timing red checkpoint: six expected failures and 30 passes. The failures showed
  injected early-review instructions, the advertised early tool, and a smoke
  harness still selecting early review.
- After removing that path, 64 targeted timing, MCP, lifecycle, and CLI-harness
  checks passed. Another 69 shared-prompt and release checks passed.
- Installed build `1.2.1-beta.6+codex.20260916084059` passed the live CLI check:
  zero early-review calls; two automatic Stop calls; incomplete response rejected,
  corrected response approved, with rejection observed before continuation.
- Six installed real-reviewer cases passed: two incomplete/complete pairs,
  cancellation, and quoted historical work.
- The desktop app's bundled backend passed an ephemeral read-only test with the
  installed plugin: two automatic Stop reviews, zero early-review calls, an
  incomplete final response followed by a blocking hook and then the corrected
  response and approval. The rejection emitted a `hookPrompt`, as expected; this
  does not establish that visible feedback or history collapse has disappeared.
  The test did not navigate or alter an existing desktop task.
- Final Codex suite: 372 passed. The 21 CLI-harness checks also passed after
  removing unused early-review bookkeeping. Plugin validation and Python 3.9
  syntax checks passed. Changed installed runtime files matched the source tree
  at the tested build before the release identity change below.
- Commit review identified that Claude installations already on beta.6 would
  treat an unchanged version as current. A live update command reproduced
  `up_to_date`. The package declarations were advanced consistently to beta.7;
  the parity checker rejected the intermediate mismatch and passed after all
  declarations were updated. The final Codex identity is
  `1.2.1-beta.7+codex.20260916090227`.
