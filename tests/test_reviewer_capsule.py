# SPDX-License-Identifier: MPL-2.0
"""The capsule: standing instructions repeated on EVERY reviewer invocation.

The composed brief is sent once, at session creation, and neither backend re-sends it -- so codex
compaction erodes it (session 019faaea reached 216 turns). The capsule is the durable copy, and it
carries the absolute path of the project brief so a compacted reviewer can re-read the detail.
"""
import subprocess
from pathlib import Path

import pytest

import reviewer_lib as rl

PROJ = "/Users/x/proj"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """AUTOUSE. Half the tests here did not request it, so `reviewer_home()` resolved to the
    operator's real ~/.claude/reviewer-agent -- harmless while these accessors only computed
    paths, and immediately visible as stray files there once one of them started writing."""
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    return tmp_path


def _repo(path, brief=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if brief is not None:
        (path / "reviewerAgent.md").write_text(brief, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------
def test_capsule_names_the_absolute_path_of_the_project_brief(tmp_path):
    repo = _repo(tmp_path / "c1", "B\n")
    text = rl.reviewer_capsule(str(repo))
    expected = str((repo / "reviewerAgent.md").resolve())
    assert expected in text


def test_capsule_resolves_the_brief_from_a_subdirectory(tmp_path):
    repo = _repo(tmp_path / "c2", "B\n")
    sub = repo / "x" / "y"
    sub.mkdir(parents=True)
    assert str((repo / "reviewerAgent.md").resolve()) in rl.reviewer_capsule(str(sub))


def test_capsule_names_no_path_when_the_project_has_no_brief(tmp_path):
    """Telling a reviewer to read a file that does not exist costs it a wasted tool call."""
    repo = _repo(tmp_path / "c3")
    text = rl.reviewer_capsule(str(repo))
    assert "reviewerAgent.md" not in text or "no project brief" in text.lower()
    assert str(repo / "reviewerAgent.md") not in text


def test_commit_capsule_carries_the_change_table_labels_and_disposition_rule(tmp_path):
    # Commit form only: the labels describe a block that exists on no other hook's turn.
    repo = _repo(tmp_path / "c4", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    for label in ("CHANGED", "ADDED", "UNCHANGED", "REMOVED"):
        assert label in text, label
    assert "disposition" in text.lower()


@pytest.mark.parametrize("backend", ["codex", "pi", "claude"])
def test_a_prior_finding_can_be_withdrawn_as_incorrect(tmp_path, monkeypatch, backend):
    """OBSERVED 2026-08-02. The dispositions were fixed / not fixed / superseded, and a
    reviewer whose finding was simply WRONG could reach none of them: nothing was fixed,
    nothing superseded it, so the only remaining slot was "not fixed" -- which blocks. Three
    retries produced three byte-identical rejections of a correct file, because the vocabulary
    had no way to say "I was mistaken".

    Compounding it, the UNCHANGED label tells the reviewer not to re-derive a byte-identical
    path, so on a file that needed no edit the finding could not be cleared by working on the
    code either. Both exits were closed at once."""
    monkeypatch.setenv("REVIEWER_BACKEND", backend)
    repo = _repo(tmp_path / ("c4w-" + backend), "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    low = text.lower()
    assert "withdrawn" in low
    # The disposition needs its TRIGGER stated, or "withdrawn" reads as a polite synonym for
    # "superseded" and the reviewer keeps choosing "not fixed".
    assert "incorrect" in low or "was wrong" in low
    # Unverifiable is not the same as disproved, and it needs the other exit: SUGGESTIONS.
    assert "suggestions" in low


def test_the_standard_is_fitness_for_use_in_both_forms(tmp_path):
    """REPLACES test_depth_is_preserved_in_both_forms, which asserted the opposite text.

    That test pinned "soften" -- from "never soften, drop, or defer a finding" -- and it was
    doing its job: the capsule really did say that. The operator's finding is that the
    sentence is the problem. Read as a standing instruction it rewards VOLUME, and since a
    blocking rejection is the only channel the output contract offers, every marginal doubt
    arrived as a required fix and the gated code grew hardening it did not need.

    So the assertion is inverted deliberately, not deleted: the capsule must now state the
    standard (fitness for the code's actual use) and must NOT carry the nitpick language.
    """
    repo = _repo(tmp_path / "c5", "B\n")
    for kwargs in ({}, {"commit_audit": True}):
        low = rl.reviewer_capsule(str(repo), **kwargs).lower()
        assert "circumstances it is used in" in low
        assert "extremely unlikely" in low
        assert "suggestions" in low
        assert "soften" not in low, "the nitpick language is back in the capsule"
        assert "depth is expected" not in low
    assert "staged" in rl.reviewer_capsule(str(repo), commit_audit=True).lower()


def test_the_reminder_is_authoritative_for_an_already_running_session(tmp_path):
    """A prompt change has to reach LIVE sessions, and only the capsule can do that.

    Neither backend re-sends the brief, so a session briefed before this change keeps the old
    "DEPTH IS EXPECTED / never soften" text until /reviewerSession rotate. The capsule is
    prepended on EVERY call, which is the whole point of the two-layer design -- but a
    reviewer holding both would be reading a contradiction, and nothing said which was
    current. So the reminder claims precedence explicitly.

    It also has to be SELF-SUFFICIENT: nothing in a per-call prompt may defer to the brief for
    the standard, because in a live session that is the stale copy. The commit prompts point
    at the reminder for exactly this reason -- see
    test_the_commit_prompts_point_at_the_reminder_not_the_brief."""
    repo = _repo(tmp_path / "c5c", "B\n")
    for kwargs in ({}, {"commit_audit": True}):
        text = rl.reviewer_capsule(str(repo), **kwargs)
        low = text.lower()
        assert "current: where it differs from anything sent earlier" in low
        assert "this wins" in low
        # Self-sufficient: the standard itself is present, not merely referenced.
        assert "CIRCUMSTANCES IT IS USED IN" in text


def test_the_capsule_does_not_license_a_full_re_audit_every_round(tmp_path):
    """The per-call layer must not contradict the brief on re-auditing.

    `_CAPSULE_COMMIT_TAIL` said "A change in one file can invalidate a conclusion you reached
    about another, so an UNCHANGED file is not automatically settled" -- UNCONDITIONAL, on
    every round, which is a standing licence to re-audit the whole staged set and defeats the
    CHANGED/UNCHANGED labels that exist to make that unnecessary. The brief now says the
    opposite, and the capsule is the layer that reaches a LIVE session, so the capsule was
    the one actually being obeyed.

    Removed rather than reworded: item 1 already says the unit is "the STAGED set, as a
    whole", which is the only case the sentence was doing real work in. The conditional
    re-open rule now sits on the UNCHANGED label itself, where the condition is already
    established -- see the next test."""
    repo = _repo(tmp_path / "c5d", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "not automatically settled" not in text
    assert "STAGED set, as a whole" in text


def test_the_unchanged_label_permits_a_justified_re_open(tmp_path):
    """Discouraging re-derivation must NOT discourage finding real defects.

    The rule trims re-deriving conclusions about bytes nothing has touched. A change that
    plausibly reaches an unchanged file -- a moved signature, contract, shared constant,
    error path, or data shape -- is a real lead and must still be followed, so the
    permission is stated explicitly rather than left as an exception the reviewer has to
    infer from a prohibition."""
    repo = _repo(tmp_path / "c5e", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "Re-open it" in text
    assert "signature" in text
    assert "re-deriving settled content, never following a real lead" in text.lower()


def test_the_capsule_never_turns_uncertainty_into_a_blocker(tmp_path):
    """The second clause the operator's finding indicts.

    "If you are unsure whether something is a defect, raise it" converts uncertainty
    straight into a REJECTION, because rejection is the only outlet the contract had.
    Uncertainty now belongs in SUGGESTIONS, so this instruction must not come back."""
    repo = _repo(tmp_path / "c5b", "B\n")
    for kwargs in ({}, {"commit_audit": True}):
        low = rl.reviewer_capsule(str(repo), **kwargs).lower()
        assert "if you are unsure" not in low
        assert "raise it and say you are unsure" not in low


def test_capsule_does_not_restate_the_verdict_contract(tmp_path):
    # Owned by COMMIT_PROMPT; two sources of truth for the parsed string is a defect.
    repo = _repo(tmp_path / "c6", "B\n")
    assert "REJECTED:" not in rl.reviewer_capsule(str(repo))


def test_capsule_stays_short(tmp_path):
    """Paid on every invocation, so a capsule that grows into a second brief defeats itself."""
    repo = _repo(tmp_path / "c7", "B\n")
    assert len(rl.reviewer_capsule(str(repo))) <= 2000


def test_capsule_has_no_emoji(tmp_path):
    repo = _repo(tmp_path / "c8", "B\n")
    for ch in rl.reviewer_capsule(str(repo)):
        assert ord(ch) < 0x2190, repr(ch)


# --------------------------------------------------------------------------
# delivery: apply_context is the one assembly point every hook and both backends share
# --------------------------------------------------------------------------
def test_apply_context_prepends_the_capsule_with_no_operator_context(tmp_path, home):
    repo = _repo(tmp_path / "d1", "B\n")
    out = rl.apply_context("TASK PROMPT", str(repo))
    assert out.startswith(rl.reviewer_capsule(str(repo)))
    assert out.endswith("TASK PROMPT")


def test_the_capsule_precedes_operator_directives(tmp_path, home):
    repo = _repo(tmp_path / "d2", "B\n")
    rl.add_directive(str(repo), "THE GPU RUN IS AUTHORIZED")
    out = rl.apply_context("TASK PROMPT", str(repo))
    assert "THE GPU RUN IS AUTHORIZED" in out
    assert out.index(rl.reviewer_capsule(str(repo))) < out.index("THE GPU RUN IS AUTHORIZED")
    assert out.endswith("TASK PROMPT")


def test_every_hook_path_receives_a_capsule(tmp_path, home):
    # commit / askq / stop all assemble through apply_context.
    repo = _repo(tmp_path / "d3", "B\n")
    assert rl.apply_context(rl.COMMIT_PROMPT, str(repo), commit_audit=True).startswith(
        rl.reviewer_capsule(str(repo), commit_audit=True))
    assert rl.apply_context(rl.STOP_SUPERVISE_PROMPT, str(repo)).startswith(
        rl.reviewer_capsule(str(repo)))


# --------------------------------------------------------------------------
# The staged-set instructions belong ONLY to the commit audit. apply_context also serves the
# question answerer and the stop supervisor, where there is no staged set and no audit block --
# telling those turns to "judge the STAGED set only" describes a thing that does not exist.
# --------------------------------------------------------------------------
def test_non_commit_capsule_says_nothing_about_a_staged_set(tmp_path):
    repo = _repo(tmp_path / "n1", "B\n")
    text = rl.reviewer_capsule(str(repo))
    low = text.lower()
    assert "staged" not in low
    assert "git diff --cached" not in low
    assert "STAGED SET vs YOUR LAST AUDIT" not in text
    assert "disposition" not in low


def test_non_commit_capsule_keeps_the_universal_parts(tmp_path):
    # "soften" used to stand in for "bullet 1 is present". The layer-separation intent is
    # unchanged; only the marker moved, because bullet 1 is now the fitness-for-use standard.
    repo = _repo(tmp_path / "n2", "B\n")
    text = rl.reviewer_capsule(str(repo))
    assert "CIRCUMSTANCES IT IS USED IN" in text
    assert str((repo / "reviewerAgent.md").resolve()) in text


def test_commit_capsule_says_how_to_SEE_the_staged_patch(tmp_path, monkeypatch):
    """Naming `git diff --cached` is not the same as telling the reviewer to run it."""
    repo = _repo(tmp_path / "n3", "B\n")
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "git diff --cached" in text
    assert "run" in text.lower()


def test_commit_capsule_tells_pi_to_use_git_read_since_it_has_no_shell(tmp_path, monkeypatch):
    # pi is hermetically read-only: no bash. Telling it to run a shell command is unactionable.
    repo = _repo(tmp_path / "n4", "B\n")
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "git_read" in text
    assert "git diff --cached" not in text


def test_commit_capsule_tells_codex_to_diff_a_changed_path_before_re_reading_it(
    tmp_path, monkeypatch
):
    # Item 4 previously ordered a full re-read of every CHANGED file, which is the duplicated
    # effort the whole annotation exists to remove.
    repo = _repo(tmp_path / "n8", "B\n")
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    low = text.lower()
    assert "git diff" in low
    assert "since your last audit" in low
    assert "git show :" in text  # the full read stays available as the fallback


def test_commit_capsule_gives_pi_no_delta_command(tmp_path, monkeypatch):
    """No command, because pi cannot run one -- but it is still given a SOURCE.

    The second assertion used to require the words "re-read the current staged content", which was
    the instruction pi got in place of a delta. Fix 4 inlines the delta for pi, so that sentence
    became a contradiction of the diff already in its prompt, and pinning it here would have held
    the defect in place. What the test is really about -- no command, and never left without a
    source -- is unchanged."""
    repo = _repo(tmp_path / "n9", "B\n")
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "git diff" not in text
    assert "staged patch" in text.lower()


def test_the_pi_capsule_does_not_contradict_the_diff_the_block_just_showed(tmp_path, monkeypatch):
    """This capsule is appended AFTER the change block, so its CHANGED branch is the LAST thing pi
    reads about a changed path -- and it must agree with what the block sent.

    It said "re-read the current staged content and derive your findings from it", which was right
    while pi got no delta and became a contradiction the moment one was inlined: pi reads a diff,
    then is told to go read the file instead. Worse, pi's only read tool returns the WORKING TREE,
    which item 3 of this same capsule forbids it from citing as staged content -- so the
    instruction pointed at the one source the capsule rules out."""
    repo = _repo(tmp_path / "n10", "B\n")
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)

    assert "re-read the current staged content" not in text.lower(), (
        "pi is sent back to re-read a file whose diff it has just been shown:\n" + text)
    assert "shows a diff" in text, (
        "pi's CHANGED branch never mentions the diff the block inlines for it:\n" + text)
    assert "staged patch" in text, (
        "pi is given no source for a path with no diff shown:\n" + text)


def test_commit_capsule_says_where_the_audit_block_is(tmp_path):
    # commit_hook prepends staged_change_block BEFORE apply_context, so the block is earlier in
    # the same message. Unstated, "if the request carries a block" is a riddle.
    repo = _repo(tmp_path / "n5", "B\n")
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert "earlier in this message" in text.lower()


@pytest.mark.parametrize("backend", ["codex", "pi", "claude"])
def test_both_capsule_forms_stay_short(tmp_path, home, monkeypatch, backend):
    # WORST-case CONTENT: the commit form grows by the prior-verdict pointer, which the previous
    # version of this test never created -- so it measured ~300 characters less than the real
    # thing. Absolute paths are subtracted, because they are unbounded input rather than prompt
    # text: what the ceiling exists to bound is the wording, so that the capsule cannot quietly
    # grow into a second brief.
    monkeypatch.setenv("REVIEWER_BACKEND", backend)
    repo = _repo(tmp_path / "n6", "B\n")
    sid = "9ca1d44b-d7c4-436b-b9d3-ff1e6c4a82e2"
    rl.save_review("PRIOR", proj=str(repo), claude_session_id=sid)
    brief = str((Path(repo) / "reviewerAgent.md").resolve())
    prior = str(rl.last_review_file(str(repo), sid))

    # MEASURED 2026-07-30, not estimated: universal 872 of 900 (both backends), commit 2606
    # codex / 2468 pi. The commit ceiling moved 2600 -> 2700 to accommodate those 6 characters;
    # the universal ceiling did NOT move.
    #
    # Two arithmetic slips are worth recording, because both came from measuring the wrong
    # thing. Bullet 1 went 303 -> 420 characters when it became the fitness-for-use standard,
    # and the prompt spec sized that against the universal form ALONE -- but the commit form is
    # the universal form PLUS layer 2, so it absorbed the same +117 and no one had checked it.
    # And the spec's "796" was the _CAPSULE_UNIVERSAL CONSTANT, whereas this test measures the
    # RENDERED capsule, which also carries bullet 3; the real figure was 892 of 900, i.e. eight
    # characters of headroom where the spec implied a hundred.
    #
    # That near-miss is why the precedence clause had to pay for itself: it was funded by
    # compressing the header from 151 to 138 characters (the old text explained that the brief
    # is sent once, which bullet 3 already implies), so the capsule now carries MORE meaning in
    # FEWER characters than before this change.
    # PER BACKEND since 2026-08-02, and that is the point rather than an implementation detail.
    # pi's CHANGED branch has to carry in words what codex carries as a command, so the two forms
    # now differ by 174 characters. One shared ceiling would have to be sized for pi and would
    # hand codex ~200 characters of unearned growth -- codex measures 2,690 against 2,700, so its
    # guard is 10 characters from biting and keeps its exact strength here.
    #
    # pi's 2,900 is a RAISE, not a measurement that drifted: a first cut of its branch was
    # compressed to fit 2,700, and that was reversed on the principle that a self-imposed budget
    # must not silently degrade the instruction it budgets for. MEASURED 2026-08-02: pi 2,864,
    # codex 2,690. Cut prompt wording only when the wording is wrong, never to fit this number.
    #
    # RAISED AGAIN 2026-08-02, +283 on both, for the WITHDRAWN disposition and its UNCHANGED
    # clause. Same principle, and this one was paid for by a deadlock rather than an estimate:
    # with only fixed / not fixed / superseded available, a reviewer whose finding was wrong
    # could reach no disposition that released the commit, and three retries produced three
    # byte-identical rejections of a correct file. Both exits were shut at once, since UNCHANGED
    # separately told it not to re-derive the path. 283 characters is what reopening them cost.
    # MEASURED, not estimated: codex 2,973 of 3,000 and pi 3,147 of 3,175, so both guards keep
    # roughly the ~10-30 characters of headroom they had before and neither backend is handed
    # room to drift into. Universal stayed at 872: the growth is commit-form only.
    #
    # RAISED AGAIN 2026-08-21, pi only, +125, and paid for by a live failure rather than an
    # estimate. pi's CHANGED branch told the reviewer to consult the full staged patch "only if"
    # the inline delta did not settle its findings -- while `run_pi_review` converts any verdict
    # reached without a successful staged `git_read` into a REJECTION. The release matrix hit
    # exactly that on its first retry: pi followed the instruction, skipped the read, and its
    # correct APPROVED became a synthetic rejection of a fixed file. The branch now requires the
    # staged read on every turn, which is the clause this raise buys. MEASURED after the change:
    # pi 3,256 of 3,300 and codex 2,966 of 3,000 -- codex is untouched and keeps its ~34
    # characters of headroom, pi keeps ~44.
    #
    # claude ADDED 2026-08-21 with its own ceiling, on the same principle that gave pi one: its
    # CHANGED and SEE branches say in words what codex says as a command, but it needs no
    # "you must still read the staged patch" clause -- the patch is in the prompt -- so it lands
    # between the two. MEASURED: claude 3,177 of 3,200, pi 3,263 of 3,300, codex 2,973 of 3,000.
    # Every guard keeps 20-40 characters of headroom, which is the point: none of the three has
    # room to drift into, and a wording change that needs more has to move a number deliberately.
    COMMIT_CEILING = {"codex": 3000, "pi": 3300, "claude": 3200}
    universal = rl.reviewer_capsule(str(repo), claude_session_id=sid)
    commit = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id=sid)
    assert len(universal) - len(brief) <= 900
    assert len(commit) - len(brief) - len(prior) <= COMMIT_CEILING[backend]


def test_a_long_project_path_only_costs_its_own_length(tmp_path, home, monkeypatch):
    # The paths are the one unbounded term, so pin how many times they can appear: a wording
    # change that interpolated one of them repeatedly would otherwise slip past the ceiling
    # above, which subtracts exactly one of each.
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    sid = "9ca1d44b-d7c4-436b-b9d3-ff1e6c4a82e2"
    short = _repo(tmp_path / "s", "B\n")
    deep = _repo(tmp_path.joinpath(*["a-reasonably-long-directory-name"] * 6), "B\n")
    for r in (short, deep):
        rl.save_review("PRIOR", proj=str(r), claude_session_id=sid)

    def sizes(r):
        cap = rl.reviewer_capsule(str(r), commit_audit=True, claude_session_id=sid)
        paths = len(str((Path(r) / "reviewerAgent.md").resolve())) + len(
            str(rl.last_review_file(str(r), sid)))
        return len(cap), paths

    short_len, short_paths = sizes(short)
    deep_len, deep_paths = sizes(deep)
    assert deep_len - short_len == deep_paths - short_paths


def test_commit_capsule_says_how_to_read_a_staged_file(tmp_path, monkeypatch):
    # Reading the working-tree copy can show unstaged edits that are NOT in the commit being
    # approved, so a finding cited from it may not exist in what the reviewer is gating.
    repo = _repo(tmp_path / "n7", "B\n")
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    assert "git show :" in rl.reviewer_capsule(str(repo), commit_audit=True)


# --------------------------------------------------------------------------
# "Restate your prior findings" is unactionable if the reviewer cannot recover them: on a
# compacted session its own earlier turn is gone. save_review() has them on disk, so the capsule
# names that file -- but only once the file is per-project, or it would hand the reviewer another
# repository's findings.
# --------------------------------------------------------------------------
def test_the_prior_verdict_file_is_per_project(tmp_path, home):
    r1 = _repo(tmp_path / "p1")
    r2 = _repo(tmp_path / "p2")
    rl.save_review("FINDINGS FOR ONE", proj=str(r1), claude_session_id="s")
    rl.save_review("FINDINGS FOR TWO", proj=str(r2), claude_session_id="s")
    assert rl.last_review_file(str(r1), "s") != rl.last_review_file(str(r2), "s")
    assert rl.last_review_file(str(r1), "s").read_text(encoding="utf-8") == "FINDINGS FOR ONE"
    assert rl.last_review_file(str(r2), "s").read_text(encoding="utf-8") == "FINDINGS FOR TWO"


def test_the_prior_verdict_file_is_per_session_within_one_project(tmp_path, home):
    # Two Claude sessions committing in the SAME repo each hold their own reviewer session and
    # therefore their own findings. Sharing one file lets session B's verdict overwrite session
    # A's, and the capsule then hands A a verdict A never gave -- the same cross-contamination
    # audited_stage_file is session-scoped to avoid.
    repo = _repo(tmp_path / "p5", "B\n")
    rl.save_review("FINDINGS FROM SESSION A", proj=str(repo), claude_session_id="sid-a")
    rl.save_review("FINDINGS FROM SESSION B", proj=str(repo), claude_session_id="sid-b")
    a = rl.last_review_file(str(repo), "sid-a")
    b = rl.last_review_file(str(repo), "sid-b")
    assert a != b
    assert a.read_text(encoding="utf-8") == "FINDINGS FROM SESSION A"
    assert b.read_text(encoding="utf-8") == "FINDINGS FROM SESSION B"


def test_the_capsule_never_names_another_sessions_verdict(tmp_path, home):
    repo = _repo(tmp_path / "p6", "B\n")
    rl.save_review("FINDINGS FROM SESSION A", proj=str(repo), claude_session_id="sid-a")
    text = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id="sid-b")
    assert str(rl.last_review_file(str(repo), "sid-a")) not in text


def test_an_unidentifiable_session_still_gets_a_project_scoped_file(tmp_path, home):
    # required_fixes_message must always have a real path to report -- losing the full findings
    # list is worse than a coarse scope. But it is still per PROJECT, never global.
    repo = _repo(tmp_path / "p7", "B\n")
    p = rl.last_review_file(str(repo), None)
    assert p != rl.last_review_file(str(repo), "sid-a")
    assert str(rl.reviewer_home()) in str(p)
    assert str(repo) not in str(p.parent)  # never inside the reviewed repository
    rl.save_review("ORPHAN", proj=str(repo))
    assert p.read_text(encoding="utf-8") == "ORPHAN"


def test_the_capsule_does_not_claim_an_unattributable_file_as_your_verdict(tmp_path, home):
    # Written and reported, but not presented to the reviewer as findings it gave.
    repo = _repo(tmp_path / "p10", "B\n")
    rl.save_review("ORPHAN", proj=str(repo))
    text = rl.reviewer_capsule(str(repo), commit_audit=True)
    assert str(rl.last_review_file(str(repo), None)) not in text


def test_commit_capsule_names_the_prior_verdict_only_once_one_exists(tmp_path, home):
    repo = _repo(tmp_path / "p3", "B\n")
    first = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id="s1")
    assert str(rl.last_review_file(str(repo), "s1")) not in first
    rl.save_review("EARLIER FINDINGS", proj=str(repo), claude_session_id="s1")
    second = rl.reviewer_capsule(str(repo), commit_audit=True, claude_session_id="s1")
    assert str(rl.last_review_file(str(repo), "s1")) in second


def test_apply_context_passes_the_session_through_to_the_capsule(tmp_path, home):
    repo = _repo(tmp_path / "p8", "B\n")
    rl.save_review("EARLIER FINDINGS", proj=str(repo), claude_session_id="s2")
    out = rl.apply_context("TASK", str(repo), claude_session_id="s2", commit_audit=True)
    assert str(rl.last_review_file(str(repo), "s2")) in out


def test_rotating_a_session_clears_its_prior_verdict(tmp_path, home):
    # A rotated reviewer never produced those findings, so "your previous verdict" would be a
    # lie -- and staged_change_block already goes silent for the same reason when the codex id
    # differs.
    repo = _repo(tmp_path / "p9", "B\n")
    rl.save_review("EARLIER FINDINGS", proj=str(repo), claude_session_id="s3")
    f = rl.last_review_file(str(repo), "s3")
    assert f.exists()
    cleared = rl.rotate_session(str(repo), "s3")
    assert not f.exists()
    assert str(f) in cleared


def test_a_deep_project_path_still_gets_a_writable_filename(tmp_path, home):
    # A project path is unbounded; a filename component is capped at 255 bytes. Every writer
    # keyed this way swallows OSError, so an over-long name meant the verdict was silently never
    # written and the capsule silently never named it -- no error anywhere.
    deep = tmp_path.joinpath(*["a-reasonably-long-directory-name"] * 8)
    repo = _repo(deep, "B\n")
    sid = "9ca1d44b-d7c4-436b-b9d3-ff1e6c4a82e2"
    p = rl.last_review_file(str(repo), sid)
    assert len(p.name.encode("utf-8")) <= 255
    rl.save_review("DEEP", proj=str(repo), claude_session_id=sid, history=True)
    assert p.read_text(encoding="utf-8") == "DEEP"
    assert [q.read_text(encoding="utf-8") for q in rl.review_history(str(repo))] == ["DEEP"]
    assert str(rl.last_review_file(str(repo), sid)) in rl.reviewer_capsule(
        str(repo), commit_audit=True, claude_session_id=sid)


def test_two_deep_projects_sharing_a_prefix_do_not_collide(tmp_path, home):
    base = tmp_path.joinpath(*["a-reasonably-long-directory-name"] * 8)
    one = _repo(base / "alpha", "B\n")
    two = _repo(base / "beta", "B\n")
    assert rl.last_review_file(str(one), "s") != rl.last_review_file(str(two), "s")
    assert rl.review_history_dir(str(one)) != rl.review_history_dir(str(two))
    assert rl.audited_stage_file(str(one), "s") != rl.audited_stage_file(str(two), "s")


def test_a_space_and_an_underscore_are_not_the_same_project(tmp_path, home):
    # The sanitizer maps BOTH "/" and " " to "_", so "a b/repo" and "a_b/repo" collapsed to one
    # name -- two different repositories sharing a saved verdict, a history and an audited-stage
    # record. Short paths are the common case, so this must not depend on truncation.
    one = _repo(tmp_path / "a b" / "repo", "B\n")
    two = _repo(tmp_path / "a_b" / "repo", "B\n")
    assert rl.last_review_file(str(one), "s") != rl.last_review_file(str(two), "s")
    assert rl.review_history_dir(str(one)) != rl.review_history_dir(str(two))
    assert rl.audited_stage_file(str(one), "s") != rl.audited_stage_file(str(two), "s")

    rl.save_review("ONE", proj=str(one), claude_session_id="s", history=True)
    rl.save_review("TWO", proj=str(two), claude_session_id="s", history=True)
    assert rl.last_review_file(str(one), "s").read_text(encoding="utf-8") == "ONE"
    assert rl.last_review_file(str(two), "s").read_text(encoding="utf-8") == "TWO"


def test_control_files_do_not_collide_between_a_space_and_an_underscore(tmp_path, home):
    # The existence of these files IS the setting, so a shared name meant /reviewerAgent on in
    # one repo silently turned it on in the other.
    one = _repo(tmp_path / "c b" / "repo", "B\n")
    two = _repo(tmp_path / "c_b" / "repo", "B\n")
    for f in (rl.flag_file, rl.project_backend_file, rl.stopcontinue_flag_file,
              rl.directives_file):
        assert f(str(one)) != f(str(two)), f.__name__

    rl.set_active(str(one))
    assert rl.is_active(str(one))
    assert not rl.is_active(str(two))


def test_legacy_control_state_is_adopted_under_the_new_name(tmp_path, home):
    # Renaming the file must not silently flip a setting off: whatever the operator had set
    # under the old stem has to keep applying.
    repo = _repo(tmp_path / "legacy_adopt", "B\n")
    key = rl.canonical_project_key(str(repo))
    state = rl.reviewer_home() / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / (rl._sanitize(key) + ".active")).write_text("", encoding="utf-8")
    (state / (rl._sanitize(key) + ".stopcontinue.active")).write_text("", encoding="utf-8")
    (state / (rl._sanitize(key) + ".backend")).write_text("pi\n", encoding="utf-8")

    assert rl.is_active(str(repo))
    assert rl.is_stopcontinue_active(str(repo))
    assert rl.reviewer_backend(str(repo)) == "pi"


def test_two_projects_sharing_one_legacy_file_both_keep_their_setting(tmp_path, home):
    # They were sharing it, so both behaved as ON. Moving the file would turn one of them off
    # with no message; copying preserves what each was observably doing.
    one = _repo(tmp_path / "d b" / "repo", "B\n")
    two = _repo(tmp_path / "d_b" / "repo", "B\n")
    state = rl.reviewer_home() / "state"
    state.mkdir(parents=True, exist_ok=True)
    legacy = rl._sanitize(rl.canonical_project_key(str(one)))
    assert legacy == rl._sanitize(rl.canonical_project_key(str(two)))
    (state / (legacy + ".active")).write_text("", encoding="utf-8")

    assert rl.is_active(str(one))
    assert rl.is_active(str(two))


def test_turning_the_reviewer_off_is_not_undone_by_the_legacy_file(tmp_path, home):
    # Without an exactly-once guard, /reviewerAgent off deletes the new file and the next call
    # resurrects it from the legacy copy -- the toggle would look broken.
    repo = _repo(tmp_path / "legacy_off", "B\n")
    state = rl.reviewer_home() / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / (rl._sanitize(rl.canonical_project_key(str(repo))) + ".active")).write_text(
        "", encoding="utf-8")
    assert rl.is_active(str(repo))

    rl.clear_active(str(repo))
    assert not rl.is_active(str(repo))
    assert not rl.is_active(str(repo))  # and it stays off


