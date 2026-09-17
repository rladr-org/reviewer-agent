# Claude Code handoff: commit exactly the reviewed content

## Objective and scope

Implement the Claude Code side of the commit-integrity fixes found during the release audit.
The required rule is:

> An agent's commit must go through the review gate. The gate may publish a commit only when
> the exact content being committed has been approved by the independent reviewer.

The user proposed that the review gate execute the approved commit itself. Adopt that ownership
model for the Claude integration, using a documented host mechanism. The independent AI reviewer
supplies the verdict; deterministic plugin code performs and verifies the Git operation. Giving
the reviewer model unrestricted Git write access is not required.

This protects agent-initiated commits. Human commits from an independent terminal remain
unrestricted. Do not install a repository-wide Git gate or change human Git permissions.

There are two Claude-side defects to address:

1. Review approval is not bound to the content eventually committed.
2. Retry comparisons ignore Git file modes, so a staged executable-bit fix can be classified as
   unchanged and rejected again.

This document is an implementation handoff, not a claim that either fix exists. The current
commit-path finding was established by source inspection in Claude. The analogous Codex race
was reproduced, but no separate Claude race reproduction was performed. The user asked to inspect
the Claude path rather than spend time on another standalone reproduction. Add meaningful red
regression tests as part of implementation.

## Distinguish the hosts

| Concern | Claude Code today | Native Codex today |
| --- | --- | --- |
| Who executes the approved commit? | The developer agent's original Bash command proceeds after the hook allows it. | The plugin's `review_commit` tool invokes Git after approval. |
| Content binding | No immutable reviewed-content binding or final staged-content check in the approval path. | Reviews a captured index and HEAD, validates a private candidate, and publishes under branch/index locks. |
| Retry identity | Stores blob IDs without file modes. | Stores modes and blob IDs; cursors without modes require a full audit. |
| Commit options | Normally survive because the original command executes. | Carries supported sign-off/quiet flags in the one-use ticket; rejects unsupported forms explicitly. |
| Native MCP lifecycle-hook version requirement | Does not govern this Claude hook path, including when Codex is its reviewer backend. | Uses a validated native-host baseline of 0.154.0. |

The Codex-only option-loss and native-host-version findings are not additional Claude bugs.
However, changing Claude's commit execution path must not introduce the option-loss bug.
Implement the Claude package independently; coordinate any necessary changes to shared files.

## Evidence and source map

Read the current code before editing; function names are more durable than line numbers.

| File | Relevant responsibilities |
| --- | --- |
| [commit_hook.py](../../commit_hook.py) | `main()`: identifies commits, resolves their repository, checks enablement and staged scope, invokes a reviewer, saves results, and returns an allow/deny decision. Inspect `elif approved:`. |
| [reviewer_prompts.py](../../reviewer_prompts.py) | `allow_output()`, `staged_blob_shas()`, `record_audited_stage()`, and `staged_change_block()`. |
| [reviewer_git.py](../../reviewer_git.py) | `is_git_commit()`, `git_c_effective_dir()`, `commit_c_unresolvable()`, `commits_beyond_index()`, staged-file and patch readers. |
| [reviewer_backends.py](../../reviewer_backends.py) | Codex, Pi, and Claude reviewer execution; environment propagation, result validation, cancellation, and process cleanup. |
| [reviewer_state.py](../../reviewer_state.py) | Repository/session state, retry state, reviewer rotation, and retained outcomes. |
| [reviewer_audit.py](../../reviewer_audit.py) | Commit-attempt records and provenance. |
| [reviewer_lib.py](../../reviewer_lib.py) | Compatibility facade; avoid breaking callers when adding or relocating helpers. |
| [hooks/hooks.json](../../hooks/hooks.json) | Claude `PreToolUse` hook registration for Bash. |
| [tests/test_commit_hook.py](../../tests/test_commit_hook.py) | Existing hook-path tests, temporary repositories, and backend fixtures. |
| [tests/test_rereview.py](../../tests/test_rereview.py) | Retry snapshots, changed/unchanged annotations, and inline deltas. |
| [tests/test_process_cancellation.py](../../tests/test_process_cancellation.py) | Existing cancellation guarantees. |
| [tools/release_matrix.py](../../tools/release_matrix.py) | Real backend acceptance tests in isolated temporary repositories and state. |

### Defect 1: approval and commit are separate operations

