# SPDX-License-Identifier: MPL-2.0
"""Integration tests for `reviewerAgent-ignore.md` in the commit hook.

Coverage:
- Channel A: the ignore file's markdown reaches codex in a labelled prompt block.
- Fail-closed on parse error: a malformed staged ignore file blocks the commit BEFORE
  any backend runs.
- Absence: no ignore block appears when the file isn't present, matching today's baseline.

Tests exercise the codex backend (the default and the one the plan targets for both
channels). See docs/plans/reviewer_ignore_guidance.md."""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE

PROJ_INPUT = lambda repo: {  # noqa: E731
    "cwd": repo, "tool_name": "Bash",
    "tool_input": {"command": "git commit -m x"},
}


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def make_repo_with_ignore(tmp_path, ignore_content):
    """Repo with reviewerAgent.md (brief) + a staged code file + a committed
    `reviewerAgent-ignore.md` carrying `ignore_content`. Some other file is staged
    so the commit-hook path is exercised."""
    repo = tmp_path / "ignore_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / rl.IGNORE_FILE_NAME).write_text(ignore_content)
    subprocess.run(["git", "-C", str(repo), "add", rl.IGNORE_FILE_NAME], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    # Some code change staged this commit — the ignore file itself is unchanged.
    (repo / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src.py"], check=True)
    return str(repo)


def make_repo_with_staged_malformed_ignore(tmp_path):
    """Repo where the ignore file is being INTRODUCED (or MODIFIED) in this commit with
    malformed content. The staged-validation branch must fail closed."""
    repo = tmp_path / "malformed_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    subprocess.run(["git", "-C", str(repo), "add", "reviewerAgent.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    (repo / rl.IGNORE_FILE_NAME).write_text(
        "```reviewer-ignore\n!oops-negation\n```\n"
    )
    (repo / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", rl.IGNORE_FILE_NAME, "src.py"], check=True)
    return str(repo)


def make_staged_repo_no_ignore(tmp_path):
    repo = tmp_path / "plain_repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "src.py"], check=True)
    return str(repo)


def _run_hook_in_process(monkeypatch, repo, capture_prompt=True, backend_calls=None):
    """Invoke commit_hook.main() in-process with codex stubs. Returns the captured stdout
    (a JSON blob) and, when `capture_prompt`, the prompt passed to the codex backend.

    Pins the backend to codex — `reviewer_backend()` honors the REVIEWER_BACKEND env var,
    so a suite run with the env set to pi or claude would otherwise route around the
    codex stub and invoke a real reviewer. `set_project_backend` writes the codex
    selection to per-project state, which the resolver prefers over the env var."""
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    rl.set_project_backend(repo, "codex")

    sys.path.insert(0, str(ROOT))
    import commit_hook

    monkeypatch.setattr(commit_hook.rl, "ensure_session",
                        lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "save_review", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage", lambda *a, **k: None)
    # Defensive: stub pi/claude too so a misrouted call cannot reach a real backend.
    monkeypatch.setattr(commit_hook.rl, "run_pi_review",
                        lambda *a, **k: pytest.fail("pi backend called; codex expected"))
    monkeypatch.setattr(commit_hook.rl, "run_claude_review",
                        lambda *a, **k: pytest.fail("claude backend called; codex expected"))

    captured = {}

    def fake_run(sid, cwd, sandbox, prompt, **kwargs):
        captured["prompt"] = prompt
        if backend_calls is not None:
            backend_calls.append(prompt)
        return 0, "APPROVED", ""

    monkeypatch.setattr(commit_hook.rl, "run_codex_streaming_audit", fake_run)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(PROJ_INPUT(repo)))
    )

    # Capture stdout — commit_hook.main() emits a JSON blob on the deny path.
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = commit_hook.main()
    return rc, buf.getvalue(), captured.get("prompt")


# --------------------------------------------------------------------------
# Channel A: prose reaches codex, in a labelled block, when the ignore file exists.
# --------------------------------------------------------------------------
def test_channel_a_prompt_contains_labelled_ignore_block(tmp_path, monkeypatch):
    """A committed, well-formed ignore file → its markdown appears in the prompt sent to
    codex, wrapped in a labelled block so codex can distinguish it from other content."""
    ignore_md = (
        "# Skim data files\n\nSKIM-RATIONALE-MARKER-42\n\n"
        "```reviewer-ignore\n"
        "data/**/*.jsonl\n"
        "```\n"
    )
    repo = make_repo_with_ignore(tmp_path, ignore_md)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)

    rc, out, prompt = _run_hook_in_process(monkeypatch, repo)
    assert rc == 0
    assert prompt is not None, "codex backend was not called"
    # The prose (with its distinctive marker) must appear.
    assert "SKIM-RATIONALE-MARKER-42" in prompt
    # And in a labelled block, so codex knows what this is.
    assert "reviewerAgent-ignore.md" in prompt


def test_channel_a_absent_when_ignore_file_is_missing(tmp_path, monkeypatch):
    """No ignore file anywhere → no ignore block in the prompt (exactly today's baseline)."""
    repo = make_staged_repo_no_ignore(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)

    rc, out, prompt = _run_hook_in_process(monkeypatch, repo)
    assert rc == 0
    assert prompt is not None, "codex backend was not called"
    # The labelled-block marker must not appear at all — nothing to relay.
    assert "reviewerAgent-ignore.md" not in prompt


# --------------------------------------------------------------------------
# Fail-closed: a malformed staged ignore file blocks the commit BEFORE any backend runs.
# --------------------------------------------------------------------------
def test_malformed_staged_ignore_blocks_commit_without_calling_backend(tmp_path, monkeypatch):
    """A parse error in the STAGED reviewerAgent-ignore.md fails the commit closed. The
    backend must NOT be invoked — the parse gate runs upstream of any codex call."""
    repo = make_repo_with_staged_malformed_ignore(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)

    backend_calls = []
    rc, out, prompt = _run_hook_in_process(
        monkeypatch, repo, backend_calls=backend_calls
    )
    assert rc == 0  # hook always exits 0; denial goes through the JSON blob
    assert prompt is None, "backend was called despite ignore-file parse error"
    assert backend_calls == [], "backend should not be invoked when parse fails"

    # Denial JSON on stdout carries the reason.
    payload = json.loads(out)
    text = json.dumps(payload)
    assert "reviewerAgent-ignore.md" in text, (
        "denial reason should name the file that failed to parse: %r" % payload
    )
    # The specific pattern (or a truncation of it) should be surfaced for actionability.
    assert "negation" in text.lower() or "!" in text
