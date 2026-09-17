# Claude Code: the gate performs the reviewed commit

Authorized scope: the **Claude package at the repository root**. The Codex package is a separate,
already-tested task and is not edited here. The two halves fix one defect: an approval is not
bound to the content that actually gets committed. The required outcomes for this half are in
[`claude_exact_reviewed_commit.md`](claude_exact_reviewed_commit.md); the Codex contract is in
[`codex_exact_reviewed_commit.md`](codex_exact_reviewed_commit.md).

This document settles the one thing that handoff leaves open: which Claude host mechanism is used,
and why the agent's own command cannot run afterwards.

## The rule

> The reviewer hook performs the commit when it approves. Nothing else commits. Before publishing,
> it confirms that what it is about to publish is what it reviewed.

Three refinements matter, because an implementation can satisfy that sentence literally and still
publish unreviewed content:

- **Identity is the tree and the parent, not the file list.** A staged file can be edited after
  review without the set of staged paths changing. The comparison is the tree object from
  `write-tree`, which carries content, file modes, deletions, symlinks and submodule pointers,
  together with the recorded parent and the exact `HEAD` bytes.
- **Comparing and then committing still races.** Between the check and the write, anything may
  stage. The check has to happen while the locks that would let someone else move are already
  held.
- **"Nothing else commits" is scoped to the agent's tool calls** in a gated repository. Commits
  made by a person in their own terminal stay unrestricted, deliberately.

## The mechanism: deny, and commit inside the hook

`commit_hook.py` runs as a `PreToolUse` hook on `Bash`, so its decisions are allow, deny, or a
rewritten input. On approval the gate **performs the commit itself and then denies the original
tool call**, naming the published commit id in the denial.

Deny rather than a rewritten input: rewriting still ends with the host running a shell command
against the live index later, which replaces one unbound execution with another.

Why the agent's command cannot run afterwards: a deny decision means the host never executes the
tool call. There is no second dispatch to race with. That is not an assumption about the host; it
is the same mechanism every rejection in this gate already uses, on every rejected commit today.

Because the command never runs, no *content* hazard in it can take effect. Staging inside the
committing call, wrapper commands, operators without spaces and `GIT_DIR`-style redirection all
become moot: none of them execute, so none of them can put unreviewed content into a commit. A
lexical guard would have to enumerate every spelling of those; ownership removes that whole
category.

**But denying the call drops whatever else was in it.** `git commit -m x && git push` would
publish the commit and silently skip the push; `git add f.py && git commit -m x` would commit
without ever staging `f.py`, so the agent gets a commit that is not the one it asked for. The
gate must not quietly discard half of a command.

So one narrow lexical rule survives, and it is a much simpler question than the one a hazard
guard has to answer:

> **The tool call must be exactly one `git commit` invocation and nothing else.** Anything
> further in the call — another command, a pipe, a redirection, a wrapper, a second commit — is
> refused *before* any review, naming what was found and asking for it as its own tool call.

This fails closed on anything it does not recognise, which is the safe direction: an unparseable
or unfamiliar call is refused rather than half-executed. It asks only "is this one plain commit?"
rather than "does this contain any of a growing list of dangerous forms", so it does not grow as
new spellings appear.

Reporting a success through a denial has to be handled deliberately, or an agent reads "denied"
and commits twice:

- The denial states the commit was published and names its id, first.
- A failure in bookkeeping *after* publication is never reported as "nothing committed".
- A retry is additionally inert: publication leaves the index clean, so a second attempt meets the
  existing nothing-staged refusal.

No review ticket is needed. Codex mints a one-use token because its commit arrives as a separate
call from the shell attempt that was blocked. The Claude hook reviews and commits inside one
interception, so there is nothing to bind.

## The transaction

1. **Capture** private Git state inside the repository's own metadata directory: the index copied,
   `HEAD` bytes and parent recorded, effective configuration serialized with includes and
   repository-conditional sections resolved, and the real object database and hooks path still in
   use. Reviewer Git commands receive this through an explicit environment, never by mutating
   process-global state, so concurrent attempts cannot see each other.
2. **Verify** the captured tree still matches the live index and HEAD before spending a review.
3. **Review the captured snapshot.** See the per-backend requirement below.
4. **Build a candidate** with a real `git commit` against the private state, so hooks and signing
   behave normally, then reject any candidate whose tree or parents differ from what was reviewed.
   A hook that rewrites content is caught here. No public reference has moved yet.
   The candidate runs against the **live worktree** with the repository's real hooks path, which
   is what the Codex implementation does and what makes `pre-commit` hooks behave as they
   normally would. The consequence is spelled out under *What preservation covers* below.
