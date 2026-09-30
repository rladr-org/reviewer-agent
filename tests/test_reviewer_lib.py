# SPDX-License-Identifier: MPL-2.0
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import reviewer_lib as rl
import reviewer_stopcontinue

PROJ = "/Users/x/proj"

FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


def _make_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (Path(path) / "seed").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "seed"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


# --------------------------------------------------------------------------
# is_git_commit truth table
# --------------------------------------------------------------------------
COMMIT_POSITIVE = [
    "git commit",
    "git commit -m 'msg'",
    'git commit -am "a message mentioning commit"',
    "git -C /path/to/repo commit -m x",
    "git add -A && git commit -m x",
    "cd sub; git commit",
    "git commit-tree abc123",  # #15: commit-tree writes a commit the reviewer never sees
]
COMMIT_NEGATIVE = [
    "git status",
    "git log --oneline",
    "git diff HEAD",
    "git add -A",
    "ls -la",
    "echo committed files",
    "git show HEAD",
    "git committed",
]


@pytest.mark.parametrize("cmd", COMMIT_POSITIVE)
def test_is_git_commit_positive(cmd):
    assert rl.is_git_commit(cmd) is True


@pytest.mark.parametrize("cmd", COMMIT_NEGATIVE)
def test_is_git_commit_negative(cmd):
    assert rl.is_git_commit(cmd) is False


# --------------------------------------------------------------------------
# flag set / clear / is_active (isolated REVIEWER_HOME, per-project keyed)
# --------------------------------------------------------------------------
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def test_flag_inactive_by_default(home):
    assert rl.is_active("/Users/x/proj") is False


def test_set_and_clear_active_is_per_project(home):
    rl.set_active("/Users/x/proj")
    assert rl.is_active("/Users/x/proj") is True
    assert rl.is_active("/Users/x/other") is False  # different project unaffected
    rl.clear_active("/Users/x/proj")
    assert rl.is_active("/Users/x/proj") is False


def test_clear_when_absent_is_noop(home):
    rl.clear_active("/Users/x/proj")  # must not raise
    assert rl.is_active("/Users/x/proj") is False


