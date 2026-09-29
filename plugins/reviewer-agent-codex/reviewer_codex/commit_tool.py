# SPDX-License-Identifier: MPL-2.0
"""One blocking review-and-commit operation for the bundled MCP tool."""

import re
import shlex
import subprocess
from pathlib import Path

from . import backend, cancellation, prompts, state
from .git import git_head, run_commit, staged_files, staged_tree


_APPROVED = re.compile(r"^APPROVED\b")
_REJECTED = re.compile(r"\bREJECTED\b")
_SUGGESTIONS_MARKER = "SUGGESTIONS:"
# Recognize direct admissions that the audit input was not inspected, including the
# contradictory APPROVED/SUGGESTIONS response observed with a broken Linux sandbox.
# Anchor at a clause boundary: discussing a regression *about* this failure is not
# admitting it happened in this audit. This is a bounded prose guard, not evidence
# that every otherwise-approved review actually inspected its input.
# Negations are spelled out or contracted, with a straight or curly apostrophe.
_INSPECTION_FAILURE = re.compile(
    r"(?:^|[.;\n])\s*(?:\d+[.)]\s*)?(?:"
    r"(?:the\s+)?staged\s+(?:files?|changes?|diff|content|input|set|patch)\s+"
    r"(?:(?:could|can)\s*not\s+be|(?:could|can)n['’]t\s+be"
    r"|(?:was|were|is|are)\s+not|(?:was|were|is|are)n['’]t)\s+"
    r"(?:inspected|read|reviewed|accessed)\b"
    r"|(?:I\s+(?:could\s+not|couldn['’]t|cannot|can['’]t"
    r"|was\s+(?:unable|not\s+able)\s+to|wasn['’]t\s+able\s+to)|unable\s+to)\s+"
    r"(?:inspect|read|review|access)\s+(?:the\s+)?staged\s+"
    r"(?:files?|changes?|diff|content|input|set|patch)\b)",
    re.IGNORECASE,
)


def inspection_failure(output):
    text = str(output or "").strip()
    return bool(re.search(r"^\s*REVIEW_FAILED\s*:", text, re.IGNORECASE | re.MULTILINE)
                or _INSPECTION_FAILURE.search(text))


def _suggestion_starts(text):
    return [
        index
        for index, line in enumerate(str(text or "").splitlines())
        if line.strip().startswith(_SUGGESTIONS_MARKER)
    ]


def split_suggestions(output):
    """Split one unambiguous SUGGESTIONS block from the blocking verdict text.

    Ambiguous or malformed output stays wholly blocking: repeated markers, a missing
    verdict, or a marker without a section all return ``(text, None)``.
    """
    text = str(output or "").strip()
    lines = text.splitlines()
    starts = _suggestion_starts(text)
    if len(starts) != 1:
        return text, None
    index = starts[0]
    verdict = "\n".join(lines[:index]).strip()
    suggestions = "\n".join(lines[index:]).strip()
    suggestion_body = suggestions.split(":", 1)[1].strip()
    if not verdict or not suggestion_body:
        return text, None
    return verdict, suggestions


def approved(output):
    text = str(output or "").strip()
    if inspection_failure(text):
        return False
    verdict, suggestions = split_suggestions(text)
    if suggestions is None and _suggestion_starts(text):
        return False
    if _REJECTED.search(verdict.upper()):
        return False
    for line in verdict.splitlines():
        text = line.strip().upper()
        if text:
            return bool(_APPROVED.match(text))
    return False


DELTA_INLINE_MAX = 48000
DELTA_INLINE_TOTAL_MAX = 96000
_DISPOSITION_INSTRUCTION = (
    "Give every finding from your previous verdict an explicit disposition: fixed, not fixed, "
    "superseded, or WITHDRAWN because the finding was incorrect or rests on a fact you have not "
    "established and belongs in SUGGESTIONS:."
)


