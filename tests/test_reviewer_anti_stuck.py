# SPDX-License-Identifier: MPL-2.0
"""Anti-stuck-reviewer measures: reframe prior-verdict language so a persistent reviewer
does not anchor on its own prior verdict when the staged content HAS changed.

Failure mode observed 2026-08-28 (reviewer sid 01a04967): the reviewer produces
`DISPOSITION: not fixed` for each prior finding across multiple retries -- with the same
stale line-number ranges -- even though the file was substantially reorganised between
attempts and the inlined current bytes contradict the finding. Prompt already warns "your
earlier line numbers are STALE; cite only what you can see now" (reviewer_prompts.py:307-353),
but the framing ("your previous verdict") biases the model toward restating its prior text.

MEASURE 1 (this file): remove the first-person "your previous verdict" language from every
place the harness emits it, while KEEPING the pointer to the saved verdict file (which is
load-bearing after Claude-session compaction -- see reviewer_prompts.py:1498-1508). Reframe
prior findings as third-party concerns to verify against the current inlined content."""
import re
import subprocess
from pathlib import Path

import pytest

import reviewer_lib as rl


PROJ = "/Users/x/proj"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    return tmp_path


def _repo(path, brief=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    if brief is not None:
        (path / "reviewerAgent.md").write_text(brief, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Measure 1: standing brief reframing.
# --------------------------------------------------------------------------
def test_standing_brief_no_longer_says_your_previous_verdict():
    """The self-anchoring framing "your previous verdict" is the root cause of the observed
    stuck-loop failure: the model reads it as "defend what you said last time" rather than
    "verify against the current content." Reframe as third-party concerns."""
    brief = rl.REVIEWER_STANDING_BRIEF
    assert "your previous verdict" not in brief.lower(), (
        "Standing brief still contains self-anchoring 'your previous verdict' framing; "
        "reframe as 'earlier audit round' or equivalent third-party phrasing."
    )
    assert "your prior verdict" not in brief.lower(), (
        "Standing brief still contains self-anchoring 'your prior verdict' framing."
    )


def test_standing_brief_names_earlier_audit_rounds_as_third_party_concerns():
    """Positive assertion: the reframed brief must talk about prior findings as external
    claims to verify. Guards against the reframe accidentally removing the disposition
    instruction entirely -- the requirement to disposition each prior concern stays; only
    the ownership pronoun changes."""
    brief = rl.REVIEWER_STANDING_BRIEF.lower()
    assert "earlier audit" in brief, (
        "Standing brief lacks the 'earlier audit' reframing that replaces 'your previous "
        "verdict'."
    )
    # The disposition requirement itself must remain; only the ownership pronoun changed.
    assert "disposition" in brief


def test_standing_brief_still_requires_dispositions_for_every_prior_concern():
    """Regression: measure 1's reframe must not remove the requirement to disposition
    each prior concern. The vocabulary (fixed / not fixed / superseded / WITHDRAWN) is
    load-bearing for a re-briefed reviewer to signal what it did with prior findings."""
    low = rl.REVIEWER_STANDING_BRIEF.lower()
    # At least one of the disposition verbs must appear alongside the "disposition" word.
    assert "disposition" in low
    assert any(v in low for v in ("fixed", "not fixed", "superseded", "withdrawn"))


# --------------------------------------------------------------------------
# Measure 1: reviewer_capsule reframing while KEEPING the saved-verdict pointer.
# --------------------------------------------------------------------------
def test_commit_capsule_no_longer_says_your_previous_verdict(tmp_path):
    """The capsule reaches the reviewer on every turn (unlike the brief which is one-shot
    per session). The self-anchoring pronoun in the capsule's REPEAT tail is a per-turn
    reinforcement of the same failure mode. Reframe it too."""
    repo = _repo(tmp_path / "c1", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True).lower()
    assert "your previous verdict" not in text, (
        "reviewer_capsule (commit_audit=True) still contains 'your previous verdict'; "
        "reframe as third-party concerns."
    )
    assert "your prior verdict" not in text


def test_commit_capsule_reframes_prior_findings_as_earlier_audit_rounds(tmp_path):
    """Positive assertion: the capsule's disposition-requirement paragraph names 'earlier
    audit round' or equivalent third-party phrasing. Verifies the reframe reached the
    capsule too, not just the standing brief."""
    repo = _repo(tmp_path / "c2", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True).lower()
    assert "earlier audit" in text, (
        "reviewer_capsule (commit_audit=True) lacks the 'earlier audit' reframing."
    )
    # Disposition requirement itself must survive the reframe (matches
    # test_commit_capsule_carries_the_change_table_labels_and_disposition_rule).
    assert "disposition" in text


def test_commit_capsule_still_carries_the_saved_verdict_pointer_when_one_exists(tmp_path):
    """CRITICAL SAFEGUARD (finding #4 from the reviewer's own analysis of the design
    plan): the saved-verdict pointer at reviewer_prompts.py:1498-1508 is load-bearing
    after Claude-session compaction -- a compacted reviewer that lost its prior turns
    needs the pointer to recover the previous concerns before dispositioning them.
    Dropping the pointer entirely reintroduces the compacted-session-approves-without-
    addressing-blocker failure the pointer exists to prevent.

    Measure 1 REFRAMES the pointer's surrounding prose; it must NOT remove the pointer.
    """
    repo = _repo(tmp_path / "c3", "B\n")
    sid = "sess-anti-stuck-1"
    # Write a saved verdict on disk so the capsule has something to point to.
    saved = rl.last_review_file(str(repo), sid)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text("REJECTED:\n1. something\n", encoding="utf-8")

    text = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id=sid)
    assert str(saved) in text, (
        "reviewer_capsule dropped the saved-verdict pointer even when a saved verdict "
        "exists on disk; measure 1 was supposed to REFRAME the surrounding prose, not "
        "remove the pointer. See finding #4 in docs/plans/reviewer_anti_stuck.md."
    )


def test_commit_capsule_saved_verdict_pointer_prose_is_reframed(tmp_path):
    """The saved-verdict pointer stays, but the sentence around it must NOT use the
    self-anchoring 'previous verdict' phrasing. Reframe as 'earlier audit round' or
    equivalent so a compacted reviewer reading the pointer treats the saved file as
    external context to verify, not as its own conclusion to defend."""
    repo = _repo(tmp_path / "c4", "B\n")
    sid = "sess-anti-stuck-2"
    saved = rl.last_review_file(str(repo), sid)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text("REJECTED:\n1. something\n", encoding="utf-8")

    text = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id=sid)
    # Isolate the sentence(s) that mention the saved file.
    sentence_match = re.search(
        r"[^.\n]*" + re.escape(str(saved)) + r"[^.\n]*",
        text,
    )
    assert sentence_match is not None, "saved-verdict pointer sentence not found in capsule"
    sentence = sentence_match.group(0).lower()
    assert "previous commit-review verdict" not in sentence, (
        "The saved-verdict pointer sentence still uses the self-anchoring 'previous "
        "commit-review verdict' phrasing. Reframe it as 'earlier audit round' or "
        "equivalent while KEEPING the pointer path itself."
    )