# --------------------------------------------------------------------------
# is_git_repo
# --------------------------------------------------------------------------
def test_is_git_repo_true(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert rl.is_git_repo(str(tmp_path)) is True


def test_is_git_repo_false(tmp_path):
    assert rl.is_git_repo(str(tmp_path)) is False


# --------------------------------------------------------------------------
# serialize_questions
# --------------------------------------------------------------------------
def test_serialize_questions_includes_text_headers_and_options():
    tool_input = {
        "questions": [
            {
                "question": "Which database?",
                "header": "DB",
                "options": [
                    {"label": "Postgres", "description": "relational"},
                    {"label": "Mongo", "description": "document"},
                ],
            }
        ]
    }
    s = rl.serialize_questions(tool_input)
    assert "Which database?" in s
    assert "Postgres" in s
    assert "relational" in s
    assert "Mongo" in s


# --------------------------------------------------------------------------
# run_codex_resume_by_id with the fake codex. Resuming names the session it attaches to;
# there is no session-less resume (see test_session_isolation.py).
# --------------------------------------------------------------------------
def test_run_codex_resume_success(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_STDOUT", "ANSWER 42")
    monkeypatch.setenv("FAKE_CODEX_EXIT", "0")
    code, out, err = rl.run_codex_resume_by_id(
        "sid-1", str(tmp_path), "read-only", "QUESTION FROM AGENT: hi"
    )
    assert code == 0
    assert "ANSWER 42" in out


def test_run_codex_resume_failure_surfaces_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_STDERR", "boom")
    monkeypatch.setenv("FAKE_CODEX_EXIT", "3")
    code, out, err = rl.run_codex_resume_by_id("sid-1", str(tmp_path), "read-only", "q")
    assert code == 3
    assert "boom" in err


def test_run_codex_brief_pipes_file_as_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_STDOUT", "briefed")
    brief = tmp_path / "reviewerAgent.md"
    brief.write_text("You are a strict reviewer.\n")
    code, out, err = rl.run_codex_brief(str(tmp_path), str(brief))
    assert code == 0
    assert "briefed" in out


# --------------------------------------------------------------------------
# output shaping helpers
# --------------------------------------------------------------------------
def test_deny_output_shape():
    out = rl.deny_output("the reason", "a notice")
    assert out["systemMessage"] == "a notice"
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"] == "the reason"


def test_info_output_has_no_decision():
    out = rl.info_output("just a notice")
    assert out["systemMessage"] == "just a notice"
    assert "hookSpecificOutput" not in out


# --------------------------------------------------------------------------
# codex argv construction (codex exec has NO --ask-for-approval flag; the
# approval policy is set via `-c approval_policy="never"`)
# --------------------------------------------------------------------------
def test_resume_argv_uses_config_approval_not_flag():
    argv = rl.resume_by_id_argv("sid-1", "/p", "workspace-write", "QUESTION FROM AGENT: hi")
    assert "--ask-for-approval" not in argv
    assert "-c" in argv
    assert 'approval_policy="never"' in argv
    assert argv[1] == "exec"
    ci = argv.index("-C")
    assert argv[ci + 1] == "/p"
    si = argv.index("--sandbox")
    assert argv[si + 1] == "workspace-write"
    assert argv[-3:] == ["resume", "sid-1", "QUESTION FROM AGENT: hi"]


def test_brief_argv_uses_config_approval_and_reads_stdin_dash():
    argv = rl.brief_argv("/p")
    assert "--ask-for-approval" not in argv
    assert "-c" in argv
    assert 'approval_policy="never"' in argv
    si = argv.index("--sandbox")
    assert argv[si + 1] == "read-only"
    assert argv[-1] == "-"


def test_resume_argv_commit_audits_under_workspace_write_sandbox():
    # Hook-commits: the commit audit runs under workspace-write (tests can write the
    # tree + tmp; .git stays read-only so Codex cannot commit) with NO permission
    # profile / extra config -- the hook performs the commit on approval.
    argv = rl.resume_by_id_argv("sid-1", "/p", "workspace-write", rl.COMMIT_PROMPT)
    si = argv.index("--sandbox")
    assert argv[si + 1] == "workspace-write"
    assert 'approval_policy="never"' in argv
    assert 'default_permissions="reviewer-commit"' not in argv  # no profile dependency
    assert argv[1] == "exec"
    assert argv[-3:] == ["resume", "sid-1", rl.COMMIT_PROMPT]


def test_resume_argv_supports_optional_sandbox_and_extra_config():
    # The generic capability (omit --sandbox, pass extra -c overrides) is retained.
    argv = rl.resume_by_id_argv("sid-1", "/p", None, "do it", extra_config=['foo="bar"'])
    assert "--sandbox" not in argv
    assert "foo=\"bar\"" in argv
    assert 'approval_policy="never"' in argv
    assert argv[-3:] == ["resume", "sid-1", "do it"]


# --------------------------------------------------------------------------
# Service tier. Every reviewer call is something a person is BLOCKED on -- a commit
# waiting at a fail-closed gate, an AskUserQuestion, a stop check -- so the reviewer
# buys latency with quota. Verified live against codex-cli 0.142.3: the model catalog
# advertises `priority` for GPT-5.4 as "Fast, 1.5x speed, increased usage", and
# `RUST_LOG=info` shows `service_tier: Some("priority")` in the SessionConfiguredEvent
# on both the fresh-exec and the `resume <id>` paths.
# --------------------------------------------------------------------------
def test_every_codex_argv_requests_the_priority_service_tier():
    for argv in (rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi"),
                 rl.brief_argv("/p"), rl.brief_capture_argv("/p")):
        assert rl.SERVICE_TIER_CONFIG in argv, argv
        assert argv[argv.index(rl.SERVICE_TIER_CONFIG) - 1] == "-c"
    assert rl.SERVICE_TIER_CONFIG == 'service_tier="priority"'


def test_every_codex_argv_disables_plugins():
    for argv in (rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi"),
                 rl.brief_argv("/p"), rl.brief_capture_argv("/p")):
        assert rl.DISABLE_PLUGINS in argv, argv
        assert argv[argv.index(rl.DISABLE_PLUGINS) - 1] == "-c"


def test_both_commit_prompts_offer_a_non_blocking_suggestions_channel():
    """The standard says "you may make suggestions which are not commit rejections", and
    that sentence is unactionable unless the output contract admits such a form. Before
    this, form (1) was "APPROVED on the first line and nothing else" -- so the reviewer's
    only outlets were silence and a blocking rejection, which is exactly why non-defects
    arrived as required fixes."""
    for name, text in (("COMMIT_PROMPT", rl.COMMIT_PROMPT),
                       ("PI_COMMIT_PROMPT", rl.PI_COMMIT_PROMPT)):
        assert "SUGGESTIONS" in text, name
        assert "never block a commit" in text.lower(), name
        # `or other edits` licensed requiring changes that are not defects at all -- the
        # same bloat pressure from the opposite direction. pi's form never had it, so the
        # two backends also disagreed about what a rejection may demand.
        assert "or other edits" not in text, name
        assert "APPROVED" in text, name


def test_codex_commit_prompt_requests_location_evidence_whenever_possible():
    normalized = " ".join(rl.COMMIT_PROMPT.split())

    assert "file:line evidence whenever possible" in normalized
    assert "file:line evidence where appropriate" not in normalized


def test_both_commit_prompts_offer_suggestions_on_a_REJECTION_too():
    """The SUGGESTIONS channel existed only on the APPROVAL path: form (2) ended "Output
    nothing else", so a reviewer rejecting for one real defect had nowhere to put anything
    else it noticed, and everything it wanted to say became a required fix.

    Observed 2026-07-31: a ten-round review of a design document did not converge (finding
    counts 16, 13, 8, 8 -- flat), because findings about code the staged set did not modify
    kept arriving as blocking items."""
    # pi keeps the text verdict, so its SUGGESTIONS: block must be OFFERED on the rejection form
    # too -- the marker appears at least twice (approval form + rejection form).
    assert "Output nothing else" not in rl.PI_COMMIT_PROMPT
    assert rl.PI_COMMIT_PROMPT.count("SUGGESTIONS") >= 2
    # codex emits JSON: `suggestions` is a REQUIRED key present on BOTH verdicts by construction,
    # so the rejection channel is offered structurally, not by repeating a text marker.
    assert "Output nothing else" not in rl.COMMIT_PROMPT
    assert "EITHER verdict" in rl.COMMIT_PROMPT


def test_both_commit_prompts_carry_the_staged_set_scope_boundary():
    """Reviewing a diff, the diff is the unit. Reviewing a design document, every absolute
    sentence becomes a testable claim against the whole repository -- which is how findings
    about `apply_context` and `reviewer_backend()`, neither touched by the change, became
    blocking. The reviewer was not wrong; it had no boundary to apply."""
    for name, text in (("COMMIT_PROMPT", rl.COMMIT_PROMPT),
                       ("PI_COMMIT_PROMPT", rl.PI_COMMIT_PROMPT)):
        low = text.lower()
        assert "does not modify" in low, name
        assert "reachable, worse, or newly wrong" in low, name
        # The clause that actually ends the observed loop: the remedy for a false claim
        # about unmodified code is to fix the CLAIM, not to refactor that code.
        assert "correct the claim" in low, name


def test_both_commit_prompts_demote_an_unestablished_outside_diff_finding():
    """OBSERVED 2026-08-02, three identical blocked rounds. The reviewer computed an expected
    test count by adding the staged diff's new tests to the total documented in the previous
    commit, and rejected the (correct, measured) documented figure for not matching. The
    baseline it trusted was itself wrong -- an earlier commit had documented a count measured
    against an uncommitted tree -- so the arithmetic was sound and the premise was not.

    The boundary above settles WHICH CODE may be cited. This settles WHAT STANDARD OF PROOF a
    citation needs: a fact the diff does not contain has to be established before it can block.
    Unestablished, it is a suggestion, which the operator can act on without the gate deadlocking
    on it."""
    for name, text in (("COMMIT_PROMPT", rl.COMMIT_PROMPT),
                       ("PI_COMMIT_PROMPT", rl.PI_COMMIT_PROMPT)):
        low = text.lower()
        assert "not established" in low, name
        assert "outside the staged diff" in low, name
        # The demotion must name its destination. "Do not block on it" leaves the finding
        # nowhere to go, and the reviewer's only other outlet is silence.
        assert "suggestion" in low, name
        # ...and must NOT weaken the ordinary case: a defect the diff itself shows still
        # blocks. Without this the sentence reads as a licence to downgrade anything.
        assert "full force" in low, name


def test_the_standing_brief_carries_the_same_disposition_vocabulary_as_the_capsule():
    """THIRD COPY. The disposition list is written out in the capsule, in the spec, AND in
    REVIEWER_STANDING_BRIEF, and the 2026-08-02 fix initially updated only the first. A
    reviewer holding a brief that offers three dispositions and a capsule that offers four
    has been told two different things about what it may conclude.

    The brief reaches only NEW sessions -- neither backend re-sends it -- so a stale copy
    here is not corrected by the next hook invocation the way the capsule is. That makes
    the two drifting apart harder to notice, not easier."""
    brief = rl.REVIEWER_STANDING_BRIEF
    assert "WITHDRAWN" in brief
    assert "superseded" in brief
    # The brief's own UNCHANGED rule closed the same second exit the capsule's did.
    assert "restate those findings" not in brief.lower()


def test_only_the_codex_commit_prompt_offers_to_establish_a_fact_by_running_something():
    """The two backends need DIFFERENT remedies for the same rule, which is why this is not one
    shared sentence. Codex may run commands, so its route is to go and check. Pi is hermetically
    read-only -- PI_READ_ONLY_TOOLS omits bash after a live redirect-write reproduction -- so
    telling it to verify would hand it an instruction it cannot follow. That precise mistake is
    already recorded at reviewer_prompts.py's _CAPSULE_COMMIT_CHANGED, where pi was told to
    re-read a file when its only read tool returns the working tree. Pi's route is therefore to
    demote, never to check."""
    assert "you may run" in rl.COMMIT_PROMPT.lower()
    low = rl.PI_COMMIT_PROMPT.lower()
    assert "you may run" not in low
    assert "cannot run" in low


def test_codex_demotes_when_the_check_it_would_run_is_unavailable():
    """OBSERVED 2026-08-02, the round after the PROOF clause shipped. The reviewer reported
    "Pytest was unavailable in this environment" and STILL rejected on a test-count claim it
    had therefore not established.

    The first cut of this clause said only that codex MAY run a command and that the finding
    blocks once it has. It never said what happens when the command cannot be run, so the
    unavailable case fell through to the old behaviour. Pi's branch always stated it, because
    pi can never run anything; codex needed it too, for the case where the capability exists
    but the tool does not. This repository's suite is a live example -- it runs under a conda
    pytest that a sandboxed reviewer has no path to."""
    low = rl.COMMIT_PROMPT.lower()
    assert "cannot run it" in low or "unavailable" in low
    # The unavailable branch must land in the same place as pi's: SUGGESTIONS, not a block.
    tail = low.split("proof:", 1)[1]
    assert "suggestion" in tail


def test_a_suggestions_block_never_opens_the_gate_on_a_rejection():
    """MIRROR of test_is_approved_accepts_a_suggestions_block_and_still_rejects_rejected,
    pinned for the same reason: the gate is FAIL-CLOSED, so a trailing block must never
    turn a rejection into an approval."""
    assert not rl.is_approved("REJECTED:\n1. broken\nSUGGESTIONS:\n1. also consider X\n")
    assert not rl.is_approved("REJECTED:\nSUGGESTIONS:\n1. APPROVED elsewhere\n")


def test_the_commit_prompts_point_at_the_reminder_not_the_brief():
    """REGRESSION. Both prompts briefly read "a defect under the standard in your standing
    instructions", which points a LIVE reviewer at the once-sent brief -- the stale copy
    still carrying the exact "DEPTH IS EXPECTED / never soften" text being removed. A
    per-call prompt must reference the per-call layer: `apply_context` prepends the capsule
    above the task prompt, so the STANDING REMINDER is always present and always current."""
    for name, text in (("COMMIT_PROMPT", rl.COMMIT_PROMPT),
                       ("PI_COMMIT_PROMPT", rl.PI_COMMIT_PROMPT)):
        assert "STANDING REMINDER above" in text, name
        assert "standing instructions" not in text, name
        assert "your brief" not in text.lower(), name


def test_required_fixes_message_labels_suggestions_as_NON_BLOCKING():
    """The relay already passed the FULL text through untruncated, so suggestions reach the
    agent with no plumbing change -- but the framing said "Treat the full text below as the
    REQUIRED FIXES", which would relabel them as blocking and reintroduce in the relay the
    exact problem the prompt change fixes."""
    out = ("REJECTED:\n1. real defect at foo.py:10\n"
           "SUGGESTIONS:\n1. unrelated pre-existing thing in bar.py\n")
    reason, msg = rl.required_fixes_message(out, "/tmp/review.txt")

    # Both halves survive.
    assert "real defect at foo.py:10" in reason
    assert "unrelated pre-existing thing in bar.py" in reason
    # And they are labelled differently: the suggestions half is marked non-blocking.
    low = reason.lower()
    assert "non-blocking" in low or "do not block" in low
    # The required-fixes framing must NOT be the last word covering the suggestions.
    assert reason.index("real defect") < reason.index("unrelated pre-existing")


def test_a_rejection_without_suggestions_is_byte_identical_to_before():
    """The common path must be provably unaffected: no suggestions marker, no new wording."""
    out = "REJECTED:\n1. only a defect\n"
    reason, _msg = rl.required_fixes_message(out, "/tmp/r.txt")
    assert reason == (
        "reviewerAgent did NOT approve the staged changes and the commit was "
        "blocked. Treat the full text below as the REQUIRED FIXES before this commit "
        "can proceed; address every item, then stage and attempt the commit again (it "
        "will be re-reviewed). The complete list is also saved at {}:\n\n{}".format(
            "/tmp/r.txt", out.strip()))


def test_a_malformed_or_repeated_suggestions_marker_degrades_to_required():
    """Fail-closed direction: if the split is ambiguous, treat everything as required
    rather than silently demoting a real defect to a suggestion."""
    out = ("REJECTED:\n1. defect\nSUGGESTIONS:\n1. a\nSUGGESTIONS:\n1. b\n")
    reason, _ = rl.required_fixes_message(out, "/tmp/r.txt")
    assert "defect" in reason and "1. a" in reason and "1. b" in reason


def test_is_approved_accepts_a_suggestions_block_and_still_rejects_rejected():
    """PINS the parser the SUGGESTIONS channel rests on.

    `is_approved` reads the first non-empty line, so the new form already parses -- no
    parser change was needed. This test exists so a later "tightening" to an exact match
    cannot silently delete the channel and push these findings back into rejections."""
    assert rl.is_approved("APPROVED\nSUGGESTIONS:\n1. consider caching this\n")
    assert rl.is_approved("APPROVED")
    assert not rl.is_approved("REJECTED:\n1. broken\n")
    # A preamble before the token still fails closed: the token must be FIRST.
    assert not rl.is_approved("Here is my review.\nAPPROVED\n")


def test_the_stated_contract_matches_what_is_approved_actually_accepts():
    """REGRESSION. The prompts said "output APPROVED on the first line ... and nothing else"
    while `is_approved` accepted a trailing note on that line. A contract the parser does not
    enforce is a real defect: a reader tightening the parser to match the words would start
    blocking approvals, and a reader trusting the parser would think the prompt lied.

    Resolved by making the PROMPT describe the accepted form, not by tightening the parser.
    The gate is fail-CLOSED, so a parser stricter than reviewer behaviour converts ordinary
    approvals into blocked commits, and every block costs a full paid review round. `begin the
    first line with the word APPROVED` is what the parser enforces, and the one genuine
    ambiguity -- a line that approves and rejects at once -- is called out in the prompt AND
    refused by the parser."""
    # Text-verdict backends (pi) state the exact APPROVED-line form the parser accepts.
    for name, text in (("PI_COMMIT_PROMPT", rl.PI_COMMIT_PROMPT),):
        assert "begin the first line with the word APPROVED" in text, name
        assert "Do not write REJECTED anywhere on the APPROVED line" in text, name
        # The old absolute phrasing must not come back while the parser stays lenient.
        assert "APPROVED on the first line and nothing else" not in text, name

    # The CODEX backend emits a strict JSON verdict (--output-schema {verdict,findings,
    # suggestions}) that codex_verdict_to_text reconstructs into the canonical APPROVED/REJECTED
    # text BEFORE is_approved sees it -- so its prompt states the JSON contract, not the text
    # APPROVED-line form. The reconstruction (tested in test_audit_record.py) keeps the prompt
    # and the parser in agreement here.
    assert "begin the first line with the word APPROVED" not in rl.COMMIT_PROMPT
    assert ('"verdict"' in rl.COMMIT_PROMPT and '"findings"' in rl.COMMIT_PROMPT
            and '"suggestions"' in rl.COMMIT_PROMPT)
    assert "APPROVED" in rl.COMMIT_PROMPT and "REJECTED" in rl.COMMIT_PROMPT

    # The forms the prompt sanctions must be exactly the forms the parser accepts.
    assert rl.is_approved("APPROVED")
    assert rl.is_approved("APPROVED\nSUGGESTIONS:\n1. x\n")
    assert rl.is_approved("APPROVED -- tests green")
    assert not rl.is_approved("APPROVED but REJECTED really")

    # The contradiction rule must forbid exactly the token the prompt forbids, no more.
    # A `\bREJECT` prefix match also caught REJECTION and REJECT-LEVEL, so these
    # prompt-compliant approvals were blocked by a rule the reviewer was never told about --
    # the same prompt/parser gap one level down.
    assert rl.is_approved("APPROVED, not rejection-worthy")
    assert rl.is_approved("APPROVED -- nothing here is reject-level")
    assert rl.is_approved("APPROVED; I considered rejecting and did not")


def test_approval_requires_the_whole_word_and_no_self_contradiction():
    """REGRESSION. `startswith("APPROVED")` matched any line with APPROVED as a PREFIX, so
    `APPROVED_REJECTED` and `APPROVEDX` opened the gate -- a word that is not the token at all.

    The self-contradictory forms fail closed for the same reason: a first line that approves
    and rejects in one breath carries no usable verdict, and at a fail-closed gate the
    ambiguous reading must be the blocking one.

    NOT tightened to exact equality, deliberately. `APPROVED -- clean diff, tests green` is
    realistic reviewer output and is already pinned by
    tests/test_commit_hook.py::test_approved_verdict_is_echoed_in_systemmessage. Requiring a
    bare token would turn every such approval into a BLOCKED commit, which is a far worse
    failure than the malformed strings above -- and the SUGGESTIONS channel exists precisely
    so a reviewer with something to add still approves."""
    assert not rl.is_approved("APPROVED_REJECTED")
    assert not rl.is_approved("APPROVEDX")
    assert not rl.is_approved("APPROVEDLY so")
    assert not rl.is_approved("APPROVED: actually rejected")
    assert not rl.is_approved("APPROVED, but REJECTED on reflection")

    # Ordinary approvals, with and without trailing prose, must all still pass.
    assert rl.is_approved("APPROVED -- clean diff, tests green")
    assert rl.is_approved("APPROVED but please consider caching this")
    assert rl.is_approved("  approved  ")
    assert rl.is_approved("APPROVED.")


def test_the_spec_names_only_prompt_constants_that_actually_exist():
    """REGRESSION. REVIEWER_PROMPT_SPEC.md opens by naming the constants it is transcribed
    into, and that list drifted: it claimed `_CAPSULE_COMMIT_REPEAT`, which has never
    existed -- the repeat-attempt text is assembled from `_CAPSULE_COMMIT_REPEAT_HEAD`,
    `_CAPSULE_COMMIT_CHANGED` and `_CAPSULE_COMMIT_REPEAT_TAIL`.

    A spec is only useful if you can follow it to the code. Sending a reader to a symbol
    that does not exist is the specific failure this file was written to prevent, and the
    correspondence is mechanical, so assert it rather than re-proofreading it by hand.

    The spec is maintainer-local: it lives in the git-ignored `docs/internal/` and does not ship
    in the release export, so this SKIPS rather than fails where it is absent -- a public clone
    has nothing to check, and a missing internal document is not a defect in the package."""
    spec = Path(rl.__file__).resolve().parent / "docs" / "internal" / "REVIEWER_PROMPT_SPEC.md"
    if not spec.exists():
        pytest.skip("REVIEWER_PROMPT_SPEC.md is maintainer-local and absent here")
    text = spec.read_text(encoding="utf-8")
    head, _, rest = text.partition("required to match it:")
    assert rest, "the spec's opening 'source of truth' paragraph moved or was reworded"
    listed, _, _ = rest.partition("are its transcription targets")
    names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", listed)
    assert names, "no constants named in the transcription-target list"
    missing = [n for n in names if not hasattr(rl, n)]
    assert not missing, "spec names constants reviewer_lib.py does not define: {}".format(
        missing)


def test_extra_config_can_still_override_the_service_tier():
    """It must be an ordinary `-c`, not a late append.

    codex applies repeated `-c` overrides in order, so a caller passing its own
    service_tier via extra_config has to WIN. If ours were appended after
    extra_config, that override would be silently dead."""
    argv = rl.resume_by_id_argv("sid-1", "/p", None, "do it",
                                extra_config=['service_tier="flex"'])
    assert argv.index('service_tier="flex"') > argv.index(rl.SERVICE_TIER_CONFIG)


# --------------------------------------------------------------------------
# codex model override: env REVIEWER_CODEX_MODEL > state file > None (codex default)
# --------------------------------------------------------------------------
def test_codex_model_default_none_and_no_model_in_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    assert rl.codex_model() is None
    for argv in (rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi"),
                 rl.brief_argv("/p"), rl.brief_capture_argv("/p")):
        assert not any(a.startswith('model="') for a in argv)


def test_codex_model_state_file_applies_to_all_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    rl.set_codex_model("gpt-5.5-codex")
    assert rl.codex_model() == "gpt-5.5-codex"
    for argv in (rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi"),
                 rl.brief_argv("/p"), rl.brief_capture_argv("/p")):
        mi = argv.index('model="gpt-5.5-codex"')
        assert argv[mi - 1] == "-c"
    rl.clear_codex_model()
    assert rl.codex_model() is None


def test_codex_model_env_beats_state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_codex_model("file-model")
    monkeypatch.setenv("REVIEWER_CODEX_MODEL", "env-model")
    assert rl.codex_model() == "env-model"
    assert 'model="env-model"' in rl.resume_by_id_argv("sid-1", "/p", None, "x")


def test_codex_model_blank_file_means_no_override(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    f = rl.codex_model_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("  \n")
    assert rl.codex_model() is None


def test_codex_model_invalid_env_is_skipped_falls_through_to_file(tmp_path, monkeypatch):
    # The toggle validates on SET, but the env var and a hand-edited state file bypass it:
    # an invalid candidate must never reach `-c model="..."`. Invalid env falls through to
    # the (valid) file; both invalid -> None.
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_codex_model("file-model")
    monkeypatch.setenv("REVIEWER_CODEX_MODEL", 'bad"quote model')
    assert rl.codex_model() == "file-model"
    assert 'model="file-model"' in rl.resume_by_id_argv("sid-1", "/p", None, "x")


def test_codex_model_invalid_file_contents_mean_no_override(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_CODEX_MODEL", raising=False)
    f = rl.codex_model_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('two tokens" injected=evil\n')
    assert rl.codex_model() is None
    argv = rl.resume_by_id_argv("sid-1", "/p", None, "x")
    assert not any(a.startswith('model="') for a in argv)


# --------------------------------------------------------------------------
# codex reasoning effort override: env REVIEWER_CODEX_EFFORT > state file >
# config.json `reasoningEffort` > None (inherit the codex CLI's own config).
#
# Scoped to the RESUME path -- the commit audit and the askq answer. The brief
# stays at BRIEF_REASONING_CONFIG (its response is discarded) and the stop
# supervisor keeps its own low default, which survives because codex applies
# repeated `-c` overrides in order and extra_config is appended last.
# --------------------------------------------------------------------------
def _no_effort_env(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)


def _efforts(argv):
    return [a for a in argv if a.startswith("model_reasoning_effort=")]


def test_codex_effort_default_none_and_no_effort_in_resume_argv(tmp_path, monkeypatch):
    """Unset must leave today's behaviour byte for byte: no `-c` at all, so the
    audit keeps inheriting whatever ~/.codex/config.toml says."""
    _no_effort_env(monkeypatch, tmp_path)
    assert rl.codex_effort() is None
    assert _efforts(rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi")) == []


def test_codex_effort_state_file_reaches_the_resume_path(tmp_path, monkeypatch):
    _no_effort_env(monkeypatch, tmp_path)
    rl.set_codex_effort("max")
    assert rl.codex_effort() == "max"
    argv = rl.resume_by_id_argv("sid-1", "/p", "read-only", "hi")
    i = argv.index('model_reasoning_effort="max"')
    assert argv[i - 1] == "-c"
    rl.clear_codex_effort()
    assert rl.codex_effort() is None


def test_codex_effort_leaves_the_BRIEF_at_low(tmp_path, monkeypatch):
    """The brief only mints a session id and its response text is discarded, so
    paying max there buys latency and nothing else.

    Asserted per path, because the two differ and only one is live: the
    session-creating `brief_capture_argv` (the sole caller is
    `_brief_capture_run`) pins BRIEF_REASONING_CONFIG, while `brief_argv` sets
    no effort at all and inherits the codex CLI default. This override must not
    change either -- including not silently GIVING the plain brief an effort it
    never had."""
    _no_effort_env(monkeypatch, tmp_path)
    rl.set_codex_effort("max")
    assert _efforts(rl.brief_capture_argv("/p")) == [rl.BRIEF_REASONING_CONFIG]
    assert _efforts(rl.brief_argv("/p")) == []


def test_the_stop_supervisor_keeps_its_own_effort(tmp_path, monkeypatch):
    """Measured, not assumed (codex-cli 0.146.0): a repeated `-c` key is
    LAST-WINS -- `max` then `low` banners `low`, `low` then `max` banners `max`.
    The stop supervisor passes its own effort via extra_config at every turn end
    and must stay snappy, so the global override has to be placed BEFORE it."""
    _no_effort_env(monkeypatch, tmp_path)
    rl.set_codex_effort("max")
    argv = rl.resume_by_id_argv("sid-1", "/p", None, "do it",
                                extra_config=[rl.stopcontinue_effort_config()])
    assert argv.index(rl.stopcontinue_effort_config()) > argv.index(
        'model_reasoning_effort="max"')


def test_codex_effort_env_beats_state_file(tmp_path, monkeypatch):
    _no_effort_env(monkeypatch, tmp_path)
    rl.set_codex_effort("high")
    monkeypatch.setenv("REVIEWER_CODEX_EFFORT", "xhigh")
    assert rl.codex_effort() == "xhigh"
    assert 'model_reasoning_effort="xhigh"' in rl.resume_by_id_argv("sid-1", "/p", None, "x")


def test_codex_effort_falls_back_to_config_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"reasoningEffort": "high"}), encoding="utf-8")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("REVIEWER_CODEX_EFFORT", raising=False)
    assert rl.codex_effort() == "high"


def test_codex_effort_invalid_env_is_skipped_falls_through_to_file(tmp_path, monkeypatch):
    """A malformed level is not tolerated by the API: `banana` returns HTTP 400
    `[reasoning.effort] [invalid_enum_value]`. The commit gate is fail-CLOSED, so
    an unvalidated value reaching `-c` would block EVERY commit until found."""
    _no_effort_env(monkeypatch, tmp_path)
    rl.set_codex_effort("high")
    monkeypatch.setenv("REVIEWER_CODEX_EFFORT", "banana")
    assert rl.codex_effort() == "high"
    assert 'model_reasoning_effort="high"' in rl.resume_by_id_argv("sid-1", "/p", None, "x")


def test_codex_effort_invalid_file_contents_mean_no_override(tmp_path, monkeypatch):
    _no_effort_env(monkeypatch, tmp_path)
    f = rl.codex_effort_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('max" injected=evil\n')
    assert rl.codex_effort() is None
    assert _efforts(rl.resume_by_id_argv("sid-1", "/p", None, "x")) == []


def test_codex_effort_blank_file_means_no_override(tmp_path, monkeypatch):
    _no_effort_env(monkeypatch, tmp_path)
    f = rl.codex_effort_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("  \n")
    assert rl.codex_effort() is None


def test_every_level_codex_accepts_is_valid_and_nothing_else_is(tmp_path, monkeypatch):
    """The accepted set is closed and known, so this is a whitelist rather than a
    regex. Two independent sources, each UNDER-reporting the other: the API's own
    400 enumerates `none, minimal, low, medium, high, xhigh, max`; the CLI model
    catalog adds `ultra` (on gpt-5.6-sol/terra), and `ultra` was accepted
    end-to-end on gpt-5.6-luna despite being absent from that enum."""
    for level in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
        assert rl.is_valid_codex_effort(level), level
    for bad in ("banana", "MAX", "", "  ", "high low", 'x" injected=evil', None):
        assert not rl.is_valid_codex_effort(bad), bad


# --------------------------------------------------------------------------
# Session file manifest: WHICH files this agent actually touched, and in WHICH
# repositories.
#
# Both defects this fixes were observed live on 2026-07-31. The stop supervisor
# was asked (STOP_SUPERVISE_PROMPT check (a)) to find "files the AGENT created or
# modified this session" but was handed no record of them -- only the operator's
# messages and the agent's last reply -- so it inferred them from `git status` in
# ONE checkout:
#
#   1. that checkout is always `data["cwd"]`, so a session whose work happened in
#      another working directory looked idle, and the supervisor substituted that
#      repo's own unrelated outstanding work as the agent's task;
#   2. in a SHARED checkout it credited the agent with files another concurrent
#      session was writing -- it directed a commit of a 61KB document whose mtime
#      was 8 seconds old and still being edited by a live session.
#
# The manifest is derived from the transcript, which is a record of what THIS
# agent did, and the repositories are derived from the touched files themselves
# rather than from cwd -- so one mechanism answers both questions.
# --------------------------------------------------------------------------
def _tool_use_event(name, **inp):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": name, "input": inp},
        ]},
    }


