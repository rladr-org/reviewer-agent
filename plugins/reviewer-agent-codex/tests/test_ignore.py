# SPDX-License-Identifier: MPL-2.0
"""Tests for `parse_reviewer_ignore(proj, staged_paths) -> (matcher, prose, err)` — the
loader that reads `reviewerAgent-ignore.md` from the git index or HEAD, extracts
`reviewer-ignore` fenced blocks, and returns a self-exempting matcher plus prose for
Channel A. See docs/plans/reviewer_ignore_guidance.md."""
import os
import subprocess
from pathlib import Path

import pytest

from reviewer_codex import ignore as rl

IGNORE_NAME = "reviewerAgent-ignore.md"


def _git_env():
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "t")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@t")
    env.setdefault("GIT_COMMITTER_NAME", "t")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@t")
    return env


def git(proj, *args, check=True):
    return subprocess.run(
        ["git", "-C", proj, *args],
        check=check, capture_output=True, text=True, env=_git_env(),
    )


def make_repo(tmp_path):
    """Init a repo and land an initial empty commit so HEAD exists (the parser reads
    `git show HEAD:...` for the delayed-application case)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    git(str(proj), "init", "-q")
    git(str(proj), "commit", "--allow-empty", "-q", "-m", "init")
    return str(proj)


def write(proj, relpath, content):
    p = Path(proj) / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def stage(proj, *relpaths):
    git(proj, "add", "--", *relpaths)


def commit(proj, msg="c"):
    git(proj, "commit", "-q", "-m", msg)


WELL_FORMED = """\
# Skim these
Prose explaining why data files are skimmed.

```reviewer-ignore
data/**/*.jsonl
```
"""

MALFORMED = """\
# Bad
```reviewer-ignore
!oops-negation
```
"""


# --------------------------------------------------------------------------
# Absence / working-tree isolation.
# --------------------------------------------------------------------------
def test_missing_ignore_file_returns_empty_matcher_no_error(tmp_path):
    """No ignore file anywhere (not in HEAD, not in index, not on disk) → the parser
    returns an empty matcher and empty prose. This is the plan's baseline behavior."""
    proj = make_repo(tmp_path)
    matcher, prose, err = rl.parse_reviewer_ignore(proj, ["src/foo.py"])
    assert err is None
    assert prose == ""
    assert not matcher.matches("data/x.jsonl")
    assert not matcher.matches("src/foo.py")


def test_working_tree_only_file_is_ignored_not_used(tmp_path):
    """The parser MUST read from git objects, not the working tree. An unstaged working-tree
    copy cannot influence filtering — that would let an untracked file affect staged deltas."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, WELL_FORMED)  # on disk, but NOT `git add`ed
    matcher, prose, err = rl.parse_reviewer_ignore(proj, ["src/foo.py"])
    assert err is None
    assert prose == ""
    assert not matcher.matches("data/x.jsonl")


def test_untracked_file_never_added_is_ignored(tmp_path):
    """Same guarantee as above, worded as an explicit untracked-file test."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, WELL_FORMED)
    # Deliberately do NOT stage. `git status -s` would show `??`.
    matcher, prose, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert prose == ""


def test_working_tree_never_influences_matcher_when_ignore_file_is_staged(tmp_path):
    """When the ignore file is IN staged_paths, delayed application says the effective source
    is HEAD (empty here, since the initial commit had no ignore file). Regardless: a
    working-tree copy with pattern `src/**` must NOT leak into the matcher — proves the
    parser never reads from disk."""
    proj = make_repo(tmp_path)
    a = "```reviewer-ignore\ndata/**\n```\n"
    b = "```reviewer-ignore\nsrc/**\n```\n"
    write(proj, IGNORE_NAME, a)
    stage(proj, IGNORE_NAME)
    write(proj, IGNORE_NAME, b)  # working tree diverges from index
    matcher, prose, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME])
    assert err is None
    assert not matcher.matches("src/foo.py"), "working-tree patterns leaked into the matcher"


