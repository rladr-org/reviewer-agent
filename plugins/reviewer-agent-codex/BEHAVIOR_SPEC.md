# Behavioral contract

This project is a Codex-native implementation, not a compatibility shim for Claude Code.

## Commit gate

- Disabled by default and enabled per canonical Git repository.
- Intercepts only `git commit` commands.
- Audits and commits exactly the staged index; intercepted `-a`, `--all`, and pathspec forms are
  never executed or carried into the MCP commit.
- Fails closed when the reviewer fails, times out, returns empty output, or rejects.
- On timeout, compare-safely retires only the reviewer thread that was actually invoked and clears
  only its matching staged-index cursor. A concurrent replacement survives, and the next commit
  retry starts a fresh reviewer with the full brief rather than resuming the timed-out thread.
- Commit reviews default to a 3,600-second fail-closed limit configurable from 60 through 7,200
  seconds by environment or persistent JSON. The MCP tool ceiling exceeds the largest supported
  backend limit.
- A plain `git commit` is denied without minting a token and is rewritten as an explicit absolute
  `git -C <repository> commit ...` retry. Codex hook events omit a shell tool's separate `workdir`,
  so this prevents a token from silently binding to the session's outer repository.
- The explicit-repository shell retry returns immediately with a one-use,
  developer-session-bound token and its exact `cwd`. Codex calls `review_commit` once as a direct
  MCP tool; the developer model is suspended until the result.
- The MCP tool commits the staged index only when the reviewer begins its verdict with `APPROVED`.
- Either verdict may end with one `SUGGESTIONS:` block. Suggestions are explicitly non-blocking;
  rejected results render them separately from required fixes, and approved suggestions never
  invalidate approval merely because they discuss something that was rejected. A missing or
  repeated section marker is never used to demote rejection text from blocking status.
- Rejects approval if the staged bytes change during the review.
- Reuses one isolated commit reviewer per developer session, never across developer sessions.
- Exposes exact namespaced `reviewer-session status|rotate` controls through `UserPromptSubmit`.
  Manual rotation affects only that developer session's commit reviewer and cursor, never its
  separate StopContinue reviewer, and runs without a model request or shell write.
- Scopes the stored reviewer thread to a fingerprint of its static role prompt. Unchanged prompts
  reuse the thread; a prompt upgrade starts a fresh reviewer even if Codex resumes the same
  developer session ID, without rotating the separately fingerprinted StopContinue thread.
- The first audit includes the standing/project brief and returns the ruling in that thread's first
  model turn; no separate briefing turn is run or discarded.
- The standing and per-commit prompts preserve the Claude implementation's demanding audit
  contract: fitness for actual use, source reasoning, whole-staged-set review, explicit fresh/retry
  semantics, staged-claim verification, targeted reading, immediate artifact-free checks, and all
  required fixes in one verdict. More-specific project rules win.
- A fresh reviewer never receives an older saved verdict labelled as its own; prior-verdict
  pointers appear only when the current prompt-revision-scoped reviewer already exists.
- Records staged blob SHAs after completed verdicts and annotates retries on the same HEAD as
  changed, unchanged, added, or removed, with bounded exact index deltas for changed paths.
- Handles MCP cancellation while the audit runs, terminates the nested reviewer process tree
  including descendants that created their own process groups or sessions, and emits no late
  result or commit.
- Binds every nested reviewer process group to its MCP server with a lifecycle pipe. Graceful
  cancellation and abrupt host/server death both use kernel descendant enumeration before killing
  parents, preventing nested test commands from escaping cleanup as orphan audits.
- Saves every completed verdict in both a stable per-project/session latest file and a unique
  per-project history entry. The history crosses developer sessions, retains the newest 100
  verdicts by default, is operator-configurable, and prunes oldest first. Operational failures and
  partial output are not recorded as verdicts.
- Places the latest full-review path before inline verdict text so host display truncation cannot
  hide the recovery path.

## Stop supervisor

- Disabled by default and enabled independently per repository.
- Uses the documented Codex `last_assistant_message`; it never parses rollout JSONL.
- Uses a dedicated minimal prompt and never receives `reviewerAgent.md`.
- Judges operator prompts, the captured current `update_plan` task list, the proposed final
  message, and only plan/task documents explicitly named in that context.
- Does not inspect source code, diffs, Git state, tests, or unrelated repository files.
- The bundled Stop hook calls the hook-specific MCP entry point synchronously. It creates or
  resumes the dedicated reviewer and adjudicates the original final response in that same Stop
  event, with no briefing turn or model-directed routing prompt. The running status is short;
  approval returns an empty hook decision without a routine success warning. Only an incomplete
  ruling creates a continuation prompt, containing the actionable directive and the existing
  internal continuation marker. The plugin does not control desktop history expansion.
- Legacy command-hook and token-based `stop_continue` entry points remain available for existing
  sessions. New bundled configuration registers only the native MCP Stop handler.
- A stop permit covers one completion handoff, bound to operator-input and observed-work
  generations. It expires after five minutes and is consumed once. New genuine operator prompts,
  tool calls, and subagent starts invalidate it, including approvals still running when new input
  or work arrives. The review tool snapshots work after its own PreToolUse event. Rewording the
  final acknowledgment or receiving a fresh host continuation turn ID does not require another
  review. Older permit formats are rejected.
- The native Stop hook consumes its permit in the same invocation, including the generation
  checks. It cannot leave an approval behind for a subsequent stop or approve new work/input
  that arrived while the review was running. MCP hook responses use the hook JSON contract,
  rather than human-readable review-tool text.