def _write_transcript(path, events):
    path.write_text(
        "".join(json.dumps(ev) + "\n" for ev in events), encoding="utf-8"
    )
    return str(path)


def test_manifest_collects_written_and_edited_paths(tmp_path):
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Write", file_path="/a/one.py", content="x")
                          + _tool_pair("Edit", file_path="/a/two.py",
                                       old_string="a", new_string="b"))
    assert rl.session_touched_files(t) == ["/a/one.py", "/a/two.py"]


def test_manifest_EXCLUDES_reads(tmp_path):
    """Reading a file is not touching it. Crediting the agent with every file it
    looked at would restore the very over-attribution this exists to remove."""
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Read", file_path="/a/looked_at.py")
                          + _tool_pair("Write", file_path="/a/wrote.py", content="x"))
    assert rl.session_touched_files(t) == ["/a/wrote.py"]


def test_manifest_deduplicates_and_sorts(tmp_path):
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Write", uid="a", file_path="/a/z.py", content="1")
                          + _tool_pair("Edit", uid="b", file_path="/a/a.py",
                                       old_string="x", new_string="y")
                          + _tool_pair("Edit", uid="c", file_path="/a/z.py",
                                       old_string="1", new_string="2"))
    assert rl.session_touched_files(t) == ["/a/a.py", "/a/z.py"]


