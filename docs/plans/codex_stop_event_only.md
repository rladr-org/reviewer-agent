# Supervise only attempted stops

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
