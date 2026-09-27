# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_audit as ra
import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
COMMIT = str(ROOT / "commit_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

PROJ = "/Users/x/proj"


def _input(command, cwd=PROJ):
    return {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": command}}


def run_hook(stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, COMMIT],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def make_staged_repo(tmp_path):
    repo = tmp_path / "staged_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")  # brief at the canonical root
    (repo / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.py"], check=True)  # md stays unstaged
    return str(repo)


def make_unstaged_repo(tmp_path):
    repo = tmp_path / "unstaged_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "untracked.py").write_text("y = 2\n")  # present but NOT staged
    return str(repo)


def make_committed_repo(tmp_path, name="wt_base"):
    """A repo with an initial commit (so `git worktree add` has a HEAD to branch from)
    and a reviewerAgent.md brief at the root."""
    repo = tmp_path / name
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "base.py").write_text("base = 0\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return str(repo)


def test_inactive_produces_no_output(tmp_path):
    home = str(tmp_path / "rh")
    p = run_hook(_input("git commit -m x"), {"REVIEWER_HOME": home})
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_active_non_commit_command_passes_through(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_active(PROJ)
    p = run_hook(_input("ls -la"), {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE})
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_an_unresolved_command_word_without_a_commit_passes_through_a_gated_repo(
    tmp_path, monkeypatch
):
    # Task C2.2: everyday commands that start with a variable are not commits.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(_input('"$PYTHON" -m pytest -q', cwd=repo),
                 {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_active_commit_nothing_staged_tells_claude_to_stage_without_calling_codex(
    tmp_path, monkeypatch
):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_unstaged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m x", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN",
        },
    )
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "stage" in reason.lower()
    assert "CODEX_SHOULD_NOT_RUN" not in reason  # Codex was not invoked


def test_active_commit_beyond_index_is_denied_before_calling_codex(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -am 'feature'", cwd=repo),  # -a reaches beyond the staged set
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN",
        },
    )
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "stage" in reason.lower()
    assert "CODEX_SHOULD_NOT_RUN" not in reason  # Codex was NOT invoked for a beyond-index commit


def test_active_commit_rejected_denies_and_relays_issue_list(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "REJECTED",
            "FAKE_CODEX_FINDINGS": "fix f.py:1",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fix f.py:1" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_active_commit_approved_commits_it_and_allows_the_call(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    # Codex approved -> the gate performs the commit ITSELF, then ALLOWS the call with the agent's
    # git commit rewritten to a harmless echo, so it succeeds and no real commit runs a second time.
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    rewritten = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert rewritten.startswith("echo ") and "git commit" not in rewritten
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head, "a real commit landed"  # unborn -> real commit
    assert head[:12] in (rewritten + out.get("systemMessage", ""))  # confirmation names it


def test_a_delivered_review_consumes_the_carryover_and_does_not_re_deliver(tmp_path, monkeypatch):
    # The commit hook consumes the carryover marker AFTER a completed review that actually DELIVERED
    # it -- so a delivered+completed review ends with the marker gone, and the next review does not
    # re-deliver. (A review that did not deliver leaves it armed for the retry -- see the unit tests.)
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "cc-carry"
    csid = rl.claude_session_key(payload)
    rl.save_review("REJECTED\nREQUIRED FIXES:\n1. WIDGET_FINDING", proj=repo, claude_session_id=csid)
    rl.set_commit_carryover(repo, csid)

    argv1 = str(tmp_path / "argv1.jsonl")
    p1 = run_hook(payload, {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                            "FAKE_CODEX_VERDICT": "APPROVED", "FAKE_CODEX_EXIT": "0",
                            "FAKE_CODEX_ARGV_FILE": argv1})
    assert p1.returncode == 0, p1.stderr
    assert "WIDGET_FINDING" in Path(argv1).read_text(encoding="utf-8")  # fresh successor inherited
    assert not rl.commit_carryover_pending(repo, csid)                  # consumed after delivery

    # The first approval committed and consumed the staging; stage a fresh change so the second
    # review has something to audit.
    (Path(repo) / "f.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)
    argv2 = str(tmp_path / "argv2.jsonl")
    p2 = run_hook(payload, {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
                            "FAKE_CODEX_VERDICT": "APPROVED", "FAKE_CODEX_EXIT": "0",
                            "FAKE_CODEX_ARGV_FILE": argv2})
    assert p2.returncode == 0, p2.stderr
    assert "WIDGET_FINDING" not in Path(argv2).read_text(encoding="utf-8")  # not re-delivered


def test_real_rotation_carries_former_findings_into_the_successor_prompt(tmp_path, monkeypatch):
    # END-TO-END through the ACTUAL rotation trigger (not a manually-set marker): with the audit
    # limit at 1, a rejected NON-retry commit rotates the reviewer session and arms the carryover;
    # the successor's retry-review PROMPT (captured from the fake codex argv) must then carry the
    # retired session's finding text AND the verify instruction, and -- because commit 2 both
    # delivered the carryover and completed -- the marker is consumed afterward.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_reviewer_session_audit_limit(1)          # rotate after ONE completed non-retry audit
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "cc-rot"
    csid = rl.claude_session_key(payload)
    finding = "CARRYOVER_MARKER_FIX_42"

    # Commit 1: non-retry, REJECTED -> record_completed_reviewer_audit hits the limit -> rotate ->
    # retire (preserve findings) -> arm carryover.
    p1 = run_hook(
        payload,
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_VERDICT": "REJECTED",
         "FAKE_CODEX_FINDINGS": finding, "FAKE_CODEX_EXIT": "0"},
    )
    assert p1.returncode == 0, p1.stderr
    assert json.loads(p1.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert rl.commit_carryover_pending(repo, csid), "the real rotation did not arm the carryover"

    # Commit 2: the retry. Capture the successor review's prompt from the fake codex argv.
    argv_file = str(tmp_path / "codex_argv.jsonl")
    p2 = run_hook(
        payload,
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_VERDICT": "APPROVED",
         "FAKE_CODEX_EXIT": "0", "FAKE_CODEX_ARGV_FILE": argv_file},
    )
    assert p2.returncode == 0, p2.stderr
    prompts = Path(argv_file).read_text(encoding="utf-8")
    assert finding in prompts, "successor prompt did not inherit the retired finding"
    assert "VERIFY" in prompts.upper(), "successor prompt did not carry the verify instruction"
    assert not rl.commit_carryover_pending(repo, csid), "delivered+completed review must consume it"


def test_audit_runs_with_tmpdir_scoped_to_the_session_scratch_dir(tmp_path, monkeypatch):
    # INTEGRATION for the scratch-cleanup change: the real hook must create a per-generation scratch
    # dir keyed by the reviewer session id AND launch the audit subprocess with TMPDIR scoped to it.
    # Proven end-to-end by the fake codex dumping the TMPDIR value it was actually launched with.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    scratch_root = tmp_path / "scratch"
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-tmp", "sid-tmp")   # pre-set -> audit resumes, no brief
    seen = tmp_path / "seen_tmpdir.txt"
    p = run_hook(
        {"cwd": repo, "session_id": "claude-tmp", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "REVIEWER_SCRATCH_ROOT": str(scratch_root),
            "FAKE_CODEX_DUMP_TMPDIR": str(seen),
            "FAKE_CODEX_VERDICT": "APPROVED",
        },
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed
    expected = scratch_root / rl.scratch_key("sid-tmp")
    assert seen.read_text().strip() == str(expected)   # audit ran under the scoped TMPDIR
    assert expected.is_dir()                            # the hook created the scratch dir


def test_low_disk_warning_surfaces_on_stderr_without_blocking_the_commit(tmp_path, monkeypatch):
    # INTEGRATION for warn-only disk reporting: an impossibly high threshold forces the warning,
    # which must appear on stderr yet NEVER change the allow verdict.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    scratch_root = tmp_path / "scratch"
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-disk", "sid-disk")
    p = run_hook(
        {"cwd": repo, "session_id": "claude-disk", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "REVIEWER_SCRATCH_ROOT": str(scratch_root),
            # above any value statvfs can report (block counts are 64-bit; x block size < 2**84)
            "REVIEWER_DISK_WARN_BYTES": str(10 ** 30),
            "FAKE_CODEX_VERDICT": "APPROVED",
        },
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed
    assert "low disk" in p.stderr.lower()               # warned, but the commit still proceeded


def test_auto_rotation_counts_three_completed_non_retries_and_excludes_retries(
    tmp_path, monkeypatch
):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    repo = make_committed_repo(tmp_path, "rotation")
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-rotate", "sid-A")
    env = {
        "REVIEWER_HOME": home,
        "REVIEWER_CONFIG_FILE": str(tmp_path / "missing.json"),
        "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_EXIT": "0",
    }
    payload = {
        "cwd": repo,
        "session_id": "claude-rotate",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m 'x'"},
    }

    (Path(repo) / "f.py").write_text("rejected = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)
    rejected = run_hook(payload, dict(env, FAKE_CODEX_VERDICT="REJECTED",
                                      FAKE_CODEX_FINDINGS="fix it"))
    assert json.loads(rejected.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert rl.commit_retry_pending(repo, "claude-rotate")

    # The next attempt is a retry because a rejection preceded it.
    (Path(repo) / "f.py").write_text("fixed = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)
    retry = run_hook(payload, dict(env, FAKE_CODEX_VERDICT="APPROVED"))
    # The gate performs the commit itself now, so an approval ALLOWS the tool call with the command
    # rewritten (it committed "fixed = 1"); there is no separate manual commit any more.
    assert json.loads(retry.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert rl.reviewer_session_completed_audits(repo, "claude-rotate", "sid-A") == 1

    rl.write_session_id(repo, "claude-rotate", "stop-sid", role=rl.ROLE_STOP)
    for number in range(1, 3):
        (Path(repo) / "f.py").write_text(
            "qualifying = {}\n".format(number), encoding="utf-8"
        )
        subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)
        approved = run_hook(payload, dict(env, FAKE_CODEX_VERDICT="APPROVED"))
        # Each qualifying audit is approved and the gate commits it, so the tool call is allowed.
        assert json.loads(approved.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
        if number < 2:
            assert rl.read_session_id(repo, "claude-rotate") == "sid-A"

    assert rl.read_session_id(repo, "claude-rotate") is None
    assert rl.read_session_id(
        repo, "claude-rotate", role=rl.ROLE_STOP
    ) == "stop-sid"


def test_rejection_at_limit_rotates_and_keeps_findings_for_the_fresh_retry(
    tmp_path, monkeypatch
):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_committed_repo(tmp_path, "reject_rotation")
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-reject", "sid-reject")
    (Path(repo) / "f.py").write_text("bad = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)

    result = run_hook(
        {
            "cwd": repo,
            "session_id": "claude-reject",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'x'"},
        },
        {
            "REVIEWER_HOME": home,
            "REVIEWER_SESSION_AUDIT_LIMIT": "1",
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "REJECTED",
            "FAKE_CODEX_FINDINGS": "Preserve me",
            "FAKE_CODEX_EXIT": "0",
        },
    )

    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert rl.read_session_id(repo, "claude-reject") is None
    assert rl.commit_retry_pending(repo, "claude-reject")
    saved = rl.last_review_file(repo, "claude-reject")
    assert saved.exists()
    assert "Preserve me" in saved.read_text(encoding="utf-8")
    capsule = rl.reviewer_capsule(
        repo, commit_audit=True, claude_session_id="claude-reject"
    )
    # 2026-08-28 anti-stuck-reviewer reframe (measure 1 of docs/plans/reviewer_anti_stuck.md
    # once it lands): the pointer wording changed from "previous commit-review verdict" to
    # "earlier audit round's verdict" to drop the self-anchoring pronoun. The saved-verdict
    # file path itself is still named -- that is the load-bearing invariant this test guards.
    assert "earlier audit round's verdict" in capsule or "previous commit-review verdict" in capsule
    assert str(saved) in capsule


def test_rejected_findings_are_echoed_in_systemmessage(tmp_path, monkeypatch):
    # The operator must see the required fixes in the terminal, not just "commit blocked";
    # the systemMessage also points at the on-disk full review.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "REJECTED",
            "FAKE_CODEX_FINDINGS": "fix f.py:1",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fix f.py:1" in out["systemMessage"]
    # The reported path is the scoped one; there is no shared last_commit_review.txt any more.
    assert "last_review.txt" in out["systemMessage"]
    assert "last_commit_review.txt" not in out["systemMessage"]


def test_approved_verdict_is_echoed_in_systemmessage(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_CODEX_SUGGESTIONS": "clean diff, tests green",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed on approval
    assert "clean diff, tests green" in out["systemMessage"]


def test_an_approved_verdict_relays_a_long_suggestions_list_whole(tmp_path, monkeypatch):
    """The approved relay used `rl.short(out)`, truncating at 600 characters.

    That was tolerable only while form (1) was "APPROVED and nothing else" -- by contract
    there was nothing to read. Now that APPROVED may carry a SUGGESTIONS block, truncation
    would cut the list mid-item, and the operator would not even see that it happened: the
    rejection path relays the FULL text via required_fixes_message(), so an unexplained
    ellipsis on the approved path reads as if a required-fix list were involved. A
    suggestion the reviewer bothered to write must arrive intact."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    suggestions = ["suggestion number {} about caching and allocation".format(i)
                   for i in range(1, 21)] + ["LAST_SUGGESTION_MARKER"]
    payload = "||".join(suggestions)
    assert len(payload) > 600, "fixture must exceed the old truncation point"
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_CODEX_SUGGESTIONS": payload,
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed on approval
    msg = out["systemMessage"]
    assert "LAST_SUGGESTION_MARKER" in msg, "the suggestions list was truncated"
    assert "...[truncated]" not in msg
    assert "suggestion number 1 about" in msg


def test_an_oversized_approved_verdict_is_bounded_but_recoverable(tmp_path, monkeypatch):
    """The other half of the relay contract.

    `systemMessage` is the ONLY channel on the allow path -- unlike a rejection, there is no
    permissionDecisionReason carrying the full text -- so relaying it unbounded would let an
    off-contract reviewer dump an essay on every approved commit. It is bounded at 1500 and
    names the saved review, matching what required_fixes_message() does for the
    operator-facing half of a rejection. save_review() has already written the complete text,
    so the bound costs nothing."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_CODEX_SUGGESTIONS": ("x" * 4000) + "||TAIL_MARKER",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed on approval
    msg = out["systemMessage"]
    assert len(msg) < 2500, "an oversized verdict was relayed unbounded"
    assert "...[truncated]" in msg, "truncation must be visible, not silent"
    assert "full review:" in msg
    # The complete text must actually be recoverable, or the bound would lose it. Read the
    # path the message itself names, so the test proves the pointer is correct rather than
    # re-deriving it independently.
    named = msg.split("(full review: ")[1].rstrip(")\n")
    assert "TAIL_MARKER" in Path(named).read_text(encoding="utf-8")


def test_a_non_schema_conforming_verdict_fails_closed(tmp_path, monkeypatch):
    """The gate must not loosen. codex now emits a STRICT JSON verdict; a final message that does
    NOT parse as the exact {verdict,findings,suggestions} schema -- here a stray text blob that
    even contains the word APPROVED -- reconstructs to "" and returns a NONZERO code, so the
    commit is denied. is_approved never sees a hand-rolled 'APPROVED' that slipped past the
    schema, exit 0."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT_JSON": "SUGGESTIONS:\n1. looks fine to me\nAPPROVED\n",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_active_commit_from_worktree_is_reviewed_not_bypassed(tmp_path, monkeypatch):
    # The bug: a commit made inside a linked worktree bypassed the reviewer because the
    # active flag was keyed by the worktree's cwd. Now the gate resolves to the project
    # root, so the worktree inherits the toggle and the commit IS reviewed.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    main = make_committed_repo(tmp_path)
    rl.set_active(main)  # activate from the MAIN checkout ONLY
    wt = str(tmp_path / "wtree")
    subprocess.run(["git", "-C", main, "worktree", "add", "-q", wt], check=True)
    (Path(wt) / "wt_change.py").write_text("z = 9\n")
    subprocess.run(["git", "-C", wt, "add", "wt_change.py"], check=True)
    p = run_hook(
        _input("git commit -m 'from worktree'", cwd=wt),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "REJECTED",
            "FAKE_CODEX_FINDINGS": "fix wt_change.py:1",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    # Reviewed (NOT silently bypassed): Codex rejected -> deny + relay the fix list.
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fix wt_change.py:1" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_active_commit_resumes_preseeded_session_without_rebriefing(tmp_path, monkeypatch):
    # When SessionStart already captured a codex session id for this Claude session, the
    # commit hook resumes it BY ID and must NOT re-brief (the persisted id is untouched).
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-S", "preexisting-sid")
    p = run_hook(
        {
            "cwd": repo,
            "session_id": "claude-S",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'x'"},
        },
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_VERDICT": "APPROVED",
            "FAKE_CODEX_EXIT": "0",
            "FAKE_CODEX_SESSION_ID": "SHOULD_NOT_BE_CAPTURED",
        },
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed on approval
    assert rl.read_session_id(repo, "claude-S") == "preexisting-sid"  # no re-brief overwrote it


def test_active_commit_codex_failure_fails_closed_with_error(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input("git commit -m 'feature'", cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDERR": "review crashed",
            "FAKE_CODEX_EXIT": "4",
        },
    )
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ERROR" in out["systemMessage"]


# --------------------------------------------------------------------------
# Audit records: one line per ATTEMPT, so a real review can be re-issued to another model and
# so "did the reviewer take the delta" stops being a guess.
# --------------------------------------------------------------------------
def _audit_env(home, **extra):
    env = {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0"}
    env.update(extra)
    return env


def test_a_completed_review_is_recorded_with_its_cost_and_inputs(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-A", "sid-A")
    p = run_hook(
        {"cwd": repo, "session_id": "claude-A", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_VERDICT="REJECTED", FAKE_CODEX_FINDINGS="fix f.py:1"),
    )
    assert p.returncode == 0, p.stderr

    recs = ra.read_audit_records(repo)
    assert len(recs) == 1
    r = recs[0]
    assert r["backend"] == "codex" and r["reviewer_session"] == "sid-A"
    assert r["claude_session"] == "claude-A"
    assert r["exit"] == 0 and r["completed"] is True and r["approved"] is False
    assert r["verdict_file"] == str(rl.last_review_file(repo, "claude-A"))
    assert r["staged_files"] == 1 and r["staged_lines"] == 1
    assert r["staged"] == rl.staged_blob_shas(repo)
    # No stdout "tokens used" footer in --json mode; per-turn usage now comes from the
    # turn.completed event (the fake emits fixed counts), so the cumulative footer is absent.
    assert r["tokens_cumulative"] is None
    assert r["turn_output_tokens"] == 340 and r["turn_input_tokens"] == 1200
    assert r["turn_cached_tokens"] == 100 and r["turn_reasoning_tokens"] == 88
    assert r["dur_s"] >= 0
    assert r["request"]["timeout_s"] == rl.commit_review_timeout()
    assert r["request"]["argv"][0] == FAKE


def test_the_recorded_argv_never_carries_the_prompt(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-B", "sid-B")
    run_hook(
        {"cwd": repo, "session_id": "claude-B", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_VERDICT="APPROVED"),
    )
    r = ra.read_audit_records(repo)[0]
    blob = json.dumps(r)
    assert "AUDIT the STAGED" not in blob and "reviewerAgent.md" not in blob
    assert any(str(a).startswith("sha256:") for a in r["request"]["argv"])
    # The prompt is recoverable by hash, not by copy.
    assert ra.load_prompt_part(r["static"]["capsule_sha256"])


def test_a_crashed_review_is_recorded_as_an_incomplete_attempt(tmp_path, monkeypatch):
    # The rows that matter most for a model comparison: a reviewer that crashes or times out is
    # a worse reviewer, and dropping those attempts makes every model look equally reliable.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-C", "sid-C")  # pre-seeded, so the failing exit hits the AUDIT
    p = run_hook(
        {"cwd": repo, "session_id": "claude-C", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_EXIT="4", FAKE_CODEX_STDERR="review crashed"),
    )
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    recs = ra.read_audit_records(repo)
    assert len(recs) == 1
    assert recs[0]["exit"] == 4 and recs[0]["completed"] is False
    assert recs[0]["verdict_file"] is None  # nothing was retained, so nothing is pointed at


def test_records_accumulate_one_per_attempt(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-D", "sid-D")
    stdin = {"cwd": repo, "session_id": "claude-D", "tool_name": "Bash",
             "tool_input": {"command": "git commit -m 'x'"}}
    run_hook(stdin, _audit_env(home, FAKE_CODEX_VERDICT="REJECTED", FAKE_CODEX_FINDINGS="fix"))
    run_hook(stdin, _audit_env(home, FAKE_CODEX_VERDICT="APPROVED"))
    recs = ra.read_audit_records(repo)
    assert [r["approved"] for r in recs] == [False, True]
    assert [r["round"] for r in recs] == [1, 2]


def test_the_exec_trace_survives_into_the_record(tmp_path, monkeypatch):
    # stderr is truncated to 300 characters in the log line; the counters must be derived
    # BEFORE that, or the only direct evidence of what the reviewer did is gone.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-E", "sid-E")
    trace = (
        "exec\n/bin/bash -lc 'git diff " + "a" * 40 + " :f.py' in /r\n succeeded in 7ms:\n"
        + "padding output line\n" * 30  # pushes the FIRST entry out of the 300-char log tail
        + "exec\n/bin/bash -lc 'python -m pytest -q' in /r\n succeeded in 900ms:\n"
    )
    run_hook(
        {"cwd": repo, "session_id": "claude-E", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_VERDICT="APPROVED", FAKE_CODEX_STDERR=trace),
    )
    r = ra.read_audit_records(repo)[0]
    assert r["exec_delta_diffs"] == 1
    assert r["exec_test_runs"] == 1
    assert r["exec_count"] == 2 and r["exec_ms_total"] == 907


def test_a_failed_command_is_recorded_with_its_exit_code(tmp_path, monkeypatch):
    """The counters say HOW MANY commands failed; this says WHICH and WHY. Without it, an
    audit that reported a test count it could not verify leaves no trace of the attempt, and
    "the reviewer said pytest was unavailable" stays uncorroborable -- the exact position we
    were in on 2026-08-02.

    Successful commands are deliberately NOT duplicated here: they are already in exec_trace,
    and a failure list that also carries the successes is just a second copy of the trace."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-EF", "sid-EF")
    trace = (
        "exec\n/bin/bash -lc 'pytest -q' in /r\n exited 127 in 4ms:\n"
        "exec\n/bin/bash -lc 'git show :f.py' in /r\n succeeded in 5ms:\n"
    )
    run_hook(
        {"cwd": repo, "session_id": "claude-EF", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_VERDICT="APPROVED", FAKE_CODEX_STDERR=trace),
    )
    r = ra.read_audit_records(repo)[0]
    assert r["exec_test_runs"] == 1
    assert r["exec_test_runs_failed"] == 1
    assert len(r["exec_failures"]) == 1
    assert r["exec_failures"][0]["code"] == 127
    assert "pytest" in r["exec_failures"][0]["cmd"]


def test_the_recorded_capsule_is_the_one_that_was_sent(tmp_path, monkeypatch):
    # The capsule NAMES the saved verdict only when one already exists. Building the record
    # after save_review therefore stores a capsule the reviewer never saw -- a record that is
    # confidently wrong about its own input, which is worse than no record.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-H", "sid-H")
    stdin = {"cwd": repo, "session_id": "claude-H", "tool_name": "Bash",
             "tool_input": {"command": "git commit -m 'x'"}}
    run_hook(stdin, _audit_env(home, FAKE_CODEX_VERDICT="REJECTED", FAKE_CODEX_FINDINGS="fix"))
    first = ra.read_audit_records(repo)[0]
    assert "last_review.txt" not in ra.load_prompt_part(first["static"]["capsule_sha256"])

    run_hook(stdin, _audit_env(home, FAKE_CODEX_VERDICT="REJECTED", FAKE_CODEX_FINDINGS="still"))
    second = ra.read_audit_records(repo)[1]
    assert "last_review.txt" in ra.load_prompt_part(second["static"]["capsule_sha256"])


def test_the_commit_log_line_carries_the_cost_fields(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-F", "sid-F")
    run_hook(
        {"cwd": repo, "session_id": "claude-F", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'x'"}},
        _audit_env(home, FAKE_CODEX_VERDICT="APPROVED"),
    )
    line = (Path(home) / "logs" / "reviewer.log").read_text(encoding="utf-8")
    assert "dur_s=" in line
    assert "staged_files=1" in line and "staged_lines=1" in line
    # The streaming audit's timing fields ride the same log line (there is no stdout token
    # footer in --json mode, so tokens= is present but None for codex).
    assert "t_first=" in line and "max_gap=" in line and "idle_retried=" in line
    assert "tokens=" in line


def test_an_audit_record_failure_never_blocks_the_gate(tmp_path, monkeypatch):
    # Every state path in this library swallows OSError so instrumentation cannot take a commit
    # down. An unwritable reviews/ directory must still produce a verdict.
    home = tmp_path / "rh"
    (home / "reviews").mkdir(parents=True)
    (home / "reviews").chmod(0o500)
    monkeypatch.setenv("REVIEWER_HOME", str(home))
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "claude-G", "sid-G")
    try:
        p = run_hook(
            {"cwd": repo, "session_id": "claude-G", "tool_name": "Bash",
             "tool_input": {"command": "git commit -m 'x'"}},
            _audit_env(str(home), FAKE_CODEX_VERDICT="APPROVED"),
        )
        assert p.returncode == 0, p.stderr
        # A record-write failure must not block the gate: it still reviews, commits, and allows.
        assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        assert head, "the commit still landed despite the record failure"
    finally:
        (home / "reviews").chmod(0o700)


def test_the_pi_record_uses_the_session_id_pi_actually_returned(tmp_path, monkeypatch):
    """REGRESSION. pi learns its session id from the RESPONSE, so before the turn the hook holds
    only the "pi:new" placeholder.

    Recording that split one real pi session across two cursor keys: the second turn restarted
    at round 1 and lost its token delta, every unrelated fresh pi session collided on the same
    "pi:new" key, and the audited-stage record it keyed made the next turn's CHANGED annotation
    look like a different reviewer and vanish. In-process because the id is written inside
    run_pi_review, which is exactly what a stub has to stand in for.
    """
    import io
    sys.path.insert(0, str(ROOT))
    import commit_hook

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.set_project_backend(repo, "pi")

    def fake_pi(cwd, claude_session_id, prompt, timeout=None, thinking=None, **kwargs):
        rl.write_pi_session_if_absent(repo, claude_session_id, "REAL-PI-42")  # as the real one does
        return 0, "APPROVED\ntokens used\n1,000\n", ""

    monkeypatch.setattr(commit_hook.rl, "run_pi_review", fake_pi)
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(json.dumps({"cwd": repo, "session_id": "claude-P", "tool_name": "Bash",
                                "tool_input": {"command": "git commit -m x"}})))
    assert commit_hook.main() == 0

    rec = ra.read_audit_records(repo)[0]
    assert rec["reviewer_session"] == "pi:REAL-PI-42"
    assert rec["round"] == 1
    assert ra.session_cursor("pi:new") == {"round": 0, "tokens_cumulative": None}
    assert ra.session_cursor("pi:REAL-PI-42")["tokens_cumulative"] == 1000


def test_two_failed_first_pi_turns_do_not_share_a_cursor(tmp_path, monkeypatch):
    """REGRESSION. A first pi turn that dies before the session header is parsed leaves NO real
    id, and the placeholder is the same string for everybody.

    Two unrelated Claude sessions then landed on one cursor key, so the second was recorded as
    round 2 of a session it had never been part of, with a token delta taken against another
    session's counter.
    """
    import io
    sys.path.insert(0, str(ROOT))
    import commit_hook

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.set_project_backend(repo, "pi")
    # Crashes before persisting an id, exactly as a timed-out or malformed first turn does.
    monkeypatch.setattr(commit_hook.rl, "run_pi_review",
                        lambda *a, **k: (4, "", "pi crashed"))

    for claude_sid in ("claude-P1", "claude-P2"):
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO(json.dumps({"cwd": repo, "session_id": claude_sid, "tool_name": "Bash",
                                    "tool_input": {"command": "git commit -m x"}})))
        assert commit_hook.main() == 0

    first, second = ra.read_audit_records(repo)
    # No session exists, so neither attempt claims one: the empty key means every per-session
    # accessor is a no-op, which is why both are round 1 and no cursor was ever written.
    assert first["reviewer_session"] == second["reviewer_session"] == ""
    assert [first["round"], second["round"]] == [1, 1]
    assert ra.session_cursor("pi:new") == {"round": 0, "tokens_cumulative": None}
    assert ra.session_cursor_file("") is None


def test_a_pi_turn_with_a_verdict_but_no_session_header_claims_no_continuity(tmp_path,
                                                                             monkeypatch):
    """REGRESSION. pi can return a perfectly good verdict and still yield no session header, in
    which case NO session was established and the next commit is a fresh reviewer turn.

    Keying per-session state on a stable placeholder invented continuity that does not exist:
    the next turn was recorded as round 2, and -- far worse than a wrong number -- it received a
    "STAGED SET vs YOUR LAST AUDIT ... restate the findings you raised" block addressed to a
    reviewer holding no prior context, which would leave unchanged files unreviewed.
    """
    import io
    sys.path.insert(0, str(ROOT))
    import commit_hook

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.set_project_backend(repo, "pi")
    prompts = []

    def fake_pi(cwd, claude_session_id, prompt, timeout=None, thinking=None, **kwargs):
        prompts.append(prompt)                      # verdict, but no id is ever persisted
        return 0, "REJECTED:\n1. fix f.py:1", ""

    monkeypatch.setattr(commit_hook.rl, "run_pi_review", fake_pi)
    for _ in range(2):
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO(json.dumps({"cwd": repo, "session_id": "claude-Q", "tool_name": "Bash",
                                    "tool_input": {"command": "git commit -m x"}})))
        assert commit_hook.main() == 0

    # The block is PREPENDED, so its absence is a prefix check. Searching the whole prompt would
    # hit the capsule, which explains the block's labels whether or not one was sent.
    assert not prompts[1].startswith("STAGED SET")
    assert rl.read_audited_stage(repo, "claude-Q") is None
    assert [r["round"] for r in ra.read_audit_records(repo)] == [1, 1]


def test_commit_audit_uses_the_configured_timeout(tmp_path, monkeypatch):
    """The audit must read the TUNABLE, not a module constant.

    In-process (not via the CLI) because the timeout is enforced by the parent's
    subprocess.run and is invisible to the fake codex child -- the only place it can be
    observed is the call itself. A 5700-line staged diff exceeded the old hardcoded 600s and,
    because the gate is fail-closed, blocked the commit.
    """
    sys.path.insert(0, str(ROOT))
    import commit_hook

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    seen = {}

    def fake_run(sid, cwd, sandbox, prompt, timeout=None, extra_config=None, **kwargs):
        seen["timeout"] = timeout
        return 0, "APPROVED", ""

    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit", fake_run)
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "save_review", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage", lambda *a, **k: None)
    # The commit itself is not what this test observes; stub the digest+commit so the approval
    # path does not consume the staging, leaving both calls a staged set to review.
    monkeypatch.setattr(commit_hook.rl, "staged_tree", lambda *a, **k: "T")
    monkeypatch.setattr(commit_hook.rl, "run_commit",
                        lambda *a, **k: ("committed", "committed-sha", ""))

    import io

    def _call():
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO(json.dumps(_input("git commit -m x", cwd=repo))))
        assert commit_hook.main() == 0

    _call()
    assert seen["timeout"] == 900, "default must be the raised 900s, not the old 600s"

    monkeypatch.setenv("REVIEWER_COMMIT_TIMEOUT_SECS", "2400")
    _call()
    assert seen["timeout"] == 2400, "an operator-raised timeout must reach the audit call"


def test_timeout_blocks_the_commit_and_names_the_remedy(tmp_path, monkeypatch):
    # Still fail-CLOSED: a timeout carries no verdict, so it must never read as approval. But
    # it must be distinguishable from a crashed reviewer, and must name both remedies (retry;
    # raise the budget) -- reported as a generic failure, it cost a blind retry.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "save_review", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(
        commit_hook.rl, "run_codex_streaming_audit",
        lambda *a, **k: (rl.TIMEOUT_EXIT, "", "review timed out after 900s"))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_input("git commit -m x", cwd=repo))))

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    assert commit_hook.main() == 0
    out = json.loads(captured.getvalue())

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "TIMEOUT" in out["systemMessage"]
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "900" in reason
    assert "not an approval" in reason
    assert "/reviewerModel timeout" in reason


def test_a_timeout_does_not_record_the_stage_as_audited(tmp_path, monkeypatch):
    # The re-review machinery tells the next attempt which paths MOVED since the last audit.
    # A timed-out review read nothing conclusive, so recording it would make the retry think
    # the diff was already seen.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    recorded = []
    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "save_review", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage",
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(
        commit_hook.rl, "run_codex_streaming_audit",
        lambda *a, **k: (rl.TIMEOUT_EXIT, "", "review timed out after 900s"))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(_input("git commit -m x", cwd=repo))))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert commit_hook.main() == 0
    assert recorded == []