def test_manifest_is_EMPTY_for_a_tool_only_or_unreadable_transcript(tmp_path):
    assert rl.session_touched_files(tmp_path / "missing.jsonl") == []
    t = _write_transcript(tmp_path / "t.jsonl", _tool_pair("Bash", command="git status"))
    assert rl.session_touched_files(t) == []


def test_a_bash_write_is_NOT_claimed_as_attribution(tmp_path):
    """Bash carries only a command STRING -- no structured file argument -- so a
    file written via `cp` or a heredoc cannot be attributed from the transcript.
    The manifest must not guess: over-reporting would make the supervisor direct
    commits of files the agent never touched. What it must NOT do is call the
    resulting record complete -- see the mixed-session test below."""
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Bash", command="cp /src/x.py /dst/x.py"))
    assert rl.session_touched_files(t) == []
    assert rl.session_attribution(t)["complete"] is False


def test_a_modifying_call_with_NO_recorded_result_is_uncertain(tmp_path):
    """An interrupted turn leaves a tool_use with no tool_result. That is neither a
    confirmed touch nor a confirmed miss, and calling it either would be a guess."""
    t = _write_transcript(tmp_path / "t.jsonl", [
        _tool_use_event("Write", file_path="/a/interrupted.py", content="x"),
    ])

    att = rl.session_attribution(t)

    assert att["confirmed"] == {}
    assert att["uncertain"] == ["/a/interrupted.py"]
    assert att["complete"] is False


def test_manifest_groups_files_by_their_OWN_repository(tmp_path):
    """The repo comes from the FILE, not from cwd. This is the multi-root fix:
    work in a second working directory has to be visible as work."""
    repo_a, repo_b = tmp_path / "ra", tmp_path / "rb"
    repo_a.mkdir()
    repo_b.mkdir()
    _make_repo(repo_a)
    _make_repo(repo_b)
    (repo_a / "one.py").write_text("x")
    (repo_b / "two.py").write_text("y")
    t = _write_transcript(
        tmp_path / "t.jsonl",
        _tool_pair("Write", uid="a", file_path=str(repo_a / "one.py"), content="x")
        + _tool_pair("Write", uid="b", file_path=str(repo_b / "two.py"), content="y"))

    roots = rl.session_touched_roots(t)

    assert [str(Path(r).resolve()) for r in roots] == sorted(
        [str(repo_a.resolve()), str(repo_b.resolve())])
    assert list(roots.values()) == [["one.py"], ["two.py"]]


def test_a_file_outside_any_repository_is_kept_but_not_invented_into_one(tmp_path):
    loose = tmp_path / "loose.txt"
    loose.write_text("x")
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Write", file_path=str(loose), content="x"))

    roots = rl.session_touched_roots(t)

    assert list(roots) == [""], "no repo must map to the empty root, not to cwd"
    assert roots[""] == [str(loose)]


def test_the_block_names_every_repository_and_is_empty_when_nothing_was_touched(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "one.py").write_text("x")
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Write", file_path=str(repo / "one.py"), content="x"))

    block = rl.session_manifest_block(t)
    assert str(repo.resolve()) in block
    assert "one.py" in block
    assert "ATTRIBUTION: COMPLETE" in block

    # Nothing happened at all -> no block. A heading with nothing under it reads as
    # "the agent touched nothing", which is a claim, not an absence. (A Bash-only
    # session is NOT this case: it DOES emit a block, marked INCOMPLETE.)
    empty = _write_transcript(tmp_path / "e.jsonl", [
        {"type": "assistant", "message": {"role": "assistant", "content": "just text"}},
    ])
    assert rl.session_manifest_block(empty) == ""


def test_the_block_is_bounded(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    events = []
    for i in range(400):
        name = "f{}.py".format(i)
        (repo / name).write_text("x")
        events += _tool_pair("Write", uid="u{}".format(i),
                             file_path=str(repo / name), content="x")
    t = _write_transcript(tmp_path / "t.jsonl", events)

    block = rl.session_manifest_block(t)

    assert len(block) < 8000, "the supervisor prompt must not be flooded"
    assert "400" in block, "the TOTAL count survives truncation"


# --- reviewer-found P1s (2026-07-31). Each failed against the first version. ---

def _tool_pair(name, ok=True, uid=None, **inp):
    """A tool_use AND its tool_result, the shape a real transcript records."""
    uid = uid or "toolu_{}_{}".format(name, abs(hash(str(inp))) % 100000)
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": uid, "name": name, "input": inp}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": uid, "is_error": not ok,
             "content": "ok" if ok else "ERROR"}]}},
    ]


def test_a_MIXED_bash_and_structured_session_is_marked_INCOMPLETE(tmp_path):
    """P1 #1. A pure-Bash session yields an empty manifest, which the prompt already
    treats as unknown -- but a session that writes one file through Write and another
    through Bash produced a manifest that LOOKED complete. The Bash-written file was
    then 'not listed', which the prompt read as 'not this agent's work', so it could
    be left uncommitted and the stop approved."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "structured.py").write_text("x")
    events = []
    events += _tool_pair("Write", file_path=str(repo / "structured.py"), content="x")
    events += _tool_pair("Bash", command="printf x > {}/via_bash.py".format(repo))
    t = _write_transcript(tmp_path / "t.jsonl", events)

    att = rl.session_attribution(t)

    assert att["complete"] is False, (
        "unparsed Bash file effects mean absence from the manifest is NOT proof")
    assert "structured.py" in rl.session_manifest_block(t)
    assert "INCOMPLETE" in rl.session_manifest_block(t)


def test_a_FAILED_bash_still_clouds_attribution(tmp_path):
    """P1 #1, round 2. The first fix counted only SUCCESSFUL Bash, on the reasoning
    that a failed one changed nothing. That reasoning is wrong: a command can write
    a file and THEN exit nonzero (`cp a b && false`, a partial pipeline, a script
    that fails after its side effects). Exit status is not a statement about the
    filesystem."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "structured.py").write_text("x")
    events = _tool_pair("Write", uid="w", file_path=str(repo / "structured.py"),
                        content="x")
    events += _tool_pair("Bash", uid="b", ok=False,
                         command="cp /src/x {}/via_bash.py && false".format(repo))
    t = _write_transcript(tmp_path / "t.jsonl", events)

    assert rl.session_attribution(t)["complete"] is False


def test_a_bash_call_with_NO_result_still_clouds_attribution(tmp_path):
    """An interrupted turn records the tool_use and no result. The command may have
    run to completion; nothing here says otherwise."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "structured.py").write_text("x")
    events = _tool_pair("Write", uid="w", file_path=str(repo / "structured.py"),
                        content="x")
    events.append(_tool_use_event("Bash", command="printf x > /tmp/whatever"))
    t = _write_transcript(tmp_path / "t.jsonl", events)

    assert rl.session_attribution(t)["complete"] is False


def test_a_FAILED_write_is_NOT_attributed_as_touched(tmp_path):
    """P1 #2. The first version read tool_use and never looked at the result, so a
    REJECTED or errored edit counted as a touch. Observed live this session: a Write
    was refused with 'File has not been read yet' and would have been attributed. If a
    concurrent session then dirties that same path, the supervisor directs THIS agent
    to commit SOMEONE ELSE'S file -- the exact defect the manifest exists to stop."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "never_written.py").write_text("someone else's content")
    t = _write_transcript(tmp_path / "t.jsonl", _tool_pair(
        "Write", ok=False, file_path=str(repo / "never_written.py"), content="x"))

    att = rl.session_attribution(t)

    assert att["confirmed"] == {}, "a failed write is not a touch"
    assert str(repo / "never_written.py") in att["uncertain"]
    assert att["complete"] is False


def test_a_SUCCESSFUL_write_is_still_attributed(tmp_path):
    """The fix for #2 must not swing to attributing nothing."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "written.py").write_text("x")
    t = _write_transcript(tmp_path / "t.jsonl", _tool_pair(
        "Write", ok=True, file_path=str(repo / "written.py"), content="x"))

    att = rl.session_attribution(t)

    assert att["confirmed"] == {str(repo.resolve()): ["written.py"]}
    assert att["uncertain"] == []
    assert att["complete"] is True


def test_a_retried_write_that_eventually_SUCCEEDS_is_confirmed_not_uncertain(tmp_path):
    """The live sequence: a Write fails, the agent reads the file, the Write succeeds.
    One failure must not poison a path the agent demonstrably did write."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "f.py").write_text("x")
    events = _tool_pair("Write", ok=False, uid="u1", file_path=str(repo / "f.py"),
                        content="x")
    events += _tool_pair("Write", ok=True, uid="u2", file_path=str(repo / "f.py"),
                         content="x")
    t = _write_transcript(tmp_path / "t.jsonl", events)

    att = rl.session_attribution(t)

    assert att["confirmed"] == {str(repo.resolve()): ["f.py"]}
    assert att["uncertain"] == []


def test_repo_resolution_is_cached_per_directory(tmp_path, monkeypatch):
    """The stop hook runs at EVERY turn end. A session touching hundreds of files in
    a handful of directories must not pay one `git rev-parse` per FILE."""
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    rl._TOPLEVEL_CACHE.clear()
    calls = []
    real = subprocess.run

    def counting_run(argv, *a, **kw):
        if isinstance(argv, list) and "rev-parse" in argv:
            calls.append(argv)
        return real(argv, *a, **kw)

    monkeypatch.setattr(rl.subprocess, "run", counting_run)
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text("x")
        rl.git_toplevel(str(repo / name))

    assert len(calls) == 1, "three files in one directory, one resolution"
    rl._TOPLEVEL_CACHE.clear()


def test_truncation_NEVER_drops_a_repository():
    """P1 #3. The block was truncated at a character bound, and the prompt tells the
    supervisor to check only the repositories NAMED there -- so a big multi-root
    session could have entire repos silently fall off the end and their uncommitted
    files read as unrelated. Roots are structural; only file lines may be elided."""
    roots = {"/repo/number_{:03}".format(i): ["f{}.py".format(j) for j in range(50)]
             for i in range(60)}
    att = {"confirmed": roots, "uncertain": [], "bash_calls": 0, "complete": True}

    block = rl.render_manifest(att)

    for root in roots:
        assert root in block, "every repository root must survive rendering"
    assert "INCOMPLETE" in block, "eliding file lines makes attribution incomplete"
    # Roots are structural, so the only remaining lever against a prompt-flooding
    # session is the per-root FILE budget. 60 roots x 50 files unbounded is ~60KB.
    assert len(block) < 30000, "many roots must shrink the per-root list, not the set"


def test_apply_context_carries_the_manifest_to_the_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = tmp_path / "r"
    repo.mkdir()
    _make_repo(repo)
    (repo / "one.py").write_text("x")
    t = _write_transcript(tmp_path / "t.jsonl",
                          _tool_pair("Write", file_path=str(repo / "one.py"), content="x"))

    out = rl.apply_context("THE PROMPT", str(tmp_path), transcript_path=t)

    assert "one.py" in out
    assert out.endswith("THE PROMPT")


# --------------------------------------------------------------------------
# logging is concise: a single entry must never dump Codex's full transcript
# --------------------------------------------------------------------------
def test_tail_keeps_the_end():
    assert rl.tail("abcdefghij", 3).endswith("hij")
    assert rl.tail("short", 100) == "short"


def test_log_caps_huge_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.log("[on] " + ("x" * 500000))
    content = (tmp_path / "rh" / "logs" / "reviewer.log").read_text()
    assert len(content) < 8000  # bounded, not the full 500k transcript


# --------------------------------------------------------------------------
# commit outcome: HEAD-before/after proof + full (untruncated) fix-list relay
# --------------------------------------------------------------------------
def test_git_head_none_for_non_repo_and_sha_for_repo(tmp_path):
    assert rl.git_head(str(tmp_path)) is None
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
    sha = rl.git_head(str(tmp_path))
    assert sha and len(sha) >= 7


