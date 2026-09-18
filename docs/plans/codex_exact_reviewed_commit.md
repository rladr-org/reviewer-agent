# Codex: ordinary Git commits after review

The operator chose the ordinary-Git approach on 2026-09-18 to preserve existing repository
workflows and align the Codex package with the Claude gate. This supersedes the earlier private
index, candidate commit, and locked reference-transaction design.

## Commit flow

1. Accept one plain commit command, preserving sign-off and quiet options. Refuse unsupported
   arguments and compound commands before review. The existing MCP tool receives the message.
2. Record the staged set's `git write-tree` hash before review. It covers content, modes, names,
   and structure. Reviewer Git commands read the real repository.
3. After approval, compare the staged hash again immediately before committing. A mismatch or
   unreadable staged tree blocks the commit and requires a fresh review.
4. Run ordinary `git commit` synchronously with the repository's own hooks and signing. Hooks may
   reformat or restage files; their changes are honored. There is no private Git environment,
   candidate commit, custom reference transaction, or additional Git-version floor.
5. Report the actual outcome. Timeout or cancellation can occur after Git publishes, so inspect
   HEAD before reporting failure. After an unclean exit, changed or unreadable HEAD means
   publication is uncertain: a hook or another writer could have moved it. Instruct the caller
   not to retry blindly, including when interruption may have followed successful publication.

Codex continues to block the original shell commit and perform review/commit through its MCP tool.
Claude instead performs the commit in its hook and rewrites the original command to a harmless
confirmation. Both arrangements prevent a second execution of the original commit command.

## Deliberate limits

The hash comparison and Git commit are separate operations. There is no atomic binding to the
reviewed parent or branch. Another writer can change staging during Git's hook window. Use separate
worktrees for concurrent agents. Hooks remain responsible for their own content changes and
independently detached helpers. A failed hook can leave working files or staging changed.

Cancellation stops active Git and known children, but cannot undo a commit already published by
Git. Output uses temporary files so an external helper cannot keep a pipe open indefinitely.
The reviewer's existing lifecycle guard, native Stop/Interrupt hooks, and desktop cleanup remain
unchanged. Human terminal commits remain unrestricted.

## Verification

Real temporary Git repositories exercise hook-restaged content, real hook metadata, post-commit
visibility, staging drift, mode-aware retries, sign-off, ordinary/unborn/detached/linked-worktree
commits, conditional configuration, cancellation, timeout, and ambiguous publication. Only reviewer
verdicts are stubbed. Export tests start the exported MCP server and discover commit and Stop/
Interrupt tools. Run the Codex suite and relevant release checks before the reviewed commit.
