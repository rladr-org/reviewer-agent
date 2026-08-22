# SPDX-License-Identifier: MPL-2.0
"""Reviewer-session isolation.

One Claude session gets one Codex reviewer; no Claude session may share, inherit, or
silently attach to another's. Four hazards are pinned down here:

  1. UNIDENTIFIABLE SESSION. A hook payload carrying neither `session_id` nor
     `transcript_path` fell back to the literal state key "default" -- one slot shared by
     every such session AND every project.
  2. SESSION-LESS RESUME. `codex resume --last` attaches to whatever Codex session ran most
     recently on the MACHINE, which is another Claude session's reviewer.
  3. CONCURRENT BRIEF. Two hooks in one Claude session (parallel subagent tool calls, or
     SessionStart overlapping a first commit) can both find no stored id and both brief; the
     second write orphaned the first reviewer, leaving the two hooks auditing against
     different sessions.
  4. TORN PUBLISH. The id was written by truncating in place, so a concurrent reader could
     observe an empty file, read None, and brief a second reviewer.

The PROJECT dimension is covered by tests/test_project_session_isolation.py: reviewer state is
keyed by (project, session, role, backend), so one Claude session that moves between two
repositories gets a reviewer per repository rather than reusing the first one's brief.
"""
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


def state_dir():
    return rl.reviewer_home() / "state"


def make_repo(tmp_path, name, brief=True):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if brief:
        (repo / "reviewerAgent.md").write_text("be strict\n")
    return repo


# --------------------------------------------------------------------------
# 1. an unidentifiable Claude session gets NO persisted reviewer
# --------------------------------------------------------------------------
def test_unidentified_session_id_is_not_persisted(home):
    rl.write_session_id(PROJ, None, "codex-X")
    assert rl.read_session_id(PROJ, None) is None
    assert not (state_dir() / "sess_default.codex").exists()
    rl.clear_session_id(PROJ, None)  # must stay a safe no-op


def test_unidentified_pi_session_is_not_persisted(home):
    rl.write_pi_session(PROJ, None, "pi-X")
    assert rl.read_pi_session(PROJ, None) is None
    assert not (state_dir() / "sess_default.pi").exists()


def test_ensure_session_never_hands_out_a_shared_default_slot(home, tmp_path, monkeypatch):
    # A "default" slot left by an older install (or by another unidentifiable session) must
    # never be resumed: brief a fresh reviewer instead.
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "fresh-1")
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "sess_default.codex").write_text("foreign-sid\n")
    repo = make_repo(tmp_path, "repo_default")
    sid, err = rl.ensure_session(str(repo), None)
    assert err == ""
    assert sid == "fresh-1"


# --------------------------------------------------------------------------
# 2. no helper may resume the globally-most-recent Codex session
# --------------------------------------------------------------------------
def test_no_helper_builds_a_session_less_resume():
    src = Path(rl.__file__).read_text(encoding="utf-8")
    assert '"--last"' not in src
    assert "'--last'" not in src
    assert not hasattr(rl, "resume_argv")
    assert not hasattr(rl, "run_codex_resume")
    # resuming BY ID stays: it names the session it attaches to.
    assert hasattr(rl, "resume_by_id_argv")
    assert hasattr(rl, "run_codex_resume_by_id")


# --------------------------------------------------------------------------
# 3. concurrent briefs converge on one reviewer
# --------------------------------------------------------------------------
def test_write_session_id_if_absent_keeps_the_stored_id(home):
    rl.write_session_id(PROJ, "claude-C", "first")
    assert rl.write_session_id_if_absent(PROJ, "claude-C", "second") == "first"
    assert rl.read_session_id(PROJ, "claude-C") == "first"


def test_write_session_id_if_absent_publishes_when_slot_is_free(home):
    assert rl.write_session_id_if_absent(PROJ, "claude-D", "mine") == "mine"
    assert rl.read_session_id(PROJ, "claude-D") == "mine"


def test_concurrent_brief_adopts_the_stored_reviewer(home, tmp_path, monkeypatch):
    # The race: we read no id, start briefing, and the other hook's brief lands first.
    # ensure_session must adopt the STORED id rather than overwrite it and orphan it.
    #
    # Patches brief_and_capture_TEXT: ensure_session used to pipe the project brief FILE via
    # brief_and_capture when one existed, and now always sends the COMPOSED brief as text. The
    # behavior under test (compare-and-set adoption) is unchanged -- only the seam moved. Worth
    # noting that while this patched the stale name, the call fell through to the real codex
    # binary and started a live session; conftest now pins the fake binary so that cannot recur.
    repo = make_repo(tmp_path, "repo_race")

    def brief_then_lose(root, brief_text, timeout=None, **kwargs):
        rl.write_session_id(str(repo), "claude-R", "winner")
        return "loser", 0, "", ""

    # Patched on reviewer_backends, not on rl: `ensure_session` lives there too and resolves
    # this name in that module's globals (LIB_SPLIT_PLAN.md 5.3). Assertions are unchanged.
    monkeypatch.setattr(reviewer_backends, "brief_and_capture_text", brief_then_lose)
    sid, err = rl.ensure_session(str(repo), "claude-R")
    assert err == ""
    assert sid == "winner"
    assert rl.read_session_id(str(repo), "claude-R") == "winner"