@pytest.mark.parametrize(
    "result",
    [
        pytest.param((rl.TIMEOUT_EXIT, "", "review timed out after 900s"), id="timeout"),
        pytest.param((1, "", "codex crashed"), id="crash"),
        pytest.param((0, "   \n", ""), id="empty_verdict"),
    ],
)
def test_a_failed_review_does_not_destroy_the_prior_verdict(tmp_path, monkeypatch, result):
    # The capsule NAMES the per-session verdict file to the reviewer so a repeat attempt can
    # recover its own earlier findings. A timeout or crash returns empty or partial output, so
    # overwriting the file with it destroys the very findings the retry is meant to reuse --
    # and the hook then tells the agent "no verdict" in the same breath.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.save_review("EARLIER FINDINGS", proj=repo, claude_session_id="sid-keep")
    prior = rl.last_review_file(repo, "sid-keep")
    assert prior.read_text(encoding="utf-8") == "EARLIER FINDINGS"

    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit", lambda *a, **k: result)
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "sid-keep"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert commit_hook.main() == 0

    assert prior.read_text(encoding="utf-8") == "EARLIER FINDINGS"


def test_an_empty_verdict_does_not_record_the_stage_as_audited(tmp_path, monkeypatch):
    # Same reason as the timeout case: a review that returned no verdict read nothing
    # conclusive. Recording it would make the NEXT attempt receive a "STAGED SET vs YOUR LAST
    # AUDIT" block whose UNCHANGED rows say "restate the findings you raised" -- when there are
    # no findings, and no saved verdict to restate them from.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    recorded = []
    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage",
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit", lambda *a, **k: (0, "  \n", ""))
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "sid-keep"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert commit_hook.main() == 0
    assert recorded == []


