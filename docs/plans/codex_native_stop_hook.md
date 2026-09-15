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

## Live finding: native cancellation needs an Interrupt hook

Live Codex 0.154.0 tests on 2026-09-15 confirmed silent approval, actionable continuation, and
fail-open reviewer errors. A persistent app-server `turn/interrupt` test exposed a host behavior:
the native MCP Stop call did not receive `notifications/cancelled`, and its nested reviewer ran
to completion after interruption. CLI shutdown did terminate the process through lifecycle-pipe
cleanup, but that does not prove cancellation while the desktop host remains alive.

Add a native MCP `Interrupt` handler to cancel active native Stop reviews in the same reviewer
server. It receives `cwd`, `session_id`, `turn_id`, and `hook_event_name: "Interrupt"`, validates
the event, and cancels only active reviews with that exact canonical working directory, session,
and interrupted turn. Keep an in-memory registry of active request cancellation tokens, register
before starting review, and unregister in `finally`. Support multiple matching requests without
overwriting one another. Snapshot matching tokens under a lock and invoke cancellation outside
that registry lock. Ignore unrelated, malformed, repeated, and already-completed interruptions.

The Interrupt tool must return empty hook JSON promptly, use a maximum three-second hook timeout,
and never launch another reviewer, grant approval, cancel a commit review, or restart work. Reuse
the existing cancellation callback to grant the bounded abort bypass, terminate the nested process,
and suppress late MCP output. Regular MCP cancellation continues to work independently.

Follow the same documentation / red tests / implementation commit sequence for this correction.
Test exact event scoping, simultaneous reviews, idempotence, registry cleanup, no late response,
and preservation of the existing commit path. Then rerun a live `turn/interrupt` while keeping the
host and MCP connection alive, and verify the nested reviewer exits and no permit remains.

Reference: [Interrupt lifecycle contract](https://learn.chatgpt.com/docs/hooks#interrupt).

## Validation result — 2026-09-15

Implementation is committed in `e758d10` (native Stop) and `54178a0` (explicit Interrupt).
The preceding documentation and red-test commits are `dbb5c1e`, `2ed3574`, `4f34864`,
`87d1356`, and `0e6a337`. Each commit passed the independent commit gate.

- Full plugin suite: **338 passed** in the amygdala environment.
- Release metadata/export checks: **50 passed**; plugin manifest validation passed.
- Live approval with a real nested reviewer: one native Stop call, empty hook result, original
  final response preserved, no routing prompt and no reusable permit.
- Live unfinished task: the reviewer returned an actionable continuation; the agent completed
  the requested second step, and the next native review approved silently.
- Live reviewer launch failure: the stop failed open with a warning, preserving the final answer.
- Live persistent app-server interruption after the fix: `turn/interrupt` reached the matching
  cancellation token, the nested process exited, no stop permit remained, and the host continued
  responding. This exercised the explicit Interrupt hook, not CLI process shutdown.
- Installed package: `1.2.1-beta.6+codex.20260915121313`. The exact Stop and Interrupt definitions
  were trusted through Codex's configuration RPC and independently re-read as trusted.
- Live installed-package app-server test, without injected MCP tools or a trust bypass: one
  review ran from the installed plugin. The Stop lifecycle record reported `handlerType: mcpTool`,
  `statusMessage: Checking completion`, `status: completed`, and an empty `entries` array.

The live host was Codex 0.154.0. Standalone `codex exec` did not invoke the installed bundled
hooks in this setup, although explicit session-configured hooks worked; the package's existing
caveat about CLI as the active developer host remains. The installed app-server path did run
the bundled hooks successfully.

Desktop visual inspection remains unverified: computer-use reported that the Mac was locked.
These results establish removal of the synthetic success continuation and verify the hook data
supplied to the app; they do not establish the exact visual treatment or change the app's general
history-folding behavior. New tasks should load the updated plugin; existing tasks can retain
their prior hook/MCP definitions until the desktop app is restarted.

## Desktop follow-up — 2026-09-15

The operator reported no prominent Stop output and no history collapse in the original task.
That alone was insufficient: inspection found no new supervisor review, and a controlled
premature stop in that task still failed to trigger supervision after a full app restart.
The cause remains unresolved; restarting alone is not a verified remedy for that task.

Fresh live tests with the desktop's exact backend (`0.154.0-alpha.6.2`), installed package,
persisted trust, and desktop launch flags passed. A fresh backend session in the original
repository also passed. These comparisons did not establish operation in the actual app.

The operator then authorized creation of the actual desktop task **Verify desktop Stop
supervision**, ID `01a0a695-2b75-7d01-9393-e06dbed32e56`, in a project worktree. It passed:

1. The agent answered `WORK_PENDING` and attempted to stop.
2. Supervisor task `01a0a695-4aee-7ec1-86f7-b4793b7a8a9a` issued a continuation directive.
3. The agent automatically answered `NATIVE_STOP_RESUMED`.
4. The supervisor returned `STOP_OK`; the task completed without another routing prompt.

The plugin recorded two reviews and no leftover Stop permit. This verifies automatic
continuation and approval in a newly created desktop task. It does not resolve the failed
test in the original task, or establish the desktop app's general history-folding behavior.
