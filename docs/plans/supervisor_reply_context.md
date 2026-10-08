# Stop supervisor: read the operator's reply against what it answers, and let a compaction handover end the turn

## Observed (2026-10-08, recipes session)

1. **An answered question was approved as a stop.** The approved stop ended "Shall I start step 1?".
   The operator replied "commit the plan first. remember to follow our protocol". The agent
   committed, then asked "Step 1 is next ... Shall I start?" again, and the supervisor approved
   ("Awaiting operator approval"). The AGENT'S OWN EARLIER REPLIES block only holds replies after
   the last approved stop, so the question that "commit the plan first" answers was not in the
   prompt. The block was empty, and the operator's message read as a bare commit request.
2. **A compaction handover was pushed to continue.** The operator wrote "we need to compact.
   Please write a NUTRIENT-EVALUATION-HANDOVER.md file ...". The agent wrote it and stopped. The
   supervisor directed "Run the Task 7a unknowns report, which is still listed as a next step".
   Across the Codex supervisor sessions since September, roughly half of the supervisor calls
   whose newest operator message asks for a handover before compacting were blocked, usually by
   pointing at next steps the handover itself records.

## Change

- **Context:** `agent_replies_block` (`reviewer_prompts.py`). When no reply falls in the window,
  it adds the last reply before the turn being judged: the message the last approval let through.
  That reply is labelled as the one the operator's next message replies to. Only the stop
  supervisor gets the block, as before.
- **Rules:** `plugins/reviewer-agent-codex/prompts/stop_supervisor.txt`. This file is shared by
  both hosts.
  - *QUESTIONS AND OPTIONS:* read each operator message as a reply to the agent message just
    before it. Suppose the agent asked whether to start authorized work that needs no AUTHORITY
    LIMITS action. A reply that only adds a step before it ("X first") approves starting that
    work once the step is done. It never authorizes an AUTHORITY LIMITS action: "run the tests
    first" does not approve a proposed push. This matches the prompt's existing rule that a
    polite request to proceed with in-scope work is not a reason to stop.
  - *PREPARING FOR COMPACTION OR A NEW SESSION* (new section): when the current request is to
    prepare for compaction, a restart, or a new session, that preparation is the whole outcome.
    Next steps the handover records are for after the compaction. Once it is done and reported,
    the reply is STOP_OK.

## Evidence before shipping

Replay the captured supervisor prompts through `codex exec` at the configured effort (medium),
fresh and resumed from a copy of the session cut before the turn. Case 1 and at least three
historical compaction blocks must flip, and the cases that were rightly approved must stay
STOP_OK.

## Tests

- `tests/test_stop_agent_replies.py`:
  - the approved message the operator answered is included, and labelled;
  - it is not added when the window already has replies.
- `tests/test_shared_stop_prompt.py`: both rules are present in the shared prompt.
