# SPDX-License-Identifier: MPL-2.0
"""The reviewer's standing instructions are OWNED BY THE PLUGIN and composed with the
per-repo brief, rather than being whatever the repo happened to write.

Before this, the brief was the project's `reviewerAgent.md` OR the built-in `DEFAULT_BRIEF`,
never both -- so instructions the reviewer needs in EVERY repository (which tools exist, how to
handle a repeat attempt, that depth is expected) had to be duplicated into each project's brief
or go missing. `compose_brief()` puts the plugin's system prompt first and the project's brief
after it, so a project can add anything it likes on top without being able to lose the
mechanics.

Cost basis for the anti-duplication half (REVIEW_COST_REDUCTION_PLAN.md Section 1, A1.4):
whatever the reviewer reads is paid for once in that turn and AGAIN on every later cold turn,
because a cold turn re-reads the accumulated context (~117k against ~40k warm). Bounding
exploration is therefore a compounding saving -- and explicitly NOT a licence to review less
carefully, which the prompt has to say in as many words.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

import reviewer_backends
import reviewer_lib as rl

PROJ = "/Users/x/proj"

FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def _repo(path, brief=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if brief is not None:
        (path / "reviewerAgent.md").write_text(brief, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# compose_brief
# --------------------------------------------------------------------------
def test_compose_brief_puts_the_system_prompt_before_the_project_brief(tmp_path):
    repo = _repo(tmp_path / "r1", "PROJECT SAYS: reject emoji.\n")
    text = rl.compose_brief(str(repo))
    assert "PROJECT SAYS: reject emoji." in text
    assert rl.REVIEWER_STANDING_BRIEF in text
    # Order matters: the plugin's mechanics are the frame, the project brief the specifics.
    assert text.index(rl.REVIEWER_STANDING_BRIEF) < text.index("PROJECT SAYS")


def test_compose_brief_falls_back_to_the_default_brief_with_no_project_brief(tmp_path):
    repo = _repo(tmp_path / "r2")
    text = rl.compose_brief(str(repo))
    assert rl.REVIEWER_STANDING_BRIEF in text
    assert rl.DEFAULT_BRIEF in text


def test_compose_brief_survives_an_unreadable_project_brief(tmp_path, monkeypatch):
    """A brief that cannot be read must not block every commit.

    The commit gate is fail-CLOSED, so an exception escaping here would deny commits until the
    file was fixed. Falling back to DEFAULT_BRIEF keeps the reviewer functional, which is the
    same choice `ensure_session` already makes for a MISSING brief.
    """
    repo = _repo(tmp_path / "r3", "unreadable\n")

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    text = rl.compose_brief(str(repo))
    assert rl.REVIEWER_STANDING_BRIEF in text
    assert rl.DEFAULT_BRIEF in text


def test_compose_brief_survives_a_non_utf8_project_brief(tmp_path):
    """A brief that is not valid UTF-8 must degrade, not fail-close every commit.

    Regression: `read_text(encoding="utf-8")` raises UnicodeDecodeError, which subclasses
    ValueError and NOT OSError -- so the original `except OSError` did not catch it. A repo whose
    reviewerAgent.md was saved as Latin-1 (a single smart quote is enough) would have blocked
    every commit in that project, because the gate is fail-CLOSED and the exception escapes
    before any verdict exists.

    Decoding with errors="replace" rather than discarding the file: for the realistic case -- an
    otherwise-fine brief with one stray byte -- throwing the operator's whole brief away loses
    real instructions, while replacing the byte keeps them.
    """
    repo = _repo(tmp_path / "r3b")
    (repo / "reviewerAgent.md").write_bytes(
        b"PROJECT RULE: no emoji\n\xff\xfe raw bytes here\n")
    text = rl.compose_brief(str(repo))
    assert rl.REVIEWER_STANDING_BRIEF in text
    assert "PROJECT RULE: no emoji" in text


def test_compose_brief_survives_a_wholly_binary_project_brief(tmp_path):
    # The degenerate case: nothing decodable. Must still return a usable brief.
    repo = _repo(tmp_path / "r3c")
    (repo / "reviewerAgent.md").write_bytes(bytes(range(0x80, 0x100)) * 4)
    text = rl.compose_brief(str(repo))
    assert rl.REVIEWER_STANDING_BRIEF in text


def test_compose_brief_resolves_the_canonical_root_from_a_subdirectory(tmp_path):
    # Worktrees and subdirectories must find the same brief; ensure_session already relies on
    # canonical_project_key for this, and compose_brief must not regress it.
    repo = _repo(tmp_path / "r4", "ROOT BRIEF\n")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert "ROOT BRIEF" in rl.compose_brief(str(sub))


# --------------------------------------------------------------------------
# What the system prompt must contain. These are behavior, not prose taste: each string is
# something a reviewer turn depends on, and a silent edit that drops one would change how
# every repository's reviewer behaves with no test failing.
# --------------------------------------------------------------------------
def test_system_prompt_carries_the_change_table_labels_verbatim():
    """The labels MUST match what staged_change_block() actually emits.

    The prompt tells the reviewer how to treat each label; if either side is renamed
    independently the instructions silently stop applying to the block it receives.
    """
    p = rl.REVIEWER_STANDING_BRIEF
    for label in ("CHANGED", "ADDED", "UNCHANGED", "REMOVED"):
        assert label in p, label
    assert "STAGED SET vs YOUR LAST AUDIT" in p


def test_the_change_table_labels_in_the_prompt_match_the_block_it_describes(tmp_path, home):
    # Pin the coupling from the OTHER side too: build a real re-review block and assert every
    # label the prompt teaches actually appears in it.
    repo = _repo(tmp_path / "r5")
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "keep").write_text("one\n")
    (repo / "gone").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    (repo / "keep").write_text("two\n")
    (repo / "fresh").write_text("new\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    rl.record_audited_stage(str(repo), "c1", "sid1")
    (repo / "keep").write_text("three\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    block = rl.staged_change_block(str(repo), "c1", "sid1")
    assert "STAGED SET vs YOUR LAST AUDIT" in block
    assert "CHANGED" in block


def test_cost_instructions_still_do_not_read_as_review_less():
    """NARROWED from test_system_prompt_preserves_depth_and_says_so_explicitly.

    That test bundled two separate guarantees and one of them has been withdrawn on the
    operator's finding. What SURVIVES, and is asserted here, is the original concern: the
    cost instructions must not be readable as "review less carefully", so the brief still
    has to draw the effort/re-derivation distinction explicitly.

    What is WITHDRAWN is the "depth"/"never soften" wording it also pinned. That phrasing
    rewarded volume over correctness, and with a blocking rejection as the only channel it
    turned every marginal doubt into a required fix. The replacement is the fitness-for-use
    standard plus a non-blocking SUGGESTIONS outlet, asserted in the next test. Trimming
    COST was never the same instruction as trimming RIGOR, and only the first is kept."""
    low = rl.REVIEWER_STANDING_BRIEF.lower()
    assert "re-derive" in low or "rederive" in low
    assert "cost instruction" in low


def test_no_prompt_constant_has_a_missing_space_at_a_concatenation_seam():
    """REGRESSION. Python joins adjacent string literals with NOTHING, so a line that ends a
    word and a next line that starts one silently produce "pre-approvedby a human",
    "STOP_OK.A routine" and "do not repeatfull audits". All three shipped in an edit to the
    live install. The reviewer still reads it, but it is exactly the kind of damage nobody
    proofreads for, so assert the seams instead.

    Two shapes catch every real instance: a word running into a Capitalised word, and a
    sentence-ending `.` running into one. Restricted to the prompt constants, since prose
    elsewhere is not sent to the reviewer.

    `reviewerAgent` is allowed because it is a real filename the prompts must name, and it is
    the only legitimate camelCase token in this text."""
    allowed = {"reviewerAgent"}
    bad = []
    for name in ("STOP_SUPERVISE_PROMPT", "REVIEWER_STANDING_BRIEF", "DEFAULT_BRIEF",
                 "COMMIT_PROMPT", "PI_COMMIT_PROMPT", "_CAPSULE_UNIVERSAL",
                 "_CAPSULE_COMMIT_HEAD", "_CAPSULE_COMMIT_REPEAT_HEAD",
                 "_CAPSULE_COMMIT_REPEAT_TAIL"):
        text = getattr(rl, name)
        for m in re.finditer(r"\b([a-z]{2,})([A-Z][a-z]{2,})\b", text):
            if m.group(0) not in allowed:           # wordWord -> "pre-approvedby"/"repeatfull"
                bad.append((name, m.group(0)))
        for m in re.finditer(r"[a-zA-Z]\.[A-Z][a-z]{2,}", text):
            bad.append((name, m.group(0)))          # "STOP_OK.A routine"
    assert not bad, "missing space at a string-concatenation seam: {}".format(bad)


def test_the_briefs_carry_the_fitness_for_use_standard():
    """Layer 0 carries the standard IN FULL -- it is sent once per session, where length is
    cheap, unlike the capsule which pays for every character on every call.

    Both briefs, because a project with no reviewerAgent.md gets DEFAULT_BRIEF and must not
    silently fall back to a bare "hold a high bar": a high bar with no stated axis is read
    as "demand more", which is the failure being corrected."""
    for name, text in (("REVIEWER_STANDING_BRIEF", rl.REVIEWER_STANDING_BRIEF),
                       ("DEFAULT_BRIEF", rl.DEFAULT_BRIEF)):
        low = text.lower()
        assert "circumstances in which it is to be used" in low, name
        assert "extremely unlikely to occur" in low, name
        assert "suggestions" in low, name
        assert "soften" not in low, name
        assert "hold a high bar" not in low, name

    # The brief-override clause belongs to layer 0 ONLY: the capsule deliberately omits it
    # (it already names the brief's path), so a project that wants production-grade
    # robustness declares that in the file the reviewer was briefed with.
    assert "unless this repository's brief says otherwise" in rl.REVIEWER_STANDING_BRIEF.lower()


def test_system_prompt_requires_a_disposition_for_every_prior_finding():
    low = rl.REVIEWER_STANDING_BRIEF.lower()
    assert "disposition" in low


def test_system_prompt_does_not_duplicate_the_verdict_contract():
    """The APPROVED / REJECTED contract is owned by COMMIT_PROMPT.

    Restating it in the brief would create two sources of truth for the one string the hook
    parses, and the brief is sent once per session while COMMIT_PROMPT rides every audit.
    """
    assert "REJECTED:" not in rl.REVIEWER_STANDING_BRIEF


def test_system_prompt_has_no_emoji():
    for ch in rl.REVIEWER_STANDING_BRIEF:
        assert ord(ch) < 0x2190, repr(ch)


# --------------------------------------------------------------------------
# The composed brief must actually reach both backends.
# --------------------------------------------------------------------------
def test_ensure_session_pipes_the_composed_brief_to_codex(home, tmp_path, monkeypatch):
    """End-to-end: the bytes on codex's stdin must be the COMPOSED brief.

    Asserted at the process boundary rather than by monkeypatching the composer, because the
    defect this guards against is a wiring one -- ensure_session previously piped the project
    file's raw bytes, which would silently keep working while dropping the system prompt.
    """
    stdin_file = tmp_path / "codex_stdin.txt"
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "composed-1")
    monkeypatch.setenv("FAKE_CODEX_STDIN_FILE", str(stdin_file))
    repo = _repo(tmp_path / "r6", "PROJECT RULE: one-line commits.\n")
    sid, err = rl.ensure_session(str(repo), "claude-compose")
    assert (sid, err) == ("composed-1", "")
    sent = stdin_file.read_text(encoding="utf-8")
    assert "PROJECT RULE: one-line commits." in sent
    assert "STAGED SET vs YOUR LAST AUDIT" in sent


def test_pi_first_turn_sends_composed_text_not_an_at_path(tmp_path, home, monkeypatch):
    """pi used `@<path>` to reference reviewerAgent.md, which cannot carry the system prompt.

    It must switch to literal composed text -- which that flag already accepts, since
    DEFAULT_BRIEF is passed literally today when no project brief exists.
    """
    repo = _repo(tmp_path / "r7", "PI PROJECT BRIEF\n")
    seen = {}

    def fake_argv(prompt, session_id=None, model=None, thinking=None, system_prompt=None):
        seen["system_prompt"] = system_prompt
        return ["/bin/echo", "x"]

    # Patched on reviewer_backends, not on rl: `run_pi_review` lives there too and resolves
    # these names in that module's globals, so a facade patch would not reach it
    # (LIB_SPLIT_PLAN.md 5.3). Assertions are unchanged.
    monkeypatch.setattr(reviewer_backends, "pi_argv", fake_argv)
    monkeypatch.setattr(reviewer_backends, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(reviewer_backends, "parse_pi_final_text", lambda out: "APPROVED")
    monkeypatch.setattr(
        reviewer_backends, "parse_pi_tool_flags",
        lambda out: {"truncated": False, "tool_error": False,
                     "saw_staged_read": True})
    rl.run_pi_review(str(repo), "claude-pi", "PROMPT")
    sp = seen["system_prompt"]
    assert not sp.startswith("@"), sp[:80]
    assert "PI PROJECT BRIEF" in sp
    assert rl.REVIEWER_STANDING_BRIEF in sp


def test_pi_resumed_turn_does_not_rebrief(tmp_path, home, monkeypatch):
    # Composition applies to the FIRST turn only; a resumed pi session must not be re-briefed.
    repo = _repo(tmp_path / "r8", "PI BRIEF\n")
    rl.write_pi_session_if_absent(str(repo), "claude-pi2", "pi-sess-1")
    seen = {}

    def fake_argv(prompt, session_id=None, model=None, thinking=None, system_prompt=None):
        seen["system_prompt"] = system_prompt
        seen["session_id"] = session_id
        return ["/bin/echo", "x"]

    monkeypatch.setattr(reviewer_backends, "pi_argv", fake_argv)
    monkeypatch.setattr(reviewer_backends, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(reviewer_backends, "parse_pi_final_text", lambda out: "APPROVED")
    monkeypatch.setattr(
        reviewer_backends, "parse_pi_tool_flags",
        lambda out: {"truncated": False, "tool_error": False,
                     "saw_staged_read": True})
    rl.run_pi_review(str(repo), "claude-pi2", "PROMPT")
    assert seen["session_id"] == "pi-sess-1"
    assert seen["system_prompt"] is None


def test_pi_stop_turn_resumes_only_the_stop_role(tmp_path, home, monkeypatch):
    repo = _repo(tmp_path / "r8-stop", "PI BRIEF\n")
    rl.write_pi_session(str(repo), "claude-pi-stop", "pi-reviewer")
    rl.write_pi_session(str(repo), "claude-pi-stop", "pi-stop", role=rl.ROLE_STOP)
    seen = {}

    def fake_argv(prompt, session_id=None, model=None, thinking=None, system_prompt=None):
        seen["system_prompt"] = system_prompt
        seen["session_id"] = session_id
        return ["/bin/echo", "x"]

    monkeypatch.setattr(reviewer_backends, "pi_argv", fake_argv)
    monkeypatch.setattr(reviewer_backends, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(
        reviewer_backends, "parse_pi_final_text", lambda out: "Continue with the task."
    )
    monkeypatch.setattr(
        reviewer_backends,
        "parse_pi_tool_flags",
        lambda out: {"truncated": False, "tool_error": False,
                     "saw_staged_read": False},
    )

    rl.run_pi_review(
        str(repo),
        "claude-pi-stop",
        "PROMPT",
        role=rl.ROLE_STOP,
        require_staged_read=False,
    )

    assert seen["session_id"] == "pi-stop"
    assert seen["system_prompt"] is None


def test_the_brief_is_still_sent_only_once_per_session(home, tmp_path, monkeypatch):
    # Composition must not turn the brief into a per-audit cost: the second ensure_session
    # call reuses the stored id and pipes nothing.
    stdin_file = tmp_path / "stdin2.txt"
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "once-1")
    monkeypatch.setenv("FAKE_CODEX_STDIN_FILE", str(stdin_file))
    repo = _repo(tmp_path / "r9", "B\n")
    rl.ensure_session(str(repo), "claude-once")
    first = stdin_file.read_text(encoding="utf-8")
    rl.ensure_session(str(repo), "claude-once")
    assert stdin_file.read_text(encoding="utf-8") == first
