# Reviewer prompt spec

The exact text the reviewer receives, and how each instruction is executable. Written 2026-07-30.

This file is the source of truth for the prompt strings, in the sense that the code is
required to match it: `_CAPSULE_UNIVERSAL`, `_CAPSULE_COMMIT_HEAD`, `_CAPSULE_COMMIT_SEE`,
`_CAPSULE_COMMIT_REPEAT_HEAD`, `_CAPSULE_COMMIT_CHANGED`, `_CAPSULE_COMMIT_REPEAT_TAIL`,
`COMMIT_PROMPT`, `PI_COMMIT_PROMPT`, `CLAUDE_COMMIT_PROMPT`, `REVIEWER_STANDING_BRIEF` and
`DEFAULT_BRIEF` are its transcription targets, and the two are edited together. They are DEFINED
in `reviewer_prompts.py` and reached through the `reviewer_lib` facade, which re-exports them;
before the 2026-08-01 module split they lived in `reviewer_lib.py` itself, and any line number in
this file older than that split points into the pre-split monolith. The
repeat-attempt text is assembled from three of those, not one: a HEAD, a per-backend CHANGED
fragment, and a TAIL. `tests/test_reviewer_lib.py` asserts that every constant named in this
paragraph is reachable on the facade. That is the whole of the guarantee: it catches a name
that has been renamed away or never existed, which is the drift that actually occurred here.
It does NOT check the converse -- a real transcription target left off this list, or a listed
name that is some unrelated constant, still passes. Keeping the list complete stays a manual
obligation when a prompt constant is added.

**Status, section by section.** ALL IMPLEMENTED as of 2026-08-02. This table previously carried a
PENDING half, because this repository requires the plan to be reviewable before code lands, so the
design was committed first (`4ed1279`) and the implementation followed. Every row is now flipped;
the text below describes the indexed code. Keep the header date and the rows in step: a row still
reading DESIGNED under an ALL IMPLEMENTED header is the contradiction this note exists to prevent,
and it survived here from 2026-07-31 until 2026-08-02.

| Section | Status |
|---|---|
| Layer 1 capsule, bullets 2 and 3 | IMPLEMENTED |
| Layer 2 commit-audit capsule | IMPLEMENTED |
| Every instruction's mechanism | IMPLEMENTED, verified against a real repository |
| The standard: fitness for use | IMPLEMENTED |
| Layer 1 capsule, bullet 1 | IMPLEMENTED |
| The SUGGESTIONS channel | IMPLEMENTED -- prompt form, plus a relay bounded at 1500 with the saved review named |
| The task prompts and the briefs | IMPLEMENTED -- "or other edits" removed; both backends share the form |
| Repeat attempts: scope to the delta | IMPLEMENTED |
| SUGGESTIONS on a REJECTION + the scope boundary | IMPLEMENTED 2026-07-31 |
| WITHDRAWN disposition + the PROOF standard | IMPLEMENTED 2026-08-02 |

**Two corrections found during implementation, both from measuring the wrong thing.** This file
sized the new bullet 1 against the UNIVERSAL form only and did not recompute the COMMIT ceiling --
but the commit form is the universal form PLUS layer 2, so it absorbed the same +117. And "796 of
900" was the `_CAPSULE_UNIVERSAL` CONSTANT, while the test measures the RENDERED capsule, which
also carries bullet 3: the real figure was 892 of 900, eight characters of headroom rather than a
hundred. Final MEASURED values: universal 872/900 (unchanged ceiling), commit 2606 codex and 2468
pi, ceiling 2600 -> 2700. See `test_both_capsule_forms_stay_short`.

