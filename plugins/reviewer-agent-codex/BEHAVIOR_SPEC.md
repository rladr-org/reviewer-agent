# Behavioral contract

This project is a Codex-native implementation, not a compatibility shim for Claude Code.

## Commit gate

- Disabled by default and enabled per canonical Git repository.
- Repository identity uses plain `--git-common-dir`, resolving relative output against the
  queried root. Linked worktrees share state; unrelated repositories have separate state.
- Enablement requires a readable, nonempty project brief with the known template placeholder
  replaced. Setup instructions live outside the model-facing template.
- Intercepts only `git commit` commands.
- Records the live staged set's `write-tree` digest (content, modes, names, and structure) before
  review, then compares it immediately before ordinary `git commit`. A changed digest requires
  a fresh review. Git reads use the real repository; no private snapshot is injected.
- Preserves `-s`/`--signoff` and `-q`/`--quiet` through the one-use ticket; the MCP call supplies
  the `-m`/`--message` text. Rejects all other commit options, pathspecs, alternate Git configuration,
  and compound shell commands before creating a ticket, with an explicit unsupported-form error.
- Fails closed when the reviewer fails, times out, returns empty output, or rejects.
- The PreToolUse entrypoint emits JSON deny on input, import, or gate exceptions. Its shell
  launcher returns blocking exit 2 if the Python command fails. Missing/untrusted host hook
  registration and host-enforced launcher timeouts remain outside that failure boundary.
  Stop liveness/plan storage failures remain best effort. Installation checks Python 3.9+
  through the same `python3` PATH command as the hooks before writing configuration; the
  operator must also ensure that command resolves in the actual Codex launch environment.
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
- Runs Git hooks and configured signing in the real repository. Hook-restaged content is honored,
  including formatting changes. Failing hooks may leave the working tree or index changed.
- Does not make the digest comparison and commit atomic or pin the reviewed parent/branch.
  This matches the Claude gate's ordinary-Git policy. Concurrent writers should use separate
  worktrees. There is no private candidate, special reference-format requirement, or Git 2.48 floor.
- Inspects commit completion and HEAD after timeout/cancellation before reporting the outcome.
  An unclean exit with changed or unreadable HEAD returns `publication_unknown` and forbids
  automatic retry: HEAD movement does not prove this invocation published. A successful Git
  exit with an unreadable commit ID still reports success, with a warning.
- Git runs directly and synchronously with a 300-second budget, without a lifecycle guard,
  detached session, or background monitoring thread. Output uses temporary files so external
  hook helpers cannot hold completion open. Cancellation/timeout terminates active Git and its
  known children; it cannot undo an already-published commit. Independently detached helpers left
  behind by an exited hook remain the hook's responsibility. The reviewer guard is unchanged.
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
- Records staged modes and blob SHAs after completed verdicts and annotates retries on the same HEAD as
  changed, unchanged, added, or removed, with bounded exact index deltas for changed paths.
  A cursor without mode evidence requires a full audit.
- Handles MCP cancellation while the audit runs, terminates the nested reviewer process tree
  including descendants that created their own process groups or sessions, and emits no late
  result or commit.
- Also handles the native Interrupt event when the host omits MCP cancellation. Before dispatch,
  the server registers each commit call under the developer session from its valid one-use ticket.
  An Interrupt cancels all pending commit calls for that session, including calls targeting another
  worktree or using a ticket issued in an earlier turn. Other developer sessions are unaffected.
  Missing, unreadable, or expired ticket ownership fails closed before review dispatch. Registration
  is removed when the call exits. Cancellation after Git starts can race publication; inspect HEAD.
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
- The bundled Stop hook calls the hook-specific MCP entry point synchronously when Codex
  emits Stop. Request receipt and ordinary work do not initiate completion review. It creates or
  resumes the dedicated reviewer and adjudicates the original final response in that same Stop
  event, with no briefing turn or model-directed routing prompt. The running status is short;
  approval returns an empty hook decision without a routine success warning. Only an incomplete
  ruling creates a continuation prompt, containing the actionable directive and the existing
  internal continuation marker. The plugin does not control desktop history expansion.
- Completion review is exposed only through the native MCP Stop handler. The token-based
  `stop_continue` tool and legacy command-hook routing script are removed, including instructions
  telling the developer agent to call them. Completed question answers receive the same Stop
  review as completed work; there is no question classifier or active-assignment gate.
- A stop permit covers one completion handoff, bound to operator-input and observed-work
  generations. It expires after five minutes and is consumed once. New genuine operator prompts,
  tool calls, and subagent starts invalidate it, including approvals still running when new input
  or work arrives. The internal review snapshots work before invoking the reviewer. Its permit
  validates the result within the same Stop invocation; it is not an agent-facing handoff.
  Old handoff permits cannot skip a new Stop review. Older permit formats are rejected.
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
  canonical cwd, session ID, and interrupted turn ID, as well as pending commit calls owned by that
  developer session (see commit cancellation above). This covers hosts that do not forward MCP
  cancellation. The registries are cleaned up after every outcome; unrelated Stop requests and
  other developer sessions are unaffected. The Interrupt handler is silent and bounded to three
  seconds, and cannot restart or prevent interruption.
- Fails open on reviewer errors, empty output, or a misplaced commit-gate `APPROVED` verdict,
  with a failure message rather than an approval or an instruction to execute that verdict.
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

## Automatic completion review and one shared prompt

The agent-initiated `review_completion` tool, its instructions, and its token binding are
removed, as are the legacy `stop_continue` tool and command-hook routing path. Completion
supervision runs entirely within the automatic Stop event. A question answer also ends a turn
and receives the same review as completed work. See the
[approved sprint scope](../../docs/plans/codex_stop_hook_parity.md).

Both applications load `prompts/stop_supervisor.txt` from the Codex plugin distribution.
Application-specific context supplies evidence, but there are no separate pre-final or
automatic-Stop instructions. The canonical text participates in Codex's stop-reviewer
fingerprint. Cancellation and bounded fail-open behavior remain unchanged.
