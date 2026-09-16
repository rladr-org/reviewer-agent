# Track active work before invoking completion supervision

**Superseded; not implemented and out of scope.** The operator explicitly chose
review at every Stop, including question answers, matching Claude Code. See the
[approved sprint scope](codex_stop_hook_parity.md). Do not implement the proposal
below as part of this sprint.

## Requirement

Invoke the supervisor only at Stop and only when an assignment may remain open.
Completed informational Q&A with no open assignment or running work must pass
silently without creating a reviewer session, ticket, or consuming a review count.
Keep one shared supervisor prompt; do not add a classifier model or review during
request receipt or ordinary execution.

## State and decisions

Maintain a repository/session-scoped active-work record with a revision:

- A genuine work request opens an assignment. Recognize clear informational
  questions and conversational acknowledgements locally; they do not create an
  assignment. An action phrased as a question, such as "Can you fix this?", is
  still work. Ambiguous prompts remain eligible for review rather than silently
  bypassing protection.
- Follow-up questions never clear an existing assignment. Pending plans and
  explicit editing tools open work. Running shells or subagents independently
  require a Stop review until they settle.
- STOP_OK closes only the assignment revision that was reviewed. A new request
  or plan arriving during review must survive an older approval. Rejection keeps
  work active. Failure, budget exhaustion, and interruption are not completion.
- An approved old plan must not reactivate work during later Q&A. A subsequent
  pending-plan update can open a new assignment.
- Missing tracking data in older sessions remains conservatively reviewable.
  Invalid or unreadable tracking state follows the existing bounded fail-open
  error policy; it must not masquerade as an approved completion.

The question recognizer is intentionally conservative and is not a guarantee of
semantic understanding. It distinguishes clear informational requests from work
requests; it does not infer an assignment from recommendations in an answer or
from a question mark alone. Stop is still the timing trigger. The shared reviewer
remains responsible for judging completion when an assignment is active.

## Protocol and verification

Documentation commit, failing behavioral tests commit, implementation commit,
then live tests. Cover fresh Q&A, work phrased as questions, unfinished work
followed by clarification, rejection followed by another question, completion
followed by Q&A, pending plans, running work, session isolation, and a new request
during an older review. Existing cancellation, retry bounds, and commit-review
tests must continue to pass. Live tests must observe zero reviewer calls for a
fresh completed question and rejection/continuation for unfinished work.
