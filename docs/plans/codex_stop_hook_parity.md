# Stop-hook-only supervision: approved sprint scope

Status: documentation-first checkpoint. The removals below are approved requirements,
not a claim that the current runtime already implements them. Tests and code follow
in separate commits under the operator's required protocol.

## Decisions

The operator confirmed that a Stop occurs after a completed question answer as
well as at the end of extended work. Match the Claude Code completion flow:

1. Codex invokes the completion supervisor synchronously from its `Stop` hook.
2. Remove the advertised token-based `stop_continue` MCP tool, its dispatch,
   and all instructions telling the developer agent to request completion review.
   Remove the legacy command-hook routing script and routing branch.
3. A completed answer is reviewed using the existing shared supervisor prompt.
   `STOP_OK` allows that Stop; unfinished authorized work returns a continuation
   directive directly from the same hook. No extra review handoff is needed.
4. Request receipt, ordinary tool use, and interruption never start supervision.
   Interruption cancels an existing review and must not publish a late verdict.
   Preserve the existing Interrupt registration, handler, cancellation transport,
   and abort-bypass behavior; the operator explicitly excluded reworking these.
5. Preserve the shared Claude/Codex prompt, bounded retries, fail-open failures,
   generation checks, cancellation, and the independent commit gate.

No active-work tracker, question classifier, additional prompt, watchdog, or new
supervision trigger is part of this sprint. Existing lifecycle context collection
remains evidence for the reviewer; it does not become an additional review path.
The internal review function and its ticket/permit state remain implementation
details of the Stop handler, not model-callable completion entry points.

This supersedes the proposed work tracker and the legacy compatibility allowance
in the earlier Stop-only plan. Existing sessions must reload the updated plugin
to discard old tool definitions and instructions. Missing/outdated hook support
is not a reason to restore an agent-initiated fallback.

## Desktop presentation

Successful review adds no continuation prompt. An incomplete ruling uses Codex's
documented blocking Stop result. Codex creates the resulting continuation prompt
and controls its display/history folding; this sprint does not add another
invocation path to work around that display behavior.

## Protocol and acceptance

Documentation commit, meaningful failing behavioral tests commit, implementation
commit, then live verification. Tests must prove:

- Both completed Q&A and completed work run one reviewer at Stop and can finish.
- Unfinished work gets a direct continuation, then the completed response passes.
- Removed completion tools cannot invoke the backend, even with a valid old token.
- Non-Stop lifecycle events cannot start a review; the interruption handler is
  inert when idle and cancels matching active reviews without a late response.
- The installed MCP tool list and instructions contain no agent-driven review
  path; the shared prompt remains unchanged.

Run the Codex suite, shared-prompt checks, and real-reviewer cases. Exercise the
actual CLI and desktop backend Stop lifecycle without manually calling Stop from
the developer model. Distinguish controlled transport tests from real-model
judgment and report any live-test limitation honestly.

References: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude Code Stop](https://code.claude.com/docs/en/hooks#stop).