**The reminder is authoritative, and that is load-bearing.** Neither backend re-sends the brief, so
a session briefed before this change keeps the old "DEPTH IS EXPECTED / never soften" text until
`/reviewerSession rotate`. The capsule reaches live sessions on the very next call, which is the
entire point of the two-layer design -- but a reviewer holding both would be reading a
contradiction with nothing to say which was current. The capsule header therefore claims precedence
("repeated on every call, and CURRENT: where it differs from anything sent earlier in this session,
this wins"), and it was funded by compressing that same header from 151 to 138 characters, so the
capsule carries more meaning in fewer characters than before this change.

It follows that NO per-call prompt may defer to the brief for the standard. A draft of
`COMMIT_PROMPT` read "a defect under the standard in your standing instructions", which points a
live reviewer at precisely the stale text being removed; both prompts say "the STANDING REMINDER
above" instead. `test_the_commit_prompts_point_at_the_reminder_not_the_brief` pins it.

**Scope of this file (corrected 2026-07-30).** It originally covered only the two capsule layers,
which left `COMMIT_PROMPT`, `PI_COMMIT_PROMPT`, `REVIEWER_STANDING_BRIEF` and `DEFAULT_BRIEF`
outside any spec at all -- so a phrase in `COMMIT_PROMPT` requiring "ALL the required corrections,
changes, bug fixes, **or other edits**" survived a prompt review earlier the same day, and the two
backends drifted apart on what a rejection may demand (pi's form has no such tail). Those four are
specified here now, alongside the capsule.

What this file does NOT cover, stated so the "source of truth" claim stays true: the
STOP-SUPERVISION path. `STOP_SUPERVISE_PROMPT` (`reviewer_prompts.py`) and the question prompt the
askq hook composes are reviewer-facing text with no spec. Both DO receive layer 1, because
`apply_context()` prepends the capsule on every call, so the standard below reaches them; their own
bodies are simply unspecified. Speccing them is separate work and not attempted here.

Rendered length is not a fixed number: two absolute paths are interpolated, and the commit form
varies by backend and by whether a prior verdict exists. The tests assert ceilings on the WORDING
only -- the interpolated paths are subtracted, since they are unbounded input rather than prompt
text -- at 900 characters universal, and a per-backend commit-audit ceiling (codex 3,000, pi 3,175
as of 2026-08-02), plus a test pinning that each path can appear only once.

## Why there are two layers

- `REVIEWER_STANDING_BRIEF` + the project's `reviewerAgent.md` (`compose_brief`) is sent ONCE, at
  session creation. Neither backend re-sends it, so a long session compacts it away.
- `REVIEWER_CAPSULE` (`reviewer_capsule`) is prepended by `apply_context()` on EVERY call. It is the
  durable copy and names the brief's path so a compacted reviewer can re-read the detail.

## The standard: fitness for use, not defensive volume

**IMPLEMENTED 2026-07-30.** This section and the two that follow describe the indexed prompts.

Added 2026-07-30, on the operator's finding that the reviewer was requiring large amounts of
unnecessary hardening and bloating the code it gated.

The prompts caused it. Three clauses rewarded volume rather than correctness:

- the capsule and `REVIEWER_STANDING_BRIEF` both said to report every real defect "however small
  the change that exposed it" and to "never soften, drop, or defer a finding", which reads as a
  standing instruction to nitpick;
- both then said "if you are unsure whether something is a defect, raise it", which converts
  uncertainty directly into BLOCKING findings, since a rejection is the only channel that exists;
- `COMMIT_PROMPT` licensed demanding "or other edits", i.e. changes that are not defects at all.

The replacement standard, which every layer states in some form:

> As a reviewer, your job is to find bugs, logic errors, omissions, or other issues which may
> prevent the code from performing properly under the circumstances in which it is to be used. For
> example, one-off diagnostic or experimental code does not have to protect robustly against
> operator error to the extent that production code does. Do not require code changes to address
> potential failure modes that are extremely unlikely to occur, unless this repository's brief says
> otherwise. You may make suggestions which are not commit rejections, with regard to any findings
> about easy efficiency gains (where such gains would actually be useful) or potential
> misconceptions that could affect overall project objectives, but do not make these blockers with
> required fixes.

Two decisions inside that text are load-bearing:

1. **The test is the code's actual use, NOT whether the reviewer can run it.** An earlier draft
   said to block only on a defect the reviewer "can trigger". That is wrong and would have been
   worst exactly where the gate matters most: GPU kernels, production-only paths and expensive
   jobs cannot run in the review environment at all, so that rule would have switched the gate off
   for the code with the least other coverage. Reasoning from the source is sufficient evidence.
2. **"unless this repository's brief says otherwise" replaces "in code that is marked as production
   code".** Nothing in these repositories marks production code, so a reviewer asked to apply that
   exception has to guess. The per-project `reviewerAgent.md` already exists, already declares a
   repo's own standards, and already wins over harness defaults where it is more specific -- so a
   repository that wants production-grade robustness says so there.

### The SUGGESTIONS channel

"You may make suggestions which are not commit rejections" was not expressible in the old output
contract: it admitted exactly two forms, and form (1) was "the single word APPROVED on the first
line and nothing else". The reviewer's only outlets were silence or a blocking rejection, which is
why non-defects arrived as required fixes.

Form (1) therefore gained an OPTIONAL trailing block:

```
APPROVED
SUGGESTIONS:
1. <non-blocking observation>
```

The VERDICT parser needs no change, and that is verified rather than assumed: `is_approved()` reads
the first non-empty line, so `is_approved("APPROVED\nSUGGESTIONS:\n1. ...")` is already `True`
while `is_approved("REJECTED:\n1. ...")` is `False`. `test_is_approved_accepts_a_suggestions_block_
and_still_rejects_rejected` pins both, so that a later "tightening" of `is_approved` to an exact
match cannot silently delete the channel and push these findings back into rejections.

### SUGGESTIONS on a REJECTION, and the scope boundary (2026-07-31)

The SUGGESTIONS channel solved "the reviewer's only outlets were silence or a blocking rejection"
-- but **only on the approval path**. Form (2) still ends "Output nothing else", so a reviewer that
rejects for one real defect has nowhere to put anything else it noticed. Everything it wants to say
becomes a required fix.

**The observed failure.** A ten-round review of a design document
(`CONFIG_SCOPING_PLAN.md` / `CONFIG_SCOPING_IMPL_PLAN.md`, 2026-07-31) did not converge. The
finding counts across rounds ran 16, 13, 8, 8 -- flat, not falling. Diagnosis: roughly 85-90% of
findings were genuine defects in the change and were correctly blocking, including one that would
have shipped the feature as a **no-op**. The non-converging tail was different in kind: findings
about `apply_context` -> `directives_block` -> `directives_file` and `reviewer_backend()` --
**code the staged set does not modify** -- pulled in because the staged document asserted "every
read path is write-free", which is true of the new layer and false of the surrounding module.

The reviewer was not wrong. It had no boundary to apply. Reviewing a diff, the diff is the unit;
reviewing a design document, every absolute sentence becomes a testable claim against the whole
repository, and the required "fix" becomes refactoring untouched code.

**Two changes, both in the prompt.**

Form (2) gains the same optional trailing block form (1) already has:

```
REJECTED:
1. <required fix>
SUGGESTIONS:
1. <non-blocking observation, including anything out of scope>
```

And the prompt states the boundary explicitly:

> The STAGED CHANGES are the unit of review. A defect in code the staged set does not modify is
> NOT a required fix -- put it in SUGGESTIONS -- unless the staged change makes it reachable,
> worse, or newly wrong. Where the staged text makes a false claim ABOUT unmodified code, the
> required fix is to correct the CLAIM, not the unmodified code.

That final clause is the one that ends the loop actually observed: the correct remedy for "every
read path is write-free" was to narrow the sentence, not to refactor `apply_context`.

A second clause sets the standard of proof, and the two do different jobs. The boundary settles
WHICH CODE may be cited; PROOF settles WHAT A CITATION MUST ESTABLISH before it can block:

> A finding resting on a fact OUTSIDE THE STAGED DIFF that you have not established is a
> SUGGESTION, not a required fix. [codex: You may run a command to establish it, and it blocks
> once you have.] [pi: You cannot run commands, so where the staged patch does not establish such
> a fact, the finding goes in SUGGESTIONS.] An inference from a figure recorded elsewhere in the
> repository is NOT established, because that figure can itself be wrong. A defect the staged diff
> establishes blocks with full force.

**Why not a third verdict** ("accept with follow-ups"). It would need a parser change, a new
branch in both hooks, and a new failure mode on a FAIL-CLOSED gate. Reusing SUGGESTIONS needs
neither: `is_approved()` reads the first non-empty line, so `REJECTED:` still rejects with a
trailing block present, exactly as `APPROVED` still approves with one. Verified, not assumed --
the same property the existing channel already relies on.

**The relay needs one fix, and only one.** `required_fixes_message()` (`reviewer_stopcontinue.py`)
already passes the FULL output untruncated into `reason`, so suggestions reach the agent intact
with no plumbing change. But its framing says "Treat the full text below as the REQUIRED FIXES",
which would mislabel the suggestions as blocking -- reintroducing the exact problem in the relay
that the prompt just fixed. The message splits on the `SUGGESTIONS:` marker and labels the two
parts distinctly: required fixes to be addressed before retrying, suggestions explicitly marked
non-blocking and safe to defer. A rejection with no suggestions block is unchanged, byte for byte.

**Cost note.** Every unnecessary rejection is a paid review round plus roughly as much wall-clock
again in rework. The ten-round case above spent four rounds on a tail that this change routes to a
non-blocking channel.

It DID need one narrow fix, found in review of this change. `startswith("APPROVED")` matched any
prefix, so `APPROVED_REJECTED` and `APPROVEDX` -- words that never say APPROVED -- opened the gate.
The token is now matched as a whole word, and a self-CONTRADICTORY first line (one that approves and
rejects in the same breath) fails closed.

It was deliberately NOT tightened to exact equality, which is what that review asked for, twice.
The review's underlying complaint was correct -- the prompt said "APPROVED on the first line and
nothing else" while the parser accepted a trailing note, and a contract the parser does not enforce
is a genuine defect. But the fix runs the other way: the PROMPT now describes the accepted form
("begin the first line with the word APPROVED"), rather than the parser being tightened to the
words.

The reason is the direction of the failure. This gate is FAIL-CLOSED, so a parser stricter than
real reviewer behaviour does not catch bad commits -- it BLOCKS good ones. `APPROVED -- clean diff,
tests green` is realistic output, already pinned by `test_approved_verdict_is_echoed_in_systemmessage`
since well before this change, and the SUGGESTIONS channel actively invites a reviewer to say more.
Every false block also costs a full paid review round plus the rework behind it. The one genuine
ambiguity -- a first line that approves and rejects at once -- is worth failing closed on, so it is
called out in the prompt AND refused by the parser -- naming exactly the token `REJECTED`, since a
`\bREJECT` prefix rule also caught REJECTION and REJECT-LEVEL and would have blocked
prompt-compliant approvals under a rule the reviewer was never given.

`test_the_stated_contract_matches_what_is_approved_actually_accepts` pins prompt and parser to each
other, and `test_approval_requires_the_whole_word_and_no_self_contradiction` pins the parser's two
halves, so neither can drift back.

The RELAY did need a change, and without it the channel would have been worse than useless -- it
would have looked like it worked while dropping most of what the reviewer wrote. `commit_hook`'s
allow branch relayed the verdict through `rl.short(out)`, truncating at 600 characters, so a
suggestions list of any length was cut off mid-item. Truncation was tolerable only while form (1)
was "APPROVED and nothing else": on the approved path there was, by contract, nothing to read.

The bound is now 1500 with the saved review named, which is what `required_fixes_message()` already
does for the OPERATOR-facing half of a rejection. An earlier draft of this change made the approved
relay unbounded and described that as "consistent with the rejection path"; that was wrong in a way
worth recording, because the rejection path is asymmetric. Its `reason` -- the channel Claude reads
-- is unbounded, but its `systemMessage` is `short(out, 1500)` plus the on-disk path. The allow path
has NO `reason` field at all, so `systemMessage` is its only channel, and relaying it unbounded
would let an off-contract reviewer dump an essay on every approved commit. `save_review()` runs for
any completed audit, approved included, so the complete text is already on disk and the bound costs
nothing. Both directions are pinned in `tests/test_commit_hook.py`: a realistic suggestions list
arrives whole, and an oversized one is visibly truncated with a working pointer to the full text.

## Repeat attempts: scope to the delta

Added 2026-07-31, on the operator's finding that the reviewers "are doing very complete reviews over
and over again".

The machinery to avoid that already existed: `staged_change_block` labels every path CHANGED /
ADDED / UNCHANGED / REMOVED against the version last audited, hands codex a `git diff <sha> :<path>`
per changed path, and both layers say not to re-derive an UNCHANGED path. Two UNCONDITIONAL clauses
defeated it -- capsule item 5 ("an UNCHANGED file is not automatically settled") and the brief's
"Judge the staged set AS A WHOLE" -- each a standing licence to re-audit everything on every round.

Cross-file invalidation is real and this does not deny it; the risk is CONDITIONAL, so the
instruction is too:

> On a repeat attempt, the CHANGED and ADDED paths are the review. Re-open an UNCHANGED file only
> when a change you have just read plausibly affects it -- a changed signature, contract, shared
> constant, error path, or data shape that it depends on -- and say which change sent you there.

It does not cap or time-box anything, and it must not chill real findings: a change that plausibly
reaches an unchanged file is a real lead and must still be followed, which is why the permission is
stated positively on the label ("Re-open it only when...") rather than left as an exception the
reviewer has to infer from a prohibition. What is trimmed is RE-DERIVING conclusions about bytes
nothing has touched. It does not apply to a FRESH review, which has no prior audit to scope against.

An earlier draft also asked the reviewer to "say which change sent you there" unconditionally. That
was dropped: the output contract admits only APPROVED (+ optional SUGGESTIONS) or REJECTED with a
numbered list, so a disclosure of reading trail has nowhere to go, and a clean widening across many
files would have produced a list of files with nothing wrong with them. If such a re-open yields an
actual finding, the change that led there belongs in that finding as ordinary evidence.

## Layer 1 -- universal capsule, sent on every call

Commit audits, question answers, and stop supervision all assemble through `apply_context()`, so
this text must be true for a turn with no staged set at all. `<ABS_BRIEF>` is the absolute path of
the project's `reviewerAgent.md`, resolved at the canonical project root.

All three bullets are IMPLEMENTED. Bullet 1 REPLACED this text, which the indexed
`_CAPSULE_UNIVERSAL` carried until 2026-07-30: "Depth is expected. Report every real defect you
find, however small the change that exposed it. Never soften, drop, or defer a finding to save
effort. If you are unsure whether something is a defect, raise it and say you are unsure."

```
REVIEWER STANDING REMINDER -- repeated on every call, and CURRENT: where it differs
from anything sent earlier in this session, this wins.

- Your job is to find bugs, logic errors, and omissions that would stop the code
  performing properly IN THE CIRCUMSTANCES IT IS USED IN. One-off diagnostic or
  experimental code need not guard against operator error the way production code
  must. Do not require changes for failure modes that are extremely unlikely to
  occur. Efficiency gains and misconceptions worth raising go in SUGGESTIONS,
  which never block a commit.
- Read narrowly, but read what you need: this repository's own files, the ones
  under review and what they directly depend on. Do not open third-party
  packages, installed dependencies, or the internals of any external tool.
- This repository's standing brief is at <ABS_BRIEF>. Read that file if you need
  its detail again.
```

The first bullet is the compressed form of the standard above: the full statement lives in
`REVIEWER_STANDING_BRIEF`, which is sent once per session where length is cheap, while this copy is
paid on EVERY call. It keeps the three operative rules -- fitness for actual use, no demands for
extremely-unlikely failure modes, and a non-blocking outlet. Measured: 420 characters against the
old bullet's 303. Rendered and with paths subtracted, the forms measured 872 of the
unchanged 900 universal ceiling and 2606 codex against a commit ceiling moved 2600 -> 2700 (see the
correction note above). Those are the figures AS OF 2026-07-30 and the commit one has since moved
twice; the current ceilings are in the WITHDRAWN section at the end of this file. Universal is
still 872 of 900.

The "unless this repository's brief says otherwise" exception is deliberately NOT repeated
here. The next bullet already names the brief's absolute path, the brief is what carries the
exception, and a project that has set a higher bar has said so in the file the reviewer was briefed
with.

When the project has no `reviewerAgent.md`, the third bullet becomes:

```
- This repository has no project brief; the harness defaults apply.
```

Naming a file that is absent costs the reviewer a tool call to discover it is absent.

## Layer 2 -- appended only for a commit audit

`<ABS_REVIEW>` is the per-project saved-verdict path, included ONLY when that file already exists.

### codex (has a shell, `--sandbox workspace-write`)

```
THIS CALL IS A COMMIT AUDIT.

1. What you are judging: the STAGED set, as a whole. Unstaged and untracked files
   are NOT part of it. Do not review them and do not cite them as evidence.
2. To see it: run `git diff --cached`.
3. To read one file's staged content in full: run `git show :<path>`. Do NOT read
   the working-tree file -- it can contain unstaged edits that are not in this
   commit, so a finding cited from it may not exist in what you are approving.
4. If this is a REPEAT ATTEMPT, a block headed "STAGED SET vs YOUR LAST AUDIT"
   appears EARLIER IN THIS MESSAGE, above this reminder. It labels every staged
   path relative to the version you already reviewed. Act on it:
     CHANGED -- your earlier line numbers are stale. Each carries a `delta:`
       command, `git diff <sha> :<path>`. RUN IT: it shows exactly what moved
       since your last audit, read from the index. Derive your findings from that
       delta; read the full staged file with `git show :<path>` only if the delta
       does not settle it.
     ADDED, or CHANGED with no delta command -- read the current staged content
       in full and derive your findings from it.
     UNCHANGED -- byte-identical to what you already reviewed. Do not re-derive
       it. Carry your findings on it into the disposition step below, which may
       retire one; if you raised none, do not invent any. Re-open it only when a
       change you just read plausibly affects it -- a signature, contract,
       shared constant, or data shape it depends on. What is avoided is
       re-deriving settled content, never following a real lead.
     REMOVED -- no longer staged. Drop the findings you raised on it.
   Then give EVERY finding from your previous verdict an explicit disposition:
   fixed, not fixed, superseded, or WITHDRAWN -- you have established the
   finding was itself incorrect, or it rests on a fact you have not established
   and belongs in SUGGESTIONS. This applies on an UNCHANGED path too: a wrong
   finding cannot be cleared by editing correct code. A finding you silently
   omit reads as fixed.
   Your previous verdict for this repository is saved at <ABS_REVIEW> -- read it
   if you need its exact wording.
```

**There is deliberately no item 5.** It read "A change in one file can invalidate a conclusion you
reached about another, so an UNCHANGED file is not automatically settled" -- unconditional, on every
round, i.e. a standing licence to re-audit the whole staged set. The conditional re-open now lives
on the UNCHANGED label in item 4, where the presence of a prior audit is already established; a
separate numbered item had to restate that condition, and "when the block above is present" is
unreadable when there is no block. Item 1 already carries the cross-file point for the no-block
case. See "Repeat attempts: scope to the delta" above.

pi's item 4 differs, because it cannot RUN a delta command -- but it is SHOWN the delta, so the
branch is written around the diff already in the prompt rather than around fetching one:

```
     CHANGED -- your earlier line numbers are stale. Where the block shows a
       diff, that is exactly what moved since your last audit, read from the
       index: derive your findings from it, and consult the full staged patch
       only if it does not settle them.
     ADDED, or CHANGED with no diff shown -- derive your findings from the
       staged patch (git_read {"subcommand":"diff","staged":true}), never from
       your read tool, which returns the working tree.
```

This capsule is appended AFTER the change block, so a branch that says "re-read the current staged
content" -- as this one did while pi got no delta -- contradicts a diff the reviewer has just read,
and points at the one source item 3 forbids it from citing.

It deliberately repeats what the change block and `PI_COMMIT_PROMPT` already say -- the "only if it
does not settle them" escape hatch and the literal `{"subcommand":"diff","staged":true}` call
shape. A first cut was compressed to cut both, because it exceeded the commit-capsule ceiling by
70 characters; that was reversed. **The ceiling is self-imposed by a single test assertion, so it
moves before the wording does.** It is now per backend -- codex 2,700, pi 2,900 -- because pi's
branch has to carry in words what codex carries as a command, and one shared number sized for pi
would loosen codex's guard, which currently sits 10 characters from biting.

### pi (hermetically read-only, tools `read,grep,find,ls,git_read`)

Identical except items 2 and 3:

```
2. To see it: call the git_read tool with {"subcommand":"diff","staged":true}.
3. That staged patch is the ONLY authoritative view of what is being committed.
   Reading a file with your read tool gives the WORKING-TREE copy, which can
   contain unstaged edits that are not in this commit: use it for surrounding
   context only, and never cite a line from it as staged content. You have no
   shell; do not attempt shell commands or try to run tests.
```

The asymmetry with codex is deliberate. codex has a shell, so `git show :<path>` gives it the exact
indexed content. The `git_read` interface this repo relies on -- `{"subcommand":"diff","staged":true}`
and `{"subcommand":"status"}`, per `PI_COMMIT_PROMPT` -- exposes no indexed-file read, so pi is told
to treat the staged patch as authoritative rather than being given a call shape this repo does not
document. Anything beyond those two subcommands is out-of-repo behaviour and must not be promised in
a prompt.

**The re-review delta is the one place pi does NOT fall back to a full re-read.** That premise still
holds -- pi has no indexed-file read -- but the conclusion no longer follows, because the delta is
INLINED rather than fetched: `staged_change_block` runs `git diff <blob> :<path>` in the hook's own
process and puts the result in the prompt. That needs no capability from the reviewer, so it is not
gated on the backend. What IS gated is the command: `_delta_commands` stays codex-only, and the
prose that would otherwise name `git show :<path>` resolves through `_reread_route` /
`_reread_clause` / `_reread_source`, which return pi's documented staged-patch call instead. Naming
`git show :<path>` to pi is doubly wrong -- its `git_read` builds `git show --stat <ref>`, which
never emits file content, and its ref validator rejects the `:` an index spec needs.

### claude (hermetically read-only, tools `Read,Grep,Glob`)

Identical except items 2 and 3, which drop the fetch entirely:

```
2. To see it: it is already here. The complete staged patch is included in this
   message; there is nothing to fetch and no command to run.
3. That patch is the ONLY authoritative view of what is being committed. Your
   read and search tools return the WORKING TREE, which can contain unstaged
   edits that are not in this commit: use them for surrounding context only, and
   never cite a line from them as staged content. You have no shell and no git
   tool; do not attempt to run tests.
```

The claude backend has no git access AT ALL -- no shell, and `Read` returns the working tree --
so unlike codex and pi it cannot be told how to obtain the patch. The gate inlines it instead
(`staged_patch()`, bounded by `REVIEWER_STAGED_PATCH_MAX`), which is why item 2 states its
presence rather than a call shape. The same asymmetry removes the CHANGED branch's re-read route:
claude's `_CAPSULE_COMMIT_CHANGED` entry says only that the reviewer's own earlier line numbers
are stale and that the patch in THIS message is current, because a complete patch arrives on
every turn and there is nothing else it could be sent to.

**A partial patch must never reach the reviewer.** Where pi converts "the staged diff was
truncated" into a synthetic rejection AFTER the fact, claude's gate refuses BEFORE the call: an
over-budget patch denies the commit with an actionable message and no review is run at all. The
two rules have one purpose -- a verdict formed on half a diff cannot be allowed to approve the
whole of it.

## Layer 0 -- the briefs, sent once at session creation

`compose_brief()` = `REVIEWER_STANDING_BRIEF` + `_BRIEF_SEPARATOR` + the project's
`reviewerAgent.md`, or `DEFAULT_BRIEF` when the project has none. Sent once, by
`ensure_session()` for codex and as an appended system prompt on the first turn for pi
(`--append-system-prompt`) and for claude (the same flag; under `--safe-mode` the child loads no
`CLAUDE.md`, so there is no other channel). Length is cheap
here and expensive in the capsule, which is why the full standard lives in this layer.

`REVIEWER_STANDING_BRIEF`'s opening section is now the standard quoted above, headed "THE STANDARD:
FITNESS FOR USE", addressed to the reviewer in the second person and carrying the brief-override
clause. It REPLACED the section headed "DEPTH IS EXPECTED", which was the primary source of the
nitpick pressure. It also states decision 1 explicitly -- the test is the code's actual use, not
whether the reviewer can run it here -- because that is the reading most likely to go wrong in the
compressed capsule copy. Dispositions and scope-your-reading are unchanged.

**IMPLEMENTED, two edits in this brief for the repeat-attempt scoping rule above.** WHAT YOU ARE
JUDGING used to end "Judge the staged set AS A WHOLE, because a change in one file can
invalidate a conclusion you reached about another"; that stays as the rule for a FRESH review and
is now scoped to one, since on a repeat it read as a standing licence to re-audit everything.
REPEAT ATTEMPTS carries the conditional rule in full, with its cost rationale -- length is cheap
in this layer, which is why the capsule carries only the compressed form.

The brief holds the THIRD copy of the disposition vocabulary, after the capsule and this file, and
it gained `WITHDRAWN` on 2026-08-02 alongside them. Keeping the three in step is a manual
obligation, and one that bites asymmetrically: the capsule and task prompts take effect on the next
hook invocation, while a brief change reaches only NEW sessions, so a stale brief keeps
contradicting a fixed capsule until `/reviewerSession rotate`. The brief also carries the demotion
half of the PROOF standard next to its existing "a test count ... is a claim you should check"
instruction -- that instruction was present and unfollowed on 2026-08-02, which is why the rule now
states the consequence (do not reject on an unchecked claim) rather than only the duty.

`DEFAULT_BRIEF`'s "you are the gate -- hold a high bar" became the same standard in one sentence.
"A high bar" with no stated axis is read as "demand more", which is precisely the failure.

**A brief change reaches only NEW sessions.** Neither backend re-sends the brief, so existing
reviewer sessions keep the old text until `/reviewerSession rotate`. The capsule and the task
prompts below take effect on the very next hook invocation. That asymmetry is why the standard is
stated in both layers rather than only in the brief.

## The task prompts, sent on every call

**IMPLEMENTED.** `reviewer_prompts.py` carries the forms below, and `commit_hook.py` relays the
approved verdict bounded at 1500 characters with the saved review named -- see the SUGGESTIONS
channel section for why that bound rather than none.

### `COMMIT_PROMPT` (codex), `PI_COMMIT_PROMPT` (pi) and `CLAUDE_COMMIT_PROMPT` (claude)

The rejection form previously read, for codex only:

```
followed by a numbered list of ALL the required corrections, changes, bug fixes,
or other edits, with file:line evidence whenever possible.
```

`or other edits` was REMOVED. It licensed requiring changes that are not defects at all, which is
the same bloat pressure from the opposite direction, and pi's form never had it -- so the two
backends disagreed about what a rejection may demand.

After that removal both backends shared one form, whose form (2) ended "Output nothing else".
**That intermediate form is not reproduced here.** An earlier draft of this section quoted it and
then quoted the current contract below it, which left the file carrying two contract blocks; a
reader landing on the first would take a superseded rejection form as authoritative. Only the
current contract appears in this file, once, immediately below.

**Revised 2026-07-31** -- form (2) gains the same optional trailing block that form (1) already
had, and both forms gain the scope boundary. This reverses the "Output nothing else" ending, which
is the change's whole point: it was the clause leaving a rejecting reviewer nowhere to put a
non-blocking observation. This is the SINGLE authoritative statement of the output contract:

```
(1) If the staged changes pass your audit: begin the first line with the word
    APPROVED. You may then add a SUGGESTIONS: block -- a numbered list of
    non-blocking observations -- and nothing else. Suggestions never block a
    commit, so put every non-defect there rather than rejecting. Do not write
    REJECTED anywhere on the APPROVED line.

(2) If any of the staged changes fail your audit: output a first line 'REJECTED:'
    followed by a numbered list of ALL the required corrections and bug fixes,
    with file:line evidence whenever possible. Every item must be a defect under
    the STANDING REMINDER above. Do not abbreviate or truncate the list. You may
    then add a SUGGESTIONS: block, on the same terms as form (1).

SCOPE: the STAGED CHANGES are the unit of review. A defect in code the staged set
does not modify is NOT a required fix -- put it in SUGGESTIONS -- unless the
staged change makes it reachable, worse, or newly wrong. Where the staged text
makes a false claim ABOUT unmodified code, the required fix is to correct the
CLAIM, not the unmodified code.

PROOF: a finding resting on a fact OUTSIDE THE STAGED DIFF that you have not
established is a SUGGESTION, not a required fix. You may run a command to
establish it, and it blocks once you have -- an inference from a figure recorded
elsewhere in the repository is NOT established, because that figure can itself be
wrong. A defect the staged diff establishes blocks with full force: this narrows
what may block, never how hard it blocks.
```

**The PROOF clause is per backend, and that is the point.** Codex may run commands, so its route is
to go and establish the fact. Pi is hermetically read-only, so being told to verify would hand it
an instruction it cannot follow -- the exact mistake already recorded above for the CHANGED branch,
where pi was told to re-read a file when its only read tool returns the working tree. Pi's clause
therefore replaces "you may run a command" with "you cannot run commands, so where the staged patch
does not establish such a fact, the finding goes in SUGGESTIONS". Claude's clause says the same
thing in its own terms -- it can read repository files but run nothing, so a finding resting on a
fact neither the staged diff nor the files it read establish is a SUGGESTION.

It was paid for by a deadlock, not proposed in the abstract. On 2026-08-02 the reviewer computed an
expected test count by adding a staged diff's new tests to the total documented in an earlier
commit, and rejected the correct, measured figure for not matching. That baseline was itself wrong:
an earlier commit had documented a count measured against an uncommitted tree whose tests were then
deleted. The arithmetic was sound and the premise was not, and nothing in the contract required the
premise to be established before it could block.

**Why the SCOPE clause lives in the output contract and NOT in the Layer 2 capsule.** The capsule
is under a pinned character ceiling and has only **94 characters of headroom** -- measured, not
estimated: commit form 2606 against 2700 (`test_both_capsule_forms_stay_short`). This clause is
far longer than that, and this file records two prior occasions where capsule arithmetic was done
against the wrong artifact and the real headroom turned out to be eight characters rather than a
hundred. The output contract lives in `COMMIT_PROMPT` / `PI_COMMIT_PROMPT`, which that ceiling
does not govern, and it is also where the clause is actionable -- immediately beside the choice
between a required fix and a suggestion. Layer 2 item 1 already bounds which FILES are in scope;
this bounds which FINDINGS are, which is a different question and belongs with the verdict form.

"the standard above" became "the standard in the STANDING REMINDER above", naming the layer rather
than relying on position alone. It must NOT say "your standing instructions": that points at the
once-sent brief, which in a live session is the stale copy. See the reminder-precedence note above.

The APPROVED token must remain FIRST on the first line: `is_approved()` reads the first non-empty
line, so a preamble before it reads as a rejection and fails the commit closed.

The code change it required, in one place: `commit_hook.py`'s allow branch now relays
`rl.short(out, 1500)` plus the saved-review path, rather than `rl.short(out)` at 600 with no
pointer. See the SUGGESTIONS channel section above for why 1500-with-a-pointer rather than
unbounded. Tests written, all in `tests/test_commit_hook.py`: an approved verdict carrying a
suggestions list longer than 600 characters reaches the operator whole; an oversized one is
visibly truncated and its full text is recoverable at the named path; the commit is still ALLOWED;
and a verdict whose first line is not APPROVED still denies.

## Every instruction's mechanism

| Instruction | codex | pi | claude |
|---|---|---|---|
| See the staged patch | `git diff --cached` | `git_read` diff staged | inlined in the prompt by the harness; the reviewer fetches nothing |
| Read one staged file in full | `git show :<path>` | **not available** in the documented interface; the staged patch is authoritative and a plain `read` is the working tree | **not available**; same rule, and `Read` is the working tree |
| Know which paths moved | harness block, earlier in the same prompt | same | same |
| See what moved INSIDE a changed path | `git diff <sha> :<path>`, emitted per path by the harness | the harness runs that diff ITSELF and inlines the result; pi needs no capability for it, and is never given the command, which its `git_read` cannot express | not needed: the whole staged patch is in every prompt |
| Recover prior findings | read `<ABS_REVIEW>` | `read` tool on `<ABS_REVIEW>` | `Read` tool on `<ABS_REVIEW>` |
| Re-read the standing brief | read `<ABS_BRIEF>` | `read` tool | `Read` tool |
| Run tests | shell | cannot; the prompt says so | cannot; no shell tool exists and the prompt says so |

Verified 2026-07-30, each against a real repository rather than assumed:

- `git diff <blob-sha> :<path>` emits a unified diff with the real path in the `+++ b/` header,
  reads the INDEX (an edit made after `git add` did not appear), and leaves `git status`
  byte-identical. A test runs the command the harness actually emits, not a hand-written one.
- `git show :<path>` returns indexed content, excludes a working-tree-only line, and tolerates a
  space in the path.
- `git cat-file -e <sha>` exits 1 for an absent object, which is what gates the fallback.

The old bytes need no snapshot, branch, or ref: `git add` already wrote them to the object
database, and `record_audited_stage()` already kept the sha.

## Implementation

Signatures:

```
reviewer_capsule(proj, commit_audit=False, claude_session_id=None) -> str
apply_context(prompt, proj, transcript_path=None, claude_session_id=None,
              commit_audit=False, role=ROLE_REVIEWER) -> str
                                              # prepends the capsule, then operator blocks
last_review_file(proj, claude_session_id=None) -> Path
save_review(text, proj, claude_session_id=None, history=False) -> str
```

Wiring:

- Backend is chosen inside `reviewer_capsule` via `reviewer_backend(proj)`, so pi never receives
  shell instructions.
- `commit_hook` passes `commit_audit=True` at ALL THREE call sites (codex, pi and claude).
  `askq_answer_hook` and `stop_hook` pass neither `commit_audit` nor a staged block, so they get
  layer 1 only.
- `role` selects WHICH backend session the prompt is composed for, and therefore which
  operator-message watermark applies. Only `stop_hook` passes it (`ROLE_STOP`); `commit_hook` and
  `askq_answer_hook` address the reviewer session and take the default. See
  `SESSION_SPLIT_PLAN.md`: the roles are separate sessions on both backends, so a shared watermark would let
  the stop supervisor -- which fires at every turn end -- advance the mark past a message the
  commit reviewer's session never saw, showing a fresh operator authorization as old context.
- Ordering: capsule, then operator messages, then directives, then the task prompt.
  `commit_hook` builds `staged_change_block(...) + apply_context(...)`, so the audit block sits
  ABOVE the capsule -- which is why item 4 says "earlier in this message".
- `<ABS_REVIEW>` resolves to `last_review_file(proj, sid)`. Storage layout, retention, and the
  write conditions are specified in `REVIEW_COST_REDUCTION_PLAN.md` Phase A; what matters to the
  prompt is that the path is per project and per Claude session, that the capsule names it only
  when the session is identifiable and the file exists, and that only a completed audit returning
  a non-blank verdict may overwrite it.
- No write failure may raise: the gate is fail-closed, and a failure to write a log must never
  block a commit.

## Tests

Layer separation:
- the non-commit capsule mentions no staged set, no `git diff --cached`, no audit block, no
  disposition rule;
- it still carries the fitness-for-use standard and the brief path;
- the commit capsule carries all of them.

Actionability:
- codex form says to RUN `git diff --cached` and contains `git show :`;
- pi form contains `git_read` and does NOT contain `git diff --cached`;
- the commit form says "earlier in this message";
- codex item 4 names the delta and keeps `git show :` as the fallback; pi item 4 has no shell
  command at all;
- the delta command the harness emits is RUN in a test: it shows the change, excludes an
  unstaged edit, and leaves `git status` unchanged;
- an ADDED path and a path whose recorded blob is gone both fall back to the full re-read.

Prior verdict:
- `last_review_file` differs per project, and per session within one project;
- the capsule names it only once it exists, and never names another session's;
- an unidentifiable session gets a written, reported file that the capsule does not name;
- a timeout, a crash, and a blank verdict all leave the previous findings intact; a real verdict
  replaces them;
- `rotate_session` clears it;
- history accumulates per project across sessions and is capped.

Delivery and hygiene:
- `apply_context` prepends the capsule with no operator context, and ahead of directives;
- `apply_context` passes the session through, so the prior-verdict path appears in the assembled
  prompt;
- commit gets layer 2, stop gets layer 1 only;
- both forms under their character ceilings, paths subtracted (900 universal; the commit ceiling
  is per backend and was raised on 2026-08-02 -- see the WITHDRAWN section below);
- the nitpick language is GONE from both capsule forms and both briefs: no "soften", no
  "depth is expected", no "if you are unsure ... raise it", no "hold a high bar";
- both commit prompts carry the SUGGESTIONS channel and neither carries "or other edits";
- `is_approved` accepts APPROVED followed by a SUGGESTIONS block, and still rejects a
  leading `REJECTED:` and a preamble before the token;

Rejection-side suggestions and the scope boundary (2026-07-31):
- both commit prompts offer a SUGGESTIONS block on form (2), and NEITHER still says "Output
  nothing else" on the rejection form -- asserted on the rendered prompt text, per backend, so
  the two cannot drift apart again;
- both commit prompts carry the SCOPE clause, including its "correct the CLAIM, not the
  unmodified code" sentence, which is the specific instruction that ends the observed loop;
- `is_approved("REJECTED:\n1. x\nSUGGESTIONS:\n1. y")` is still **False** -- a trailing block must
  never open a fail-closed gate. This is the mirror of the existing APPROVED-with-suggestions
  test and is pinned for the same reason: a later "tightening" must not silently delete either
  half of the channel;
- `required_fixes_message` splits on the `SUGGESTIONS:` marker and labels the two parts
  distinctly -- required fixes to address before retrying, suggestions explicitly marked
  non-blocking and deferrable;
- a rejection with NO suggestions block produces byte-identical output to before the change,
  so the common path is provably unaffected;
- the FULL reviewer text still reaches `reason` untruncated when a suggestions block is present,
  including the suggestions -- the relay must not start dropping content it used to pass through;
- a suggestions block containing the literal word `REJECTED` does not change the verdict, and a
  malformed or repeated marker degrades to "treat it all as required fixes" rather than
  silently discarding text.
- the capsule character ceilings are UNCHANGED by this work (900 / 2700): the SCOPE clause is
  added to the task prompts, not the capsule, so `test_both_capsule_forms_stay_short` must still
  pass with the same numbers. (That is the accurate record of the 2026-07-31 change. The commit
  ceiling has since moved -- see the WITHDRAWN section below for the current values.);
- the four change-table labels match `staged_change_block`'s real output;
- no `REJECTED:` (the verdict contract belongs to `COMMIT_PROMPT`);
- no emoji.

WITHDRAWN, and the standard of proof (2026-08-02):
- the disposition vocabulary carries a FOURTH option. `fixed / not fixed / superseded` left a
  reviewer whose finding was simply WRONG with no reachable disposition: nothing was fixed,
  nothing superseded it, so the only remaining slot was "not fixed", which blocks. Three retries
  produced three byte-identical rejections of a correct file. `WITHDRAWN` names the missing case
  and states its trigger, or it degrades into a synonym for `superseded`;
- the UNCHANGED label no longer says "Restate the findings you raised on it". That instruction
  shut the second exit at the same time: on a path needing no edit, a wrong finding could be
  cleared neither by working on the code nor by the disposition step. It now carries findings
  INTO that step, "which may retire one";
- both commit prompts carry the PROOF clause, per backend -- codex may run a command to establish
  the fact, pi cannot and demotes instead. Asserted on the rendered text so the two cannot drift:
  `you may run` appears in codex's and NOT in pi's;
- the demotion names SUGGESTIONS as its destination, and says defects the diff itself establishes
  still block with "full force". Both halves are pinned; without the second the clause reads as a
  licence to downgrade anything;
- the commit ceiling is RAISED to codex 3,000 and pi 3,175 for the +283 characters this costs on
  both. MEASURED 2026-08-02, not estimated: codex 2,973 and pi 3,147, so each guard keeps the
  tight headroom it had. Universal is unmoved at 872/900 -- the growth is commit-form only.
