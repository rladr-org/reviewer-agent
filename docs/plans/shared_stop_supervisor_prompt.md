# One stop-supervisor prompt

## Requirement

Codex and Claude Code must use the same stop-supervisor instructions. Remove
the separate pre-final and automatic-Stop instruction variants. Application
adapters supply operator context, work evidence, and the final response being
reviewed; they do not maintain their own completion policy.

## Design

Keep the canonical text in the self-contained Codex plugin under
`prompts/stop_supervisor.txt`. The Claude Code adapter reads that same file
relative to its installation root. The full Claude plugin distribution already
includes the Codex subtree; the Codex distribution includes the resource within
its own root. Legacy installations must receive this resource with the updated
adapter. Neither adapter falls back to a separately maintained embedded prompt.

Retain the shared completion rules and incorporate the general warning that an
intentional early-stop test does not complete the underlying assigned work.
Do not retain host-specific tool-call instructions in the supervisor prompt.
Fingerprint the shared text so changed instructions retire old Codex supervisor
sessions. Keep the commit-review prompt and its session fingerprint unchanged.

## Verification

Commit this specification, then commit meaningful failing tests before changing
runtime code. Verify that both adapters load the same canonical instructions,
that invocation variants cannot append different review instructions, that a
changed canonical prompt retires a supervisor session, and that the prompt ships
and loads in isolated copies of each distribution. Verify incomplete, complete,
cancelled, and quoted-work cases with the actual reviewer after unit tests pass.

Invocation timing is a separate requirement; the pending distinction about
question-only turn endings does not block unifying the prompt.

## Verification record

- Red checkpoint: four expected failures (two missing-resource checks and two
  checks detecting route-specific instructions); three other route checks passed.
- After implementation: 40 Claude/shared-resource checks and 46 Codex
  prompt/completion checks passed.
- Six real reviewer cases passed: two incomplete/complete pairs, explicit
  cancellation, and quoted historical work. The incomplete responses received
  continuation directives; completed and cancelled work and quoted evidence
  received `STOP_OK`.
- The intermediate compatibility check of the old pre-final path failed twice:
  it requested another review or publication of an already reviewed answer.
  General shared clarification alone did not resolve that path. The early-review
  tool and its binding were then removed as required by the timing specification;
  the failed checks are not counted as passing evidence.
- Both installed plugin distributions contain the same canonical prompt bytes
  (SHA-256 `26bd93397125bb6fd9035696c11cc7fdf67ccb3f44984e9b01284860afe943b8`).
