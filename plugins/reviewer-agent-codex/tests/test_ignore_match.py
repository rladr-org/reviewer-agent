# SPDX-License-Identifier: MPL-2.0
"""Tests for reviewer_ignore_match.IgnoreMatcher — the pure glob-matching primitive that
reviewer_ignore will use to decide which staged paths to skim. Pattern semantics are the
subset documented in docs/plans/reviewer_ignore_guidance.md 'Pattern syntax'.

The matcher is deliberately narrower than gitignore/pathspec: no negation, no escapes, no
embedded `**`. Every restriction is fail-closed with a PatternError carrying the pattern
and line number so a malformed reviewerAgent-ignore.md surfaces a useful reason to the
operator instead of silently mis-skimming a diff. See Principle 1 of the plan."""
import pytest

from reviewer_codex.ignore_match import IgnoreMatcher, PatternError


# --------------------------------------------------------------------------
# '*' glob — matches any run of characters within a single segment.
# --------------------------------------------------------------------------
def test_star_matches_within_segment():
    m = IgnoreMatcher([(1, "*.jsonl")])
    assert m.matches("x.jsonl")
    assert m.matches("abc.jsonl")
    assert not m.matches("x.txt")


def test_star_does_not_cross_slash():
    m = IgnoreMatcher([(1, "data/*.jsonl")])
    assert m.matches("data/x.jsonl")
    assert not m.matches("data/nested/x.jsonl")


# --------------------------------------------------------------------------
# '?' glob — matches exactly one character within a single segment.
# --------------------------------------------------------------------------
def test_question_mark_matches_exactly_one_char():
    m = IgnoreMatcher([(1, "a?c")])
    assert m.matches("abc")
    assert m.matches("axc")
    assert not m.matches("ac")     # too few
    assert not m.matches("abbc")   # too many


def test_question_mark_does_not_cross_slash():
    m = IgnoreMatcher([(1, "a?c")])
    assert not m.matches("a/c")


# --------------------------------------------------------------------------
# Character classes.
# --------------------------------------------------------------------------
def test_char_class_membership():
    m = IgnoreMatcher([(1, "file[123].txt")])
    assert m.matches("file1.txt")
    assert m.matches("file2.txt")
    assert not m.matches("file4.txt")
    assert not m.matches("filea.txt")


def test_char_range():
    m = IgnoreMatcher([(1, "file[a-z].txt")])
    assert m.matches("filea.txt")
    assert m.matches("filez.txt")
    assert not m.matches("fileA.txt")   # case-sensitive
    assert not m.matches("file1.txt")


# --------------------------------------------------------------------------
# '**' glob — zero-or-more full segments. Recognized ONLY as a standalone
# segment (a/**/b), leading segment (**/a), or trailing segment (a/**).
# --------------------------------------------------------------------------
def test_double_star_standalone_matches_zero_segments():
    """`data/**/results.txt` matches `data/results.txt` (zero segments between)."""
    m = IgnoreMatcher([(1, "data/**/results.txt")])
    assert m.matches("data/results.txt")


def test_double_star_standalone_matches_multiple_segments():
    m = IgnoreMatcher([(1, "data/**/results.txt")])
    assert m.matches("data/a/results.txt")
    assert m.matches("data/a/b/c/results.txt")


def test_leading_double_star_matches_at_any_depth_including_root():
    m = IgnoreMatcher([(1, "**/foo.txt")])
    assert m.matches("foo.txt")
    assert m.matches("a/foo.txt")
    assert m.matches("a/b/c/foo.txt")


def test_trailing_double_star_matches_everything_beneath():
    m = IgnoreMatcher([(1, "data/**")])
    assert m.matches("data/x.txt")
    assert m.matches("data/a/b/c.txt")
    assert not m.matches("other/x.txt")


