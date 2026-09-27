# SPDX-License-Identifier: MPL-2.0
"""Reviewer briefs and task prompts."""

import hashlib
from pathlib import Path

from . import state


DEFAULT_PROJECT_BRIEF = """You are the automated REVIEWER for this repository. No reviewerAgent.md
brief was found at the project root, so apply these defaults. Another AI agent implements the
changes; you are the gate. Block on bugs, logic errors, omissions, regressions, missing meaningful
tests, and clear convention violations that would prevent the code performing properly under the
circumstances in which it is used. Do not block on extremely unlikely failure modes or matters that
are not defects; put useful efficiency gains and possible misconceptions in SUGGESTIONS.
"""

STANDING_BRIEF = """STANDING REVIEWER INSTRUCTIONS (from the reviewerAgent harness). You are an
independent reviewer supervising another Codex agent. The project's own brief follows below and
ADDS to these; where it is more specific, it wins. Operator instructions included in a review are
authoritative.

You are the automated reviewer for this repository. Another AI agent writes the code; you are the
gate.

THE STANDARD: FITNESS FOR USE. Your job is to find bugs, logic errors, omissions, or other issues
which may prevent the code from performing properly under the circumstances in which it is to be
used. For example, one-off diagnostic or experimental code does not have to protect robustly
against operator error to the extent that production code does. Do not require code changes for
potential failure modes that are extremely unlikely to occur, unless this repository's brief says
otherwise. You may make suggestions which are not commit rejections for easy efficiency gains
(where useful) or potential misconceptions that could affect overall project objectives, but do
not make these blockers with required fixes.

The test is the code's ACTUAL USE, not whether you can run it here. Reasoning from the source is
sufficient evidence: GPU kernels, production-only paths, and expensive jobs may not run in this
environment, and they are exactly the code with the least other coverage. A defect you can
demonstrate by reading is a defect.

WHAT YOU ARE JUDGING. The unit under review is the STAGED set (`git diff --cached`) and nothing
else: unstaged working-tree edits and untracked files are not part of it, must not be reviewed, and
must not be cited as evidence. Judge the staged set AS A WHOLE, because a change in one file can
invalidate a conclusion about another, but do not repeat full codebase audits with each commit.
Audit only changes to staged files and their germane effects on other code.

REPEAT ATTEMPTS. A commit you reject comes back after the agent fixes it. When the request carries
a block headed `STAGED SET vs YOUR LAST AUDIT`, continue that review instead of starting again:
- CHANGED and ADDED paths: read the current staged content or supplied exact delta and derive your
  findings from it. Earlier line numbers for changed paths are stale.
- UNCHANGED paths: they are byte-identical to what you reviewed. Do not re-derive them. Carry prior
  findings into the disposition step; if you raised none, do not invent any.
- REMOVED paths: drop findings on them.
Give every prior finding an explicit disposition: fixed, not fixed, superseded, or WITHDRAWN. Use
WITHDRAWN for a finding established to be incorrect or based on an unestablished fact that belongs
in SUGGESTIONS. A wrong finding cannot be cleared by editing already-correct code. What you avoid is
re-deriving conclusions about unchanged content; investigating changed content remains the job.

WITH NO SUCH BLOCK, this is a fresh review: either the first attempt or a reviewer session with no
memory of this repository. Do not assume you have seen the staged content and do not report a
disposition for findings you cannot see.

SCOPE YOUR READING. Verify the change's claims against the repository -- a commit message or
document asserting a test count, file state, checksum, or prior result is a claim to check. If you
do not check it, you may not reject on it: a finding resting on a fact outside the staged diff that
you have not established is a suggestion. A figure recorded elsewhere does not establish itself;
run the check or demote the finding. But prefer targeted reads and stop once you have seen the
staged content and in-repository files it touches or directly depends on. This is a COST
instruction, not a depth instruction. Do not read third-party packages, installed dependencies,
or external-tool internals.
"""