def _stage_snapshot(cwd):
    names_result = subprocess.run(
        ["git", "-C", str(cwd), "diff", "--cached", "--name-only", "-z"],
        capture_output=True,
    )
    if names_result.returncode != 0:
        return None
    names = [
        item.decode("utf-8", "surrogateescape")
        for item in names_result.stdout.split(b"\0")
        if item
    ]
    snapshot = {name: "-" for name in names}
    if not names:
        return snapshot
    index = subprocess.run(
        ["git", "-C", str(cwd), "ls-files", "--stage", "-z", "--"] + names,
        capture_output=True,
    )
    if index.returncode != 0:
        return None
    for record in index.stdout.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        parts = metadata.split()
        if len(parts) >= 2:
            snapshot[raw_name.decode("utf-8", "surrogateescape")] = b" ".join(parts[:2]).decode("ascii")
    return snapshot


def _delta(cwd, before_sha, path):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(before_sha or "")):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "diff", str(before_sha), ":" + path],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _change_block(cwd, session_id, reviewer_id, current):
    previous = state.read_commit_cursor(cwd, session_id)
    if not previous:
        return ""
    if previous.get("reviewer_id") != reviewer_id or previous.get("head") != git_head(cwd):
        return ""
    before = previous["files"]
    if any(value != "-" and not re.fullmatch(r"[0-7]{6} (?:[0-9a-f]{40}|[0-9a-f]{64})", str(value))
           for value in before.values()):
        return ""  # Content-only cursors cannot establish that modes were reviewed.
    changed = sorted(
        name for name, sha in current.items() if name in before and before[name] != sha
    )
    added = sorted(name for name in current if name not in before)
    removed = sorted(name for name in before if name not in current)
    unchanged = sorted(name for name, sha in current.items() if before.get(name) == sha)
    header = "STAGED SET vs YOUR LAST AUDIT (same reviewer session):"
    if not (changed or added or removed):
        return (
            header
            + " NOTHING MOVED. Every staged path has identical content and mode and HEAD is unchanged. "
            "Do not redo the audit. Carry forward your existing findings, retire only findings "
            "that no longer apply, and return the current verdict. "
            + _DISPOSITION_INSTRUCTION
            + "\n\n"
        )
    lines = [header]
    spent = 0
    for name in changed:
        lines.append("CHANGED: " + name)
        previous_identity = before[name].split()
        lines.append("Index identity: {} -> {}".format(before[name], current[name]))
        old_sha = previous_identity[-1]
        delta = _delta(cwd, old_sha, name)
        size = len((delta or "").encode("utf-8", "replace"))
        if delta and size <= DELTA_INLINE_MAX and spent + size <= DELTA_INLINE_TOTAL_MAX:
            lines += ["```diff", delta.rstrip(), "```"]
            spent += size
        else:
            lines.append(
                "Read the exact index delta with: git diff {} {}".format(
                    old_sha, shlex.quote(":" + name)
                )
            )
    lines += ["ADDED: " + name for name in added]
    lines += ["UNCHANGED: " + name for name in unchanged]
    lines += ["REMOVED: " + name for name in removed]
    lines.append(
        "Judge changed and added staged content now. Preserve findings on unchanged paths unless "
        "a new dependency invalidates them; drop findings on removed paths."
    )
    lines.append(_DISPOSITION_INSTRUCTION)
    return "\n".join(lines) + "\n\n"


def _result(status, message, **values):
    result = {"status": status, "message": str(message)}
    result.update(values)
    return result


