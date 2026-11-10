# SPDX-License-Identifier: MPL-2.0
"""State and temporary file names stay within the 255-byte file name limit.

See docs/plans/state_name_length_bound.md. The atomic writers used to name their temporary
sibling after the target, 38 bytes longer, so a valid target over 217 bytes failed with
ENAMETOOLONG and the commit gate blocked every commit in a repository with a long path."""
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import commit_hook
import reviewer_audit
import reviewer_base
import reviewer_prompts
import reviewer_state as rs
import reviewer_stopcontinue
import reviewer_transcript

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
COMMIT = str(ROOT / "commit_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

NAME_MAX = 255
SESSION = "01234567-89ab-4cde-8f01-23456789abcd"  # session ids are UUIDs: 36 bytes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    # The transcript fallback looks under ~/.claude/projects; keep it out of the real home.
    (tmp_path / "userhome").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    return tmp_path


def _long_repo(tmp_path):
    """A repository whose sanitized path is over 200 bytes, as under a long TMPDIR."""
    repo = tmp_path / ("a" * 60) / ("b" * 60) / ("c" * 60) / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    assert len(rs._sanitize(os.path.realpath(str(repo)))) > 200
    return repo


# --------------------------------------------------------------------------
# the atomic writers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("size", [228, NAME_MAX])
def test_atomic_write_publishes_a_long_but_valid_name(tmp_path, size):
    target = tmp_path / ("n" * size)
    reviewer_base._atomic_write_text(target, "content\n")
    assert target.read_text(encoding="utf-8") == "content\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]  # no temporary left


@pytest.mark.parametrize("size", [228, NAME_MAX])
def test_publish_if_absent_publishes_a_long_but_valid_name(tmp_path, size):
    target = tmp_path / ("n" * size)
    assert reviewer_base._publish_if_absent(target, "first\n") == "first\n"
    assert reviewer_base._publish_if_absent(target, "second\n") == "first\n"
    assert target.read_text(encoding="utf-8") == "first\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


# --------------------------------------------------------------------------
# a repository with a long path
# --------------------------------------------------------------------------
def test_session_and_retry_records_round_trip_in_a_long_path_repository(home):
    repo = str(_long_repo(home))
    rs.write_session_id(repo, SESSION, "reviewer-thread")
    assert rs.read_session_id(repo, SESSION) == "reviewer-thread"
    assert rs.write_session_id_if_absent(repo, SESSION, "stop-thread", rs.ROLE_STOP) == "stop-thread"
    assert rs.read_session_id(repo, SESSION, rs.ROLE_STOP) == "stop-thread"
    rs.set_commit_retry_pending(repo, SESSION)
    assert rs.commit_retry_pending(repo, SESSION)


def test_the_commit_gate_approves_a_commit_in_a_long_path_repository(home):
    repo = _long_repo(home)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.py"], check=True)
    rs.set_active(str(repo))
    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": str(home / "rh"),
        "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_VERDICT": "APPROVED",
        "FAKE_CODEX_EXIT": "0",
    })
    payload = {
        "cwd": str(repo),
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m 'feature'"},
    }
    p = subprocess.run([sys.executable, COMMIT], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "allow", out.get("permissionDecisionReason")
    assert rs.read_session_id(str(repo), SESSION)  # the reviewer session was recorded


def test_a_long_working_directory_does_not_break_the_transcript_lookup(home):
    # The conventional transcript location names a folder after the whole working directory;
    # for a long one that folder name is over the limit and merely asking whether it exists
    # raises. The lookup is a best-effort fallback, so it answers "no transcript".
    data = {"session_id": SESSION, "cwd": "/" + "/".join(["a" * 60] * 5)}
    assert reviewer_transcript.resolve_transcript_path(data) is None


# --------------------------------------------------------------------------
# every state path builder
# --------------------------------------------------------------------------
def _state_paths(proj, sid):
    """Every state path whose name is built from the project or the session."""
    paths = [
        rs.flag_file(proj),
        rs.risk_acceptance_file(proj),
        rs.project_backend_file(proj),
        rs.stopcontinue_flag_file(proj),
        rs.stopcontinue_watchdog_flag_file(proj),
        rs.last_review_file(proj),
        rs.last_review_file(proj, sid),
        rs.commit_retry_file(proj, sid),
        rs.commit_carryover_file(proj, sid),
        rs.watchdog_disarm_file(proj, sid),
        rs.watchdog_teardown_ready_file(proj, sid),
        rs.watchdog_tick_seen_file(proj, sid),
        rs.api_drop_lease_file(proj, sid),
        rs.reviewer_session_audit_count_file(proj, sid),
        rs._stop_review_count_file(proj, sid),
        rs._stop_approval_file(proj, sid),
        rs.audited_stage_path(proj, sid),
        reviewer_prompts.audited_stage_file(proj, sid),
        reviewer_audit.audit_log_file(proj),
        reviewer_stopcontinue.nudge_count_file(proj, sid),
        reviewer_stopcontinue.last_directive_file(proj, sid),
        reviewer_stopcontinue.review_history_dir(proj),
    ]
    for role in (rs.ROLE_REVIEWER, rs.ROLE_STOP):
        paths += [
            rs.session_id_file(proj, sid, role),
            rs.pi_session_file(proj, sid, role),
            rs.claude_session_file(proj, sid, role),
            reviewer_prompts.operator_watermark_file(proj, sid, role),
        ]
    for kind in (reviewer_prompts.DIRECTIVE_KIND_REVIEWER, reviewer_prompts.DIRECTIVE_KIND_STOP):
        paths.append(reviewer_prompts.directives_file(proj, kind))
    return paths


@pytest.mark.parametrize("component", ["d" * 250, "é" * 120], ids=["ascii", "two-byte"])
def test_every_state_name_fits_the_file_name_limit(home, component):
    proj = "/" + "/".join([component] * 4) + "/repo"
    paths = _state_paths(proj, SESSION)
    assert all(p is not None for p in paths)
    too_long = [(len(os.fsencode(p.name)), p.name) for p in paths
                if len(os.fsencode(p.name)) > NAME_MAX]
    assert not too_long, too_long


# --------------------------------------------------------------------------
# the gate's message when a name is too long anyway
# --------------------------------------------------------------------------
def test_a_too_long_name_is_reported_as_such_and_still_blocks():
    exc = OSError(errno.ENAMETOOLONG, "File name too long", "/state/some.very.long.name.codex")
    raw = json.dumps({"tool_input": {"command": "git commit -m x"}})
    out = commit_hook._crash_output(exc, raw)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "exceeds the filesystem's limit" in reason
    assert "/state/some.very.long.name.codex" in reason
    assert "Retry" not in reason  # retrying the same call fails the same way