def test_a_real_verdict_does_record_the_stage_as_audited(tmp_path, monkeypatch):
    # The other half: the re-review annotation must still work for genuine rounds.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    recorded = []
    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage",
                        lambda *a, **k: recorded.append(a))
    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit",
                        lambda *a, **k: (0, "REJECTED:\n1. fix f.py:1", ""))
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "sid-keep"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert commit_hook.main() == 0
    assert len(recorded) == 1


def test_an_empty_verdict_is_not_reported_as_a_fix_list(tmp_path, monkeypatch):
    # Exit 0 with no output must not fall through to the rejection branch: that reports the
    # saved-verdict path as "the complete list" when nothing was written this round, so the
    # agent reads a PREVIOUS attempt's findings and treats them as current.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.save_review("STALE FINDING ABOUT A FILE THAT IS FIXED", proj=repo,
                   claude_session_id="sid-keep")

    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit", lambda *a, **k: (0, "  \n", ""))
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "sid-keep"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    assert commit_hook.main() == 0
    out = json.loads(captured.getvalue())

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "EMPTY verdict" in reason
    assert "not an approval" in reason
    assert "REQUIRED FIXES" not in reason
    assert "STALE FINDING" not in reason
    assert str(rl.last_review_file(repo, "sid-keep")) not in reason


