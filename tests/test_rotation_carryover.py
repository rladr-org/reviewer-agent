# SPDX-License-Identifier: MPL-2.0
"""One-shot carryover of a retired reviewer's outstanding findings to its successor.

When the commit reviewer rotates mid reject/retry cycle (audit-limit rotation on a REJECTED
non-retry commit), the fresh successor used to review with NO memory of the retired session's
outstanding fixes -- `staged_change_block` goes silent on a reviewer-session-id mismatch -- so it
diverged and produced a DIFFERENT fix list one commit later, which read as a second reviewer. This
carries the retired findings to the successor's FIRST review as an advisory it must VERIFY
(re-raise / resolve / correct) rather than assume, then clears the one-shot marker.
"""
import subprocess

import pytest

import reviewer_lib as rl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    return tmp_path


def _repo(path, brief=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if brief is not None:
        (path / "reviewerAgent.md").write_text(brief, encoding="utf-8")
    return str(path)


# --- the one-shot marker: set / read / clear -----------------------------------
def test_carryover_marker_round_trips(tmp_path):
    repo = _repo(tmp_path / "p1")
    assert rl.commit_carryover_pending(repo, "s1") is False
    rl.set_commit_carryover(repo, "s1")
    assert rl.commit_carryover_pending(repo, "s1") is True
    rl.clear_commit_carryover(repo, "s1")
    assert rl.commit_carryover_pending(repo, "s1") is False


def test_carryover_marker_is_keyed_per_session(tmp_path):
    repo = _repo(tmp_path / "p2")
    rl.set_commit_carryover(repo, "s1")
    assert rl.commit_carryover_pending(repo, "s2") is False


# --- retirement arms it only when findings are preserved (a rejection) ----------
def test_retire_on_rejection_arms_the_carryover(tmp_path):
    repo = _repo(tmp_path / "p3", "B\n")
    rl.write_session_id(repo, "s3", "codex-old")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. do X", proj=repo, claude_session_id="s3")
    assert rl.retire_commit_reviewer_session(repo, "s3", "codex-old", preserve_last_review=True)
    assert rl.commit_carryover_pending(repo, "s3") is True


def test_retire_on_approval_does_not_arm_the_carryover(tmp_path):
    repo = _repo(tmp_path / "p4", "B\n")
    rl.write_session_id(repo, "s4", "codex-old2")
    rl.save_review("APPROVED", proj=repo, claude_session_id="s4")
    assert rl.retire_commit_reviewer_session(repo, "s4", "codex-old2", preserve_last_review=False)
    assert rl.commit_carryover_pending(repo, "s4") is False


# --- the successor's first-review advisory block --------------------------------
def test_carryover_block_carries_findings_and_demands_verification(tmp_path):
    repo = _repo(tmp_path / "p5", "B\n")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. rename foo to bar",
                   proj=repo, claude_session_id="s5")
    rl.set_commit_carryover(repo, "s5")
    block = rl.carryover_findings_block(repo, "s5")
    assert "rename foo to bar" in block            # inherited findings
    assert "VERIFY" in block.upper()               # verify, don't assume
    assert "not assume" in block.lower()


def test_no_carryover_block_without_the_marker(tmp_path):
    repo = _repo(tmp_path / "p6", "B\n")
    rl.save_review("REJECTED\n1. x", proj=repo, claude_session_id="s6")
    assert rl.carryover_findings_block(repo, "s6") == ""


def test_no_carryover_block_when_findings_are_missing(tmp_path):
    repo = _repo(tmp_path / "p6b", "B\n")
    rl.set_commit_carryover(repo, "s6b")           # armed, but no saved review to carry
    assert rl.carryover_findings_block(repo, "s6b") == ""


def test_fresh_session_carryover_comes_from_the_block_not_staged_change_block(tmp_path):
    # The rotation carryover is delivered by the commit hook via carryover_findings_block, NOT by
    # staged_change_block (which stays SILENT on the fresh / no-record path). Keeping it out of
    # staged_change_block is what lets the hook consume the marker only after a delivered review.
    repo = _repo(tmp_path / "p7", "B\n")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. add a regression test",
                   proj=repo, claude_session_id="s7")
    rl.set_commit_carryover(repo, "s7")
    assert rl.staged_change_block(repo, "s7", "codex-fresh") == ""    # silent (delta path)
    block = rl.carryover_findings_block(repo, "s7")                   # the carryover source
    assert "add a regression test" in block
    assert "VERIFY" in block.upper()


# --- carryover: non-consuming READ; the hook consumes only after a DELIVERED review --------------
def test_carryover_findings_block_read_is_non_consuming(tmp_path):
    # carryover_findings_block only READS -- it never deletes the marker (the commit hook consumes
    # it, after a delivered review). Two reads return the block and the marker stays armed.
    repo = _repo(tmp_path / "p8", "B\n")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. do X", proj=repo, claude_session_id="s8")
    rl.set_commit_carryover(repo, "s8")
    assert "do X" in rl.carryover_findings_block(repo, "s8")
    assert "do X" in rl.carryover_findings_block(repo, "s8")   # non-consuming read
    assert rl.commit_carryover_pending(repo, "s8") is True


def test_carryover_consumed_only_after_a_delivered_review(tmp_path):
    # The commit hook's contract: consume the marker ONLY when the review actually delivered the
    # carryover (a non-empty block). A delivered+completed review therefore ends with it consumed.
    repo = _repo(tmp_path / "pd", "B\n")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. do X", proj=repo, claude_session_id="sd")
    rl.set_commit_carryover(repo, "sd")
    gen = rl.commit_carryover_generation(repo, "sd")
    block = rl.carryover_findings_block(repo, "sd")
    assert "do X" in block                                     # delivered
    if block:                                                  # commit_hook: consume iff delivered
        rl.consume_commit_carryover(repo, "sd", gen)
    assert rl.commit_carryover_pending(repo, "sd") is False    # consumed after delivery