# --------------------------------------------------------------------------
# Measure 1: staged_change_block re-review preamble reframing.
# --------------------------------------------------------------------------
def _stage_a_file(repo, path, content):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", path], check=True)


def _make_prior_audit(repo, sid, codex_sid, files_map):
    """Fake a prior audit record so staged_change_block returns the re-review preamble."""
    Path(repo).mkdir(parents=True, exist_ok=True)
    # Need at least one commit on HEAD so _head_sha returns a stable value.
    _stage_a_file(repo, "seed.txt", "seed\n")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "seed", "--no-verify"], check=True
    )
    # Stage the "current" files.
    for p, content in files_map.items():
        _stage_a_file(repo, p, content)
    # Write the audit record via the harness helper so shapes stay in sync.
    rl.record_audited_stage(str(repo), sid, codex_sid)


def test_staged_change_block_reframe_no_longer_says_your_earlier_findings(tmp_path):
    """The re-review preamble that ships with the inlined delta uses 'your earlier findings
    and line numbers for them are STALE' -- another self-anchoring pronoun the model reads
    as 'defend what you said last time'. Reframe as third-party concerns."""
    repo = _repo(tmp_path / "r1", "B\n")
    sid = "claude-sess-preamble-1"
    codex_sid = "codex-sess-preamble-1"
    # First stage & audit round: audit a file so the record exists.
    _make_prior_audit(str(repo), sid, codex_sid, {"src.py": "a = 1\n"})
    # Modify the same file so it counts as CHANGED on the next audit.
    _stage_a_file(str(repo), "src.py", "a = 1\nb = 2\n")

    block = rl.staged_change_block(str(repo), sid, codex_sid).lower()
    assert block, "staged_change_block returned empty; test setup failed to create a re-review"
    assert "your earlier findings" not in block, (
        "staged_change_block re-review preamble still uses 'your earlier findings'; "
        "reframe as 'earlier audit round' or equivalent third-party phrasing."
    )