def test_asking_for_a_control_path_writes_nothing_when_there_is_nothing_to_migrate(
    tmp_path, home
):
    # A path accessor that writes on every call is a surprise in itself: is_active() runs on
    # EVERY Bash tool call, so an unconditional sentinel would litter the state directory with
    # an entry for every project ever inspected. Caught for real -- pytest tmp paths turned up
    # in the operator's live state directory.
    repo = _repo(tmp_path / "no_legacy", "B\n")
    state = rl.reviewer_home() / "state"
    state.mkdir(parents=True, exist_ok=True)
    before = sorted(p.name for p in state.iterdir())
    for f in (rl.flag_file, rl.project_backend_file, rl.stopcontinue_flag_file,
              rl.directives_file):
        f(str(repo))
    assert sorted(p.name for p in state.iterdir()) == before


def test_a_multibyte_project_path_stays_within_the_byte_limit(tmp_path, home):
    # The limit is 255 BYTES per filename component, not characters. Bounding on len() let a
    # non-ASCII path overflow, and every writer keyed this way swallows OSError -- so the
    # verdict silently vanished with no error anywhere.
    deep = tmp_path.joinpath(*["ディレクトリ名前"] * 12)
    repo = _repo(deep, "B\n")
    sid = "9ca1d44b-d7c4-436b-b9d3-ff1e6c4a82e2"
    for p in (rl.last_review_file(str(repo), sid), rl.audited_stage_file(str(repo), sid),
              rl.review_history_dir(str(repo))):
        assert len(p.name.encode("utf-8")) <= 255, p.name

    rl.save_review("MULTIBYTE", proj=str(repo), claude_session_id=sid, history=True)
    assert rl.last_review_file(str(repo), sid).read_text(encoding="utf-8") == "MULTIBYTE"
    assert [q.read_text(encoding="utf-8")
            for q in rl.review_history(str(repo))] == ["MULTIBYTE"]


