# Codex stop-supervisor loop recovery

## Problem and scope

The Codex stop supervisor resumes the same reviewer after each continuation. A
reviewer that misreads the task can keep issuing reworded directives even when the
requested outcome is complete or progress genuinely needs operator input. The
Claude host now asks a fresh waiting-only reviewer after two text-only replies.
Codex needs the same independence, covering completion as well as waiting.

## Design

- After two consecutive blocking reviews with no intervening operator input or
  observed tool/subagent activity, the next Stop uses a fresh, disposable reviewer.
  This replaces that Stop's normal resumed review; it does not add a second model
  call or automatically permit stopping. Further unchanged continuations also use
  fresh reviewers until the chain ends or activity resets it.
- Persist the consecutive-block count per project and developer session alongside
  the existing nudge state. Compare operator/work generation snapshots from the
  documented hooks. Host turn-ID changes alone must not reset the chain. Do not
  parse rollout JSONL. Clear the chain on allowed/cancelled stops and session cleanup;
  real operator input and tool/subagent work invalidate old counts.
- Supply the normal stop policy, recorded operator requests, current plan, proposed
  final response, and background markers to the fresh reviewer. Explicitly require
  completion against the requested scope or a genuine operator-only dependency.
  Prior supervisor directives are not operator authorization and are not supplied
  as review history. A claim of completion, a repeated question, and lack of tool use
  are not sufficient grounds for approval. Existing authorized unfinished work
  must still yield a concrete continuation.
- Start the disposable backend with hooks/plugins disabled, read-only access, the
  configured model/effort, existing timeout, process cleanup, and cancellation. Do
  not resume, publish, or replace the persistent stop or commit reviewer sessions.
- Route the fresh result through the existing STOP_OK parsing, fail-open failure
  handling, cancellation bypass, generation validation, and exact background-marker
  reconciliation. New input/work during a fresh review must invalidate approval.
- Keep the existing default nudge cap of ten. Claude's eight-nudge limit responds to
  a Claude-specific host limit; this change does not assume that limit for Codex.
- Update the Codex README. Leave the Claude implementation and shared stop policy
  unchanged; additional reassessment instructions belong to the Codex prompt.

## Protocol and validation

1. Commit this plan.
2. Add focused Codex regressions and run them red; commit the tests separately.
3. Implement the minimum changes, run focused tests and the full Codex suite green,
   then commit implementation and documentation.
4. Run the full Codex suite again after the implementation commit.

Cover third-stop recovery for completed work and genuine operator waits; continued
supervision for unfinished work; fresh prompts and backend independence; resets
on operator/tool/subagent activity and allowed stops; isolation across sessions
and projects; host turn-ID changes; cancellation, timeout/empty/exception results,
new input/work and background markers during reassessment; bounded cap and cleanup.
All automated tests use stub reviewers and temporary state, without real model calls.