def test_concurrent_brief_adopts_the_stored_reviewer_without_project_brief(
    home, tmp_path, monkeypatch
):
    # Same race with NO reviewerAgent.md at the project root, so compose_brief falls back to
    # DEFAULT_BRIEF. One code path now, but both branches of that fallback stay covered.
    repo = make_repo(tmp_path, "repo_race2", brief=False)

    def brief_then_lose(root, brief_text, timeout=None, **kwargs):
        rl.write_session_id(str(repo), "claude-R2", "winner2")
        return "loser2", 0, "", ""

    monkeypatch.setattr(reviewer_backends, "brief_and_capture_text", brief_then_lose)
    sid, err = rl.ensure_session(str(repo), "claude-R2")
    assert err == ""
    assert sid == "winner2"
    assert rl.read_session_id(str(repo), "claude-R2") == "winner2"


# --------------------------------------------------------------------------
# 4. publishing an id is atomic
# --------------------------------------------------------------------------
def test_session_id_overwrite_is_published_by_rename(home):
    rl.write_session_id(PROJ, "claude-T", "codex-1")
    f = rl.session_id_file(PROJ, "claude-T")
    before = f.stat().st_ino
    rl.write_session_id(PROJ, "claude-T", "codex-2")
    assert rl.read_session_id(PROJ, "claude-T") == "codex-2"
    assert f.stat().st_ino != before


def test_pi_session_overwrite_is_published_by_rename(home):
    rl.write_pi_session(PROJ, "claude-P", "pi-1")
    f = rl.pi_session_file(PROJ, "claude-P")
    before = f.stat().st_ino
    rl.write_pi_session(PROJ, "claude-P", "pi-2")
    assert rl.read_pi_session(PROJ, "claude-P") == "pi-2"
    assert f.stat().st_ino != before


def test_audited_stage_record_is_published_by_rename(home, tmp_path):
    repo = make_repo(tmp_path, "repo_stage", brief=False)
    rl.record_audited_stage(str(repo), "claude-A", "sid-1")
    f = rl.audited_stage_file(str(repo), "claude-A")
    before = f.stat().st_ino
    rl.record_audited_stage(str(repo), "claude-A", "sid-1")
    assert rl.read_audited_stage(str(repo), "claude-A")["round"] == 2
    assert f.stat().st_ino != before


def test_publish_falls_back_when_hardlinks_are_unsupported(home, monkeypatch):
    # os.link is the compare-and-set, but not every filesystem supports it. A reviewer
    # session must still be established (last-writer-wins) rather than the hook crashing.
    def unsupported(src, dst):
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr(rl.os, "link", unsupported)
    assert rl.write_session_id_if_absent(PROJ, "claude-L", "mine") == "mine"
    assert rl.read_session_id(PROJ, "claude-L") == "mine"


# --------------------------------------------------------------------------
# 5. no OTHER per-session state may be shared through a "default"/"None" slot either
# --------------------------------------------------------------------------
def test_unidentified_session_shares_no_audited_stage(home, tmp_path):
    # A shared audited-stage record would tell one session's reviewer that files another
    # session staged were "already audited", leaving them unreviewed.
    repo = make_repo(tmp_path, "repo_audit", brief=False)
    rl.record_audited_stage(str(repo), None, "sid-1")
    assert rl.read_audited_stage(str(repo), None) is None
    assert not list(state_dir().glob("*.None.audited.json"))


def test_unidentified_session_shares_no_operator_watermark(home):
    # A shared watermark would let one session mark operator messages as "already seen" for
    # another, whose reviewer would then never be shown them.
    rl.write_operator_watermark(PROJ, None, "uuid-1")
    assert rl.read_operator_watermark(PROJ, None) is None
    assert not (state_dir() / "opmark_None").exists()


def test_unidentified_session_still_has_a_bounded_nudge_budget(home):
    # DELIBERATE ASYMMETRY (pinned, not red-first: this locks in existing behavior). For the
    # reviewer id, watermark and audited stage, SHARING is the hazard and absence degrades
    # safely. For the stopContinue nudge budget the reverse holds: not persisting means an
    # unbounded budget, i.e. a runaway continuation loop. So this one keeps its shared slot.
    assert rl.bump_nudge_count(PROJ, None) == 1
    assert rl.read_nudge_count(PROJ, None) == 1


def test_operator_watermark_is_published_by_rename(home):
    rl.write_operator_watermark(PROJ, "claude-W", "uuid-1")
    f = rl.operator_watermark_file(PROJ, "claude-W")
    before = f.stat().st_ino
    rl.write_operator_watermark(PROJ, "claude-W", "uuid-2")
    assert rl.read_operator_watermark(PROJ, "claude-W") == "uuid-2"
    assert f.stat().st_ino != before