def test_consume_only_deletes_the_matching_generation(tmp_path):
    # Round-6 regression: consumption is generation-tagged. If a rotation armed a NEWER generation
    # after this review read the marker, consuming the OLD generation must NOT delete the newer
    # marker -- the newer findings are left for their own successor. The matching generation deletes.
    repo = _repo(tmp_path / "pg", "B\n")
    rl.set_commit_carryover(repo, "sg")
    old_gen = rl.commit_carryover_generation(repo, "sg")
    rl.set_commit_carryover(repo, "sg")               # an overlapping rotation re-arms (new generation)
    assert rl.consume_commit_carryover(repo, "sg", old_gen) is False  # stale gen -> not deleted
    assert rl.commit_carryover_pending(repo, "sg") is True            # newer marker survives
    new_gen = rl.commit_carryover_generation(repo, "sg")
    assert new_gen != old_gen
    assert rl.consume_commit_carryover(repo, "sg", new_gen) is True   # matching gen -> deleted
    assert rl.commit_carryover_pending(repo, "sg") is False


def test_carryover_not_consumed_when_delivery_fails(tmp_path, monkeypatch):
    # Finding-1 regression: if the verdict read fails at prompt-build the block is empty (delivery
    # failed) even though findings exist. The hook must NOT consume it -- the marker stays armed so
    # the retry re-delivers, instead of being suppressed after a completed-but-undelivered review.
    import pathlib

    repo = _repo(tmp_path / "pe", "B\n")
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. do Y", proj=repo, claude_session_id="se")
    rl.set_commit_carryover(repo, "se")
    gen = rl.commit_carryover_generation(repo, "se")   # read the generation before the read monkeypatch

    def flaky_read(self, *a, **k):
        raise OSError("EIO")

    monkeypatch.setattr(pathlib.Path, "read_text", flaky_read)
    block = rl.carryover_findings_block(repo, "se")
    assert block == ""                                         # delivery failed (transient read)
    if block:                                                  # commit_hook: consume iff delivered
        rl.consume_commit_carryover(repo, "se", gen)
    assert rl.commit_carryover_pending(repo, "se") is True     # NOT consumed -> retry re-delivers


def test_retire_clean_discards_stale_carryover(tmp_path):
    # A clean retirement (no preserved findings) discards any stale carryover left by a prior
    # preserved rotation, so a later fresh successor is not handed findings that no longer apply.
    repo = _repo(tmp_path / "p9c", "B\n")
    rl.write_session_id(repo, "s9c", "codex-a")
    rl.set_commit_carryover(repo, "s9c")                       # a stale carryover is armed
    assert rl.commit_carryover_pending(repo, "s9c") is True
    rl.save_review("APPROVED", proj=repo, claude_session_id="s9c")
    assert rl.retire_commit_reviewer_session(repo, "s9c", "codex-a", preserve_last_review=False)
    assert rl.commit_carryover_pending(repo, "s9c") is False   # cleared on clean retirement


def test_rotate_session_clears_the_carryover_marker(tmp_path):
    # Finding-2 regression: an explicit reviewer-session rotation must clear the carryover marker
    # too, so a stale marker cannot pair with unrelated newly-saved findings for a later successor.
    repo = _repo(tmp_path / "p9d", "B\n")
    rl.write_session_id(repo, "s9d", "codex-b")
    rl.set_commit_carryover(repo, "s9d")
    assert rl.commit_carryover_pending(repo, "s9d") is True
    rl.rotate_session(repo, "s9d")
    assert rl.commit_carryover_pending(repo, "s9d") is False   # cleared by rotate_session


# --- best-effort state ops (a path error must not crash the commit hook) ---------
def test_carryover_state_ops_swallow_oserror(tmp_path, monkeypatch):
    # A state-path failure (ENAMETOOLONG etc.) must not crash the commit hook: every carryover state
    # op is best-effort. The short digest stem keeps the filename bounded so this should not happen,
    # but a swallow is the belt-and-suspenders backstop.
    import pathlib

    import reviewer_state

    repo = _repo(tmp_path / "p10")
    rl.set_commit_carryover(repo, "s10")                 # arm a real marker first
    gen = rl.commit_carryover_generation(repo, "s10")

    def boom(*a, **k):
        raise OSError("ENAMETOOLONG")

    monkeypatch.setattr(reviewer_state, "_atomic_write_text", boom)
    rl.set_commit_carryover(repo, "s10")  # write path boomed: must not raise

    monkeypatch.setattr(pathlib.Path, "unlink", boom)
    assert rl.consume_commit_carryover(repo, "s10", gen) is False  # unlink boomed -> swallowed
    rl.clear_commit_carryover(repo, "s10")  # unlink boomed: must not raise


def test_consume_swallows_lock_acquisition_failure(tmp_path, monkeypatch):
    # consume_commit_carryover runs before the commit hook emits its response, so it must NEVER raise
    # -- including when the audit lock's own mkdir/open fails. Regression: the lock was acquired
    # OUTSIDE the try, so an OSError there terminated the hook with no JSON output.
    import reviewer_state

    repo = _repo(tmp_path / "pl")
    rl.set_commit_carryover(repo, "sl")
    gen = rl.commit_carryover_generation(repo, "sl")

    class BoomLock:
        def __enter__(self):
            raise OSError("cannot acquire lock")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(reviewer_state, "_reviewer_session_audit_lock", lambda: BoomLock())
    assert rl.consume_commit_carryover(repo, "sl", gen) is False   # swallowed, no raise
    assert rl.commit_carryover_pending(repo, "sl") is True         # marker untouched