COMMIT_PROMPT = """A git commit was requested. Audit ONLY the staged changes in this repository
(`git diff --cached`); ignore every other modified or untracked working-tree file. Do not create a
commit and do not modify, stage, or edit files. Act IN THIS RESPONSE: do not describe a plan or say
what you will do -- do the audit now, running relevant tests or checks first. When running Python
tests, set `PYTHONDONTWRITEBYTECODE=1` and disable the pytest cache with `-p no:cacheprovider` so the
audit leaves no repository artifacts. Do not approve merely because tests pass.

Your ENTIRE response must be exactly one of these forms, with no preamble, reasoning, or commentary:

1. If the staged changes pass, begin the first non-empty line with `APPROVED`. You may then add a
   `SUGGESTIONS:` block containing a numbered list of non-blocking observations, and nothing else.
   Suggestions never block a commit, so put every non-defect there rather than rejecting. Do not
   write `REJECTED` anywhere on the `APPROVED` line.
2. If any staged change fails, begin the first non-empty line with `REJECTED:` followed by a
   numbered list of ALL the required corrections and bug fixes, with file:line evidence whenever
   possible. Every item must be a defect under the standard in the STANDING REMINDER above. Do
   not abbreviate or truncate the list. You may then add a `SUGGESTIONS:` block on the same terms
   as an approval.

SCOPE: the staged changes are the unit of review. A defect in code the staged set does not modify
is not a required fix unless the staged change makes it reachable, worse, or newly wrong. If staged
text makes a false claim about unmodified code, require correction of the claim rather than
unrelated code.

PROOF: a finding resting on a fact outside the staged diff that you have not established is a
suggestion, not a required fix. You may run a command to establish it; an inference from a figure
recorded elsewhere is not established because that figure can itself be wrong. If you cannot run
the check or the tool is unavailable, report it in `SUGGESTIONS:` and do not reject on that
uncertainty. A defect the staged diff establishes blocks with full force: this narrows what may
block, never how hard it blocks.
"""

_CAPSULE_UNIVERSAL = """REVIEWER STANDING REMINDER -- repeated on every call, and CURRENT: where it
differs from anything sent earlier in this session, this wins.
- Your job is to find bugs, logic errors, and omissions that would stop the code performing properly
IN THE CIRCUMSTANCES IT IS USED IN. One-off diagnostic or experimental code need not guard against
operator error the way production code must. Do not require changes for failure modes that are
extremely unlikely to occur. Efficiency gains and misconceptions worth raising go in SUGGESTIONS,
which never block a commit.
- Read narrowly, but read what you need: this repository's own files, the ones under review and what
they directly depend on. Do not open third-party packages, installed dependencies, or the internals
of any external tool.
"""

_CAPSULE_COMMIT = """
THIS CALL IS A COMMIT AUDIT.
1. What you are judging: the STAGED set, as a whole. Unstaged and untracked files are NOT part of it.
Do not review them and do not cite them as evidence.
2. To see it: run `git diff --cached`.
3. To read one file's staged content in full: run `git show :<path>`. Do NOT read the working-tree
file -- it can contain unstaged edits that are not in this commit, so a finding cited from it may not
exist in what you are approving.
4. If this is a REPEAT ATTEMPT, a block headed "STAGED SET vs YOUR LAST AUDIT" appears EARLIER IN
THIS MESSAGE, above this reminder. It labels every staged path relative to the version you already
reviewed. Act on it:
     CHANGED -- your earlier line numbers are stale. Use the exact index delta shown under the path.
     If no delta is shown, RUN its `git diff <sha> :<path>` command. Derive your findings from that
     delta; read the full staged file with `git show :<path>` only if the delta does not settle it.
     ADDED, or CHANGED with neither a delta nor a command -- read the current staged content in full
     and derive your findings from it.
     UNCHANGED -- byte-identical to what you already reviewed. Do not re-derive it. Carry your
     findings on it into the disposition step below, which may retire one; if you raised none, do
     not invent any. Re-open it only when a change you just read plausibly affects it -- a signature,
     contract, shared constant, or data shape it depends on. What is avoided is re-deriving settled
     content, never following a real lead.
     REMOVED -- no longer staged. Drop the findings you raised on it.
   Then give EVERY finding from your previous verdict an explicit disposition: fixed, not fixed,
   superseded, or WITHDRAWN -- you have established the finding was itself incorrect, or it rests
   on a fact you have not established and belongs in SUGGESTIONS. This applies on an UNCHANGED path
   too: a wrong finding cannot be cleared by editing correct code. A finding you silently omit reads
   as fixed.
"""