5. **Publish** under the live `index.lock`, through a Git reference transaction: start, update with
   the expected old value, prepare so Git holds the reference locks, re-verify HEAD identity and
   the live index under those locks, then commit the transaction.
6. **Cancel safely.** Cancellation before publication aborts the transaction and cannot produce a
   late commit. Clean up only this attempt's own files, locks and processes on every exit path.

Refuse, before any review: Git older than 2.48 or a non-files reference format, rather than
approximating a prepared transaction with a check and an unlocked write; and an active merge,
rebase, cherry-pick, revert or sequencer operation, whose parent and message semantics cannot
silently become an ordinary commit's.

### What preservation covers

Never reset, stash or absorb a person's newer staged or unstaged work to make a commit succeed.
Refuse and ask for a fresh review. Stated precisely, because the boundary matters:

- **Guaranteed.** No reference moves unless the candidate's tree and parents are the ones that
  were reviewed. The gate itself never writes to the working tree, never resets, never stashes,
  and never restores. A refusal leaves the index and every reference exactly as it found them.
- **Not guaranteed: what the repository's own hooks do.** The candidate commit runs the real
  `pre-commit` and `commit-msg` hooks against the live worktree, so a hook that reformats or
  deletes files has already done so by the time the candidate is judged. Rejecting the candidate
  afterwards does not undo those writes.

That is deliberate and it is the same behaviour Codex has. Those hooks would have run anyway had
the agent's own commit proceeded, so the gate does not change *whether* they run. What it changes
is that a rejection can now follow them, leaving their writes in place with no commit. The gate
**reports** that rather than reverting it: reverting would mean the gate writing to the working
tree, which is the one thing preservation forbids, and it cannot tell a hook's edit from a
person's.

Worktree isolation would remove the exposure and is deliberately **not** adopted here, because it
is not what the tested Codex implementation does and would change how `pre-commit` hooks see the
tree. Revisit it only with evidence that a real hook caused loss.

## Claude-specific decisions

- **The reviewer must read the captured state, and this differs per backend.** The codex backend
  reviews by running its own Git commands, so the capture environment has to reach that
  subprocess through `reviewer_backends`. The pi backend reads through its Git tool. The claude
  backend is handed a patch string, which must be generated from the captured index. A capture
  that only changes the prompt is cosmetic for the first two, and that is how this fix fails
  quietly if it is not tested on each backend.
- **The message and the optional flags are separate things.** Codex receives the message as its
  own tool argument and limits *flags* to `--signoff` and `--quiet`. The Claude hook has to
  recover both from the command:
  - **The message is carried, and must be literal.** `-m`/`--message`, including repeated `-m`,
    which Git joins as separate paragraphs. Message text is passed as an argument vector, never
    interpolated into a shell, so newlines and shell metacharacters survive intact.
  - **A message the shell would have expanded is refused.** The hook sees the unexpanded token,
    so `git commit -m "$MSG"` would commit the literal text `$MSG` rather than its value,
    silently writing the wrong message. That is the same reasoning the gate already applies to
    an unresolvable `-C` target.

    Refusing on the bare character would be wrong, though: `git commit -m 'cost $5'` is an
    ordinary message, and the point of carrying the message as an argument vector is that
    literal metacharacters survive. The two cases are indistinguishable *after* lexing, because
    quote removal has already happened, so the decision is made on the **raw argument text**.

    The rule is stated by *quoting context* rather than by listing expansions, so that it is
    complete rather than a list that grows each time someone thinks of another one. A shell has
    exactly three lexical contexts for an argument, and each gets one verdict:

    | how the message appears in the command | verdict |
    | --- | --- |
    | single-quoted | accepted; the shell expands nothing inside single quotes |
    | double-quoted | accepted unless it contains a `$` or backtick, escaped or not (see below) |
    | a bare word | accepted only if it contains no shell-special character at all: `$`, backtick, `*`, `?`, `[`, `]`, `{`, `}`, `~`, `!`, `\`, or a quote |
    | anything else, including unterminated or mixed adjacent quoting | refused, not guessed at |

    That covers parameter and command substitution, globbing, tilde and brace expansion, and
    anything else a shell might do, because the bare-word case refuses every special character
    rather than enumerating what each one means. A message that needs an unusual character can
    always be quoted, which is how messages are written anyway.

    **A backslash-escaped `$` or backtick inside double quotes is refused too**, which an earlier
    draft of this document said would be accepted. A shell resolves `"\$"` to a literal dollar
    sign; Python's lexer keeps the backslash. The gate cannot reproduce that value faithfully, so
    committing either reading would commit text that differs from what was written. Single quotes
    are refused by neither and are the remedy the refusal names. `\"` and `\\` are unaffected:
    a shell and the lexer agree on both.
  - **Optional flags are pinned to the Codex set**, `--signoff` and `--quiet`.
  - **Everything else is refused with a precise explanation**, and is not silently turned into
    an ordinary commit: other message sources (`-F`/`--file`, `-C`/`--reuse-message`,
    `--squash`, `--fixup`, `--template`), identity and verification overrides
    (`--author`, `--date`, `--no-verify`, `--gpg-sign`), and `--amend`, whose parent semantics
    the handoff requires be treated deliberately rather than replaced.
  - **A commit with no message source is refused too.** It would open an editor, which cannot
    work inside a hook.

  Pinning the flag set to Codex's, rather than deciding case by case what seems safe enough, is
  what keeps this from growing into open-ended option support.
