# SPDX-License-Identifier: MPL-2.0
"""Session-scoped reviewer scratch: per-generation dirs keyed by reviewer_session_id, rotation
cleanup, mtime-lease orphan sweep, warn-only disk reporting, and prompt guidance.

See docs/plans/temp_scratch_rotation_cleanup.md. These are the RED tests: they define the feature's
contract before the implementation exists.
"""
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

import reviewer_lib as rl

FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")
PROJ = "/Users/x/proj"


@pytest.fixture
def scratch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("REVIEWER_SCRATCH_ROOT", str(tmp_path / "scratch"))
    return tmp_path


def _dir_for(reviewer_session_id, create=False):
    return Path(rl.commit_scratch_dir(reviewer_session_id, create=create))


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


# --------------------------------------------------------------------------
# scratch_key + commit_scratch_dir: per-id path, filesystem-safe, stable, lazy-create
# --------------------------------------------------------------------------
def test_scratch_key_rejects_falsy_and_is_filesystem_safe():
    assert rl.scratch_key("") is None
    assert rl.scratch_key(None) is None
    key = rl.scratch_key("pi:abc/def ghi")
    assert key and "/" not in key and " " not in key
    assert rl.scratch_key("pi:abc/def ghi") == key  # deterministic


def test_commit_scratch_dir_is_lazy_then_created(scratch_env):
    d = rl.commit_scratch_dir("sess-1", create=False)
    assert d is not None and not Path(d).exists()
    d2 = _dir_for("sess-1", create=True)
    assert d2.is_dir() and Path(d) == d2  # stable path for the same id


def test_commit_scratch_dir_falsy_id_is_none(scratch_env):
    assert rl.commit_scratch_dir("", create=True) is None
    assert rl.commit_scratch_dir(None, create=True) is None


def test_distinct_ids_get_distinct_dirs(scratch_env):
    assert _dir_for("sess-A", create=True) != _dir_for("sess-B", create=True)


def test_create_renews_the_mtime_lease(scratch_env):
    d = _dir_for("sess-1", create=True)
    _age(d, 10_000)
    assert d.stat().st_mtime < time.time() - 5_000
    _dir_for("sess-1", create=True)  # renew lease
    assert d.stat().st_mtime > time.time() - 100


# --------------------------------------------------------------------------
# remove: best-effort, idempotent, tolerant of falsy id
# --------------------------------------------------------------------------
def test_remove_is_idempotent_and_null_safe(scratch_env):
    d = _dir_for("sess-1", create=True)
    (d / "big.bin").write_text("x" * 100)
    rl.remove_commit_scratch_dir("sess-1")
    assert not d.exists()
    rl.remove_commit_scratch_dir("sess-1")  # again: no raise
    rl.remove_commit_scratch_dir(None)      # falsy: no raise


# --------------------------------------------------------------------------
# sweep: mtime lease, NOT the persistent session-id files
# --------------------------------------------------------------------------
def test_sweep_removes_stale_keeps_fresh(scratch_env):
    stale = _dir_for("dead", create=True)
    fresh = _dir_for("live", create=True)
    _age(stale, 100_000)
    rl.sweep_orphan_scratch_dirs(max_age_s=3_600)
    assert not stale.exists() and fresh.exists()


def test_sweep_removes_ended_session_whose_id_file_lingers(scratch_env):
    # The gate's finding: an ended session's backend id file persists (only rotation unlinks it),
    # so liveness cannot come from that file -- the stale mtime lease must still reclaim the dir.
    rl.write_session_id(PROJ, "claude-x", "ended-sid")
    d = _dir_for("ended-sid", create=True)
    _age(d, 100_000)
    rl.sweep_orphan_scratch_dirs(max_age_s=3_600)
    assert not d.exists()


def test_sweep_protect_keys_keeps_named_even_if_old(scratch_env):
    d = _dir_for("keepme", create=True)
    _age(d, 100_000)
    rl.sweep_orphan_scratch_dirs(max_age_s=3_600, protect_keys=[rl.scratch_key("keepme")])
    assert d.exists()


def test_sweep_with_no_root_is_noop(scratch_env):
    rl.sweep_orphan_scratch_dirs(max_age_s=3_600)  # root absent: no raise