def test_save_review_writes_full_untruncated_text(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    big = "x" * 100000
    path = rl.save_review(big, proj="/proj", claude_session_id="sid", history=True)
    assert Path(path).read_text() == big
    assert rl.review_history("/proj")[-1].read_text() == big


def test_required_fixes_message_returns_full_long_list_and_path():
    long_list = "\n".join(
        "{}. fix experiment_v2/file{}.py:{}".format(i, i, i) for i in range(1, 250)
    )
    reason, msg = rl.required_fixes_message(long_list, "/p/last_commit_review.txt")
    assert "REQUIRED FIXES" in reason
    assert "blocked" in reason  # commit was blocked, not made
    assert "/p/last_commit_review.txt" in reason
    assert long_list in reason  # entire list relayed, not truncated
    assert "fix experiment_v2/file249.py:249" in reason  # last item present


def test_commits_beyond_index_flags_dash_a_all_and_pathspec():
    # These commit MORE than `git diff --cached` -> the staged-only audit can't see it.
    assert rl.commits_beyond_index("git commit -am 'x'") is True
    assert rl.commits_beyond_index("git commit -a -m 'x'") is True
    assert rl.commits_beyond_index("git commit --all -m 'x'") is True
    assert rl.commits_beyond_index("git -C /r commit -a") is True
    assert rl.commits_beyond_index("git commit -m 'x' file.py") is True       # pathspec
    assert rl.commits_beyond_index("git commit -- file.py") is True           # pathspec after --
    assert rl.commits_beyond_index("git add -A && git commit -a -m 'x'") is True
    assert rl.commits_beyond_index("git commit --include path.py") is True    # -i pathspec mode


def test_commits_beyond_index_finds_the_real_commit_subcommand():
    # 'commit' as a filename elsewhere must not be mistaken for the subcommand, and
    # git GLOBAL options before the subcommand (-C <path>, -c name=value) are skipped.
    assert rl.commits_beyond_index("git add commit && git commit -m 'x'") is False
    assert rl.commits_beyond_index("git -C /r -c user.name=x commit -m 'y'") is False
    assert rl.commits_beyond_index("git -C /r commit --all") is True


def test_commits_beyond_index_ignores_shell_redirection():
    # `2>&1`, `>file`, etc. are shell REDIRECTION, not pathspecs -- a plain commit piped to
    # tail must not be misread as reaching beyond the staged set (the reported false positive
    # that blocked every `git commit ... 2>&1 | tail` attempt).
    assert rl.commits_beyond_index('git commit -m "x" 2>&1 | tail -20') is False
    assert rl.commits_beyond_index('git commit -F msg.txt 2>&1 | tail -25') is False
    assert rl.commits_beyond_index('git -C /r commit -m "x" 2>&1 | tail -20') is False
    assert rl.commits_beyond_index('git commit -m "x" > out.txt') is False
    assert rl.commits_beyond_index('git commit -m "x" 1>/dev/null 2>&1') is False
    assert rl.commits_beyond_index('git commit -am "x" 2>&1') is True   # -a still caught


def test_commits_beyond_index_allows_plain_staged_only_commits():
    assert rl.commits_beyond_index("git commit -m 'x'") is False
    assert rl.commits_beyond_index("git commit") is False
    assert rl.commits_beyond_index("git commit --amend -m 'x'") is False      # amend != beyond-index
    assert rl.commits_beyond_index("git commit -s -m 'fix a bug'") is False   # 'a' only in message
    assert rl.commits_beyond_index("git -C /r commit -m 'x'") is False
    assert rl.commits_beyond_index("git commit -C HEAD@{1}") is False         # -C takes a value, not a path


def test_staged_files_lists_index_and_is_empty_otherwise(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert rl.staged_files(str(repo)) == []  # nothing staged
    (repo / "a.py").write_text("z\n")
    assert rl.staged_files(str(repo)) == []  # present but not staged
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    assert rl.staged_files(str(repo)) == ["a.py"]  # staged
    assert rl.staged_files(str(tmp_path / "nope")) == []  # non-repo


# --------------------------------------------------------------------------
# hook-commits: Codex emits APPROVED / REJECTED; the hook allows or denies
# --------------------------------------------------------------------------
def test_is_approved_recognizes_only_an_approved_first_line():
    assert rl.is_approved("APPROVED") is True
    assert rl.is_approved("APPROVED\nexperiment_v2/config.py") is True
    assert rl.is_approved("  approved  ") is True  # case-insensitive, stripped
    assert rl.is_approved("REJECTED:\n1. fix config.py:6") is False
    assert rl.is_approved("") is False
    assert rl.is_approved("Looks fine to me") is False  # must be the explicit token


def test_allow_output_shape():
    out = rl.allow_output("a notice")
    assert out["systemMessage"] == "a notice"
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"


# --------------------------------------------------------------------------
# canonical project key: stable across worktrees / subdirs (the worktree fix)
# --------------------------------------------------------------------------
def test_canonical_project_key_is_stable_across_worktrees_and_subdirs(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _make_repo(main)
    sub = main / "pkg" / "deep"
    sub.mkdir(parents=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    root = os.path.realpath(str(main))
    assert rl.canonical_project_key(str(main)) == root
    assert rl.canonical_project_key(str(sub)) == root      # subdirectory -> root
    assert rl.canonical_project_key(str(wt)) == root        # linked worktree -> SAME root


def test_canonical_project_key_falls_back_to_realpath_outside_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert rl.canonical_project_key(str(plain)) == os.path.realpath(str(plain))


def test_flag_is_keyed_by_canonical_root_across_worktree(home, tmp_path):
    main = tmp_path / "m"
    main.mkdir()
    _make_repo(main)
    wt = tmp_path / "w"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    rl.set_active(str(main))
    assert rl.is_active(str(wt)) is True       # worktree inherits the project toggle
    assert rl.is_active(str(main)) is True
    rl.clear_active(str(wt))                    # clearing from the worktree clears the project flag
    assert rl.is_active(str(main)) is False


# --------------------------------------------------------------------------
# codex session id: capture from --json, persist per Claude session, resume by id
# --------------------------------------------------------------------------
def test_parse_session_id_reads_thread_started_thread_id():
    # The REAL `codex exec --json` stdout schema (codex CLI 0.142.x).
    out = (
        '{"type":"thread.started","thread_id":"019f0fad-ccd9-75a0-a161-1f35d494d7fc"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"READY"}}\n'
    )
    assert rl.parse_session_id(out) == "019f0fad-ccd9-75a0-a161-1f35d494d7fc"


def test_parse_session_id_reads_session_meta_payload():
    # The on-disk rollout schema (kept for robustness across codex versions).
    out = (
        '{"timestamp":"t","type":"session_meta","payload":{"session_id":"abc-123","cwd":"/p"}}\n'
        '{"type":"event_msg","payload":{"type":"task_started"}}\n'
    )
    assert rl.parse_session_id(out) == "abc-123"


def test_parse_session_id_none_when_absent_or_garbage():
    assert rl.parse_session_id("") is None
    assert rl.parse_session_id("not json\nstill not json") is None
    assert rl.parse_session_id('{"type":"event_msg","payload":{"x":1}}') is None


def test_session_id_persistence_roundtrip_keyed_by_claude_session(home):
    assert rl.read_session_id(PROJ, "claude-A") is None
    rl.write_session_id(PROJ, "claude-A", "codex-1")
    rl.write_session_id(PROJ, "claude-B", "codex-2")
    assert rl.read_session_id(PROJ, "claude-A") == "codex-1"
    assert rl.read_session_id(PROJ, "claude-B") == "codex-2"   # isolated per Claude session
    rl.clear_session_id(PROJ, "claude-A")
    assert rl.read_session_id(PROJ, "claude-A") is None
    assert rl.read_session_id(PROJ, "claude-B") == "codex-2"


def test_session_id_none_key_is_never_persisted(home):
    # Superseded behavior: a None key used to share one "default" slot across every
    # unidentifiable session AND every project -- cross-session reuse of one reviewer.
    # An unidentifiable session now persists nothing and briefs its own reviewer per call.
    # Full contract in test_session_isolation.py.
    rl.write_session_id(PROJ, None, "codex-default")
    assert rl.read_session_id(PROJ, None) is None


def test_claude_session_key_prefers_session_id_then_transcript_stem():
    assert rl.claude_session_key({"session_id": "abc"}) == "abc"
    # background-job isolation fallback: the transcript filename is unique per session
    assert rl.claude_session_key(
        {"transcript_path": "/c/projects/-x/886505f2-8726-48dd.jsonl"}
    ) == "886505f2-8726-48dd"
    assert rl.claude_session_key(
        {"session_id": "abc", "transcript_path": "/c/y/z.jsonl"}
    ) == "abc"
    assert rl.claude_session_key({}) is None


# --------------------------------------------------------------------------
# stopContinue: separate flag, transcript parsing, question heuristic
# --------------------------------------------------------------------------
def test_stopcontinue_flag_is_separate_from_active_and_canonical(home, tmp_path):
    main = tmp_path / "m"
    main.mkdir()
    _make_repo(main)
    wt = tmp_path / "w"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
    assert rl.is_stopcontinue_active(str(main)) is False
    rl.set_active(str(main))                              # main reviewer flag...
    assert rl.is_stopcontinue_active(str(main)) is False  # ...does NOT enable stopContinue
    rl.set_stopcontinue_active(str(main))
    assert rl.is_stopcontinue_active(str(main)) is True
    assert rl.is_stopcontinue_active(str(wt)) is True      # worktree inherits via canonical root
    rl.clear_stopcontinue_active(str(wt))
    assert rl.is_stopcontinue_active(str(main)) is False


def _transcript(tmp_path, *entries):
    import json as _json
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(_json.dumps(e) for e in entries) + "\n")
    return str(p)


def test_last_assistant_text_extracts_final_turn_text(tmp_path):
    # Turn-scoped (2026-07-21): the mid-turn reply and the closing question are the
    # SAME turn (the intervening user event only carries a tool result), so both are
    # reviewed. Mid-turn text often contains answers the operator never saw -- the
    # supervisor must see it too.
    path = _transcript(
        tmp_path,
        {"type": "user", "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "earlier reply"}]}},
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Should I use Postgres or Mongo?"}]}},
    )
    assert rl.last_assistant_text(path) == (
        "earlier reply\nShould I use Postgres or Mongo?")


def test_last_assistant_text_recovers_text_when_turn_ends_with_tool_call(tmp_path):
    # 2026-07-21 fix: the OLD behavior read only the LAST assistant event, so any turn
    # whose final event was a tool call reviewed as empty -> the stop hook fail-opened
    # and premature stops slipped through unreviewed. Text earlier in the SAME turn
    # (no intervening real user message) must be recovered.
    path = _transcript(
        tmp_path,
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "a question?"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {}}]}},
    )
    assert rl.last_assistant_text(path, retries=1) == "a question?"
    assert rl.last_assistant_text("/no/such/file.jsonl") == ""


def test_last_assistant_text_scopes_to_final_turn(tmp_path):
    # A REAL user message is a turn boundary: earlier-turn text must NOT leak into the
    # review when the final turn is genuinely tool-only.
    path = _transcript(
        tmp_path,
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "earlier turn text"}]}},
        {"type": "user", "message": {"role": "user", "content": "next task please"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}},
    )
    assert rl.last_assistant_text(path, retries=1) == ""


def test_last_assistant_text_joins_all_text_of_final_turn(tmp_path):
    # Tool-result user events and non-assistant events (progress etc.) belong to the
    # assistant's turn; all its text blocks are joined in order.
    path = _transcript(
        tmp_path,
        {"type": "user", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "part one"}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}},
        {"type": "progress", "data": {}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "part two"}]}},
    )
    assert rl.last_assistant_text(path, retries=1) == "part one\npart two"