# --------------------------------------------------------------------------
# Well-formed staged file (no delayed-application concern — file is unchanged in this commit).
# --------------------------------------------------------------------------
def test_well_formed_committed_ignore_file_matches_and_rejects(tmp_path):
    """File committed to HEAD, not in staged_paths of this audit → use `:` (equals HEAD).
    Matcher accepts patterns from the fenced block; rejects unlisted paths."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, WELL_FORMED)
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, prose, err = rl.parse_reviewer_ignore(proj, ["src/foo.py"])
    assert err is None
    assert matcher.matches("data/a/x.jsonl")
    assert not matcher.matches("src/foo.py")


def test_multiple_fenced_blocks_are_union(tmp_path):
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, """\
```reviewer-ignore
data/**/*.jsonl
```

Intervening prose.

```reviewer-ignore
sources/*.txt
```
""")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, _, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert matcher.matches("data/x.jsonl")
    assert matcher.matches("sources/z.txt")


def test_non_reviewer_ignore_fenced_blocks_are_dropped(tmp_path):
    """A fence tagged with any other language is not a source of patterns."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, """\
```python
data/**  # this is python, not reviewer-ignore — must not become a pattern
```

```reviewer-ignore
sources/*.txt
```
""")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, _, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert not matcher.matches("data/x.jsonl"), "non-reviewer-ignore fence leaked as a pattern"
    assert matcher.matches("sources/z.txt")


def test_empty_file_returns_empty_matcher_no_error(tmp_path):
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, prose, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert prose == ""  # nothing to relay to Channel A
    assert not matcher.matches("anything.txt")


def test_prose_channel_a_carries_full_markdown(tmp_path):
    """Channel A gets the ENTIRE markdown file, prose and fences alike, so codex can read
    the operator's rationale as well as the patterns."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, WELL_FORMED)
    stage(proj, IGNORE_NAME); commit(proj)
    _, prose, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert prose == WELL_FORMED


# --------------------------------------------------------------------------
# Parse errors → fail-closed with actionable reason.
# --------------------------------------------------------------------------
def test_malformed_pattern_returns_actionable_error(tmp_path):
    """A `!negation` pattern is unsupported — parser returns non-None err naming the pattern
    and enough context for the operator to fix it."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, MALFORMED)
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, prose, err = rl.parse_reviewer_ignore(proj, [])
    assert err is not None
    assert "!oops-negation" in err or "!" in err
    # matcher may be None or empty; the point is the caller fails closed on the err.


def test_non_utf8_file_returns_error(tmp_path):
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, b"\xff\xfe not utf-8")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, prose, err = rl.parse_reviewer_ignore(proj, [])
    assert err is not None
    assert "utf" in err.lower() or "decode" in err.lower()


# --------------------------------------------------------------------------
# Self-exemption — Principle 3 of the plan.
# --------------------------------------------------------------------------
def test_self_exemption_wildcard_star_star(tmp_path):
    """Even a `**` catch-all must NOT hide the ignore file's own diff from review."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\n**\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, _, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert not matcher.matches(IGNORE_NAME), (
        "self-exemption failed: `**` matched the ignore file itself"
    )


def test_self_exemption_all_markdown(tmp_path):
    """A `*.md` or `**/*.md` pattern must not exempt the ROOT `reviewerAgent-ignore.md` from
    review. Self-exemption applies only to the root file — a nested `docs/reviewerAgent-ignore.md`
    would be a normal repo file with no special status, so an assertion about the nested path
    is deliberately out of scope here."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\n*.md\n**/*.md\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, _, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert not matcher.matches(IGNORE_NAME)


def test_self_exemption_explicit_name(tmp_path):
    """Explicitly naming the ignore file in a pattern still cannot exclude it."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\nreviewerAgent-ignore.md\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    matcher, _, err = rl.parse_reviewer_ignore(proj, [])
    assert err is None
    assert not matcher.matches(IGNORE_NAME)