`commit_hook.main()` currently calculates approval from the reviewer result, records the review,
and eventually prints `allow_output(msg)`. That response permits the original Bash command to
execute. Its message instructs the agent not to stage more files, but the instruction does not
bind Git to the reviewed content or constrain another process modifying the index.

The relevant sequence is:

```text
Agent requests a commit
    -> PreToolUse reviews staged content A
    -> reviewer approves A
    -> hook allows the original command
    -> Git commits whatever is staged when that command runs
```

Another task or a human can stage content B during the review or between approval and execution.
The approved operation can therefore commit content that the reviewer never approved. The
requesting agent does not need to bypass the gate or run concurrently with its own review.

Also inspect review bookkeeping: `record_audited_stage()` samples the index after review.
Recording whatever happens to be staged then as already audited is unsafe if it differs from
the content supplied to the reviewer. Retry state and history must describe the actual snapshot.

### Defect 2: mode changes disappear from retry comparisons

`staged_blob_shas()` parses `git ls-files --stage`, but retains the object ID and discards the
mode. `staged_change_block()` compares those IDs to label paths changed or unchanged.

For example, a newly staged script is rejected because it is not executable. The agent stages
the executable-bit fix without editing its contents. Its blob ID is unchanged, so the next
retry can say `NOTHING MOVED` and instruct the reviewer to repeat the rejection.

Record at least object identity and mode for every relevant path, with an explicit deletion
representation. Handle old records conservatively: a record lacking mode information must cause
a fresh review rather than a false unchanged classification. A content-only blob delta cannot
display a mode-only change; the retry evidence must show that change explicitly.

## Required behavioral contract

### Exact content and target

- Capture the intended repository/worktree, target branch or detached HEAD, parent commit, and
  staged tree for the attempt. Include modes, deletions, binary content, symlinks, and gitlinks
  in the content identity; a hash of a human-readable diff is not the full contract.
- The reviewer must inspect that captured content. Copying an index while continuing to point
  the reviewer at the mutable live index is insufficient. Verify the actual data path for all
  three reviewer backends, including Git reads made by their tools.
- An approval applies only to that attempt, content, target, and parent. Bind any token or saved
  approval accordingly; it must not become a general permission to commit later staging.
- Publish only the approved tree with the intended parent(s). Refuse publication if the target
  branch/HEAD identity or expected parent has changed. Two branches pointing to the same commit
  must not make a branch switch invisible to the check.
- Default to rejecting and requesting a fresh review when the live staged set changes during
  the operation. Preserve the person's newer staged and unstaged changes. Do not reset, stash,
  overwrite, or silently absorb them to make the commit succeed.
- A comparison followed by an ordinary commit from the live index still has a race. A final
  check is useful only when the publication mechanism closes the remaining gap.

### Hook integration and Git behavior

- Route enabled agent commits through the controlled review-and-commit operation. Once it has
  committed, the original unrestricted command must not execute again.
- Use an actual host control mechanism, not a prompt saying “do not commit.” Verify how the
  host consumes the result, including rejection, exceptions, cancellation, and multiple hooks.
- Preserve the agent's intended repository and supported commit arguments. Parse into explicit
  arguments; do not execute a captured shell string with `eval` or interpolate user text into
  shell code. Preserve message newlines and literal shell metacharacters.
- Preserve existing restrictions on commands that reach beyond the staged set. An unresolvable
  target or unsupported command form must fail clearly before side effects. Compound commands
  need explicit handling; do not silently discard unrelated commands when rewriting tool input.
- Inventory current handling of `--amend`, sign-off, signing, author/date overrides, and message
  sources. Preserve supported semantics. If a form cannot be supported safely, reject it with
  a precise explanation rather than execute a different commit. Document compatibility changes.
- Amend, merge, cherry-pick, and rebase states need deliberate treatment: their parent and message
  semantics cannot be replaced silently with those of an ordinary new commit.
- Preserve applicable Git hooks and signing behavior. A hook that changes the candidate content
  must not cause unreviewed content to be published. A hook rejection must leave the target ref
  unchanged. Do not switch to plumbing commands that bypass checks without accounting for that.
- Distinguish review approval, candidate creation, and successful publication. Report success
  only after publication; include the resulting commit ID. If reporting or bookkeeping fails
  after publication, do not misreport “nothing committed” and cause an automatic duplicate.

