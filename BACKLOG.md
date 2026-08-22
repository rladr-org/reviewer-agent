# Backlog

## Move timed-out AskUserQuestion handling to stopContinue

Deferred until after the current release.

- `/stopContinue on`, rather than `/reviewerAgent on`, should enable handling of timed-out, unanswered Claude Code `AskUserQuestion` calls.
- Route those questions to the stopContinue supervisor and session.
- Answer only when the answer is already established by the conversation, an approved plan, project instructions, or an earlier operator decision.
- If the answer is not established, do not invent an answer or infer authorization; leave the question for the operator.
- Update the command behavior, labels, documentation, and tests while keeping reviewerAgent focused on commit review.

## Add a Pi backend to the native Codex package

Deferred until after the current release.

- Allow a Codex developer session to use Pi for commit review and `stopContinue` supervision.
- Add Pi backend selection, executable/model/reasoning configuration, separate role sessions, failure handling, documentation, and tests.
- Preserve Pi's read-only execution boundary and do not silently fall back to Codex.
- Do not claim support for `AskUserQuestion` in Codex unless the host provides an equivalent interface.