def reviewed_commit(cwd, message, review_token, cancel_event=None):
    """Review the staged index, then commit it only after an APPROVED verdict."""
    cwd = Path(str(cwd or ""))
    message = str(message or "").strip()
    if not cwd.is_absolute():
        return _result("error", "cwd must be an absolute repository path")
    root = state.project_root(cwd)
    if not root:
        return _result("error", "cwd is not inside a Git repository")
    if not state.feature_enabled(root, "reviewer"):
        return _result("disabled", "reviewerAgent is not enabled for this repository")
    if not message:
        return _result("error", "commit message must not be empty")
    if not staged_files(root):
        return _result("error", "nothing is staged")

    ticket = state.claim_commit_ticket(review_token, root)
    if not ticket:
        return _result(
            "error",
            "review_token is missing, expired, already used, or for another repository; "
            "attempt shell git commit once to obtain a new token",
        )
    session_id = ticket["session_id"]
    options = ticket.get("commit_options", [])
    if not isinstance(options, list) or any(item not in ("--signoff", "--quiet") for item in options):
        return _result("error", "unsupported commit options in review ticket")
    try:
        return _review_staged(root, session_id, message, options, cancel_event)
    except (OSError, subprocess.SubprocessError) as error:
        return _result("error", str(error))


def _review_staged(root, session_id, message, options, cancel_event):
    reviewed_tree = staged_tree(root)
    reviewed_head = git_head(root)
    snapshot = _stage_snapshot(root)
    if not reviewed_tree or snapshot is None:
        return _result("error", "could not fingerprint the staged index")
    review_timeout = state.commit_review_timeout()
    retry_audit = state.commit_retry_pending(root, session_id)
    try:
        reviewer_id = state.read_reviewer_session(root, session_id, "reviewer")
        if reviewer_id:
            change_block = _change_block(root, session_id, reviewer_id, snapshot)
            prompt = change_block
        else:
            change_block = ""
            prompt = prompts.reviewer_brief(root, "reviewer") + "\n\n"
        # Surface the saved verdict on a retry even when the reviewer is FRESH: a rejection that
        # tripped the audit limit rotates the reviewer but preserves its verdict, so the retry's
        # new reviewer must still be pointed at those unresolved findings -- otherwise it cannot
        # see them and may approve them.
        prompt += prompts.commit_prompt(
            root, session_id, include_previous=bool(reviewer_id) or retry_audit
        )
        if cancel_event is None:
            code, output, stderr = backend.review_turn(
                root,
                session_id,
                "reviewer",
                "workspace-write",
                prompt,
                timeout=review_timeout,
            )
        else:
            code, output, stderr = backend.review_turn(
                root,
                session_id,
                "reviewer",
                "workspace-write",
                prompt,
                timeout=review_timeout,
                cancel_event=cancel_event,
            )
    except Exception as error:
        state.record_review_outcome(
            root, session_id, state.OUTCOME_FAILED,
            "the review raised before returning: {}".format(error),
        )
        return _result("error", "review failed unexpectedly: {}".format(error))

    if code == backend.CANCELLED_EXIT or (cancel_event is not None and cancel_event.is_set()):
        state.record_review_outcome(
            root, session_id, state.OUTCOME_CANCELLED,
            "the review was cancelled before it returned a verdict",
        )
        return _result("cancelled", "commit review was cancelled")
    if code == backend.TIMEOUT_EXIT:
        timed_out_reviewer = reviewer_id or state.read_reviewer_session(
            root, session_id, "reviewer"
        )
        retired = bool(
            timed_out_reviewer
            and state.retire_reviewer_session(
                root,
                session_id,
                "reviewer",
                expected_reviewer_id=timed_out_reviewer,
            )
        )
        if retired:
            state.clear_commit_cursor(
                root,
                session_id,
                expected_reviewer_id=timed_out_reviewer,
            )
        detail = stderr.strip() or "reviewer timed out"
        if state.model_rejection_detail(stderr):
            detail += "\n\n" + state.model_rejection_message(stderr)
        state.record_review_outcome(
            root, session_id, state.OUTCOME_TIMEOUT,
            "the review exceeded the {}s budget: {}".format(review_timeout, detail),
        )
        recovery = (
            "the timed-out reviewer session was retired; retry shell git commit once to "
            "start a fresh reviewer"
            if retired
            else "retry shell git commit once; a concurrent replacement reviewer was preserved"
        )
        return _result(
            "error",
            "review failed (exit {}): {}; {}".format(code, detail, recovery),
        )
    # Classify the REVIEW before the stage. A failed or empty review produced no ruling at all,
    # so if the staged set also moved, "the ruling judged a different set" would be a fiction --
    # DISCARDED is only ever for a usable verdict that could not be applied. Checking failure
    # first also gives the operator the actionable half: the reviewer's own error.
    if inspection_failure(output):
        detail = (
            "reviewer could not inspect the staged changes; restore reviewer access and "
            "retry the review. No commit was made."
        )
        state.record_review_outcome(
            root, session_id, state.OUTCOME_FAILED, detail, verdict=output,
        )
        return _result("error", detail, review=output.strip())
    if code != 0 or not output.strip():
        detail = stderr.strip() or "empty verdict"
        # A refused model or effort is a configuration failure with a specific remedy; reporting
        # it as a bare exit code leaves the operator with a permanently blocked repository and
        # no indication of what to change.
        diagnosis = (
            state.model_rejection_message(stderr)
            if state.model_rejection_detail(stderr) else ""
        )
        state.record_review_outcome(
            root, session_id, state.OUTCOME_FAILED,
            "exit {}: {}{}".format(code, detail, ("\n\n" + diagnosis) if diagnosis else ""),
        )
        if diagnosis:
            return _result("error", "review could not run. {}".format(diagnosis))
        return _result("error", "review failed (exit {}): {}".format(code, detail))
    if staged_tree(root) != reviewed_tree:
        state.record_review_outcome(
            root, session_id, state.OUTCOME_DISCARDED,
            "the staged set changed while it was being reviewed, so this ruling judged a "
            "different set than the one now staged and was not applied",
            verdict=output,
        )
        return _result(
            "error",
            "the staged set changed while it was being reviewed; retry the commit",
        )
    reviewer_id = state.read_reviewer_session(root, session_id, "reviewer")
    if not reviewer_id:
        state.record_review_outcome(
            root, session_id, state.OUTCOME_DISCARDED,
            "no reviewer thread was captured, so this ruling could not be attributed to a "
            "reviewer session and was not applied",
            verdict=output,
        )
        return _result("error", "reviewer startup failed: no reviewer thread was captured")

    review_path = state.save_review(root, session_id, output, history=True)
    state.write_commit_cursor(
        root,
        session_id,
        {"reviewer_id": reviewer_id, "head": reviewed_head, "files": snapshot},
    )
    is_approved = approved(output)
    automatically_rotated = False
    if not retry_audit:
        _, automatically_rotated = state.record_completed_reviewer_audit(
            root, session_id, reviewer_id, "reviewer"
        )
        if automatically_rotated:
            state.clear_commit_cursor(
                root, session_id, expected_reviewer_id=reviewer_id
            )
    if not is_approved:
        state.set_commit_retry_pending(root, session_id)
        return _result(
            "rejected",
            "the independent reviewer rejected the staged changes{}".format(
                "; its session was automatically rotated after the configured audit limit"
                if automatically_rotated else ""
            ),
            review=output.strip(),
            review_path=str(review_path),
        )

    state.clear_commit_retry_pending(root, session_id)

    # Compare immediately before ordinary Git. Repository hooks may intentionally
    # restage content afterwards; concurrent writers should use separate worktrees.
    if staged_tree(root) != reviewed_tree:
        return _result(
            "error", "the staged set changed after review; retry to review the current staged set",
            review=output.strip(), review_path=str(review_path),
        )
    status, published, git_output, detail = run_commit(root, message, options, cancel_event)
    if status != "cancelled":
        cancellation.complete(cancel_event, lambda: None)
    if status != "committed":
        return _result(status, detail, review=output.strip(), review_path=str(review_path),
                       git_output=git_output)
    return _result(
        "committed",
        "the staged changes were approved and committed{}{}".format(
            "; the reviewer session was automatically rotated after the configured audit limit"
            if automatically_rotated else "",
            "; " + detail if detail else "",
        ),
        review=output.strip(),
        review_path=str(review_path),
        commit=published,
        git_output=git_output.strip(),
        publication_warning=detail or None,
    )