def test_embedded_double_star_rejected():
    """`**foo`, `foo**`, `a/**foo/b` are not the recognized standalone/leading/trailing forms
    — v1 rejects them so unclear semantics can't slip in."""
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "**foo")])
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "foo**")])
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "a/**foo/b")])


# --------------------------------------------------------------------------
# Anchoring.
# --------------------------------------------------------------------------
def test_leading_slash_anchors_to_repo_root():
    m = IgnoreMatcher([(1, "/data/*.jsonl")])
    assert m.matches("data/x.jsonl")
    assert not m.matches("subdir/data/x.jsonl")


def test_unanchored_matches_at_any_depth():
    m = IgnoreMatcher([(1, "data/*.jsonl")])
    assert m.matches("data/x.jsonl")
    assert m.matches("subdir/data/x.jsonl")
    assert m.matches("a/b/data/x.jsonl")


# --------------------------------------------------------------------------
# Regex-metacharacter literalness — `.`, `(`, `+`, `{` must NOT act as regex.
# --------------------------------------------------------------------------
def test_dot_is_literal_in_pattern():
    m = IgnoreMatcher([(1, "file.txt")])
    assert m.matches("file.txt")
    assert not m.matches("fileXtxt")   # `.` in the pattern is literal, not a wildcard


def test_slash_structure_must_match():
    """`data/results.txt` matches only paths with the same slash structure."""
    m = IgnoreMatcher([(1, "data/results.txt")])
    assert m.matches("data/results.txt")
    assert not m.matches("data-results.txt")     # dash is not slash
    assert not m.matches("data/x/results.txt")   # extra segment


# --------------------------------------------------------------------------
# Comments, blanks, unions, empty matcher.
# --------------------------------------------------------------------------
def test_comment_and_blank_lines_ignored():
    m = IgnoreMatcher([
        (1, "# comment"),
        (2, ""),
        (3, "   "),
        (4, "*.jsonl"),
    ])
    assert m.matches("x.jsonl")


def test_multiple_patterns_are_a_union():
    m = IgnoreMatcher([(1, "*.jsonl"), (2, "*.csv")])
    assert m.matches("x.jsonl")
    assert m.matches("x.csv")
    assert not m.matches("x.txt")


def test_empty_matcher_matches_nothing():
    m = IgnoreMatcher([])
    assert not m.matches("x.txt")
    assert not m.matches("data/x.jsonl")


# --------------------------------------------------------------------------
# Unsupported syntax → fail-closed with actionable PatternError.
# --------------------------------------------------------------------------
def test_negation_pattern_rejected():
    with pytest.raises(PatternError) as ei:
        IgnoreMatcher([(1, "!*.jsonl")])
    txt = str(ei.value).lower()
    assert "!" in str(ei.value) or "negation" in txt


def test_backslash_escape_rejected():
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "path\\ with\\ spaces")])


def test_negated_character_class_rejected():
    """`[^a]` in a regex would match '/' and cross segment boundaries, silently mis-scoping
    the pattern. Reject explicitly rather than passing through."""
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "x[^a]y")])


def test_gitignore_style_negated_character_class_rejected():
    """`[!a]` is gitignore's negation-in-class syntax. Regex would treat it as a literal
    class matching '!', 'a' — surprising to gitignore users. Reject in v1."""
    with pytest.raises(PatternError):
        IgnoreMatcher([(1, "file[!abc].txt")])


def test_pattern_error_carries_lineno_and_pattern():
    with pytest.raises(PatternError) as ei:
        IgnoreMatcher([(42, "!bad")])
    assert ei.value.lineno == 42
    assert ei.value.pattern == "!bad"
    assert ei.value.reason      # non-empty explanation


def test_character_class_range_cannot_cross_a_path_separator():
    matcher = IgnoreMatcher([(1, "a[,-9]b")])
    assert matcher.matches("a.b")
    assert matcher.matches("a9b")
    assert not matcher.matches("a/b")
    assert not matcher.matches("nested/a/b")