def test_staged_change_block_reframe_names_earlier_audit_round(tmp_path):
    """Positive assertion for the staged_change_block preamble reframe."""
    repo = _repo(tmp_path / "r2", "B\n")
    sid = "claude-sess-preamble-2"
    codex_sid = "codex-sess-preamble-2"
    _make_prior_audit(str(repo), sid, codex_sid, {"src.py": "a = 1\n"})
    _stage_a_file(str(repo), "src.py", "a = 1\nb = 2\n")

    block = rl.staged_change_block(str(repo), sid, codex_sid).lower()
    assert "earlier audit" in block, (
        "staged_change_block re-review preamble lacks the 'earlier audit' reframing."
    )
    # The "line numbers are stale / out of date" warning must survive the reframe --
    # it is what stops the model citing prior line refs verbatim.
    assert ("stale" in block or "out of date" in block), (
        "staged_change_block preamble lost the line-numbers-are-stale warning."
    )


# --------------------------------------------------------------------------
# Measure 1: fallback branches (too-large diff, no-delta, no-command) must be reframed too.
# The `if inlined` branch is the common path, but the fallbacks are reached for oversized diffs,
# `REVIEWER_DELTA_INLINE_MAX=0`, and paths whose old blob was pruned. A reviewer hitting one of
# those branches on a re-attempt still needs the third-party framing.
# --------------------------------------------------------------------------
def test_too_large_diff_fallback_no_longer_says_your_earlier_findings(tmp_path, monkeypatch):
    """When the diff is over the per-path cap, the row falls back to a `git diff --cached ...`
    command and the block enters the `elif deltas` branch. That branch was NOT reframed by the
    initial anti-stuck edit and still emitted 'your earlier findings and line numbers ... STALE'.
    Fix must cover it."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "40")
    repo = _repo(tmp_path / "rfb1", "B\n")
    sid = "claude-sess-fb-1"
    codex_sid = "codex-sess-fb-1"
    _make_prior_audit(str(repo), sid, codex_sid, {"doc.md": "start\n"})
    _stage_a_file(
        str(repo), "doc.md", "".join("padding line {}\n".format(i) for i in range(200))
    )
    block = rl.staged_change_block(str(repo), sid, codex_sid).lower()
    assert "@@" not in block, "precondition: the diff should NOT have been inlined here"
    assert "your earlier findings" not in block, (
        "too-large-diff fallback still uses 'your earlier findings'; reframe it too."
    )
    assert "since your last audit" not in block, (
        "too-large-diff fallback still uses 'since your last audit'; reframe as "
        "'earlier audit round' or equivalent third-party phrasing."
    )


def test_no_delta_fallback_no_longer_says_your_earlier_findings(tmp_path, monkeypatch):
    """The `elif changed or added` branch (reached when no diff can be produced for a CHANGED
    or ADDED path -- an ADDED path has no old blob to diff against, so no delta command is
    generated for it; a CHANGED path with a pruned old blob reaches the branch too). Same
    reframe applies.

    An ADDED path forces this exact branch: `_delta_commands` yields nothing for a path with
    no prior version, so `deltas` is empty, `inlined` is empty, but `added` is non-empty ->
    the `elif changed or added` fallback at reviewer_prompts.py:1244 is what emits the prose.
    Using an ADDED path (not a CHANGED-with-max=1 setup, which produces a delta command and
    would exercise the `elif deltas` branch instead) is what pins the no-delta branch."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")
    repo = _repo(tmp_path / "rfb2", "B\n")
    sid = "claude-sess-fb-2"
    codex_sid = "codex-sess-fb-2"
    _make_prior_audit(str(repo), sid, codex_sid, {"doc.md": "start\n"})
    # ADDED path -- no earlier version, so no delta command is generated for it.
    _stage_a_file(str(repo), "brand_new.md", "entirely new file\n")
    block = rl.staged_change_block(str(repo), sid, codex_sid)
    low = block.lower()
    # Preconditions: this is the no-delta branch. There is an ADDED row, nothing inlined,
    # and no delta command was generated for the new path.
    assert "ADDED" in block, "precondition: no ADDED row present:\n" + block
    assert "@@" not in block, "precondition: nothing should have been inlined:\n" + block
    assert "diff shown below" not in low, "precondition: no path was inlined:\n" + block
    # And no `git diff --cached ...` command for the ADDED path (no old blob to diff).
    assert "git diff --cached" not in block, (
        "precondition: an ADDED path should not carry a delta command; this test then would "
        "be hitting the wrong branch:\n" + block
    )

    assert "your earlier findings" not in low, (
        "no-delta fallback still uses 'your earlier findings'."
    )
    assert "since your last audit" not in low, (
        "no-delta fallback still uses 'since your last audit'."
    )
    assert "earlier audit" in low, (
        "no-delta fallback lacks the positive 'earlier audit' reframing."
    )


