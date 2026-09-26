# SPDX-License-Identifier: MPL-2.0
"""The gate performs the reviewed commit itself, then ALLOWS the call with the command rewritten.

Integration for the "digest-compare + normal commit" design (see the "Implemented approach" note
in docs/plans/claude_commit_transaction_design.md): on approval the hook confirms the staged
set's `git write-tree` digest still matches what was reviewed, runs an ordinary `git commit` (the
repo's own hooks run), then rewrites the agent's Bash `git commit` to a harmless confirmation echo
and ALLOWS it (PreToolUse `updatedInput`) so the call succeeds without re-committing; a staged set
that changed since the review, or a failed/unknown commit, is DENIED; and a call that is not
exactly one plain `git commit` is refused BEFORE any review is spent.

These run real `git` against real temporary repositories -- only the model verdict is stubbed,
never the Git operation whose integrity is the point. The claude backend is used because the hook
hands the reviewer a patch, so the whole flow is in-process and deterministic.
"""
import io
import json
import subprocess
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
sys.path.insert(0, str(ROOT))

import reviewer_lib as rl  # noqa: E402
import commit_hook  # noqa: E402


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    ).stdout.strip()


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


def _claude_repo(tmp_path):
    """A repo with an initial commit and one file staged on top; backend=claude, active."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "base.py").write_text("base = 0\n")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    return str(repo)


def _activate(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.set_project_backend(repo, "claude")


def _run(repo, command, capsys, monkeypatch, session_id="claude-A"):
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(json.dumps(
            {"cwd": repo, "session_id": session_id, "tool_name": "Bash",
             "tool_input": {"command": command}})))
    assert commit_hook.main() == 0
    return json.loads(capsys.readouterr().out)


def _approve(monkeypatch, verdict="APPROVED -- looks good\n", record=None):
    def fake_claude(cwd, claude_session_id, prompt, timeout=None, effort=None,
                    on_cancel=None, **kwargs):
        if record is not None:
            record.append(prompt)
        rl.write_claude_session_if_absent(cwd, claude_session_id, "REAL-CLAUDE")
        return 0, verdict, ""
    monkeypatch.setattr(commit_hook.rl, "run_claude_review", fake_claude)


def test_claude_approval_commits_the_reviewed_tree_and_allows_the_call(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    _approve(monkeypatch)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    # The gate committed itself, then ALLOWS the call with the agent's `git commit` rewritten to a
    # harmless echo (updatedInput) -- so it succeeds (green) and no real commit runs a second time.
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    rewritten = out["hookSpecificOutput"]["updatedInput"]["command"]
    assert rewritten.startswith("echo ")  # a harmless confirmation, not a git commit
    assert "git commit" not in rewritten
    # A real commit landed, on top of the previous HEAD.
    head = _git(repo, "rev-parse", "HEAD")
    assert head != before
    assert _git(repo, "rev-parse", "HEAD^") == before
    # The confirmation NAMES the published commit id (in the rewritten echo and the systemMessage).
    assert head[:12] in (rewritten + out.get("systemMessage", ""))
    # The committed message and tree are the reviewed ones.
    assert _git(repo, "log", "-1", "--format=%s") == "feature"
    assert "f.py" in _git(repo, "show", "--stat", "HEAD")
    # Nothing is left staged, so an identical retry is inert (meets the nothing-staged refusal).
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_claude_approval_carries_repeated_messages_and_signoff(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    _approve(monkeypatch)

    _run(repo, "git commit -m 'para one' -m 'para two' --signoff", capsys, monkeypatch)

    body = _git(repo, "log", "-1", "--format=%B")
    assert "para one" in body
    assert "para two" in body  # repeated -m joined as separate paragraphs
    assert "Signed-off-by:" in body  # --signoff carried


def test_claude_refuses_a_compound_commit_before_any_review(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m x && git push", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert called == []  # refused BEFORE any review was spent
    assert _git(repo, "rev-parse", "HEAD") == before  # nothing published


def test_claude_refuses_amend_before_any_review(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)

    out = _run(repo, "git commit --amend -m x", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert called == []
    assert "amend" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_claude_refuses_a_shell_expanded_message_before_any_review(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)

    out = _run(repo, 'git commit -m "$MSG"', capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert called == []  # the literal token is never committed as the message


def test_a_content_changing_pre_commit_hook_is_honored_not_rejected(
        tmp_path, monkeypatch, capsys):
    # Design decision: the gate does not second-guess the repo's own hooks. If a pre-commit hook
    # rewrites content, that rewritten content is what gets committed -- the gate commits as the
    # repository is configured, hooks and all, rather than rejecting the hook's change.
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    hooks = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "hooks"
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'x = 1  # formatted by hook\\n' > f.py\ngit add f.py\n")
    hook.chmod(0o755)
    _approve(monkeypatch)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"  # gate committed, allowed call
    head = _git(repo, "rev-parse", "HEAD")
    assert head != before  # a commit landed -- the hook's change was NOT rejected
    assert "formatted by hook" in _git(repo, "show", "HEAD:f.py")  # the hook's version committed


def test_a_failing_pre_commit_hook_is_not_reported_as_changes_intact(
        tmp_path, monkeypatch, capsys):
    # A pre-commit hook may edit/re-stage files and THEN fail; the failure message must not falsely
    # promise the staged set is unchanged -- it should point the user at the real state instead.
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    hooks = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "hooks"
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'edited = 1\\n' >> f.py\ngit add f.py\nexit 1\n")
    hook.chmod(0o755)
    _approve(monkeypatch)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _git(repo, "rev-parse", "HEAD") == before  # nothing published
    reason = out["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "intact" not in reason  # must not falsely claim the staged set is untouched
    assert "git status" in reason  # points the user at the real state


def test_a_hook_failure_after_publication_is_surfaced_not_hidden(
        tmp_path, monkeypatch, capsys):
    # When the commit IS published but git did not exit cleanly (a hook failed or hung afterwards),
    # run_commit returns ("committed", sha, warning). The gate must SURFACE that warning to the
    # operator and agent, not merely log it behind a plain success line.
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    _approve(monkeypatch)
    warning = "git commit did not exit cleanly after publishing deadbeef123"
    monkeypatch.setattr(
        commit_hook.rl, "run_commit",
        lambda *a, **k: ("committed", "deadbeef123", warning))

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    # A published commit (even with a warning) is a success -> ALLOW with the call rewritten.
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    combined = (out["hookSpecificOutput"]["updatedInput"]["command"]
                + out.get("systemMessage", ""))
    assert "deadbeef123" in combined  # names the published commit
    assert warning in combined  # AND surfaces the hook-failure warning, not just logs it


def test_an_undetermined_publication_does_not_invite_a_blind_retry(
        tmp_path, monkeypatch, capsys):
    # When publication cannot be determined, the gate must not say "nothing committed" -- it tells
    # the agent to check rather than blindly retry (which could duplicate a commit).
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    _approve(monkeypatch)
    monkeypatch.setattr(
        commit_hook.rl, "run_commit",
        lambda *a, **k: ("unknown", None, "the repository state could not be read"))

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "unknown" in reason or "could not be determined" in reason
    assert "git log" in reason  # points the agent at checking rather than retrying blindly


def test_a_stage_during_review_blocks_publication_and_preserves_the_work(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)

    def fake_claude(cwd, claude_session_id, prompt, timeout=None, effort=None,
                    on_cancel=None, **kwargs):
        # Someone stages new content AFTER the reviewer's digest was recorded. At commit time the
        # write-tree no longer matches, so the approval no longer describes what would be committed.
        (Path(cwd) / "late.py").write_text("late = 1\n")
        subprocess.run(["git", "-C", cwd, "add", "late.py"], check=True)
        rl.write_claude_session_if_absent(cwd, claude_session_id, "REAL-CLAUDE")
        return 0, "APPROVED\n", ""
    monkeypatch.setattr(commit_hook.rl, "run_claude_review", fake_claude)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"  # digest mismatch -> re-review
    assert _git(repo, "rev-parse", "HEAD") == before  # nothing committed
    staged = _git(repo, "diff", "--cached", "--name-only").split()
    assert "late.py" in staged  # the newer work was neither reset nor stashed
    assert "f.py" in staged


def test_stage_and_commit_in_one_call_is_refused_accurately_with_an_empty_index(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _git(repo, "reset", "-q")  # nothing staged: the case that said "nothing is staged"
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)

    out = _run(repo, "git add f.py" + " && " + "git commit -m x", capsys, monkeypatch)

    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "stages and commits in one call" in reason
    assert "nothing is staged" not in reason
    assert called == []
