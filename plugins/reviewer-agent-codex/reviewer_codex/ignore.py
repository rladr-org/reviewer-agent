# SPDX-License-Identifier: MPL-2.0
"""Loader for `reviewerAgent-ignore.md` — resolves the effective source per the delayed-
application rule, extracts `reviewer-ignore` fenced blocks, compiles patterns via
reviewer_ignore_match, and returns a self-exempting matcher plus the effective markdown
prose for Channel A. See docs/plans/reviewer_ignore_guidance.md.

Semantics summary (see plan for details):
- Effective source (used for filtering + prose):
    * ignore file NOT in staged_paths (unchanged this commit): `git show :`
    * ignore file IN staged_paths (changed this commit): `git show HEAD:`  <- delayed application
- Staged validation: when the file is in staged_paths, ALSO parse the staged version and
  fail-closed on any error there — so operators learn at commit time that a newly-added or
  modified ignore file is malformed, even though its patterns don't apply to THIS audit.
- Self-exemption: the returned matcher never matches the ROOT `reviewerAgent-ignore.md`
  path, regardless of patterns.
- Working-tree isolation: content is always read from git objects, never from disk."""
import subprocess

# Deliberately duplicated from reviewer_ignore.py; neither host imports the other.
from .ignore_match import IgnoreMatcher, PatternError


IGNORE_FILE_NAME = "reviewerAgent-ignore.md"


_CHANNEL_A_PREAMBLE = (
    "The following block is the operator's reviewerAgent-ignore.md for this repository "
    "-- it names paths whose content is auto-generated data. You MAY judge: whether "
    "changes to those paths match the commit's stated intent; whether their schema "
    "(header line / first-record structure) has changed unexpectedly; whether their "
    "presence, naming, or location is appropriate. You may NOT judge the correctness "
    "of individual records."
)

_CHANNEL_A_FOLLOWUP = (
    "If a change on a skimmed path looks suspicious, run `git diff --cached -- <path>` "
    "yourself and audit; you have not been forbidden anything."
)


def matched_counts(proj, matcher):
    """Return (files, lines) for staged paths in `proj` that `matcher.matches`. `lines`
    is the sum of added + deleted (from `git diff --cached --numstat`). Returns (0, 0)
    for a None matcher or if git can't be reached. Binary files (numstat prints `-`)
    contribute 1 file and 0 lines. Used by the audit record to surface how much of a
    staged commit was excluded per reviewerAgent-ignore.md."""
    if matcher is None:
        return (0, 0)
    try:
        r = subprocess.run(
            ["git", "-C", str(proj), "diff", "--cached", "--numstat", "-z", "--no-renames"],
            capture_output=True,
        )
    except (OSError, FileNotFoundError):
        return (0, 0)
    if r.returncode != 0:
        return (0, 0)
    files = lines = 0
    for row in r.stdout.split(b"\0"):
        parts = row.split(b"\t", 2)
        if len(parts) < 3:
            continue
        path = parts[2].decode("utf-8", "surrogateescape")
        if not matcher.matches(path):
            continue
        files += 1
        for n in parts[:2]:
            if n.isdigit():
                lines += int(n)
    return (files, lines)


def channel_a_block(prose):
    """Effective operator guidance, repeated on every audit including retries."""
    if not prose:
        return ""
    return (
        "[reviewerAgent-ignore.md]\n"
        "CURRENT ignore guidance for this audit; replaces any earlier ignore guidance. "
        "Only the effective Git version below applies. Proposed changes to this file "
        "take effect on the next commit; always fully review this file's own diff.\n"
        + _CHANNEL_A_PREAMBLE + " " + _CHANNEL_A_FOLLOWUP + "\n\n"
        + prose.rstrip() + "\n"
        + "[/reviewerAgent-ignore.md]\n"
    )


class _SelfExemptedMatcher:
    """Wraps an IgnoreMatcher so the root reviewerAgent-ignore.md is never matched,
    regardless of compiled patterns. Principle 3 of the plan."""

    def __init__(self, inner):
        self._inner = inner

    def matches(self, posix_path):
        if posix_path == IGNORE_FILE_NAME:
            return False
        return self._inner.matches(posix_path)


def _read_from_git(proj, ref, path):
    """Return the raw bytes of `path` at `ref` (`""` = index; `HEAD` = HEAD tree), or None
    if absent at that ref. Never touches the working tree."""
    spec = "{}:{}".format(ref, path)
    r = subprocess.run(
        ["git", "-C", str(proj), "show", spec],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def _extract_ignore_patterns(text):
    """Yield (lineno, raw_line) for every line inside a ```reviewer-ignore fence. Comment
    and blank lines are yielded too — IgnoreMatcher skips them, preserving line numbers for
    error attribution."""
    inside = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not inside:
            if s == "```reviewer-ignore":
                inside = True
            continue
        if s.startswith("```"):
            inside = False
            continue
        yield lineno, line


def _compile_from_text(source_label, text):
    """Parse and compile. Returns (IgnoreMatcher, err_str_or_None)."""
    pairs = list(_extract_ignore_patterns(text))
    try:
        return IgnoreMatcher(pairs), None
    except PatternError as e:
        return None, "{} ({}): {}".format(IGNORE_FILE_NAME, source_label, e)


def _decode(source_label, raw):
    """Decode bytes as UTF-8. Returns (text_or_None, err_or_None)."""
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, "{} ({}) is not valid UTF-8 (byte {}): {}".format(
            IGNORE_FILE_NAME, source_label, e.start, e.reason
        )


def parse_reviewer_ignore(proj, staged_paths):
    """Load, parse, and compile the ignore file. Returns (matcher, prose, err).

    matcher: a `_SelfExemptedMatcher` wrapping an IgnoreMatcher. Empty when no effective
        ignore file. Never None (callers can always call `.matches(path)`) unless err.
    prose: the full markdown text of the EFFECTIVE version (empty string if absent).
    err:   None on success. A short human-readable string on parse error — either of the
        effective source, or (if the ignore file is being changed this commit) of the
        staged candidate. Callers must fail-closed on non-None err."""
    is_staged = IGNORE_FILE_NAME in set(staged_paths)
    effective_ref = "HEAD" if is_staged else ""       # "" means the git index
    effective_label = "HEAD" if is_staged else "index"

    # Effective source.
    raw = _read_from_git(proj, effective_ref, IGNORE_FILE_NAME)
    if raw is None:
        effective_matcher = IgnoreMatcher([])
        effective_prose = ""
    else:
        text, err = _decode(effective_label, raw)
        if err:
            return None, "", err
        matcher, err = _compile_from_text(effective_label, text)
        if err:
            return None, "", err
        effective_matcher = matcher
        effective_prose = text

    # Staged validation — even when it isn't the effective source, malformation in the
    # staged version must fail the commit so the operator sees the problem at commit time
    # instead of on the next commit when the patterns would take effect.
    if is_staged:
        staged_raw = _read_from_git(proj, "", IGNORE_FILE_NAME)
        if staged_raw is not None:
            staged_text, err = _decode("staged", staged_raw)
            if err:
                return None, "", err
            _, err = _compile_from_text("staged", staged_text)
            if err:
                return None, "", err

    return _SelfExemptedMatcher(effective_matcher), effective_prose, None