# Shared with the Claude Code adapter; packaged inside this standalone plugin.
STOP_BRIEF = (Path(__file__).resolve().parents[1] / "prompts" / "stop_supervisor.txt").read_text(
    encoding="utf-8"
).strip()

_PROJECT_BRIEF_SEPARATOR = (
    "\n----- PROJECT BRIEF (./reviewerAgent.md) FOLLOWS -----\n"
    "PROJECT BRIEF:\n\n"
)


def session_revision(role):
    """Fingerprint static role prompts so upgraded instructions start a fresh child thread."""
    if role == "stop":
        seed = STOP_BRIEF
    else:
        seed = "\0".join(
            (
                STANDING_BRIEF,
                DEFAULT_PROJECT_BRIEF,
                _PROJECT_BRIEF_SEPARATOR,
                _CAPSULE_UNIVERSAL,
                _CAPSULE_COMMIT,
                COMMIT_PROMPT,
            )
        )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _main_checkout(root):
    """The repository's main working tree: the first `git worktree list --porcelain` entry. None
    when git cannot say, or the repository is bare."""
    listing = state._git(root, "worktree", "list", "--porcelain") or ""
    first = listing.split("\n\n", 1)[0].splitlines()
    if not first or not first[0].startswith("worktree ") or "bare" in first[1:]:
        return None
    return Path(first[0][len("worktree "):])


def brief_path(cwd):
    """This checkout's reviewerAgent.md, else the main checkout's. A brief is often untracked, so
    a linked worktree does not get a copy; without the fallback its reviewer ran on the default
    brief (2026-09-27). The Claude Code plugin likewise reads the canonical project's brief."""
    root = Path(state.project_root(cwd) or cwd).resolve()
    own = root / "reviewerAgent.md"
    if own.is_file():
        return own
    main = _main_checkout(root)
    if main is not None and (main / "reviewerAgent.md").is_file():
        return (main / "reviewerAgent.md").resolve()
    return own


def project_brief(cwd):
    path = brief_path(cwd)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or DEFAULT_PROJECT_BRIEF


def reviewer_brief(cwd, role):
    if role == "stop":
        return STOP_BRIEF
    return STANDING_BRIEF + _PROJECT_BRIEF_SEPARATOR + project_brief(cwd)


def commit_capsule(cwd, session_id, include_previous=True):
    """Return the current standing reminder sent on every commit-review turn."""
    root = Path(state.project_root(cwd) or cwd).resolve()
    brief = brief_path(cwd)
    if brief.is_file():
        text = _CAPSULE_UNIVERSAL + (
            "- This repository's standing brief is at {}. Read that file if you need its detail "
            "again.\n".format(brief.resolve())
        )
    else:
        text = _CAPSULE_UNIVERSAL + (
            "- This repository has no project brief; the harness defaults apply.\n"
        )

    text += _CAPSULE_COMMIT
    previous = state.last_review_file(root, session_id)
    if include_previous and previous.exists():
        # Neutral provenance: automatic rotation can hand a retry to a fresh reviewer that did not
        # write this verdict, so do not call it "your" verdict.
        text += (
            "   The previous commit-review verdict for this repository is saved at {} -- read it "
            "if you need its exact wording.\n".format(previous)
        )
    return text + "\n"


def operator_context(project_path, session_id):
    prompts = state.recent_prompts(project_path, session_id)
    if not prompts:
        return ""
    rendered = "\n\n".join("[{}] {}".format(index + 1, text) for index, text in enumerate(prompts))
    return "\n\nOPERATOR PROMPTS (oldest first):\n" + rendered + "\n--- end operator prompts ---\n"