- Escape cancels the MCP request and nested reviewer without granting a stop permit. Because Codex
  may reassign the turn ID while ending an interrupted turn, a fresh one-use repository/session
  abort bypass allows the host's repeated Stop without restarting stopContinue. It expires after
  30 seconds, and `UserPromptSubmit` clears it before every genuine operator prompt, so a later task
  remains supervised.
- A native MCP Interrupt handler explicitly cancels active native Stop requests matching the
  canonical cwd, session ID, and interrupted turn ID. This covers hosts that do not forward MCP
  cancellation when interrupting lifecycle hooks. An in-memory registry is cleaned up after every
  outcome; unrelated turns, repositories, and commit reviews are unaffected. The Interrupt handler
  is silent and bounded to three seconds, and cannot restart or prevent interruption.
- Fails open on reviewer errors or empty output, with a failure message rather than an approval.
- Treats quoted transcripts supplied for diagnosis as evidence, not as assignments to execute.
- Continues Codex when the reviewer judges the user task incomplete.
- Stops after a bounded number of hook-generated continuations. The budget survives fresh host
  turn IDs within one continuation chain and resets only on a genuine operator prompt.

## Liveness

- Tracks yielded shell work through `PreToolUse`/`PostToolUse` and active subagents through
  `SubagentStart`/`SubagentStop`.
- Captures the latest structured plan/task list from the documented `update_plan` `PreToolUse`
  payload.
- When shell and/or subagent markers remain, the first stop routes their exact `(kind, id)` set
  directly to the minimal, cancellable stopContinue ruling rather than injecting a preliminary
  polling instruction and marker dump into the developer conversation.
- Approval requires the agent's proposed final to state explicitly that it polled or inspected
  named shell work and that no process or unified-exec session remains, and/or that it reconciled
  the collaboration registry and no live subagent remains, according to the marker kinds present.
  Without that report, the minimal supervisor directs the agent to perform the missing check after
  the blocking call returns.
- A successful or failed-open persistent-marker ruling clears only the exact `(kind, id)` markers
  in its one-use ticket on a best-effort basis; liveness state I/O errors cannot defeat fail-open.
  A newly appearing shell or subagent marker during the ruling prevents the stop and requires a
  fresh reconciliation pass. An incomplete ruling retains every ticketed marker.
- Markers expire and are cleared at `SessionEnd`; no transcript heuristics are used.

## Isolation

- Local installation atomically merges `mcp__reviewer_agent` into Codex's direct-only code-mode
  namespaces through `config/read` and `config/value/write`; it neither enables code mode nor
  replaces unrelated namespaces. This prevents long reviews from yielding control back to the
  developer model for polling.
- Local updates park prior immutable cache directories by atomic rename and move non-colliding
  directories back after installation, preserving the inodes used by live hook and MCP processes.
  On a successful same-version collision, the newly installed directory remains canonical for new
  threads and the displaced live inode remains in its dot-prefixed sibling parking directory.
- Nested reviewer processes start from a stable explicit working directory, so an invalid inherited
  server CWD cannot prevent `codex exec` from starting.
- Commit review and stop supervision use separate reviewer threads.
- The stop thread has its own minimal instructions and never inherits the commit reviewer's project
  brief.
- Every nested reviewer invocation sets `features.hooks=false`, `features.plugins=false`, and
  `approval_policy="never"`.
- Host-side output capture explicitly uses a stable existing temp root, and every nested invocation
  replaces inherited turn-scoped temp variables with that root. Resumed turns send prompts over
  stdin and capture only `--output-last-message`.
- Operator prompts are captured through `UserPromptSubmit`, not rollout parsing.
- Session startup is side-effect free and never launches a reviewer model; reviewer sessions are
  initialized lazily by an actual commit or stop review.
- Exact namespaced feature toggles are applied by `UserPromptSubmit`, without a sandboxed shell
  write, approval prompt, or model request; the hook returns `continue: false` after updating state.
- Enabling stopContinue defers supervision only for the enabling `turn_id`; a later turn is never
  skipped, even when the enabling turn was interrupted.
- A commit reviewer is retired after three completed non-retry audits by default. Rejection marks
  every later attempt as a retry until an approval, and retries do not advance the counter.
- `reviewerSessionAuditLimit` configures the limit; the reviewer-session command overrides it,
  `clear` restores it, and zero/`off` disables automatic rotation.

## Reviewer model

- Fresh and resumed nested reviews receive an explicit model and reasoning-effort override.
- Fresh and resumed nested reviews request `service_tier="priority"`.
- Defaults are `gpt-5.6-luna` and `xhigh`.
- Environment overrides outrank command-written state, which outranks `config.json`, which outranks
  built-in defaults.
- Settings are resolved at each child invocation, so changes apply without replacing the root
  Codex thread.

## Deliberate non-equivalence

Codex does not expose Claude Code's `AskUserQuestion` AFK timeout contract. This plugin does not
pretend to auto-answer arbitrary unanswered questions. Codex's native automatic approval reviewer
can handle eligible permission prompts; stop supervision handles routine decisions that should not
have been escalated to the operator.

## Planned pre-final review (not implemented yet)

The next revision will add `review_completion` before publishing a final response. Trusted
PreToolUse context will bind a one-use ticket to the host repository/session/turn and draft.
Required fixes will return as ordinary tool results, with retries in the same turn. Approval
will cover that draft and turn plus existing generation/expiry checks. The native Stop handler
will remain a fallback and can still create visible feedback when it rejects. Both paths will
share the bounded review budget and fail-open/cancellation policy. See the
[implementation and validation plan](../../docs/plans/codex_prefinal_completion_review.md).
