# SPDX-License-Identifier: MPL-2.0
"""`/reviewerSession` -- inspect the reviewer session, and retire it on demand.

Automatic rotation is covered here alongside the manual escape hatch. The explicit command still
has one concrete job beyond "the session seems confused": the brief is sent once per session, so
manual rotation makes a changed brief reach a live session immediately.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

PROJ = "/Users/x/proj"

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "reviewer_session_toggle.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def run_toggle(args, home_dir, env_extra=None):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home_dir
    # Reviewer state is per (project, session), and the command acts on the project it is
    # invoked in -- so the test has to name the same project it seeded rather than letting the
    # subprocess inherit the suite's own checkout.
    env["CLAUDE_PROJECT_DIR"] = PROJ
    env.pop("REVIEWER_CONFIG_FILE", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, TOGGLE] + args, capture_output=True, text=True, env=env
    )


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


# --------------------------------------------------------------------------
# rotate_session
# --------------------------------------------------------------------------
def test_rotate_removes_the_codex_session_so_the_next_call_rebriefs(
    home, tmp_path, monkeypatch
):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "first-sid")
    repo = _repo(tmp_path / "rot1")
    sid, _ = rl.ensure_session(str(repo), "claude-rot")
    assert sid == "first-sid"

    cleared = rl.rotate_session(str(repo), "claude-rot")
    assert any("codex" in c for c in cleared), cleared
    assert rl.read_session_id(str(repo), "claude-rot") is None

    # The NEXT gated action briefs afresh, which is the whole point: this is how a changed
    # REVIEWER_SYSTEM_PROMPT reaches a session that is already running.
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "second-sid")
    sid2, err = rl.ensure_session(str(repo), "claude-rot")
    assert (sid2, err) == ("second-sid", "")


def test_rotate_is_idempotent(home):
    # Rotating twice, or rotating a session that has no state, must succeed and report nothing.
    assert rl.rotate_session(PROJ, "claude-none") == []
    rl.write_session_id(PROJ, "claude-x", "s1")
    assert rl.rotate_session(PROJ, "claude-x") != []
    assert rl.rotate_session(PROJ, "claude-x") == []


def test_rotate_clears_the_pi_session_and_the_audited_record(home, tmp_path):
    repo = str(_repo(tmp_path / "rot2"))
    rl.write_session_id(repo, "claude-both", "codex-1")
    rl.write_pi_session(repo, "claude-both", "pi-1")
    rl.record_audited_stage(repo, "claude-both", "codex-1")
    audited = rl.audited_stage_file(repo, "claude-both")
    assert audited.exists()

    rl.rotate_session(repo, "claude-both")
    assert rl.read_session_id(repo, "claude-both") is None
    assert rl.read_pi_session(repo, "claude-both") is None
    assert not audited.exists(), "a record the new session cannot remember must not survive"


def test_rotate_touches_only_the_named_claude_session(home, tmp_path):
    repo = str(_repo(tmp_path / "rot3"))
    rl.write_session_id(repo, "claude-A", "a1")
    rl.write_session_id(repo, "claude-B", "b1")
    rl.write_pi_session(repo, "claude-B", "pib1")
    rl.record_audited_stage(repo, "claude-B", "b1")

    rl.rotate_session(repo, "claude-A")
    assert rl.read_session_id(repo, "claude-A") is None
    assert rl.read_session_id(repo, "claude-B") == "b1"
    assert rl.read_pi_session(repo, "claude-B") == "pib1"
    assert rl.audited_stage_file(repo, "claude-B").exists()


def test_rotate_clears_the_audited_record_of_this_project_only(home, tmp_path):
    # One Claude session can gate commits in more than one repository, and each has its own
    # reviewer and its own audited record. An operator typing `/reviewerSession rotate` in one
    # of them is not asking to retire a reviewer they are not looking at -- possibly one
    # mid-review in another window -- so the other repository's record survives.
    r1 = str(_repo(tmp_path / "rotp1"))
    r2 = str(_repo(tmp_path / "rotp2"))
    rl.write_session_id(r1, "claude-multi", "m1")
    rl.write_session_id(r2, "claude-multi", "m2")
    rl.record_audited_stage(r1, "claude-multi", "m1")
    rl.record_audited_stage(r2, "claude-multi", "m2")

    rl.rotate_session(r1, "claude-multi")

    assert not rl.audited_stage_file(r1, "claude-multi").exists()
    assert rl.audited_stage_file(r2, "claude-multi").exists()
    assert rl.read_session_id(r2, "claude-multi") == "m2"


def test_a_session_id_with_glob_metacharacters_cannot_clear_another_session(home, tmp_path):
    """The audited records are found by suffix match, not by glob.

    `_sanitize()` rewrites only "/" and spaces, so a session id containing "*" would, under a
    glob, expand into a pattern matching every other session's record and delete them. Session
    ids are normally UUIDs, but the id comes from a hook payload and this is a delete path.
    """
    repo = str(_repo(tmp_path / "rotg"))
    rl.write_session_id(repo, "claude-victim", "v1")
    rl.record_audited_stage(repo, "claude-victim", "v1")
    rl.rotate_session(repo, "*")
    assert rl.read_session_id(repo, "claude-victim") == "v1"
    assert rl.audited_stage_file(repo, "claude-victim").exists()


def test_rotate_without_a_session_id_is_a_noop_not_a_wildcard(home, tmp_path):
    """An unidentifiable session must clear NOTHING.

    Same reasoning as session_id_file() and audited_stage_file() returning None for a falsy id:
    there is no shared slot, so there is nothing this call could legitimately own -- and a glob
    that treated an empty id as a wildcard would wipe every other session's reviewer.
    """
    repo = str(_repo(tmp_path / "rot4"))
    rl.write_session_id(repo, "claude-keep", "k1")
    rl.record_audited_stage(repo, "claude-keep", "k1")
    for empty in (None, "", "   "):
        assert rl.rotate_session(repo, empty) == []
    # A missing PROJECT is the same kind of no-op, for the same reason: nothing is owned.
    assert rl.rotate_session("", "claude-keep") == []
    assert rl.read_session_id(repo, "claude-keep") == "k1"
    assert rl.audited_stage_file(repo, "claude-keep").exists()


# --------------------------------------------------------------------------
# the /reviewerSession command
# --------------------------------------------------------------------------
def test_show_reports_the_session_without_mutating_it(home, tmp_path):
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-show", "shown-1")
    p = run_toggle([], hd, {"CLAUDE_CODE_SESSION_ID": "claude-show"})
    assert p.returncode == 0, p.stderr
    assert "shown-1" in p.stdout
    assert rl.read_session_id(PROJ, "claude-show") == "shown-1"


def test_show_says_so_when_there_is_no_session_yet(home, tmp_path):
    p = run_toggle([], str(tmp_path / "rh"), {"CLAUDE_CODE_SESSION_ID": "claude-empty"})
    assert p.returncode == 0
    assert "no reviewer session" in p.stdout.lower()


def test_show_reports_the_claude_backends_own_session(home, tmp_path):
    hd = str(tmp_path / "rh")
    rl.write_claude_session(PROJ, "claude-c", "claude-thread-1")
    p = run_toggle([], hd, {"CLAUDE_CODE_SESSION_ID": "claude-c",
                            "REVIEWER_BACKEND": "claude"})
    assert p.returncode == 0, p.stderr
    assert "claude-thread-1" in p.stdout


def test_show_names_every_other_backends_leftover_sessions(home, tmp_path):
    # Switching backend mid-session leaves the previous one's slots on disk, and `rotate` clears
    # all of them -- so a report that named only one of the two others would understate what the
    # operator is about to retire.
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-mix", "codex-left")
    rl.write_pi_session(PROJ, "claude-mix", "pi-left")
    p = run_toggle([], hd, {"CLAUDE_CODE_SESSION_ID": "claude-mix",
                            "REVIEWER_BACKEND": "claude"})
    assert p.returncode == 0, p.stderr
    assert "codex-left" in p.stdout
    assert "pi-left" in p.stdout


def test_rotate_via_the_command_clears_the_session(home, tmp_path):
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-cmd", "cmd-1")
    p = run_toggle(["rotate"], hd, {"CLAUDE_CODE_SESSION_ID": "claude-cmd"})
    assert p.returncode == 0, p.stderr
    assert rl.read_session_id(PROJ, "claude-cmd") is None


def test_limit_command_sets_disables_and_clears_the_override(home, tmp_path):
    hd = str(tmp_path / "rh")
    env = {"CLAUDE_CODE_SESSION_ID": "claude-limit"}

    set_result = run_toggle(["limit", "5"], hd, env)
    assert set_result.returncode == 0, set_result.stderr
    assert "5" in set_result.stdout

    off_result = run_toggle(["limit", "off"], hd, env)
    assert off_result.returncode == 0, off_result.stderr
    assert "disabled" in off_result.stdout.lower()

    clear_result = run_toggle(["limit", "clear"], hd, env)
    assert clear_result.returncode == 0, clear_result.stderr
    assert "3" in clear_result.stdout


def test_rotate_reports_clearly_when_there_was_nothing_to_rotate(home, tmp_path):
    p = run_toggle(["rotate"], str(tmp_path / "rh"), {"CLAUDE_CODE_SESSION_ID": "claude-nada"})
    assert p.returncode == 0
    assert "nothing" in p.stdout.lower() or "no reviewer session" in p.stdout.lower()


def test_manual_rotation_does_not_erase_retry_classification(home, tmp_path):
    repo = str(_repo(tmp_path / "retry-rotation"))
    rl.write_session_id(repo, "claude-retry", "old-reviewer")
    rl.set_commit_retry_pending(repo, "claude-retry")

    rl.rotate_session(repo, "claude-retry")

    assert rl.commit_retry_pending(repo, "claude-retry")


def test_an_unrecognized_argument_fails_and_changes_nothing(home, tmp_path):
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-bad", "keep-1")
    p = run_toggle(["nuke"], hd, {"CLAUDE_CODE_SESSION_ID": "claude-bad"})
    assert p.returncode == 2
    assert "ERROR" in (p.stdout + p.stderr)
    assert rl.read_session_id(PROJ, "claude-bad") == "keep-1"


def test_the_harness_variable_wins_over_the_legacy_name(home, tmp_path):
    """CLAUDE_CODE_SESSION_ID is what Claude Code actually exports (verified 2026-07-30: its
    value equals the transcript stem, which is the same key the hooks derive). CLAUDE_SESSION_ID
    is accepted as a fallback only, because harness variable names have changed before and a
    rotate that silently targets the wrong session would retire a reviewer mid-review."""
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-real", "real-1")
    rl.write_session_id(PROJ, "claude-legacy", "legacy-1")
    p = run_toggle(["rotate"], hd, {"CLAUDE_CODE_SESSION_ID": "claude-real",
                                    "CLAUDE_SESSION_ID": "claude-legacy"})
    assert p.returncode == 0, p.stderr
    assert rl.read_session_id(PROJ, "claude-real") is None
    assert rl.read_session_id(PROJ, "claude-legacy") == "legacy-1"


def test_the_legacy_variable_is_used_when_the_harness_one_is_absent(home, tmp_path):
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-legacy2", "legacy-2")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env["REVIEWER_HOME"] = hd
    env["CLAUDE_PROJECT_DIR"] = PROJ
    env["CLAUDE_SESSION_ID"] = "claude-legacy2"
    p = subprocess.run(
        [sys.executable, TOGGLE, "rotate"], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert rl.read_session_id(PROJ, "claude-legacy2") is None


def test_the_command_cannot_rotate_without_an_identifiable_session(home, tmp_path):
    # Without a session id there is no session to own; the command must say so rather than
    # silently appear to succeed.
    hd = str(tmp_path / "rh")
    rl.write_session_id(PROJ, "claude-other", "o1")
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
    env["REVIEWER_HOME"] = hd
    p = subprocess.run(
        [sys.executable, TOGGLE, "rotate"], capture_output=True, text=True, env=env)
    assert p.returncode == 2
    assert rl.read_session_id(PROJ, "claude-other") == "o1"
