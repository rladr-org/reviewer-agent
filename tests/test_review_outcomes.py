# SPDX-License-Identifier: MPL-2.0
"""Every terminal review outcome is retained, and none of them forges a verdict.

Before this, only a COMPLETED audit reached the retained history, so the record of a project's
reviews silently omitted exactly the events an operator later asks about: the timeout that
blocked a commit, the review that died with the hook, the reviewer that failed to start. The log
line carried them and nothing else did.

Two rules, and they pull in opposite directions on purpose:

  RETAIN     a timeout, a cancellation, an empty verdict and an operational failure each append
             one clearly labelled outcome record to the bounded per-project history.
  DO NOT FORGE  none of them touches the latest-ruling file. That file is what the capsule shows
             the next reviewer as "your previous verdict", so overwriting an approval with
             "TIMEOUT" would put words in a reviewer's mouth and destroy the findings the retry
             exists to reuse.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
COMMIT_HOOK = str(ROOT / "commit_hook.py")
HISTORY_CLI = str(ROOT / "reviewer_history_toggle.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    return tmp_path


def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    return str(repo)


def run_commit_hook(repo, env_extra, session_id="claude-out"):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = str(rl.reviewer_home())
    env["REVIEWER_CODEX_BIN"] = FAKE
    env.update(env_extra)
    payload = {
        "cwd": repo,
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }
    return subprocess.run(
        [sys.executable, COMMIT_HOOK], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )


def history_texts(repo):
    return [path.read_text(encoding="utf-8") for path in rl.review_history(repo)]


# --------------------------------------------------------------------------
# outcomes reach the history
# --------------------------------------------------------------------------
def test_a_timeout_is_recorded_without_overwriting_the_last_ruling(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-out", "sid-1")
    rl.save_review("APPROVED\nearlier good verdict", proj=repo, claude_session_id="claude-out")

    # Exit 124 is exactly what `_run` reports for a timed-out reviewer, and driving the branch
    # this way keeps the test fast: the real wall-clock timeout has its own coverage in
    # tests/test_process_cancellation.py, where the point is that the process tree dies.
    result = run_commit_hook(repo, {"FAKE_CODEX_EXIT": "124"})

    assert result.returncode == 0, result.stderr
    assert "TIMEOUT" in result.stdout
    texts = history_texts(repo)
    assert any("OUTCOME: TIMEOUT" in text for text in texts), texts
    # The prior verdict survives: a timeout carries no findings to replace it with.
    assert "earlier good verdict" in rl.last_review_file(repo, "claude-out").read_text()


def test_an_operational_failure_is_recorded(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-out", "sid-1")

    result = run_commit_hook(repo, {"FAKE_CODEX_EXIT": "3", "FAKE_CODEX_STDERR": "boom"})

    assert result.returncode == 0, result.stderr
    texts = history_texts(repo)
    assert any("OUTCOME: FAILED" in text for text in texts), texts
    assert any("boom" in text for text in texts), texts


def test_an_empty_verdict_is_recorded(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-out", "sid-1")

    # codex exits 0 but writes NO schema verdict to its -o file: the reviewer ran yet produced
    # no usable ruling. run_codex_streaming_audit fails closed (SCHEMA_VERDICT_MISSING), which the
    # hook records as the EMPTY outcome.
    result = run_commit_hook(repo, {"FAKE_CODEX_NO_VERDICT_FILE": "1", "FAKE_CODEX_EXIT": "0"})

    assert result.returncode == 0, result.stderr
    texts = history_texts(repo)
    assert any("OUTCOME: EMPTY" in text for text in texts), texts


def test_an_approved_review_still_records_the_verdict_itself(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-out", "sid-1")

    run_commit_hook(repo, {"FAKE_CODEX_STDOUT": "APPROVED"})

    texts = history_texts(repo)
    assert any("APPROVED" in text for text in texts), texts
    assert not any("OUTCOME:" in text for text in texts), texts


def test_a_reviewer_that_never_starts_is_recorded(home, tmp_path):
    # No session is pre-written, so the hook briefs -- and the stub fails the briefing turn
    # without emitting a thread id. The commit is blocked by a review that never happened,
    # which is exactly the kind of event an operator later looks for in the record.
    repo = make_repo(tmp_path)
    rl.set_active(repo)

    result = run_commit_hook(repo, {"FAKE_CODEX_EXIT": "1", "FAKE_CODEX_STDERR": "no auth"})

    assert result.returncode == 0, result.stderr
    assert "could not start the reviewer" in result.stdout
    texts = history_texts(repo)
    assert any("OUTCOME: FAILED" in text for text in texts), texts
    assert any("could not establish a reviewer session" in text for text in texts), texts


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals only")
def test_a_cancelled_review_is_recorded(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-out", "sid-1")
    env = dict(os.environ)
    env["REVIEWER_HOME"] = str(rl.reviewer_home())
    env["REVIEWER_CODEX_BIN"] = FAKE
    # The audit runs through the streaming path (--json resume), which hangs on FIRST_DELAY (silent
    # before any event), not the plain-resume FAKE_CODEX_SLEEP. Long enough to still be running
    # when the test cancels it, but the test signals as soon as the review starts.
    env["FAKE_CODEX_FIRST_DELAY"] = "30"
    payload = {
        "cwd": repo,
        "session_id": "claude-out",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }
    # The stub records its argv the moment it starts, so this is the signal that the review is
    # genuinely under way -- cancelling before that would test the hook's import, not a review.
    started = tmp_path / "codex-argv.jsonl"
    env["FAKE_CODEX_ARGV_FILE"] = str(started)
    hook = subprocess.Popen(
        [sys.executable, COMMIT_HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
    )
    hook.stdin.write(json.dumps(payload))
    hook.stdin.close()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not started.exists():
        assert hook.poll() is None, hook.stderr.read()
        time.sleep(0.05)
    assert started.exists(), "the review never started"

    hook.send_signal(signal.SIGTERM)
    hook.wait(timeout=20)

    texts = history_texts(repo)
    assert any("OUTCOME: CANCELLED" in text for text in texts), texts


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals only")
def test_a_review_cancelled_during_the_initial_brief_is_recorded(home, tmp_path):
    # The other cancellation path, and the one the audit test cannot reach: no session id is
    # stored, so the hook is still BRIEFING when the operator presses Escape. That call is a
    # separate `_run`, and it needed its own cancellation callback to record anything.
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    env = dict(os.environ)
    env["REVIEWER_HOME"] = str(rl.reviewer_home())
    env["REVIEWER_CODEX_BIN"] = FAKE
    env["FAKE_CODEX_BRIEF_SLEEP"] = "30"
    started = tmp_path / "brief-argv.jsonl"
    env["FAKE_CODEX_ARGV_FILE"] = str(started)
    payload = {
        "cwd": repo,
        "session_id": "claude-brief",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }
    hook = subprocess.Popen(
        [sys.executable, COMMIT_HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
    )
    hook.stdin.write(json.dumps(payload))
    hook.stdin.close()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not started.exists():
        assert hook.poll() is None, hook.stderr.read()
        time.sleep(0.05)
    assert started.exists(), "the brief never started"

    hook.send_signal(signal.SIGTERM)
    hook.wait(timeout=20)

    texts = history_texts(repo)
    assert any("OUTCOME: CANCELLED" in text for text in texts), texts
    # And no reviewer id was published for a session that never finished briefing.
    assert rl.read_session_id(repo, "claude-brief") is None


# --------------------------------------------------------------------------
# the cleanup command
# --------------------------------------------------------------------------
def run_history_cli(args, proj):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = str(rl.reviewer_home())
    env["CLAUDE_PROJECT_DIR"] = str(proj)
    return subprocess.run(
        [sys.executable, HISTORY_CLI] + args, capture_output=True, text=True, env=env
    )


def test_history_command_reports_what_is_retained(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.save_review("one", proj=repo, claude_session_id="s", history=True)
    rl.save_review("two", proj=repo, claude_session_id="s", history=True)

    result = run_history_cli([], repo)

    assert result.returncode == 0, result.stderr
    assert "2" in result.stdout
    assert str(rl.review_history_dir(repo)) in result.stdout
    assert rl.canonical_project_key(repo) in result.stdout


def test_history_clear_purges_only_this_project(home, tmp_path):
    keep = make_repo(tmp_path, "keep")
    purge = make_repo(tmp_path, "purge")
    for repo in (keep, purge):
        rl.save_review("verdict", proj=repo, claude_session_id="s", history=True)

    result = run_history_cli(["clear"], purge)

    assert result.returncode == 0, result.stderr
    assert rl.review_history(purge) == []
    assert not rl.last_review_file(purge, "s").exists()
    assert len(rl.review_history(keep)) == 1
    assert rl.last_review_file(keep, "s").exists()


def test_history_clear_purges_the_audit_log_and_only_unshared_prompt_parts(home, tmp_path):
    # #21: a prompt part can hold a unified diff of reviewed code, and the audit log indexes
    # every reviewed commit; `clear` promised to purge that content and left both behind.
    import reviewer_audit as ra
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    only_a = ra.store_prompt_part("diff only A reviewed")
    shared = ra.store_prompt_part("standing brief both use")
    ra.append_audit_record(a, {"brief_sha256": shared, "static": {"capsule_sha256": only_a}})
    ra.append_audit_record(b, {"brief_sha256": shared})

    result = run_history_cli(["clear"], a)

    assert result.returncode == 0, result.stderr
    assert not ra.audit_log_file(a).exists()
    assert ra.load_prompt_part(only_a) is None
    assert ra.load_prompt_part(shared) == "standing brief both use"  # B still uses it
    assert ra.audit_log_file(b).exists()


def test_history_clear_keeps_every_prompt_part_when_a_sibling_log_is_unparseable(
        home, tmp_path):
    # An unreadable or corrupt sibling log is unknown data, not "references nothing".
    import reviewer_audit as ra
    a, b = make_repo(tmp_path, "a"), make_repo(tmp_path, "b")
    part = ra.store_prompt_part("capsule B may also use")
    ra.append_audit_record(a, {"static": {"capsule_sha256": part}})
    ra.append_audit_record(b, {"brief_sha256": "0" * 64})  # creates B's log...
    # ...then leaves it wholly unparseable: no readable line can name `part`, so only the
    # "unknown data keeps everything" rule preserves it.
    ra.audit_log_file(b).write_text("{not json\n", encoding="utf-8")

    result = run_history_cli(["clear"], a)

    assert result.returncode == 0, result.stderr
    assert not ra.audit_log_file(a).exists()
    assert ra.load_prompt_part(part) == "capsule B may also use"


def test_history_clear_reports_when_there_is_nothing_to_purge(home, tmp_path):
    repo = make_repo(tmp_path)

    result = run_history_cli(["clear"], repo)

    assert result.returncode == 0, result.stderr
    assert "nothing" in result.stdout.lower()


def test_history_command_rejects_an_unknown_argument(home, tmp_path):
    repo = make_repo(tmp_path)
    rl.save_review("verdict", proj=repo, claude_session_id="s", history=True)

    result = run_history_cli(["nuke"], repo)

    assert result.returncode == 2
    assert "ERROR" in (result.stdout + result.stderr)
    assert len(rl.review_history(repo)) == 1