# --------------------------------------------------------------------------
# rotation cleanup: per-generation, both call paths
# --------------------------------------------------------------------------
def test_retire_removes_scratch_for_that_reviewer_id(scratch_env):
    rl.write_session_id(PROJ, "claude-r", "sid-1")
    d = _dir_for("sid-1", create=True)
    assert rl.retire_commit_reviewer_session(PROJ, "claude-r", "sid-1")
    assert not d.exists()


def test_rotate_session_removes_current_reviewer_scratch(scratch_env):
    rl.write_session_id(PROJ, "claude-r", "sid-1")
    d = _dir_for("sid-1", create=True)
    rl.rotate_session(PROJ, "claude-r")
    assert not d.exists()


def test_concurrent_rotation_is_safe_via_per_generation_paths(scratch_env):
    # Rotating S1 removes ONLY S1's dir; the next reviewer (S2) resolves to a DIFFERENT path, so it
    # can never recreate or reuse a path S1's in-flight audit still holds.
    rl.write_session_id(PROJ, "claude-r", "S1")
    d1 = _dir_for("S1", create=True)
    rl.rotate_session(PROJ, "claude-r")
    assert not d1.exists()
    assert _dir_for("S2", create=False) != d1


# --------------------------------------------------------------------------
# backend TMPDIR injection: audit subprocess env is scoped to the scratch dir
# --------------------------------------------------------------------------
def test_streaming_audit_scopes_tmpdir_to_scratch_dir(scratch_env, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    seen = scratch_env / "seen_tmpdir.txt"
    monkeypatch.setenv("FAKE_CODEX_DUMP_TMPDIR", str(seen))
    rl.reset_last_invocation()
    scratch = _dir_for("sid-1", create=True)
    rl.run_codex_streaming_audit(
        "sid-1", str(scratch_env), "workspace-write", "AUDIT",
        timeout=30, first_output_timeout=5, idle_timeout=10, scratch_dir=str(scratch))
    assert seen.read_text().strip() == str(scratch)


# --------------------------------------------------------------------------
# warn-only disk reporting: a line or None, never raises, never affects the verdict
# --------------------------------------------------------------------------
def _fake_free(monkeypatch, free_bytes):
    """Report `free_bytes` free on every path. An NFS mount reported 8 EiB free (2026-09 Ubuntu
    host), so a merely "impossibly high" threshold is not a test of anything."""
    import reviewer_state
    real = reviewer_state.shutil.disk_usage
    monkeypatch.setattr(reviewer_state.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=free_bytes))


def test_low_disk_warning_warns_below_threshold(scratch_env, monkeypatch):
    _dir_for("x", create=True)  # ensure the root exists
    monkeypatch.setenv("REVIEWER_DISK_WARN_BYTES", str(10 ** 9))
    _fake_free(monkeypatch, 10 ** 6)
    w = rl.low_disk_warning()
    assert w and "low disk" in w.lower()


def test_low_disk_warning_silent_above_threshold(scratch_env, monkeypatch):
    _dir_for("x", create=True)
    monkeypatch.setenv("REVIEWER_DISK_WARN_BYTES", "0")  # nothing is below 0 bytes free
    assert rl.low_disk_warning() is None


def test_low_disk_warning_missing_root_does_not_raise(scratch_env, monkeypatch):
    monkeypatch.setenv("REVIEWER_DISK_WARN_BYTES", str(10 ** 9))
    _fake_free(monkeypatch, 10 ** 6)
    # Root does not exist (no dir created): must fall back to an existing ancestor, never raise.
    assert rl.low_disk_warning() is not None


# --------------------------------------------------------------------------
# prompt guidance: the commit audit prompt tells the reviewer to use $TMPDIR
# --------------------------------------------------------------------------
def test_commit_prompt_carries_tmpdir_guidance():
    assert "TMPDIR" in rl.COMMIT_PROMPT


# --------------------------------------------------------------------------
# SessionStart runs the sweep UNCONDITIONALLY (even with no active codex session)
# --------------------------------------------------------------------------
def test_session_start_sweeps_orphans_unconditionally(scratch_env, monkeypatch):
    import session_start_hook
    stale = _dir_for("dead", create=True)
    _age(stale, 10 ** 7)
    monkeypatch.setenv("REVIEWER_SCRATCH_TTL_S", "3600")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(scratch_env)})))
    session_start_hook.main()
    assert not stale.exists()