def test_last_assistant_text_retries_until_text_flushes(tmp_path, monkeypatch):
    # 2026-07-21 fix: the Stop event can fire before the final text event is flushed to
    # the transcript (observed race: text_len=0 on a turn that ended with a long
    # summary). The reader retries briefly instead of failing open on the first read.
    import json as _json
    path = _transcript(
        tmp_path,
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {}}]}},
    )

    def flush_then_noop(_secs):
        with open(path, "a") as fh:
            fh.write(_json.dumps(
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "late-flushed summary"}]}}) + "\n")

    # Patched on reviewer_stopcontinue, not on rl: `last_assistant_text` lives there too and
    # resolves this name in that module's globals (LIB_SPLIT_PLAN.md 5.3). Assertion unchanged.
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", flush_then_noop)
    assert rl.last_assistant_text(path, retries=3) == "late-flushed summary"


# Task 10 (#17): a NON-EMPTY read is not necessarily the final message. On 2026-09-24 the
# supervisor judged a turn by its opening paragraph (the summary landed 0.3 s later) and ordered
# finished work redone. Claude Code's Stop payload carries `last_assistant_message`.
def _text_event(text):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def _thinking_event():
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "thinking", "thinking": "..."}]}}


def test_a_non_empty_stale_read_waits_for_the_closing_summary(tmp_path, monkeypatch):
    path = _transcript(tmp_path, _text_event("Running everything now: #15, then #12"),
                       _thinking_event())

    def flush(_secs):
        with open(path, "a") as fh:
            fh.write(json.dumps(_text_event("Summary: all probes done")) + "\n")
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", flush)
    text = rl.last_assistant_text(path)
    assert text.endswith("Summary: all probes done")
    assert "Running everything now" in text


def test_the_payload_final_message_is_used_when_the_transcript_never_catches_up(
        tmp_path, monkeypatch):
    path = _transcript(tmp_path, _text_event("Running everything now"), _thinking_event())
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", lambda _s: None)
    text = rl.last_assistant_text(path, final_message="Summary: done")
    assert text.endswith("Summary: done") and "Running everything now" in text


def test_a_flushed_final_message_needs_no_wait(tmp_path, monkeypatch):
    path = _transcript(tmp_path, _text_event("Opening"), _text_event("Summary: done"))

    def fail(_s):
        raise AssertionError("must not wait once the final message is on disk")
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", fail)
    assert rl.last_assistant_text(path, final_message="Summary: done").endswith("Summary: done")
    assert rl.last_assistant_text(path).endswith("Summary: done")


def test_an_explicitly_empty_final_message_does_not_settle_on_a_text_ending(
        tmp_path, monkeypatch):
    # The payload says the closing message carried no text, so text at the end of the
    # transcript is stale: the reader must not settle on its first read.
    path = _transcript(tmp_path, _text_event("Opening paragraph"))
    waits = []
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", waits.append)
    assert rl.last_assistant_text(path, final_message="") == "Opening paragraph"
    assert len(waits) == 2, waits  # every retry was spent before giving up


def test_a_final_phrase_seen_earlier_in_the_turn_does_not_settle_a_stale_read(
        tmp_path, monkeypatch):
    # The phrase appeared mid-turn; the transcript still ends with stale output. Membership would
    # return the stale text; a terminal match waits, then appends the real final message.
    path = _transcript(tmp_path, _text_event("Plan: finish, then write All done."),
                       _text_event("Running the probes"), _thinking_event())
    waits = []
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", waits.append)
    text = rl.last_assistant_text(path, final_message="All done.")
    assert waits, "a stale read must be re-read"
    assert text.endswith("All done.") and "Running the probes" in text


def test_is_stop_ok_recognizes_the_sentinel():
    assert rl.is_stop_ok("STOP_OK") is True
    assert rl.is_stop_ok("stop_ok") is True
    assert rl.is_stop_ok("STOP_OK\n(task complete)") is True
    assert rl.is_stop_ok("Continue: run the remaining tests.") is False
    assert rl.is_stop_ok("This is a reasonable stopping point.") is False
    assert rl.is_stop_ok("") is False


def test_nudge_count_read_bump_reset(home):
    assert rl.read_nudge_count(PROJ, "c1") == 0          # default 0 when absent
    assert rl.bump_nudge_count(PROJ, "c1") == 1
    assert rl.bump_nudge_count(PROJ, "c1") == 2
    assert rl.read_nudge_count(PROJ, "c1") == 2
    assert rl.read_nudge_count(PROJ, "other") == 0       # isolated per session key
    rl.reset_nudge_count(PROJ, "c1")
    assert rl.read_nudge_count(PROJ, "c1") == 0


def test_max_nudges_default_floor_and_env_override(monkeypatch):
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_MAX_NUDGES", raising=False)
    assert rl.max_nudges() == 10
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_MAX_NUDGES", "3")
    assert rl.max_nudges() == 3
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_MAX_NUDGES", "0")
    assert rl.max_nudges() == 1                    # floored at 1
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_MAX_NUDGES", "garbage")
    assert rl.max_nudges() == 10                   # falls back on non-int


def test_stopcontinue_effort_default_low_and_env_override(monkeypatch):
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_EFFORT", raising=False)
    assert rl.stopcontinue_effort() == "low"       # snappy by default, not xhigh
    assert rl.stopcontinue_effort_config() == 'model_reasoning_effort="low"'
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_EFFORT", "medium")
    assert rl.stopcontinue_effort_config() == 'model_reasoning_effort="medium"'


def test_stop_block_output_shape():
    out = rl.stop_block_output("continue with X", "a notice")
    assert out["decision"] == "block"
    assert out["reason"] == "continue with X"
    assert out["systemMessage"] == "a notice"


def test_stop_supervise_prompt_has_one_final_message_placeholder():
    p = rl.STOP_SUPERVISE_PROMPT
    assert p.count("{") == 1 and p.count("}") == 1 and "{}" in p


def test_resume_by_id_argv_targets_the_id_not_last():
    argv = rl.resume_by_id_argv("sess-77", "/p", "workspace-write", rl.COMMIT_PROMPT)
    assert argv[1] == "exec"
    ci = argv.index("-C")
    assert argv[ci + 1] == "/p"
    si = argv.index("--sandbox")
    assert argv[si + 1] == "workspace-write"
    assert 'approval_policy="never"' in argv
    assert "--last" not in argv                      # by id, NOT cwd-filtered
    assert argv[-3:] == ["resume", "sess-77", rl.COMMIT_PROMPT]


def test_brief_capture_argv_emits_json_and_reads_stdin_dash():
    argv = rl.brief_capture_argv("/p")
    assert "--json" in argv                          # JSONL so thread.started id is capturable
    si = argv.index("--sandbox")
    assert argv[si + 1] == "read-only"
    assert 'approval_policy="never"' in argv
    assert 'model_reasoning_effort="low"' in argv     # brief is session-creation only -> fast
    assert argv[-1] == "-"


def test_brief_and_capture_text_extracts_session_id(tmp_path, monkeypatch):
    # Retargeted from the removed brief-from-file helper (see reviewer_lib's note on why it is
    # gone); this is the one path every brief now takes.
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "cap-xyz")
    sid, code, out, err = rl.brief_and_capture_text(str(tmp_path), "be strict\n")
    assert code == 0
    assert sid == "cap-xyz"


def test_ensure_session_briefs_persists_then_reuses_without_rebriefing(
    home, tmp_path, monkeypatch
):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "ensured-1")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    sid, err = rl.ensure_session(str(repo), "claudeX")
    assert sid == "ensured-1"
    assert err == ""
    assert rl.read_session_id(str(repo), "claudeX") == "ensured-1"
    # Second call must reuse the persisted id and NOT re-brief (fake id changed).
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "ensured-2")
    sid2, err2 = rl.ensure_session(str(repo), "claudeX")
    assert sid2 == "ensured-1"
    assert err2 == ""


def test_ensure_session_uses_default_brief_when_reviewerAgent_md_absent(
    home, tmp_path, monkeypatch
):
    # A missing project brief must NOT block commits: ensure_session falls back to the
    # built-in DEFAULT_BRIEF and still establishes a reviewer session.
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "defbrief-1")
    repo = tmp_path / "repo2"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # no reviewerAgent.md
    sid, err = rl.ensure_session(str(repo), "claudeY")
    assert sid == "defbrief-1"
    assert err == ""
    assert rl.read_session_id(str(repo), "claudeY") == "defbrief-1"


def test_ensure_session_returns_error_when_codex_cannot_start_a_session(
    home, tmp_path, monkeypatch
):
    # The only real failure now: codex cannot start a session at all (no thread.started).
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_EXIT", "7")
    repo = tmp_path / "repo3"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("strict\n")
    sid, err = rl.ensure_session(str(repo), "claudeW")
    assert sid is None
    assert "session" in err.lower()


# --------------------------------------------------------------------------
# stopContinue: do NOT nudge while the agent is PARKED on background work
# (a run_in_background task / sub-agent that auto-re-invokes the session on
# completion). See STOPCONTINUE_BG_WAIT_PLAN.md.
# --------------------------------------------------------------------------
def _human(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _bg_agent(tid):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Agent", "id": tid,
         "input": {"description": "d", "subagent_type": "Explore",
                   "prompt": "p", "run_in_background": True}}]}}


def _bg_bash(tid):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "id": tid,
         "input": {"command": "sleep 100", "run_in_background": True}}]}}


def _asst(text):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": text}]}}


def _task_notification(tool_use_id, task_id="t1", status="completed"):
    body = ("<task-notification>\n<task-id>{}</task-id>\n<tool-use-id>{}"
            "</tool-use-id>\n<status>{}</status>\n<summary>Agent finished</summary>\n"
            "</task-notification>".format(task_id, tool_use_id, status))
    return {"type": "user", "message": {"role": "user", "content": body}}