# --------------------------------------------------------------------------
# Delayed application — Principle 4 of the plan.
# --------------------------------------------------------------------------
def test_delayed_application_not_staged_uses_index(tmp_path):
    """Ignore file NOT in staged_paths (unchanged this commit) → read from `:` (index),
    which equals HEAD for an unchanged file. A DIVERGENT working-tree copy (modified but
    not staged) must NOT influence the parser — an impl that reads disk instead of the
    index would silently pick up whatever an attacker wrote locally."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\ndata/**\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    # Working-tree copy diverges from the index (never `git add`ed). An impl reading the
    # working tree would see `src/**`; one reading `:` sees `data/**`.
    write(proj, IGNORE_NAME, "```reviewer-ignore\nsrc/**\n```\n")
    # Some OTHER change is staged this commit — the ignore file is untouched in the index.
    write(proj, "src/foo.py", "x = 1\n")
    stage(proj, "src/foo.py")
    matcher, _, err = rl.parse_reviewer_ignore(proj, ["src/foo.py"])
    assert err is None
    assert matcher.matches("data/x.jsonl"), "index patterns (data/**) should apply"
    assert not matcher.matches("src/foo.py"), (
        "working-tree patterns (src/**) leaked past the index"
    )


def test_delayed_application_staged_uses_head(tmp_path):
    """Ignore file IN staged_paths (changed this commit) → read from HEAD, not the staged
    version. New patterns take effect only on the NEXT commit — this guards the trojan-
    commit attack described in Principle 4."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\ndata/**\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    # Now the developer agent modifies the ignore file to also skim `src/**`, and stages
    # a change under src/ in the SAME commit.
    write(proj, IGNORE_NAME, "```reviewer-ignore\ndata/**\nsrc/**\n```\n")
    stage(proj, IGNORE_NAME)
    write(proj, "src/foo.py", "x = 1\n")
    stage(proj, "src/foo.py")
    matcher, _, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME, "src/foo.py"])
    assert err is None
    assert matcher.matches("data/x.jsonl"), "HEAD's data/** pattern should still apply"
    assert not matcher.matches("src/foo.py"), (
        "trojan attack: staged src/** applied to the commit that introduced it"
    )


def test_delayed_application_first_time_introduction_has_empty_filter(tmp_path):
    """No ignore file in HEAD + one being staged for the first time → HEAD-based effective
    filter is empty. Every staged path is fully reviewed on this audit; new patterns take
    effect on the NEXT commit."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\nsrc/**\n```\n")
    stage(proj, IGNORE_NAME)
    write(proj, "src/foo.py", "x = 1\n")
    stage(proj, "src/foo.py")
    matcher, _, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME, "src/foo.py"])
    assert err is None
    assert not matcher.matches("src/foo.py"), (
        "first-time introduction leaked staged patterns into THIS audit"
    )


def test_delayed_application_prose_comes_from_head_not_staged(tmp_path):
    """Channel A prose must come from HEAD when the ignore file is being changed —
    otherwise an attacker can supply "don't judge individual records" prose that weakens
    THIS audit."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "# OLD RULES\n\n```reviewer-ignore\ndata/**\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    write(proj, IGNORE_NAME, "# NEW-TROJAN PROSE (attacker rewrites)\n\n```reviewer-ignore\n"
                             "**\n```\n")
    stage(proj, IGNORE_NAME)
    _, prose, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME])
    assert err is None
    assert "OLD RULES" in prose
    assert "NEW-TROJAN PROSE" not in prose


def test_delayed_application_first_time_prose_is_empty(tmp_path):
    """No ignore file in HEAD + newly staged one → Channel A block is absent (matches the
    'no ignore file present' baseline for this audit)."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "# NEW PROSE\n\n```reviewer-ignore\ndata/**\n```\n")
    stage(proj, IGNORE_NAME)
    _, prose, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME])
    assert err is None
    assert prose == "", "first-time-intro Channel A block should be empty on THIS audit"


# --------------------------------------------------------------------------
# Staged validation — parse errors on the staged version fail the commit even when the
# staged version is not applied to this audit.
# --------------------------------------------------------------------------
def test_staged_validation_first_time_malformed_fails_closed(tmp_path):
    """No ignore file in HEAD + malformed one being staged for the first time → the
    effective source is empty (HEAD absent), but the STAGED candidate is malformed. Parse
    error blocks the commit so the operator learns at commit time."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, MALFORMED)
    stage(proj, IGNORE_NAME)
    _, _, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME])
    assert err is not None


def test_staged_validation_malformed_modification_fails_closed(tmp_path):
    """Well-formed in HEAD + malformed staged modification. Effective source (HEAD) parses
    fine, but staged validation catches the new breakage → error."""
    proj = make_repo(tmp_path)
    write(proj, IGNORE_NAME, "```reviewer-ignore\ndata/**\n```\n")
    stage(proj, IGNORE_NAME); commit(proj)
    write(proj, IGNORE_NAME, MALFORMED)
    stage(proj, IGNORE_NAME)
    _, _, err = rl.parse_reviewer_ignore(proj, [IGNORE_NAME])
    assert err is not None