def test_fallback_branches_still_carry_the_reattempt_and_ordering_signals(tmp_path, monkeypatch):
    """Regression: reframing the fallback branches must not remove the re-attempt framing or the
    BEFORE-you-form-a-judgement ordering directive that tests/test_rereview.py:713-820 pin. Both
    must survive under the new third-party language."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "1")
    repo = _repo(tmp_path / "rfb3", "B\n")
    sid = "claude-sess-fb-3"
    codex_sid = "codex-sess-fb-3"
    _make_prior_audit(str(repo), sid, codex_sid, {"doc.md": "start\n"})
    _stage_a_file(str(repo), "doc.md", "changed\n")
    block = rl.staged_change_block(str(repo), sid, codex_sid).lower()
    assert "re-attempt" in block or "reattempt" in block
    assert "before" in block
    # And the reframed language must be present too.
    assert "earlier audit" in block, (
        "fallback branch was reframed but lost the positive 'earlier audit' phrasing."
    )


# --------------------------------------------------------------------------
# Measure 1 (contradiction fix, finding #2 of the 2026-08-28 review): the mixed inline/omitted
# case. When some paths inline and others do not, the `if inlined` branch says "cite only line
# numbers you can see in the inlined content" while the `if not_shown` branch instructs a
# fetch-and-reread of the omitted paths. Read together, those tell the reviewer to fetch the
# omitted current content but forbid citing it in findings -- which suppresses valid findings
# for those paths. The line-numbers clause must refer to CURRENT STAGED CONTENT (inlined
# content OR content the reviewer just fetched via the required re-read), not to inlined
# content alone.
# --------------------------------------------------------------------------
def _make_mixed_repo(tmp_path, monkeypatch, name):
    """Build a re-review where some CHANGED paths inline and others fall back to a command.
    Achieved with delta_inline_max=200 and two paths whose deltas straddle the cap."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "200")
    repo = _repo(tmp_path / name, "B\n")
    sid = "claude-sess-mixed-" + name
    codex_sid = "codex-sess-mixed-" + name
    _make_prior_audit(
        str(repo), sid, codex_sid,
        {"small.py": "a = 1\n", "big.py": "a = 1\n"},
    )
    # small.py: tiny change (diff well under 200 bytes) -> should inline.
    _stage_a_file(str(repo), "small.py", "a = 2\n")
    # big.py: large change (diff will exceed 200 bytes) -> should NOT inline.
    _stage_a_file(
        str(repo), "big.py",
        "a = 1\n" + "".join("payload line {}\n".format(i) for i in range(80)),
    )
    return str(repo), sid, codex_sid


