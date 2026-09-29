# SPDX-License-Identifier: MPL-2.0
"""An audit that could not inspect its input cannot authorize publication.

Observed on Linux with a broken reviewer sandbox: an `APPROVED` verdict whose only suggestion said
the staged files could not be inspected. The Codex plugin refuses such a response; the Claude Code
gate must too, for every backend, and treat it as an operational failure rather than a verdict.
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
import pytest  # noqa: E402

OBSERVED_VERDICT = (
    "APPROVED\n\nSUGGESTIONS:\n"
    "1. Re-run the audit when the execution sandbox is functional; "
    "staged files could not be inspected."
)

FAILED_VERDICTS = [
    OBSERVED_VERDICT,
    "APPROVED\nSUGGESTIONS:\n1. I could not read the staged diff because the sandbox failed.",
    "APPROVED\nSUGGESTIONS:\n1. Unable to inspect the staged changes: permission denied.",
    "APPROVED\nSUGGESTIONS:\n1. The staged content was not reviewed.",
    "APPROVED\nSUGGESTIONS:\n1. The staged input was not inspected.",
    "REVIEW_FAILED: The sandbox could not start; the staged files were not inspected.",
    "APPROVED\nREVIEW_FAILED: The sandbox could not start.",
    "REVIEW_FAILED:\n1. bwrap: setting up uid map: Permission denied",
]


@pytest.mark.parametrize("verdict", FAILED_VERDICTS)
def test_an_admitted_inspection_failure_is_never_an_approval(verdict):
    assert rl.inspection_failure(verdict)
    assert not rl.is_approved(verdict)


@pytest.mark.parametrize("suggestion", [
    "GPU tests could not be run here; source inspection found no defects.",
    "The deployment check was unavailable; staged files were inspected.",
    "Document why the rejected alternative was not selected.",
    "Add a regression for cases where staged files could not be inspected.",
    "I could not run the staged tests because the optional GPU is unavailable.",
])
def test_a_nonblocking_suggestion_still_allows_approval(suggestion):
    verdict = "APPROVED\nSUGGESTIONS:\n1. " + suggestion
    assert not rl.inspection_failure(verdict)
    assert rl.is_approved(verdict)


def test_the_codex_schema_carries_a_review_failed_verdict():
    schema = json.loads(Path(rl.codex_verdict_schema_path()).read_text(encoding="utf-8"))
    assert "REVIEW_FAILED" in schema["properties"]["verdict"]["enum"]
    obj = rl.parse_codex_verdict_json(json.dumps(
        {"verdict": "REVIEW_FAILED", "findings": ["sandbox could not start"], "suggestions": []}))
    assert obj is not None
    text = rl.codex_verdict_to_text(obj)
    assert text.startswith("REVIEW_FAILED:")
    assert "sandbox could not start" in text
    assert rl.inspection_failure(text)
    assert not rl.is_approved(text)


def test_a_review_failed_verdict_without_a_reason_fails_closed():
    assert rl.parse_codex_verdict_json(json.dumps(
        {"verdict": "REVIEW_FAILED", "findings": [], "suggestions": []})) is None


@pytest.mark.parametrize("prompt", ["COMMIT_PROMPT", "PI_COMMIT_PROMPT", "CLAUDE_COMMIT_PROMPT"])
def test_every_commit_prompt_offers_review_failed(prompt):
    assert "REVIEW_FAILED" in getattr(rl, prompt)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _staged_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "base.py").write_text("base = 0\n")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "change.py").write_text("def average(values):\n    return sum(values) / len(values)\n")
    _git(repo, "add", "change.py")
    return str(repo)


@pytest.mark.parametrize("verdict", [OBSERVED_VERDICT, "REVIEW_FAILED: the sandbox could not start"])
def test_the_gate_refuses_to_commit_and_keeps_the_previous_ruling(
        tmp_path, monkeypatch, capsys, verdict):
    repo = _staged_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rl.set_project_backend(repo, "claude")
    sid = "claude-A"
    rl.save_review("REJECTED:\n1. Earlier finding", proj=repo, claude_session_id=sid)
    rl.set_commit_retry_pending(repo, sid)

    def fake_claude(cwd, claude_session_id, prompt, timeout=None, effort=None,
                    on_cancel=None, **kwargs):
        rl.write_claude_session_if_absent(cwd, claude_session_id, "REAL-CLAUDE")
        return 0, verdict, ""
    monkeypatch.setattr(commit_hook.rl, "run_claude_review", fake_claude)
    before_head = _git(repo, "rev-parse", "HEAD")
    before_tree = _git(repo, "write-tree")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"cwd": repo, "session_id": sid, "tool_name": "Bash",
         "tool_input": {"command": "git commit -m 'must not publish'"}})))

    assert commit_hook.main() == 0
    out = json.loads(capsys.readouterr().out)

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "inspect" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert _git(repo, "rev-parse", "HEAD") == before_head, "inspection failure published a commit"
    assert _git(repo, "write-tree") == before_tree
    # Not a verdict: the saved ruling the next reviewer sees as its own stays as it was, and the
    # retry state is not advanced.
    assert rl.last_review_file(repo, sid).read_text() == "REJECTED:\n1. Earlier finding"
    assert rl.commit_retry_pending(repo, sid)
    texts = [p.read_text(encoding="utf-8") for p in rl.review_history(repo)]
    assert any("OUTCOME: FAILED" in text and verdict in text for text in texts), texts