def test_save_review_reports_the_scoped_path_not_a_shared_one(tmp_path, home):
    # The single global reviews/last_commit_review.txt is GONE: it was overwritten by every
    # review in every repo and every concurrent session, and required_fixes_message reports it
    # to the agent -- so one agent could be handed another's findings.
    repo = _repo(tmp_path / "p4")
    p = rl.save_review("X", proj=str(repo), claude_session_id="s")
    assert Path(p) == rl.last_review_file(str(repo), "s")
    assert Path(p).read_text(encoding="utf-8") == "X"
    assert not (rl.reviewer_home() / "reviews" / "last_commit_review.txt").exists()


# --------------------------------------------------------------------------
# History: per PROJECT, because a review converges across attempts and attempts span sessions.
# --------------------------------------------------------------------------
def test_history_accumulates_per_project_across_sessions(tmp_path, home):
    repo = _repo(tmp_path / "h1", "B\n")
    other = _repo(tmp_path / "h2", "B\n")
    rl.save_review("ROUND ONE", proj=str(repo), claude_session_id="sid-a", history=True)
    rl.save_review("ROUND TWO", proj=str(repo), claude_session_id="sid-b", history=True)
    rl.save_review("ELSEWHERE", proj=str(other), claude_session_id="sid-a", history=True)

    entries = rl.review_history(str(repo))
    assert [p.read_text(encoding="utf-8") for p in entries] == ["ROUND ONE", "ROUND TWO"]
    assert [p.read_text(encoding="utf-8") for p in rl.review_history(str(other))] == ["ELSEWHERE"]


