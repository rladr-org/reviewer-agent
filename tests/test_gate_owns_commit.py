# SPDX-License-Identifier: MPL-2.0
"""The Claude gate performs the reviewed commit itself and DENIES the tool call.

Integration described in docs/plans/claude_commit_transaction_design.md: on approval the hook
commits the CAPTURED snapshot through the reference transaction and denies the original Bash
call, naming the published commit id; and a call that is not exactly one plain `git commit` is
refused BEFORE any review is spent.

These run the real transaction against real temporary repositories -- only the model verdict is
stubbed, never the Git operation whose integrity is the point. The claude backend is used
because the hook itself hands the reviewer a patch, so binding it to the captured snapshot is
entirely in-process and deterministic.
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


def _no_temp_dirs_left(repo):
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    return not list(gitdir.glob("reviewer-commit-*"))


def test_claude_approval_commits_the_reviewed_tree_and_denies_the_call(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    _approve(monkeypatch)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    # The tool call is DENIED, so the agent's own commit never runs -- there is no second dispatch.
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # A real commit landed, on top of the previous HEAD.
    head = _git(repo, "rev-parse", "HEAD")
    assert head != before
    assert _git(repo, "rev-parse", "HEAD^") == before
    # The denial NAMES the published commit id (so the agent does not commit again).
    reason = out["hookSpecificOutput"]["permissionDecisionReason"] + out.get("systemMessage", "")
    assert head[:12] in reason
    # The committed message and tree are the reviewed ones.
    assert _git(repo, "log", "-1", "--format=%s") == "feature"
    assert "f.py" in _git(repo, "show", "--stat", "HEAD")
    # Nothing is left staged, so an identical retry is inert (meets the nothing-staged refusal).
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _no_temp_dirs_left(repo)


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


def test_a_stage_during_review_blocks_publication_and_preserves_the_work(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)

    def fake_claude(cwd, claude_session_id, prompt, timeout=None, effort=None,
                    on_cancel=None, **kwargs):
        # Someone stages new content AFTER the snapshot was captured and the patch was handed
        # over. The reviewer therefore approves the captured tree, not this.
        (Path(cwd) / "late.py").write_text("late = 1\n")
        subprocess.run(["git", "-C", cwd, "add", "late.py"], check=True)
        rl.write_claude_session_if_absent(cwd, claude_session_id, "REAL-CLAUDE")
        return 0, "APPROVED\n", ""
    monkeypatch.setattr(commit_hook.rl, "run_claude_review", fake_claude)
    before = _git(repo, "rev-parse", "HEAD")

    out = _run(repo, "git commit -m 'feature'", capsys, monkeypatch)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _git(repo, "rev-parse", "HEAD") == before  # nothing published
    staged = _git(repo, "diff", "--cached", "--name-only").split()
    assert "late.py" in staged  # the newer work was neither reset nor stashed
    assert "f.py" in staged
    assert _no_temp_dirs_left(repo)