def test_background_agent_launch_without_completion_is_outstanding(tmp_path):
    path = _transcript(
        tmp_path,
        _human("go do it"),
        _bg_agent("toolu_X"),
        _asst("Launched the inventory agent; waiting on its harness-tracked completion."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_background_bash_launch_without_completion_is_outstanding(tmp_path):
    path = _transcript(
        tmp_path,
        _human("kick off the long acquisition"),
        _bg_bash("toolu_B"),
        _asst("The acquisition is running in the background; I'll check when it lands."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_completed_background_launch_is_not_outstanding(tmp_path):
    path = _transcript(
        tmp_path,
        _human("go do it"),
        _bg_agent("toolu_X"),
        _task_notification("toolu_X"),          # its completion re-invoked the session
        _asst("The agent finished; here are the results and next steps."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_foreground_agent_is_not_a_background_wait(tmp_path):
    # A blocking sub-agent returns an inline tool_result -- the turn did NOT end to wait, so
    # it must not count as outstanding background work. NOTE (Amendment 7): what identifies
    # it is the INLINE RESULT, not the absent run_in_background -- the subagent tools now
    # default to background, so the flag's absence means the opposite of what it used to.
    path = _transcript(
        tmp_path,
        _human("summarize the module"),
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Agent", "id": "toolu_F",
             "input": {"description": "d", "subagent_type": "Explore", "prompt": "p"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_F", "content": "the summary"}]}},
        _asst("Here is the summary."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_launch_before_last_human_prompt_is_out_of_scope(tmp_path):
    # A background launch from a PRIOR turn must not mute stopContinue in a later turn.
    path = _transcript(
        tmp_path,
        _human("first ask"),
        _bg_agent("toolu_OLD"),                 # launched last turn, never resolved here
        _human("new ask -- this resets the turn scope"),
        _asst("On it -- done with the new ask."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_launch_before_list_content_human_prompt_is_out_of_scope(tmp_path):
    # A human prompt whose content is a LIST with a text block is still a turn boundary, so a
    # background launch before it is out of scope (a prior turn's wait, not the current one).
    path = _transcript(
        tmp_path,
        _human("first ask"),
        _bg_agent("toolu_OLD"),                 # launched last turn, never resolved here
        {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "new ask delivered as a content list"}]}},
        _asst("On it -- done with the new ask."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_launch_before_last_task_notification_is_out_of_scope(tmp_path):
    # An unrelated completion notification starts a new turn; an earlier still-pending
    # launch is out of scope so a long autonomous session isn't muted forever.
    path = _transcript(
        tmp_path,
        _human("run the pipeline"),
        _bg_bash("toolu_STALE"),                # never gets a completion in this transcript
        _task_notification("toolu_OTHER", task_id="t2"),   # a DIFFERENT task completed
        _asst("That step is done; proceeding."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_missing_or_empty_transcript_is_not_outstanding(tmp_path):
    assert rl.has_outstanding_background_task("/no/such/file.jsonl") is False
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert rl.has_outstanding_background_task(str(empty)) is False
    assert rl.has_outstanding_background_task(None) is False


def _monitor_event(event_text, task_id="b2wprha2p"):
    body = ("<task-notification>\n<task-id>{}</task-id>\n<summary>Monitor event: "
            "\"train.log\"</summary>\n<event>{}</event>\n</task-notification>".format(
                task_id, event_text))
    return {"type": "user", "message": {"role": "user", "content": body}}


def test_active_monitor_event_boundary_is_outstanding(tmp_path):
    # A Monitor relays per-epoch <event> notifications; the agent is re-invoked, writes a
    # status, and stops. The current turn's boundary IS the monitor event -> parked waiting
    # for the next event (the Monitor tool instructs "keep working -- do not poll").
    ev = _monitor_event("epoch 2 loss 0.674 val_early_auroc 0.647")
    ev["timestamp"] = _now_iso(-60)
    path = _transcript(
        tmp_path,
        _human("start the training run and monitor the log"),
        ev,
        _asst("Epoch 2 landed; healthy. Waiting for the next event."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_monitor_event_before_a_later_human_prompt_is_not_outstanding(tmp_path):
    # If the human prompts AFTER a monitor event, the human prompt is the boundary ->
    # normal supervision, not a monitor wait.
    path = _transcript(
        tmp_path,
        _human("start training"),
        _monitor_event("epoch 2 loss 0.674"),
        _human("actually, pause and tell me the plan"),
        _asst("Here is the plan."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_terminal_completion_boundary_is_not_a_monitor_wait(tmp_path):
    # A completion notification (status=completed, NOT a monitor <event>) as the current
    # boundary means a task finished -> normal supervision, not a monitor wait.
    path = _transcript(
        tmp_path,
        _human("start training"),
        _monitor_event("epoch 4 loss 0.5"),
        _task_notification("toolu_JOB"),          # a real completion of some bg job
        _asst("The job finished; here are the results."),
    )
    assert rl.has_outstanding_background_task(path) is False


# --------------------------------------------------------------------------
# Active-Monitor LIFECYCLE (plan Amendment 2): the LAUNCH itself must suppress,
# from "Monitor started (task <id>...)" until TaskStop / terminal <status> /
# timeout expiry -- not only when the turn boundary is one of its <event>s.
# --------------------------------------------------------------------------
def _monitor_started(task_id="b4p4hoc2h", persistent=True, timeout_ms=0,
                     timestamp=None):
    mode = ("persistent -- runs until TaskStop or session end" if persistent
            else "timeout {}ms".format(timeout_ms))
    body = ("Monitor started (task {}, {}). You will be notified on each event. "
            "Keep working -- do not poll or sleep.".format(task_id, mode))
    ev = {"type": "user", "message": {"role": "user", "content": [
              {"type": "tool_result", "tool_use_id": "toolu_MON", "content": body}]},
          "toolUseResult": {"taskId": task_id, "timeoutMs": timeout_ms,
                            "persistent": persistent}}
    if timestamp:
        ev["timestamp"] = timestamp
    return ev


def _task_stop(task_id):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "TaskStop", "id": "toolu_STOP",
         "input": {"task_id": task_id}}]}}


def _nudge(text):
    # Our own stop-hook feedback as the harness injects it: isMeta, NOT a human prompt.
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": "Stop hook feedback:\n" + text}}


def test_active_persistent_monitor_suppresses_after_human_prompt(tmp_path):
    # The evidenced f0252bbf shape (transcript ~14945-14991): human asks a question
    # mid-run, agent re-arms the monitor coarser, stages prep work, stops. No <event>
    # has arrived yet -> the boundary is the human prompt, but the PERSISTENT monitor
    # is armed and active -> parked waiting, suppress.
    path = _transcript(
        tmp_path,
        _human("start training and monitor the log"),
        _monitor_started("b4uskx606", persistent=False, timeout_ms=3600000,
                         timestamp=_now_iso(-300)),
        _human("every 5th epoch seems too frequent since we are doing 100?"),
        _task_stop("b4uskx606"),
        _monitor_started("b4p4hoc2h", persistent=True, timestamp=_now_iso(-60)),
        _asst("Monitor re-armed at every-10th-epoch granularity."),
        _nudge("keep going"),
        _asst("Epoch 6 is mid-flight. Nothing further is actionable until the next event."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_taskstopped_monitor_is_not_outstanding(tmp_path):
    # An explicit TaskStop ends the monitor's suppression window (fail-open: even an
    # attempted stop) -> normal supervision resumes.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon1", persistent=True),
        _task_stop("bmon1"),
        _asst("Monitoring torn down."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_monitor_underlying_task_completion_deactivates(tmp_path):
    # The monitored task reached a terminal <status> -> nothing left to relay; the
    # monitor must not keep muting stopContinue.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon2", persistent=True),
        _task_notification("toolu_UNDER", task_id="bmon2"),
        _asst("The run finished; results below."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_other_task_completion_does_not_deactivate_monitor(tmp_path):
    # A DIFFERENT job completing must not end the monitor's suppression window.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon3", persistent=True, timestamp=_now_iso(-60)),
        _task_notification("toolu_OTHER", task_id="tother"),
        _asst("Unrelated job done; still monitoring."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_timeout_monitor_within_window_is_outstanding(tmp_path):
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon4", persistent=False, timeout_ms=3600000,
                         timestamp=now_iso),
        _asst("Watching for events."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_timeout_monitor_expired_is_not_outstanding(tmp_path):
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon5", persistent=False, timeout_ms=60000,
                         timestamp="2020-01-01T00:00:00.000Z"),
        _asst("Watching for events."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_timeout_monitor_without_timestamp_is_not_outstanding(tmp_path):
    # No parseable launch time -> we cannot bound the muting window -> fail-open
    # (prefer a wasted nudge over muting stopContinue indefinitely).
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bmon6", persistent=False, timeout_ms=3600000),
        _asst("Watching for events."),
    )
    assert rl.has_outstanding_background_task(path) is False


def _now_iso(offset_secs=0):
    from datetime import datetime, timedelta, timezone
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_secs)
    return dt.isoformat().replace("+00:00", "Z")


def _queue_op_notification(body):
    # A <task-notification> recorded as a queue-operation event (harness queues
    # simultaneous notifications instead of writing user events).
    return {"type": "queue-operation", "operation": "enqueue", "content": body}


def _attachment_notification(body):
    # A <task-notification> delivered as a queued_command attachment.
    return {"type": "attachment",
            "attachment": {"type": "queued_command", "prompt": body,
                           "commandMode": "task-notification"}}


def _terminal_body(task_id):
    return ("<task-notification>\n<task-id>{}</task-id>\n<tool-use-id>toolu_T</tool-use-id>\n"
            "<status>completed</status>\n<summary>Monitor stream ended</summary>\n"
            "</task-notification>".format(task_id))


def _event_body(task_id, event_text):
    return ("<task-notification>\n<task-id>{}</task-id>\n<summary>Monitor event</summary>\n"
            "<event>{}</event>\n</task-notification>".format(task_id, event_text))


def test_terminal_status_via_queue_operation_deactivates(tmp_path):
    # The evidenced f0252bbf leak: the monitor's terminal <status> notification was
    # recorded ONLY as a queue-operation event, never as a user event -- it must still
    # end the suppression window.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bleak1", persistent=True, timestamp=_now_iso()),
        _queue_op_notification(_terminal_body("bleak1")),
        _asst("Moving on after the crash."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_terminal_status_via_attachment_deactivates(tmp_path):
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bleak2", persistent=True, timestamp=_now_iso()),
        _attachment_notification(_terminal_body("bleak2")),
        _asst("Moving on after the crash."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_stale_persistent_monitor_is_not_outstanding(tmp_path):
    # Staleness backstop: a persistent monitor with no signal (launch or <event>) inside
    # the staleness window no longer suppresses -- bounded muting, never indefinite.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bstale", persistent=True, timestamp="2020-01-01T00:00:00.000Z"),
        _asst("Long quiet."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_queued_event_refreshes_staleness(tmp_path):
    # An <event> for the monitor's task-id (even via the queue-operation path) is a fresh
    # signal: old launch + fresh event -> still active.
    old = _now_iso(-7200)
    ev = _queue_op_notification(_event_body("bfresh", "epoch 40 loss 0.01"))
    ev["timestamp"] = _now_iso(-30)
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bfresh", persistent=True, timestamp=old),
        ev,
        _asst("Epoch 40 landed; waiting."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_monitor_stale_secs_env_override(tmp_path, monkeypatch):
    # A launch 2h old: inactive under the 1h default, active when the window is widened.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bwide", persistent=True, timestamp=_now_iso(-7200)),
        _asst("Quiet but within the widened window."),
    )
    assert rl.has_outstanding_background_task(path) is False
    monkeypatch.setenv("REVIEWER_MONITOR_STALE_SECS", "10800")
    assert rl.has_outstanding_background_task(path) is True


def test_persistent_monitor_without_timestamp_is_not_outstanding(tmp_path):
    # Unified no-timestamp rule (generalizes the timeout case): no parseable launch time
    # -> the muting window cannot be bounded -> fail-open.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bnots", persistent=True),
        _asst("Waiting."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_partial_tool_use_result_does_not_clobber_text_parsed_mode(tmp_path):
    # toolUseResult carrying ONLY taskId (no persistent/timeoutMs keys) must not turn a
    # text-confirmed persistent monitor into an inactive zero-timeout one.
    ev = _monitor_started("bmon7", persistent=True, timestamp=_now_iso())
    ev["toolUseResult"] = {"taskId": "bmon7"}
    path = _transcript(tmp_path, _human("monitor the run"), ev, _asst("Waiting."))
    assert rl.has_outstanding_background_task(path) is True


# --- Amendment 3b: the monitor-event BOUNDARY rule must respect the lifecycle ---
def _monitor_event_ts(event_text, task_id="b2wprha2p", timestamp=None):
    ev = _monitor_event(event_text, task_id=task_id)
    if timestamp:
        ev["timestamp"] = timestamp
    return ev


def test_boundary_event_for_taskstopped_monitor_is_not_outstanding(tmp_path):
    # The evidenced 21:12Z sign-off: last boundary is an <event> for a monitor the agent
    # then explicitly TaskStop'd -- the stale boundary must not keep suppressing.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bdone", persistent=True, timestamp=_now_iso(-600)),
        _monitor_event_ts("epoch 23 loss 0.236", task_id="bdone",
                          timestamp=_now_iso(-120)),
        _task_stop("bdone"),
        _asst("Run complete; monitor torn down; here is the final summary."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_boundary_event_for_terminal_ended_monitor_is_not_outstanding(tmp_path):
    # Terminal <status> via the queued path also ends boundary-rule suppression.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("bqdone", persistent=True, timestamp=_now_iso(-600)),
        _monitor_event_ts("epoch 30 loss 0.05", task_id="bqdone",
                          timestamp=_now_iso(-120)),
        _queue_op_notification(_terminal_body("bqdone")),
        _asst("Stream ended; wrapping up."),
    )
    assert rl.has_outstanding_background_task(path) is False


def test_stale_boundary_event_is_not_outstanding(tmp_path):
    # A boundary event older than the staleness window no longer suppresses, even with
    # no visible launch (bounded muting everywhere).
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_event_ts("epoch 2 loss 0.674", task_id="bold",
                          timestamp="2020-01-01T00:00:00.000Z"),
        _asst("Quiet for a very long time."),
    )
    assert rl.has_outstanding_background_task(path) is False


# --- Amendment 4: harness-state liveness -- the background-job state file is ground
# truth for in-flight monitors, so a quiet-but-healthy monitor (event cadence slower
# than the staleness window) keeps suppressing for its whole lifecycle. ---
def _job_state(tmp_path, session_id, fan):
    import json as _json
    job = tmp_path / "job"
    job.mkdir(parents=True, exist_ok=True)
    (job / "state.json").write_text(_json.dumps(
        {"sessionId": session_id, "fan": fan}))
    return str(job)


def _quiet_monitor_transcript(tmp_path, task_id="bquiet"):
    # Launch 2h old with no <event> since -> stale under Amendment 3 alone.
    return _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started(task_id, persistent=True, timestamp=_now_iso(-7200)),
        _asst("Quiet for two hours; the sweep arms are long."),
    )


def test_quiet_monitor_alive_in_job_state_is_outstanding(tmp_path, monkeypatch):
    # The evidenced 2026-07-05 21:34:43 failure: arm completions ~112min apart exceed the
    # 1h staleness window, but the harness job state still lists the monitor in flight.
    path = _quiet_monitor_transcript(tmp_path)
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "bquiet", "kind": "monitor", "label": "sweep", "startedAt": 1}]))
    assert rl.has_outstanding_background_task(path) is True


def test_quiet_monitor_absent_or_done_in_job_state_stays_stale(tmp_path, monkeypatch):
    # Fan no longer lists the monitor (harness removes ended tasks) -> staleness verdict
    # stands; a lingering entry already marked doneAt must not suppress either.
    path = _quiet_monitor_transcript(tmp_path)
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, []))
    assert rl.has_outstanding_background_task(path) is False
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "bquiet", "kind": "monitor", "doneAt": 5}]))
    assert rl.has_outstanding_background_task(path) is False


def test_transcript_ended_monitor_beats_lingering_fan_entry(tmp_path, monkeypatch):
    # Terminal <status> in the transcript wins over a stale fan entry: a harness state
    # bug must not mute stopContinue for an ended monitor.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_started("blinger", persistent=True, timestamp=_now_iso(-7200)),
        _queue_op_notification(_terminal_body("blinger")),
        _asst("The monitored run crashed; moving on."),
    )
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "blinger", "kind": "monitor", "startedAt": 1}]))
    assert rl.has_outstanding_background_task(path) is False


def test_job_state_for_other_session_is_ignored(tmp_path, monkeypatch):
    # A foreign job's state (sessionId mismatch) must never suppress this session's stops.
    path = _quiet_monitor_transcript(tmp_path)
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, "some-other-session", [
        {"id": "bquiet", "kind": "monitor", "startedAt": 1}]))
    assert rl.has_outstanding_background_task(path) is False