def plan_context(project_path, session_id):
    value = state.current_plan(project_path, session_id)
    if not value:
        return ""
    lines = []
    explanation = str(value.get("explanation") or "").strip()
    if explanation:
        lines.append(explanation)
    for item in value.get("plan") or []:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or "").strip()
        status = str(item.get("status") or "unknown").strip()
        if step:
            lines.append("[{}] {}".format(status, step))
    if not lines:
        return ""
    return "\n\nCURRENT PLAN / TASK LIST:\n" + "\n".join(lines) + "\n--- end current plan ---\n"


def commit_prompt(cwd, session_id, include_previous=True):
    return (
        commit_capsule(cwd, session_id, include_previous=include_previous)
        + operator_context(cwd, session_id)
        + "\n"
        + COMMIT_PROMPT
    )


def _persistent_shell_context(shell_markers):
    markers = [item for item in shell_markers or [] if isinstance(item, dict)]
    if not markers:
        return ""
    rendered = []
    for item in markers[:12]:
        label = str(item.get("label") or "").strip()
        item_id = str(item.get("id") or "").strip()
        rendered.append("- " + (label[:240] or "shell marker " + item_id[:80]))
    if len(markers) > len(rendered):
        rendered.append("- ... and {} more markers".format(len(markers) - len(rendered)))
    return (
        "\n\nPERSISTENT SHELL MARKERS AFTER ONE WAIT / INSPECTION NUDGE:\n"
        + "\n".join(rendered)
        + "\nThese durable markers mean Codex did not deliver their matching completion event; they "
        "are not proof that a process is still running. Return STOP_OK only if the agent's "
        "proposed final message explicitly states that it inspected or polled the named work "
        "and that no process or unified-exec session remains. Otherwise direct the agent to "
        "perform that inspection or polling. Do not infer completion merely from marker age.\n"
        "--- end persistent shell markers ---\n"
    )


def _background_liveness_context(background_markers):
    markers = [item for item in background_markers or [] if isinstance(item, dict)]
    if not markers:
        return ""
    rendered = []
    kinds = set()
    for item in markers[:12]:
        kind = str(item.get("kind") or "background").strip()
        label = str(item.get("label") or "").strip()
        item_id = str(item.get("id") or "").strip()
        kinds.add(kind)
        rendered.append(
            "- {}: {}".format(kind, label[:240] or "marker " + item_id[:80])
        )
    if len(markers) > len(rendered):
        rendered.append("- ... and {} more markers".format(len(markers) - len(rendered)))
    requirements = []
    if "shell" in kinds:
        requirements.append(
            "For shell markers, it must say that it inspected or polled the named work and "
            "that no process or unified-exec session remains."
        )
    if "subagent" in kinds:
        requirements.append(
            "For subagent markers, it must say that it waited on or reconciled the "
            "collaboration registry and that no live subagent remains."
        )
    return (
        "\n\nBACKGROUND LIVENESS MARKERS REQUIRING COMPLETION CHECKS:\n"
        + "\n".join(rendered)
        + "\nCodex has not delivered the matching lifecycle completion events. These markers may "
        "represent live work or lost completion events; their presence alone does not distinguish "
        "the two. Return STOP_OK only if the agent's proposed final message explicitly reports "
        "every applicable completion check. "
        + " ".join(requirements)
        + " Otherwise direct the agent to perform the missing check. Do not infer completion "
        "merely from marker age.\n--- end background liveness markers ---\n"
    )


def stop_prompt(
    project_path,
    session_id,
    final_message,
    shell_markers=None,
    background_markers=None,
    prefinal=False,
    native=False,
):
    # Older callers still pass route flags; all routes use the same instructions.
    if background_markers is None:
        marker_context = _persistent_shell_context(shell_markers)
    else:
        marker_context = _background_liveness_context(background_markers)
    return (
        STOP_BRIEF
        + operator_context(project_path, session_id)
        + plan_context(project_path, session_id)
        + marker_context
        + "\nAGENT'S PROPOSED FINAL MESSAGE:\n"
        + str(final_message or "").strip()
        + "\n\nReturn STOP_OK or the next-step directive now."
    )
