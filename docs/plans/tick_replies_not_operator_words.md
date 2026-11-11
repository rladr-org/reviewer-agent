# The stop supervisor does not see watchdog-tick acknowledgements as conversation

## Problem

On 2026-11-09 the stop supervisor twice directed an agent to redo three pages of a site,
saying "The operator's 'ok' replies approved proceeding after the [first page] preview." The
operator had approved no such thing, and the agent declined both directives. The same pattern
appears in the log on 2026-11-02.

The prompt the supervisor received shows the cause. The watchdog tick prompts were absent from
OPERATOR MESSAGES, as intended. The section AGENT'S OWN EARLIER REPLIES, however, lists every
text reply since the last approved stop, and it held this sequence:

```
[07:38:42] ... Once you've looked at the first page, say "go" and I'll redo the other
           three.
[07:42:09] ok
[07:49:10] ok
```

The two `ok` lines are the agent's answers to watchdog ticks. Placed directly after a request
for "go", they read like the operator's answer, and the supervisor took them for one.

`agent_replies_block` (`reviewer_prompts.py`) collects every assistant text before the turn
being judged. It does not look at which prompt started the turn a reply belongs to.

Since 1.4.7 the prompt hook answers an idle tick itself, so most of these replies no longer
exist. A tick still reaches the model to resume work, to delete the cron, or when the hook
cannot decide, and its reply still lands in the section.

The Codex plugin has no watchdog tick and is unaffected.

## Change

In `agent_replies_block`, track for each reply whether its turn was started by a watchdog tick
(a main-session user event whose text starts with `stopContinueWatchdog tick`):

1. A tick-turn reply that is a bare acknowledgement (`ok`, any case, optional final `.` or
   `!`) is left out. It tells the supervisor only that a tick fired. It also no longer counts
   as "the message your last approval let through".
2. Any other tick-turn reply is kept and prefixed with a label saying it answers an automatic
   watchdog check that the operator did not write.
3. The section header gains a sentence: operator approval appears only under OPERATOR
   MESSAGES, and a short "ok" or "yes" listed among the agent's replies is the agent's.

Replies in turns started by the operator, by hook feedback or by a task notification are
unchanged.

## Tests

Written first; each fails before the change.

- A transcript shaped like the incident (the agent asks for "go"; two ticks each answered
  `ok`; a later operator turn): the block holds the request for "go" and holds no `ok` line.
- `Ok.` and `OK!` in a tick turn are left out as well; `ok` in a turn the operator started is
  kept.
- A tick turn with real text ("I removed the watchdog cron") is kept and carries the label.
- When the only replies after the last approval are tick acknowledgements, the block falls
  back to the approved message the operator answered, as it does today for an empty window.
- The header states that approval appears only under OPERATOR MESSAGES.
