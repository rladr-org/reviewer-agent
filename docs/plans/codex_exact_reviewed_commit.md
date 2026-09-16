# Codex commit integrity

Status: proposed implementation contract for the documentation-first TDD checkpoint.
The runtime and installation guidance have not yet been changed; update them with the implementation.

## Required result

An agent's commit must contain exactly the approved staged snapshot. Keep human terminal commits
unrestricted, preserve Stop/Interrupt behavior, and leave the Claude implementation to its own task.

## Implementation

1. Resolve the target and reject unsupported command forms before creating a ticket. Bind supported
   sign-off/quiet options into the existing one-use ticket; keep the MCP interface stable.
2. Capture private Git state under the repository metadata, with the captured HEAD, index, and
   access to the repository's object database and configuration. Review Git commands must receive
   this context without mutating process-global environment or another concurrent request.
3. Record mode-aware retry identity from that snapshot. Old content-only cursors force fresh review.
4. Run the approved commit in private Git state, retaining Git hooks and signing. Reject a
   candidate whose tree or parents differ from the review. No public reference moves yet.
5. Acquire the live index lock only for finalization. Start and prepare a Git reference transaction
   that verifies the original target and expected parent. Recheck HEAD identity and live index
   under the locks, then commit the transaction. Abort on any mismatch or failure.
6. Cancellation before publication aborts the transaction and cannot produce a late commit.
   Report the actual candidate ID after publication, even if another operation subsequently moves
   HEAD. Clean up only attempt-owned files, locks, and processes on every exit path.

Git 2.48 supplies the required symbolic-reference transaction operations. Check the installed
capability and fail closed rather than approximating it with a check followed by an unlocked write.
See [Git update-ref](https://git-scm.com/docs/git-update-ref/2.48.0).

The native Codex baseline is 0.154.0, which was exercised with Stop and Interrupt support. Keep the
independent Claude reviewer-backend floor unchanged.

## Acceptance

Use real temporary Git repositories with deterministic competing actions: staging during review,
staging after the last review check, HEAD advancement, same-object branch switches, and hook-added
content. Verify rejection without lost staging, exact successful trees and parents, and no duplicate
or late commits. Cover ordinary, unborn, detached, and linked-worktree targets; mode-only retry fixes;
legacy cursor fallback; preserved sign-off; explicit unsupported-option errors; and cancellation.

Follow documentation commit, meaningful red-test commit, implementation commit, then live checks.
Run the Codex suite and relevant structural checks. Repeat the release audit after the fixes; a
successful baseline audit is not evidence for these previously uncovered cases.