def test_mixed_inline_omitted_case_permits_findings_on_omitted_paths(
    tmp_path, monkeypatch
):
    """The contradiction to fix: the `if inlined` branch's 'cite only line numbers you can see
    in the inlined content' + the `if not_shown` branch's 'fetch and re-derive' can be read as
    'you must fetch this omitted content but must not cite it in findings'. That silently
    suppresses defects on omitted paths.

    The block must EITHER:
      (a) not carry the restrictive 'inlined content' phrasing when there are omitted paths,
      (b) OR name current staged content / fetched content as also-valid to cite from."""
    repo, sid, codex_sid = _make_mixed_repo(tmp_path, monkeypatch, "mx1")
    block = rl.staged_change_block(repo, sid, codex_sid)
    low = block.lower()

    # Precondition: this really is a mixed case -- one path inlined, one omitted.
    assert "diff shown below" in low, "precondition failed: no path was inlined"
    assert "big.py" in low
    # An omitted path is marked with a delta command (the fallback) or a reread route.
    assert ("git diff" in low and "big.py" in low) or "re-read" in low, (
        "precondition failed: no omitted-path fetch instruction present"
    )

    if "line numbers you can see in the inlined content" in low:
        # If the restrictive phrasing survives, it must be broadened to name current staged
        # content OR fetched content as also-valid to cite from -- else findings on omitted
        # paths are silently suppressed.
        expanded = (
            "or in the current staged content" in low
            or "or in content you re-read" in low
            or "or the content of any path you re-read" in low
            or "or the content fetched" in low
            or "current staged content" in low
        )
        assert expanded, (
            "the 'inlined content' restriction is not broadened; a reviewer reading this "
            "together with the fetch-and-reread instruction for omitted paths will conclude "
            "it must fetch those paths but must not cite line numbers from them, which "
            "silently suppresses valid findings. Broaden the phrasing to name current staged "
            "content / re-read content as also-valid citation sources.\n\nBlock:\n" + block
        )


def test_mixed_inline_omitted_case_does_not_forbid_citing_reread_content(
    tmp_path, monkeypatch
):
    """Explicit negative check: the block must not tell the reviewer to ONLY cite inlined
    content while simultaneously requiring fetch-and-reread of omitted paths. Guards against
    a fix that adds a broadening clause somewhere far from the restriction (still readable as
    contradictory)."""
    repo, sid, codex_sid = _make_mixed_repo(tmp_path, monkeypatch, "mx2")
    block = rl.staged_change_block(repo, sid, codex_sid)
    low = block.lower()

    # Precondition (same as above).
    assert "diff shown below" in low
    assert "big.py" in low

    # The word "only" adjacent to a citation restriction on inlined content is the danger sign.
    # Guard against a phrasing like "cite only line numbers you can see in the inlined content"
    # UNLESS the same sentence broadens to current staged / fetched content.
    for line in block.splitlines():
        low_line = line.lower()
        if "cite only" in low_line and "inlined content" in low_line:
            assert (
                "current staged content" in low_line
                or "re-read" in low_line
                or "fetched" in low_line
            ), (
                "sentence restricts citations to inlined content only, with no broadening in "
                "the same sentence -- reads as 'do not cite line numbers from omitted paths'. "
                "Line: " + repr(line)
            )