def test_corrupt_job_state_falls_back_to_staleness(tmp_path, monkeypatch):
    path = _quiet_monitor_transcript(tmp_path)
    job = tmp_path / "job"
    job.mkdir(parents=True, exist_ok=True)
    (job / "state.json").write_text("not json {{{")
    monkeypatch.setenv("CLAUDE_JOB_DIR", str(job))
    assert rl.has_outstanding_background_task(path) is False


def test_no_job_dir_keeps_foreground_behavior(tmp_path, monkeypatch):
    # Foreground sessions have no job state; staleness remains their only bound.
    path = _quiet_monitor_transcript(tmp_path)
    monkeypatch.delenv("CLAUDE_JOB_DIR", raising=False)
    assert rl.has_outstanding_background_task(path) is False


# --- Amendment 5: job-state liveness covers ALL harness-tracked background tasks, not
# only monitors. Evidenced 2026-07-06: a poll shell launched via Bash run_in_background
# in an EARLIER turn (fan kind "shell") was invisible to both the kind=="monitor" filter
# and the current-turn launch scan, so every later stop consulted the supervisor. ---
def _old_turn_bg_shell_transcript(tmp_path):
    # The bg shell launch sits BEFORE the last human-prompt boundary, so the
    # current-turn launch scan cannot see it.
    return _transcript(
        tmp_path,
        _human("watch the replica pack"),
        _bg_bash("toolu_POLL"),
        _asst("Poll script launched in the background; parking on it."),
        _human("status update please"),
        _asst("Two arms done, third training; parking on the poll shell again."),
    )


def test_quiet_bg_shell_alive_in_job_state_is_outstanding(tmp_path, monkeypatch):
    # The evidenced failure shape: fan lists the shell in flight the whole afternoon
    # while user-prompt-triggered turns kept flipping bg_wait False.
    path = _old_turn_bg_shell_transcript(tmp_path)
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "bshell1", "kind": "shell",
         "label": "bash poll_w0_pack.sh", "startedAt": 1783370559244}]))
    assert rl.has_outstanding_background_task(path) is True


def test_todo_fan_entries_never_suppress(tmp_path, monkeypatch):
    # Todos share the fan array but are plan items, not background work: kind "todo",
    # startedAt 0/absent (open todos carry doneAt 0, which is falsy). An open todo must
    # not mute stopContinue.
    path = _old_turn_bg_shell_transcript(tmp_path)
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "todo:8", "kind": "todo", "label": "append step",
         "startedAt": 0, "doneAt": 0},
        {"id": "todo:12", "kind": "todo", "label": "running gpu replica",
         "startedAt": 0},
        {"id": "todo:13", "kind": "todo", "label": "offline analyses"},
    ]))
    assert rl.has_outstanding_background_task(path) is False


def test_future_task_kind_with_real_start_suppresses(tmp_path, monkeypatch):
    # Exclude-list design: an unknown harness task kind with a real startedAt is live
    # background work the FIRST time it appears, not a third recurrence of this bug.
    path = _old_turn_bg_shell_transcript(tmp_path)
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "bagent9", "kind": "agent", "label": "bg subagent",
         "startedAt": 12345}]))
    assert rl.has_outstanding_background_task(path) is True


def test_transcript_completed_shell_beats_lingering_fan_entry(tmp_path, monkeypatch):
    # A terminal <status> notification for the shell's task-id wins over a stale fan
    # entry, exactly as it does for monitors (shell terminal notifications carry
    # <task-id> + <status>, verified in the 788727d2 transcript).
    path = _transcript(
        tmp_path,
        _human("watch the replica pack"),
        _bg_bash("toolu_POLL"),
        _asst("Poll script launched in the background; parking on it."),
        _queue_op_notification(_terminal_body("bshell1")),
        _human("anything else?"),
        _asst("Pack complete and artifacts pulled; nothing is running now."),
    )
    sid = Path(path).stem
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, sid, [
        {"id": "bshell1", "kind": "shell", "startedAt": 1}]))
    assert rl.has_outstanding_background_task(path) is False


def test_fresh_boundary_event_without_visible_launch_is_outstanding(tmp_path):
    # Backstop retained: launch invisible (compaction/resume), fresh <event> boundary ->
    # still a monitor wait.
    path = _transcript(
        tmp_path,
        _human("monitor the run"),
        _monitor_event_ts("epoch 2 loss 0.674", task_id="bfreshb",
                          timestamp=_now_iso(-60)),
        _asst("Epoch 2 landed; waiting."),
    )
    assert rl.has_outstanding_background_task(path) is True


def test_partial_tool_use_result_keeps_text_parsed_timeout(tmp_path):
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ev = _monitor_started("bmon8", persistent=False, timeout_ms=3600000,
                          timestamp=now_iso)
    ev["toolUseResult"] = {"taskId": "bmon8"}
    path = _transcript(tmp_path, _human("monitor the run"), ev, _asst("Waiting."))
    assert rl.has_outstanding_background_task(path) is True


# --------------------------------------------------------------------------
# Trailing STOP_OK (docs/plans/stopcontinue_trailing_stop_ok.md): a supervisor that explains
# itself and THEN answers puts the sentinel on the last line. First-line-only parsing read that
# permit-the-stop verdict as a continue directive and nudged a finished task to the cap.
# --------------------------------------------------------------------------
_REASONED_STOP_OK = (
    "The operator's stopping condition (\"all ten boxes ticked and placeholder text gone\") is "
    "fully met, and there is no authorized work left to do.\n\nSTOP_OK"
)


def test_is_stop_ok_accepts_the_sentinel_as_the_last_line():
    assert rl.is_stop_ok(_REASONED_STOP_OK) is True
    assert rl.is_stop_ok("No authorized work remains.\n\nstop_ok\n") is True
    assert rl.stop_ok_position(_REASONED_STOP_OK) == "last"


def test_is_stop_ok_trailing_line_tolerates_emphasis_and_punctuation_only():
    assert rl.is_stop_ok("Nothing is left.\n\n**STOP_OK**") is True
    assert rl.is_stop_ok("Nothing is left.\n\n`STOP_OK`") is True
    assert rl.is_stop_ok("Nothing is left.\n\nSTOP_OK.") is True
    # Exact token on the trailing line: STOP_OKAY is not the sentinel.
    assert rl.is_stop_ok("Nothing is left.\n\nSTOP_OKAY") is False
    # Only emphasis / backticks / trailing punctuation are tolerated -- not quote wrappers.
    assert rl.is_stop_ok('Nothing is left.\n\n"STOP_OK"') is False
    assert rl.is_stop_ok("Nothing is left.\n\n'STOP_OK'") is False


def test_is_stop_ok_trailing_line_must_be_the_bare_token_not_a_prefix():
    # A continue directive can END with a sentence that merely mentions the sentinel. Prefix
    # matching stays confined to the first line, where the prompt puts the verdict.
    assert rl.is_stop_ok(
        "Run the failing test and fix it.\n\nSTOP_OK is not warranted until the tests pass."
    ) is False


def test_is_stop_ok_mid_reply_sentinel_is_contradictory_and_not_honored():
    text = "The task list is complete.\n\nSTOP_OK\n\nHowever, first run the linter and fix it."
    assert rl.is_stop_ok(text) is False
    assert rl.stop_ok_position(text) == "mid"


def test_stop_ok_position_first_and_none():
    assert rl.stop_ok_position("STOP_OK -- the task is genuinely complete.") == "first"
    assert rl.stop_ok_position("STOP_OK\n(task complete)") == "first"
    assert rl.stop_ok_position("STOP_OK.") == "first"
    # Whole word on the first line: a longer token is not the sentinel (parity with the Codex
    # parser and the trailing-line rule; a bare startswith accepted this).
    assert rl.stop_ok_position("STOP_OKAY") is None
    assert rl.is_stop_ok("STOP_OKAY -- the task is complete.") is False
    assert rl.is_stop_ok("STOP_OK_NOW") is False
    assert rl.stop_ok_position("Continue: run the remaining tests.") is None
    assert rl.stop_ok_position("") is None
    assert rl.stop_ok_position("   \n\n  ") is None
    assert rl.stop_ok_position(None) is None


# Observed 2026-09-29: the supervisor ended its reasoning with the verdict as its own final sentence
# on the same line, and the hook relayed a permitted stop to the agent as "KEEP WORKING".
_SAME_LINE_STOP_OK = (
    "The diagnosis request is complete. The proposed final message reports no further authorized "
    "work; it only offers an unrequested code fix. STOP_OK"
)


def test_is_stop_ok_accepts_the_sentinel_as_the_final_sentence_of_the_last_line():
    assert rl.is_stop_ok(_SAME_LINE_STOP_OK) is True
    assert rl.stop_ok_position(_SAME_LINE_STOP_OK) == "last"
    assert rl.is_stop_ok("Nothing is left to do. **STOP_OK**.") is True
    assert rl.is_stop_ok("Nothing is left to do: STOP_OK") is True
    assert rl.is_stop_ok("Earlier reasoning.\n\nNothing is left to do. STOP_OK!") is True


def test_is_stop_ok_sentinel_mentioned_inside_a_directive_sentence_is_not_a_permit():
    assert rl.is_stop_ok("Run the tests first, and only then reply STOP_OK") is False
    assert rl.is_stop_ok("Fix the linter errors before STOP_OK.") is False
    assert rl.is_stop_ok("Nothing is left to do. STOP_OKAY") is False