### Cancellation, isolation, and existing features

- Preserve Escape/signal cancellation and descendant-process cleanup. Cancellation before
  publication must prevent a later background commit. Define and test the point at which the
  commit has already completed, so interruption cannot misrepresent its outcome.
- Clean up only this attempt's temporary files, locks, and approvals. Do not delete another
  process's lock. Handle failure or interruption at each state-changing step.
- Keep independent repositories, worktrees, developer sessions, and concurrent attempts isolated.
  Preserve the disabled gate's existing behavior and repository risk-acceptance settings.
- Retain strict verdict parsing, failure/timeout rejection, reviewer rotation, prior-finding
  continuity, ignore guidance, and audit history. Record the snapshot actually reviewed.
- Preserve Stop supervision, its shared prompt, interruption behavior, and the existing
  AskUserQuestion handling. This work does not introduce new Stop triggers or prompt variants.

## Design work to complete before implementation

The required outcomes above are settled; the exact Claude integration is not yet validated.
Document the selected mechanism and show why the original command cannot run afterward.

One candidate architecture is:

1. Resolve and validate the requested operation before starting an expensive review.
2. Capture a private staged snapshot and its intended parent/target.
3. Have the selected backend review that snapshot.
4. Build an approved candidate commit without moving the real target ref yet.
5. Check the candidate's tree and parents, including the effects of Git hooks.
6. Publish through a Git operation that verifies the expected target state atomically, while
   protecting the live index from a conflicting update during finalization.
7. Return the commit ID and consume the attempt; release owned resources on every exit path.

This is a design candidate, not a tested implementation recipe. Validate how private Git state,
configuration, worktree discovery, object storage, hooks, reflogs, and cleanup behave. Do not assume
that a temporary `GIT_INDEX_FILE` alone protects HEAD or that changing process-global environment
variables is safe when requests run concurrently.

