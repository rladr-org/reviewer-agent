# SPDX-License-Identifier: MPL-2.0
"""In-repo minimal glob-matcher for `reviewerAgent-ignore.md` patterns.

Deliberately narrower than gitignore/pathspec so the plugin has no external dependency (see
docs/plans/reviewer_ignore_guidance.md 'Pattern syntax'). Roughly ~100 lines; the surface is
the two names below and the semantics documented here.

Supported:
    *           any run within one segment (no '/')
    **          zero-or-more full segments; ONLY as a standalone segment (a/**/b),
                leading segment (**/a), or trailing segment (a/**)
    ?           exactly one character within one segment
    [abc]/[a-z] character class within one segment
    /prefix     leading '/' anchors the pattern to the repo root
    trailing /  parsed (treated as `.../**`); rare in v1 since we match file paths
    # comments and blank lines are ignored

Not supported -- fail-closed with PatternError so an operator sees a useful reason:
    !pattern    negation
    \\x         backslash escapes
    **foo       embedded ** (anything other than a whole-segment form)
    [^abc]      negated character classes (would let '/' cross segment boundaries)
    [!abc]      gitignore-style negated classes (same reason)
"""
# Deliberately duplicated from reviewer_ignore_match.py: the Codex plugin ships independently.
import re


class PatternError(Exception):
    """A pattern uses unsupported or malformed syntax. Attributes: pattern, lineno, reason."""

    def __init__(self, pattern, lineno, reason):
        self.pattern = pattern
        self.lineno = lineno
        self.reason = reason
        super().__init__(
            "line {}: pattern {!r}: {}".format(lineno, pattern, reason)
        )


class IgnoreMatcher:
    """Compile a list of `(lineno, pattern_text)` pairs into a matcher over POSIX paths.

    Raises PatternError on the first unsupported or malformed pattern. Comment lines (`#...`)
    and blank lines are silently skipped. `matches(path)` returns True iff any compiled
    pattern matches `path` (union semantics; no negation)."""

    def __init__(self, patterns_with_lineno):
        self._regexes = []
        for lineno, raw in patterns_with_lineno:
            text = (raw or "").strip()
            if not text or text.startswith("#"):
                continue
            self._add(lineno, text)

    def _add(self, lineno, pattern):
        if pattern.startswith("!"):
            raise PatternError(
                pattern, lineno, "negation ('!') is not supported in v1"
            )
        if "\\" in pattern:
            raise PatternError(
                pattern, lineno,
                "backslash escapes are not supported in v1",
            )
        # Reject embedded ** — allow only whole-segment '**'.
        for seg in pattern.split("/"):
            if "**" in seg and seg != "**":
                raise PatternError(
                    pattern, lineno,
                    "'**' must be a whole segment; embedded uses like '**foo' or 'foo**' "
                    "are not supported in v1",
                )
        try:
            rx = _pattern_to_regex(pattern)
        except ValueError as e:
            raise PatternError(pattern, lineno, str(e))
        try:
            self._regexes.append(re.compile(rx))
        except re.error as e:
            raise PatternError(pattern, lineno, "invalid syntax: {}".format(e))

    def matches(self, posix_path):
        return any(rx.match(posix_path) for rx in self._regexes)


def _pattern_to_regex(pattern):
    """Anchored regex string for a supported pattern. Raises ValueError on malformed input."""
    anchored = pattern.startswith("/")
    p = pattern[1:] if anchored else pattern
    if p.endswith("/"):
        # Trailing '/': treat as `.../**` per plan.
        p = p + "**"
    segs = p.split("/")

    # Tokenize: each segment is either its translated regex or None (a '**' placeholder).
    tokens = []
    for seg in segs:
        if seg == "**":
            tokens.append(None)
        else:
            tokens.append(_translate_segment(seg))

    if not tokens or all(t is None for t in tokens):
        # bare `**` or empty pattern -> match everything
        body = ".*"
    else:
        parts = []
        prev_was_double_star = False
        for tok in tokens:
            if tok is None:
                prev_was_double_star = True
                continue
            if not parts:
                # First real segment.
                if prev_was_double_star:
                    # Leading ** collapses to "any depth prefix, ending in /" OR empty.
                    parts.append("(?:.*/)?")
                parts.append(tok)
            else:
                if prev_was_double_star:
                    # Middle '/**/': match zero-or-more full segments between the two.
                    parts.append("/(?:.*/)?")
                else:
                    parts.append("/")
                parts.append(tok)
            prev_was_double_star = False
        if prev_was_double_star:
            # Trailing '/**': match zero-or-more chars including nothing (as long as it starts
            # with '/'). Allow either a bare `a` or `a/anything`.
            parts.append("(?:/.*)?")
        body = "".join(parts)

    if anchored:
        return "^" + body + "$"
    # Unanchored: allow arbitrary preceding directories.
    return "^(?:.*/)?" + body + "$"


def _translate_segment(seg):
    """Translate a single glob segment (no '/') into a regex fragment matching within one
    segment. Regex metacharacters are escaped; only `*`, `?`, and `[...]` retain glob meaning."""
    out = []
    i = 0
    n = len(seg)
    while i < n:
        c = seg[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = seg.find("]", i + 1)
            if end < 0:
                raise ValueError(
                    "unclosed character class starting at position {} in segment {!r}".format(
                        i, seg
                    )
                )
            klass = seg[i:end + 1]
            # Negated character classes ('[^a]', gitignore's '[!a]') are not in v1's supported
            # set. Passing them through unchanged would let '/' match (since '[^a]' matches
            # ANY char that isn't 'a', including '/'), which silently violates the plan's
            # "character class within a single segment" rule and could exclude the wrong file
            # from review.
            if klass.startswith("[^") or klass.startswith("[!"):
                raise ValueError(
                    "negated character classes ('[^...]', '[!...]') are not supported in v1"
                )
            # Even a positive range can include '/', e.g. [,-9]. Keep all classes
            # within a segment without changing their other character matches.
            out.append("(?!/)" + klass)
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)