- **The approved path changes shape.** Today approval ends in `allow_output`, letting the agent's
  own commit proceed, with a message asking it not to stage more. Both exist only because the
  commit was out of the gate's hands, and both go.
- **Adapt rather than copy.** The Codex module imports that package's own backend and cancellation
  helpers; the two packages deliberately share no code, and the Claude side has its own
  primitives.
- **Retry identity is mode-aware** and taken from the captured snapshot, per the handoff's second
  defect. A record without mode information forces a fresh review rather than a false unchanged
  classification.

## Test plan

The handoff's table applies in full and is not repeated. These are additional and specific to this
mechanism:

| Case | Required assertion |
| --- | --- |
| Approved commit | the hook publishes it, returns deny, and the denial names the commit id |
| After publication | the original tool call does not execute, and a retry finds nothing staged |
| Bookkeeping failure after publication | reported as committed with its id, never as "nothing committed" |
| Each of the three backends | the reviewer's evidence comes from the captured state, not the live index |
| Content edited after capture, same file list | publication refuses; a filename-only check would pass |
| Staging during the prepared transaction | refused under the locks, with the person's work intact |
| Hook rewrites content during the candidate commit | candidate rejected, no public reference moved |
| The same, checking the worktree afterwards | the hook's writes are reported and left alone; the gate reset nothing |
| `git commit -m` with repeated `-m`, and with newlines and shell metacharacters in the message | carried through intact, as an argument vector |
| `--amend`, `-F`, `--author`, `--no-verify`, and a commit with no message source | each refused with its own explanation, none silently committed |
| `git commit -m x && git push`, and a stage chained before a commit | refused before review; nothing published and nothing silently dropped |
| a pipe, a redirection, or a second command after the commit | refused, naming what was found |
| `git commit -m "$MSG"` and `-m "$(...)"` | refused; the literal token is never committed as the message |
| `git commit -m 'cost $5'` | accepted, and the message keeps its literal characters |
| a backslash-escaped `\$` inside double quotes | refused, because a shell and the lexer resolve it differently |
| a multiline message, and one containing a quoted `&&` or `;` | accepted as message text, not read as a command boundary |
| a newline outside quotes, and a backslash line continuation | the first refused as a second command, the second joined into one |
| `git -C ~/repo commit` versus `git -C /tmp/repo* commit` | the first accepted, since expanduser matches the shell; the second refused, since a glob would target a different repository from the one audited |
| `git -C ~+ commit`, and the other directory-stack tilde forms | refused, because a shell expands them and expanduser does not |
| `git -C ~root/repo commit`, and a tilde inside a path such as `/tmp/a~b` | accepted, because a shell and expanduser resolve both identically |
| a message whose text is the word `git` | accepted; extra commands are found structurally, not by counting words |
| a single-quoted message containing `*`, `~`, `{a,b}` or `!` | accepted; single quotes expand nothing |
| a bare-word message containing any of those | refused by the bare-word rule, without enumerating what each one expands to |
| an unterminated quote in the message | refused, not guessed at |
| Git below the floor, or a non-files ref format | fail closed with a clear message, before any review |
| Active merge, rebase or sequencer | explicit refusal before side effects |
| Cancellation before publication | no commit appears afterwards |

Use real temporary repositories and deterministic synchronization points. Stub only the model
verdict; never stub the Git operation whose integrity the test exists to prove.

## Protocol

Documentation commit, then a meaningful red-test commit, then the implementation commit, then live
checks in disposable repositories. Every commit goes through this repository's own gate, and
messages are one line with no co-author trailer. Stage explicitly by path: the working tree also
carries an unrelated held release bump and the Codex task's in-flight work, neither of which
belongs in these commits.

This document does not authorize publishing a release, pushing, or changing the operator's
installed configuration.
