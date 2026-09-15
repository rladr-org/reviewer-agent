# Native Codex Stop review

## Problem and intended behavior

When an enabled Stop reaches a fresh review, the current command returns `decision: "block"`
with an instruction to call `stop_continue`, including when the supervisor will approve completion.
Disabled, empty-message, existing-permit, abort-bypass, and nudge-cap paths do not require that
routing prompt. Codex turns the blocking reason
into a new user prompt. In desktop this creates a large Hook feedback bubble and another turn
after the useful final response. That extra turn may contribute to earlier work being collapsed.
The plugin cannot control the desktop history dropdown or guarantee all history remains expanded.

Run the completion review directly through a synchronous `mcp_tool` Stop hook. Show the short
running status `Checking completion`. An approved review returns an empty hook decision, with no
continuation prompt or routine success warning; the original final response remains the answer.
An incomplete review returns a blocking decision containing only the supervisor's actionable
directive, preceded by the existing continuation marker so it is not captured as operator input.
Operational failures remain fail-open and visibly distinguish failure from approval.

References checked on 2026-09-15:

- [Stop continuation semantics](https://learn.chatgpt.com/docs/hooks#stop)
- [Synchronous MCP lifecycle hooks](https://learn.chatgpt.com/docs/hooks#mcp-tool-hooks)
- [Output fields](https://learn.chatgpt.com/docs/hooks#common-output-fields): `systemMessage` is
  a UI warning; `suppressOutput` is not implemented.

## Implementation contract

- Add a hook-specific MCP entry point receiving the documented Stop event fields: repository
  cwd, session ID, turn ID, `hook_event_name`, stop-hook-active flag, and proposed final assistant message. Validate
  its event identity and input rather than accepting a model-supplied approval.
- Point only the bundled Stop handler at this entry point on the existing `reviewer-agent` MCP
  connection. Preserve the command hook and token-based tool as a legacy path for existing
  sessions. There must be only one configured Stop handler, so reviews are not duplicated.
- Reuse the existing minimal supervisor, session isolation, ticket claiming, background-marker
  reconciliation, bounded continuation counter, reviewer timeout, and cancellation machinery.
  The reviewer remains synchronous; no background polling by the developer model is introduced.
- Create and consume review tickets internally. On success, consume the resulting stop permit
  in the same hook invocation, validating operator and work generations. Never leave approval
  available to authorize a later Stop, and never accept a ruling invalidated by new input/work.
- A genuine incomplete ruling resumes work with its actual directive. Approval and acknowledged
  cancellation are silent. Errors/timeouts retain the existing fail-open warning behavior.
- Cancellation must reach the nested reviewer, produce no late MCP result or approval, and retain
  the existing short-lived abort bypass for a repeated Stop during interruption.
- MCP output must implement the hook JSON contract in both structured content and JSON text;
  the existing human-readable rendering for model-called review tools is not hook JSON.
- Account for native hooks not emitting PreToolUse/PostToolUse for their own MCP invocation.
  Existing external work and operator-generation invalidation must still apply.

## Required delivery protocol

1. Commit this specification and documentation updates.
2. Add meaningful failing tests and run them to establish red; commit those tests separately.
3. Implement the specification, run relevant tests and the complete plugin suite, and commit
   the passing implementation separately. Every commit goes through reviewer-agent.
4. Test through a live Codex host using the native MCP hook, including approval, incomplete
   work, cancellation, and fail-open errors. Verify desktop presentation where the installed
   host makes it observable; distinguish automated protocol checks from actual UI observation.

Tests must exercise real supervisor/ticket/permit behavior with controlled reviewer outcomes,
not merely assert that production helpers call one another. Cover successful silent completion,
actionable continuation, stale approvals, background work, bounded retries, cancellation, and
the MCP response contract. Confirm the registered Stop hook resolves to the advertised MCP tool.

## Compatibility and rollout

Native MCP hooks require support in the active host and an already-connected reviewer server.
Missing servers or tools fail open at the host level. Keep the legacy entry points for running
sessions, but new sessions use the native registration. Verify plugin server-name resolution,
hook trust, cancellation propagation, and timeout behavior live before describing this as tested.
Use the local plugin cachebuster/reinstall workflow for pickup in new sessions. Do not patch the
desktop application or edit its conversation history.