def test_history_is_not_written_unless_asked(tmp_path, home):
    repo = _repo(tmp_path / "h3", "B\n")
    rl.save_review("NO HISTORY", proj=str(repo), claude_session_id="sid-a")
    assert rl.review_history(str(repo)) == []


def test_history_is_capped_oldest_first(tmp_path, home):
    repo = _repo(tmp_path / "h4", "B\n")
    for i in range(rl.REVIEW_HISTORY_KEEP + 5):
        rl.save_review("VERDICT {}".format(i), proj=str(repo),
                       claude_session_id="sid-{}".format(i), history=True)
    entries = rl.review_history(str(repo))
    assert len(entries) == rl.REVIEW_HISTORY_KEEP
    texts = [p.read_text(encoding="utf-8") for p in entries]
    assert texts[0] == "VERDICT 5"
    assert texts[-1] == "VERDICT {}".format(rl.REVIEW_HISTORY_KEEP + 4)


def test_history_retention_is_operator_configurable(tmp_path, home, monkeypatch):
    # 100 is a guess about how long a convergence runs, and this corpus is the input to the
    # model comparison -- how much of it survives is the operator's call, not the library's.
    repo = _repo(tmp_path / "h7", "B\n")
    monkeypatch.setenv("REVIEWER_REVIEW_HISTORY_KEEP", "3")
    for i in range(6):
        rl.save_review("V{}".format(i), proj=str(repo), claude_session_id="s", history=True)
    assert [p.read_text(encoding="utf-8")
            for p in rl.review_history(str(repo))] == ["V3", "V4", "V5"]