Claude's current documentation provides `PreToolUse` allow/deny decisions and `updatedInput` for
replacing tool arguments. If using `updatedInput`, preserve required unchanged input fields and
test the rewritten command against the installed host's permission handling. That API does not
itself provide an immutable reviewed snapshot or atomic commit. A dedicated tool/helper is another
possible integration; verify its routing and completion behavior before choosing it.
[Claude Code hook reference](https://code.claude.com/docs/en/hooks#pretooluse-decision-control)

Git documents expected-old-value updates and ref transactions. Those primitives can support
publication checks, but index protection, symbolic HEAD changes, and supported Git versions still
need an explicit design and tests. Check the versioned documentation for the Git floor actually
supported; do not silently require a newer transaction feature.
[Git update-ref](https://git-scm.com/docs/git-update-ref)

Use the documented commit and hook contracts when assessing normal Git behavior.
[Git commit](https://git-scm.com/docs/git-commit),
[Git hooks](https://git-scm.com/docs/githooks),
[Git environment variables](https://git-scm.com/docs/git#_environment_variables)

## Test plan

Use real temporary Git repositories and deterministic synchronization points. Stub only the model
verdict where appropriate; do not stub the Git operation whose integrity the test is meant to prove.
Use events/barriers or a controlled callback at the vulnerable boundary instead of timing sleeps.

| Case | Required assertion |
| --- | --- |
| Ordinary approval | Exactly one commit lands, with the approved tree and expected parent. |
| Original Bash command | After the controlled path commits, the original commit command cannot execute a second time. Test the host interaction, not just returned JSON. |
| Rejection, empty/malformed verdict, backend failure, timeout | No commit or target-ref movement; staged and unstaged work survives. |
| Staging during review | Reviewer evidence remains the captured snapshot; later staging is neither marked reviewed nor committed. Default outcome is a fresh-review error. |
| Staging after the final comparison | No unreviewed content can land. Place the competing stage operation at the exact old vulnerability boundary. |
| A changes to B and back to A | The reviewer cannot read B and accidentally approve A because endpoint comparisons match. |
| Branch advances during review or candidate creation | Publication refuses; the other commit remains intact. |
| Branch switches while both branches initially share a commit | Approval cannot follow the switch to the wrong branch. |
| Cancellation while reviewing or immediately before publication | No later commit; child processes terminate and attempt resources are released. |
| Interruption after publication | The result identifies the committed state; recovery does not duplicate the commit. |
| Git hook rejects | No publication; no damage to live staging. |
| Git hook stages additional content | Candidate-tree mismatch blocks publication or triggers a new review; approval does not silently cover the extra content. |
| Partial staging | Only approved index content is committed; unstaged edits are preserved. |
| Mode-only retry fix | The executable-bit change is reported as changed and reaches the reviewer as evidence. |
| Old retry records without modes | Fresh review, not an incorrect unchanged annotation. |
| Deletes, renames, binary files, symlinks, gitlinks | Committed tree identity matches the reviewed snapshot. |
| Unborn branch, detached HEAD, linked worktree | Correct target and parent behavior, or an explicit documented refusal before side effects. |
| Options, unusual paths, multiline messages | Supported semantics survive; unsupported forms fail clearly; no shell interpolation. |
| Two concurrent attempts | An approval cannot be reused across attempts; one attempt cannot publish another's content or remove its resources. |
| Crash/lock cleanup | No late commit, damaged index, or permanent lock left by a recoverable failure. |
| Gate disabled; separate human terminal | Existing unprotected behavior remains; no machine-wide Git enforcement is installed. |

Run the meaningful baseline, implementation, and retry tests across Claude-host/Codex-reviewer,
Claude-host/Pi-reviewer, and Claude-host/Claude-reviewer. The mechanisms for supplying staged
content differ; testing only one backend is not sufficient.

## Required development protocol

Follow the user's sequence for the Claude changes:

1. Update the behavioral documentation and chosen design; commit that documentation.
2. Add regression tests that fail for the actual missing behavior; demonstrate red and commit
   the tests as an explicit red TDD checkpoint.
3. Implement the fix, make the tests pass, and commit the implementation.
4. Test live in disposable repositories/worktrees with tiny changes.

Use the repository's required commit-review gate for those commits. Preserve unrelated changes
and coordinate shared-file edits with the Codex-side task. Do not assume `HANDOVER.md` identifies
the current task: at audit time it still described the abandoned display-label investigation.

Runtime dependencies remain standard-library-only, with Python 3.9 compatibility. Follow
applicable repository instructions and module-layering/facade conventions. In the maintainer's
environment, run Python tooling through the `amygdala` conda environment.

From the repository root:

```bash
conda run --no-capture-output -n amygdala python -m pytest -p no:cacheprovider
conda run --no-capture-output -n amygdala python tools/check_split_layering.py
conda run --no-capture-output -n amygdala python tools/release_versions.py
conda run --no-capture-output -n amygdala python tools/scan_release.py
```

Regenerate the facade contract with `tools/gen_facade_contract.py` if the public facade changes.
Run the independent Codex suite from `plugins/reviewer-agent-codex` to detect shared-file regressions:

```bash
conda run --no-capture-output -n amygdala python -m pytest -p no:cacheprovider
```

After deterministic tests pass, validate the relevant live backend combinations:

```bash
conda run --no-capture-output -n amygdala python tools/release_matrix.py \
  --scenario claude-codex --scenario claude-pi --scenario claude-claude
```

Update that harness if its current approval assertions assume the original command performs
the commit. Assert the actual resulting Git tree, parent, commit count, and preserved user changes.
Keep real-model cases tiny; the concurrency tests should not require long model calls.

Exercise Escape and the successful host handoff in a real Claude session. A subprocess cancellation
test or a mocked hook response alone does not prove the interactive host behavior.

## Audit baseline and completion criteria

The pre-fix audit passed the root suite (1,193 tests; three skipped), the Codex suite (379 tests),
Pi unit/live Git checks (55 and eight), all five release-matrix scenarios, and an installed Codex
Stop-hook completion check. These results do not establish correctness of the missing cases above.
The export scenario completed locally; it did not publish or upload a release.

Finish with a concise account of:

- The chosen Claude hook/tool mechanism and how it prevents the original command running twice.
- How reviewer input and the published tree/parents are bound together, including concurrent
  staging and target changes.
- Supported commit options and any explicit compatibility limitations.
- Regression and live-test results, separated from checks that still need a human session.
- Commits made, unresolved findings, and any remaining processes or execution sessions.

Passing the old suite alone is not completion. Demonstrate the new regression tests passing and
verify the controlled commit flow in the actual Claude host. Release publication waits until
the open issues are fixed and the final release audit passes; this handoff does not authorize
publishing, pushing, or changing the operator's installed configuration.