def test_a_completed_verdict_does_replace_the_prior_one(tmp_path, monkeypatch):
    # The other half of the guard above: a real verdict MUST land, or the reviewer would keep
    # being pointed at stale findings forever.
    sys.path.insert(0, str(ROOT))
    import commit_hook
    import io

    repo = make_staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.save_review("EARLIER FINDINGS", proj=repo, claude_session_id="sid-keep")

    monkeypatch.setattr(commit_hook.rl, "ensure_session", lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(
        commit_hook.rl, "run_codex_streaming_audit",
        lambda *a, **k: (0, "REJECTED:\n1. NEW FINDING", ""))
    payload = _input("git commit -m x", cwd=repo)
    payload["session_id"] = "sid-keep"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert commit_hook.main() == 0

    assert "NEW FINDING" in rl.last_review_file(repo, "sid-keep").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# `git -C <path>` must gate the repo the commit ACTS ON, not the session cwd.
# --------------------------------------------------------------------------
def test_git_c_effective_dir_no_c_returns_cwd():
    assert rl.git_c_effective_dir("git commit -m x", "/sess") == "/sess"


def test_git_c_effective_dir_absolute_target():
    assert rl.git_c_effective_dir("git -C /other/repo commit -m x", "/sess") == "/other/repo"


def test_git_c_effective_dir_relative_joins_cwd():
    assert rl.git_c_effective_dir("git -C sub commit", "/sess") == "/sess/sub"


def test_git_c_effective_dir_multiple_c_is_cumulative():
    assert rl.git_c_effective_dir("git -C /a -C b commit -m x", "/sess") == "/a/b"


def test_git_c_effective_dir_finds_commit_in_compound_command():
    assert rl.git_c_effective_dir("git add x && git -C /other commit -m y", "/sess") == "/other"


def test_git_c_effective_dir_unparseable_returns_cwd():
    assert rl.git_c_effective_dir("git -C 'unterminated commit", "/sess") == "/sess"


def test_git_c_effective_dir_shell_variable_returns_cwd():
    # `git -C "$R" commit`: shlex keeps the token `$R` literal (no variable expansion), so the
    # target repo is unknowable here. git_c_effective_dir returns the session cwd as an INTERNAL
    # FALLBACK -- it must NEVER fabricate `cwd/$R` (no real repo). The commit GATE does not act on
    # this cwd: commit_c_unresolvable makes commit_hook FAIL THE COMMIT CLOSED (see
    # test_dash_C_shell_variable_fails_closed). Regression for the amazon-reviews fail-open.
    assert rl.git_c_effective_dir('git -C "$R" commit -m x', "/sess") == "/sess"


def test_git_c_effective_dir_command_substitution_returns_cwd():
    # `$(...)` and backticks are command substitution the lexer cannot resolve either; same internal
    # cwd fallback, and the gate itself fails closed on these (see commit_c_unresolvable).
    assert rl.git_c_effective_dir('git -C "$(cat p)" commit', "/sess") == "/sess"
    assert rl.git_c_effective_dir("git -C `pwd`/r commit -m y", "/sess") == "/sess"


def test_dash_C_shell_variable_fails_closed(tmp_path, monkeypatch):
    # End-to-end regression for the amazon-reviews fail-open, HARDENED. A commit issued as
    # `git -C "$R" commit` from a session whose OWN repo is active must be BLOCKED (fail-closed):
    # the reviewer parses the command lexically and cannot expand `$R`, so it cannot know which
    # repository the commit targets. Gating the session cwd (the earlier fix) reviews the WRONG
    # repo when `$R` points elsewhere -- itself a fail-open -- so an unresolvable target is denied
    # and the reviewer never runs.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)
    rl.set_active(repo)
    p = run_hook(
        _input('git -C "$R" commit -m x', cwd=repo),
        {
            "REVIEWER_HOME": home,
            "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN",
            "FAKE_CODEX_EXIT": "0",
        },
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip(), "gate silently passed the commit through (fail-open)"
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout, "the reviewer must not run on an unknowable repo"
    assert "shell variable" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_commit_c_unresolvable_flags_only_commit_shell_expansions():
    # True only when a `-C` target on a COMMIT invocation is shell expansion the gate can't resolve.
    assert rl.commit_c_unresolvable('git -C "$R" commit -m x') is True
    assert rl.commit_c_unresolvable('git -C "$(cat p)" commit') is True
    assert rl.commit_c_unresolvable("git -C `pwd`/r commit -m y") is True
    # a literal -C path is resolvable; no -C; and a non-commit subcommand are all NOT flagged
    assert rl.commit_c_unresolvable("git -C /repo commit -m x") is False
    assert rl.commit_c_unresolvable("git commit -m x") is False
    assert rl.commit_c_unresolvable('git -C "$R" status') is False


def test_dash_C_shell_variable_passes_through_when_reviewer_off_everywhere(tmp_path, monkeypatch):
    # off means off: with NO project active, an unresolvable `-C` commit is left ALONE (the plugin
    # is inert), not blocked. The fail-closed only engages when the reviewer is active somewhere.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_staged_repo(tmp_path)  # a repo, but the reviewer is NOT turned on for it
    p = run_hook(
        _input('git -C "$R" commit -m x', cwd=repo),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""  # pass-through: no deny, reviewer inert
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout


def test_dash_C_shell_variable_fails_closed_from_an_inactive_session(tmp_path, monkeypatch):
    # The wrong-repo fail-open: the session cwd itself is NOT active, but a shell-variable `-C`
    # could target the OTHER repo that IS active. Because a project is active, the unknowable commit
    # fails CLOSED regardless of the session cwd's own activation state.
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    active_dir = tmp_path / "active"
    active_dir.mkdir()
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    active = make_staged_repo(active_dir)     # some OTHER repo has the reviewer ON
    rl.set_active(active)
    session = make_staged_repo(session_dir)   # the session cwd is NOT active
    p = run_hook(
        _input('git -C "$R" commit -m x', cwd=session),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"},
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout


def test_commit_targets_the_dash_C_repo_not_the_session_cwd(tmp_path, monkeypatch):
    # Repro of the packaging-time block: `git -C <other_repo> commit` from a session whose
    # OWN repo has the reviewer ON (but nothing staged) must be gated against <other_repo>,
    # not the session cwd. The -C target has NO reviewer flag -> the commit PASSES THROUGH
    # (return 0, no deny, codex never runs) instead of being blocked as "nothing staged".
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    session = make_unstaged_repo(tmp_path)      # reviewer target repo; nothing staged
    rl.set_active(session)                       # reviewer ON for the SESSION repo
    other = make_staged_repo(tmp_path)           # different repo, staged, NO reviewer flag
    p = run_hook(
        _input("git -C {} commit -m x".format(other), cwd=session),
        {"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_STDOUT": "CODEX_SHOULD_NOT_RUN"},
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""                # pass-through: no deny
    assert "CODEX_SHOULD_NOT_RUN" not in p.stdout


# --------------------------------------------------------------------------
# effort labelling: the independent variable for comparing reviewer cost
# --------------------------------------------------------------------------
def test_the_pi_backend_is_not_labelled_with_codex_reasoning_effort(monkeypatch):
    """REGRESSION. pi takes `thinking`, NOT codex's model_reasoning_effort.

    Logging rl.codex_effort() unconditionally labelled a pi review with whatever
    the codex override happened to be -- `max`, say -- so a pi row would be
    counted as a max-effort codex review and silently corrupt exactly the
    effort comparison this field exists to support.
    """
    import commit_hook

    monkeypatch.setattr(commit_hook.rl, "codex_effort", lambda: "max")
    # pi's level is now resolved per backend rather than read from a module constant, so the
    # label must follow the RESOLVER. Pinned explicitly: if the label ever went back to reading
    # the codex chain, "max" would reappear here.
    monkeypatch.setattr(commit_hook.rl, "reviewer_effort", lambda backend: "low")

    pi = commit_hook._effort_label("pi")
    assert "max" not in pi
    assert "low" in pi
    assert pi != commit_hook._effort_label("codex")


def test_the_pi_label_and_the_pi_call_read_the_same_resolver(monkeypatch):
    """REGRESSION. The label and the `--thinking` argument were a shared module constant, which
    made them impossible to disagree. Now both call rl.reviewer_effort("pi"), and the point of
    this test is that they must keep doing so: a review recorded at one level and run at another
    corrupts the cost/catch-rate comparison the field exists for."""
    import commit_hook
    import inspect

    src = inspect.getsource(commit_hook)
    assert 'thinking=rl.reviewer_effort("pi")' in src, "the pi CALL stopped using the resolver"
    assert '"pi-thinking:" + rl.reviewer_effort("pi")' in src, "the LABEL stopped using it"


def test_the_codex_label_reports_the_resolved_override(monkeypatch):
    import commit_hook

    monkeypatch.setattr(commit_hook.rl, "codex_effort", lambda: "xhigh")
    assert commit_hook._effort_label("codex") == "xhigh"


def test_an_unset_codex_override_is_named_not_left_null(monkeypatch):
    # An unset override passes no -c model_reasoning_effort at all, so a bare
    # null cannot be told apart from "unknown" once the CLI default changes.
    import commit_hook

    monkeypatch.setattr(commit_hook.rl, "codex_effort", lambda: "")
    assert commit_hook._effort_label("codex") == "cli-default"


@pytest.mark.parametrize("stdin_text", [
    '{not json, "git commit -m x"',          # JSONDecodeError
    json.dumps(["git commit -m x"]),          # not an object
])
def test_a_crash_on_a_commit_payload_denies_instead_of_exiting_nonzero(tmp_path, stdin_text):
    # Claude Code blocks only on exit 2 or an explicit deny; exit 1 lets the call run.
    p = subprocess.run([sys.executable, COMMIT], input=stdin_text,
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "crashed" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_crash_on_an_unrelated_payload_stays_out_of_the_way(tmp_path):
    p = subprocess.run([sys.executable, COMMIT], input='{not json, "ls -la"',
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_a_crash_judges_the_parsed_command_not_the_whole_payload():
    # "commit" in the cwd (or anywhere else in the JSON) must not block an unrelated command.
    import commit_hook
    unrelated = json.dumps({"cwd": "/repos/commit-helper", "tool_input": {"command": "git status"}})
    committing = json.dumps({"cwd": "/repos/x", "tool_input": {"command": "git commit -m x"}})
    assert commit_hook._crash_output(RuntimeError("boom"), unrelated) is None
    out = commit_hook._crash_output(RuntimeError("boom"), committing)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_tool_input_null_is_not_a_crash(tmp_path):
    p = run_hook({"cwd": str(tmp_path), "tool_input": None}, {})
    assert p.returncode == 0 and p.stdout.strip() == ""