def test_history_retention_defaults_and_refuses_a_zero_cap(tmp_path, home, monkeypatch):
    monkeypatch.delenv("REVIEWER_REVIEW_HISTORY_KEEP", raising=False)
    assert rl.review_history_keep() == rl.REVIEW_HISTORY_KEEP
    # A cap of 0 would delete the verdict written in the same call, so the floor is 1.
    monkeypatch.setenv("REVIEWER_REVIEW_HISTORY_KEEP", "0")
    assert rl.review_history_keep() == 1
    monkeypatch.setenv("REVIEWER_REVIEW_HISTORY_KEEP", "not-a-number")
    assert rl.review_history_keep() == rl.REVIEW_HISTORY_KEEP


def test_history_retention_can_be_raised_from_the_config_file(tmp_path, home, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"reviewHistoryKeep": 250}', encoding="utf-8")
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("REVIEWER_REVIEW_HISTORY_KEEP", raising=False)
    assert rl.review_history_keep() == 250


def test_history_retention_is_read_at_call_time_not_import_time(tmp_path, home, monkeypatch):
    # Every hook is a fresh process, so an import-time capture would look correct in production
    # and only ever be wrong under test -- the worst way for a knob to be broken.
    repo = _repo(tmp_path / "h8", "B\n")
    monkeypatch.setenv("REVIEWER_REVIEW_HISTORY_KEEP", "2")
    for i in range(4):
        rl.save_review("A{}".format(i), proj=str(repo), claude_session_id="s", history=True)
    assert len(rl.review_history(str(repo))) == 2
    monkeypatch.setenv("REVIEWER_REVIEW_HISTORY_KEEP", "5")
    rl.save_review("A4", proj=str(repo), claude_session_id="s", history=True)
    assert len(rl.review_history(str(repo))) == 3  # nothing pruned under the raised cap


def test_rotation_clears_the_current_verdict_but_keeps_history(tmp_path, home):
    # History is a record, not reviewer state.
    repo = _repo(tmp_path / "h5", "B\n")
    rl.save_review("ROUND ONE", proj=str(repo), claude_session_id="sid-a", history=True)
    rl.rotate_session(str(repo), "sid-a")
    assert not rl.last_review_file(str(repo), "sid-a").exists()
    assert len(rl.review_history(str(repo))) == 1


def test_no_review_file_is_written_inside_the_reviewed_repository(tmp_path, home):
    repo = _repo(tmp_path / "h6", "B\n")
    before = sorted(p.name for p in Path(repo).iterdir())
    rl.save_review("X", proj=str(repo), claude_session_id="sid-a", history=True)
    assert sorted(p.name for p in Path(repo).iterdir()) == before
