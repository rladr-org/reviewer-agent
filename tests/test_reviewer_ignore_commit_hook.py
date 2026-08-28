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


def _run_hook_in_process(monkeypatch, repo, backend="codex", backend_calls=None):
    """Invoke commit_hook.main() in-process. Pins the routed backend and stubs its
    review runner (capturing the assembled prompt); the other two backends' runners
    are replaced with pytest.fail traps so an env-var-driven misroute is caught.

    Returns (returncode, stdout_str, prompt_str_or_None)."""
    assert backend in ("codex", "pi", "claude")
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    rl.set_project_backend(repo, backend)

    sys.path.insert(0, str(ROOT))
    import commit_hook

    monkeypatch.setattr(commit_hook.rl, "ensure_session",
                        lambda cwd, sid, **kwargs: ("sid-1", ""))
    monkeypatch.setattr(commit_hook.rl, "staged_change_block", lambda *a, **k: "")
    monkeypatch.setattr(commit_hook.rl, "apply_context", lambda p, *a, **k: p)
    monkeypatch.setattr(commit_hook.rl, "save_review", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(commit_hook.rl, "record_audited_stage", lambda *a, **k: None)
    # claude needs a staged patch — stub to a tiny fixed one.
    monkeypatch.setattr(commit_hook.rl, "staged_patch",
                        lambda *a, **k: ("--- a/x\n+++ b/x\n@@\n+y\n", False))
    monkeypatch.setattr(commit_hook.rl, "read_claude_session", lambda *a, **k: None)
    monkeypatch.setattr(commit_hook.rl, "read_pi_session", lambda *a, **k: None)
    monkeypatch.setattr(commit_hook.rl, "write_pi_session_if_absent", lambda *a, **k: None)

    captured = {}

    def fake_codex(sid, cwd, sandbox, prompt, **kwargs):
        captured["prompt"] = prompt
        if backend_calls is not None:
            backend_calls.append(prompt)
        return 0, "APPROVED", ""

    def fake_pi(cwd, claude_session_id, prompt, **kwargs):
        captured["prompt"] = prompt
        if backend_calls is not None:
            backend_calls.append(prompt)
        return 0, "APPROVED\ntokens used\n1,000\n", ""

    def fake_claude(cwd, claude_session_id, prompt, **kwargs):
        captured["prompt"] = prompt
        if backend_calls is not None:
            backend_calls.append(prompt)
        return 0, "APPROVED", ""

    routes = {
        "codex": ("run_codex_streaming_audit", fake_codex),
        "pi": ("run_pi_review", fake_pi),
        "claude": ("run_claude_review", fake_claude),
    }
    active_attr, active_fn = routes[backend]
    monkeypatch.setattr(commit_hook.rl, active_attr, active_fn)
    for other_backend, (attr, _) in routes.items():
        if other_backend == backend:
            continue
        def _trap(*a, **k):
            pytest.fail("wrong backend routed; expected {}".format(backend))
        monkeypatch.setattr(commit_hook.rl, attr, _trap)

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(PROJ_INPUT(repo)))
    )

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
# --------------------------------------------------------------------------
# Backend-specific guidance: codex/pi/claude get follow-ups their capabilities can execute.
# --------------------------------------------------------------------------
def test_channel_a_codex_followup_names_shell_fetch(tmp_path, monkeypatch):
    """Codex has shell + git — its follow-up must direct it to fetch via the shell, using a
    distinctive phrase from _CHANNEL_A_FOLLOWUP['codex'] (not text COMMIT_PROMPT itself
    provides, which would be a trivially-true assertion). Must NOT reference pi's git_read
    tool or claude's inlined-patch phrasing."""
    repo = make_repo_with_ignore(tmp_path, "```reviewer-ignore\ndata/**\n```\n")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rc, out, prompt = _run_hook_in_process(monkeypatch, repo, backend="codex")
    assert rc == 0 and prompt is not None
    # Distinctive phrase from the codex-specific follow-up; not in COMMIT_PROMPT.
    assert "you have not been forbidden anything" in prompt, (
        "codex follow-up not routed: %r" % prompt[-800:]
    )
    assert "git_read" not in prompt, "pi's git_read tool leaked into codex prompt"
    assert "no shell or git tool" not in prompt, (
        "claude's no-git phrasing leaked into codex prompt"
    )


def test_channel_a_pi_followup_names_git_read(tmp_path, monkeypatch):
    """Pi has git_read (read-only) — its follow-up must direct it to that tool, not to a
    shell fetch (codex-only) or the inlined-patch fallback (claude-only)."""
    repo = make_repo_with_ignore(tmp_path, "```reviewer-ignore\ndata/**\n```\n")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rc, out, prompt = _run_hook_in_process(monkeypatch, repo, backend="pi")
    assert rc == 0 and prompt is not None
    # Distinctive phrase from the pi-specific follow-up.
    assert "fetch it through your git_read tool" in prompt, (
        "pi follow-up not routed: %r" % prompt[-800:]
    )
    assert "you have not been forbidden anything" not in prompt, (
        "codex phrasing leaked into pi prompt"
    )
    assert "no shell or git tool" not in prompt, (
        "claude phrasing leaked into pi prompt"
    )


def test_channel_a_claude_followup_names_inlined_patch(tmp_path, monkeypatch):
    """Claude has neither shell nor git — its follow-up must point at the inlined patch,
    the only bytes it holds, and NOT invite an operation it cannot perform."""
    repo = make_repo_with_ignore(tmp_path, "```reviewer-ignore\ndata/**\n```\n")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rc, out, prompt = _run_hook_in_process(monkeypatch, repo, backend="claude")
    assert rc == 0 and prompt is not None
    # Distinctive phrase from the claude-specific follow-up.
    assert "You have no shell or git tool" in prompt, (
        "claude follow-up not routed: %r" % prompt[-800:]
    )
    assert "you have not been forbidden anything" not in prompt, (
        "codex phrasing leaked into claude prompt"
    )
    assert "git_read" not in prompt, "pi's git_read tool leaked into claude prompt"


def test_channel_a_appears_after_the_standing_commit_prompt(tmp_path, monkeypatch):
    """The labelled ignore block must FOLLOW the standing commit prompt in the assembled per-
    audit message. A repository-controlled block leading the prompt would let a compromised
    ignore file rewrite standing instructions before codex reads them."""
    repo = make_repo_with_ignore(
        tmp_path, "```reviewer-ignore\ndata/**\n```\n"
    )
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rc, out, prompt = _run_hook_in_process(monkeypatch, repo)
    assert rc == 0 and prompt is not None
    # apply_context is stubbed to identity in _run_hook_in_process, so the "standing prompt"
    # in the assembled message is literally rl.COMMIT_PROMPT (the first ~20 chars are a
    # distinctive prefix).
    standing_at = prompt.find(rl.COMMIT_PROMPT[:40])
    ignore_at = prompt.find("[reviewerAgent-ignore.md]")
    assert standing_at >= 0, "standing commit prompt not found in assembled message"
    assert ignore_at >= 0, "ignore block not found in assembled message"
    assert standing_at < ignore_at, (
        "ignore block precedes standing prompt — a compromised ignore file could rewrite "
        "the standing instructions before codex reads them"
    )


# --------------------------------------------------------------------------
# Channel B: matcher threads through to staged_change_block so codex retry inlining
# skips ignored paths. We don't call the full commit hook here — we call
# staged_change_block directly, since its behaviour is what Channel B constrains.
# --------------------------------------------------------------------------
def test_channel_b_ignored_paths_are_absent_from_retry_delta_block(tmp_path, monkeypatch):
    """A path matched by the ignore matcher must be dropped from the delta candidate set
    that feeds `_delta_commands` and `_inline_deltas`. Codex still has fetch authority
    via `git diff --cached -- <path>`; what's removed is only the hook-attached bytes."""
    from reviewer_ignore_match import IgnoreMatcher

    proj = tmp_path / "retry_repo"
    proj.mkdir()
    _init_repo(proj)
    (proj / "reviewerAgent.md").write_text("strict reviewer\n")
    (proj / "data").mkdir()
    (proj / "data/big.jsonl").write_text('{"k":1}\n')
    (proj / "src.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"], check=True)

    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(str(proj))
    rl.set_project_backend(str(proj), "codex")

    # Round 1: modify both files, stage, record the audit. This snapshots the round-1
    # staged blob shas into the audit record so `staged_change_block` can compare against
    # them on the next call.
    (proj / "data/big.jsonl").write_text('{"round1": true}\n')
    (proj / "src.py").write_text("x = 1  # round1\n")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)
    rl.record_audited_stage(str(proj), claude_session_id="c1", codex_session_id="sid-1")

    # Round 2 (retry): modify further and re-stage. staged_change_block will now see this
    # as "changed since your last audit."
    (proj / "data/big.jsonl").write_text('{"round2": true}\n')
    (proj / "src.py").write_text("x = 2  # round2\n")
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)

    matcher = IgnoreMatcher([(1, "data/**")])
    from reviewer_ignore import _SelfExemptedMatcher
    wrapped = _SelfExemptedMatcher(matcher)
    block_with = rl.staged_change_block(str(proj), "c1", "sid-1", ignore_matcher=wrapped)
    block_without = rl.staged_change_block(str(proj), "c1", "sid-1")

    # `data/big.jsonl` diff bytes should be present in the un-matched block and absent from
    # the matcher-filtered block. `src.py` diff bytes should be in both.
    assert '"round2": true' in block_without
    assert '"round2": true' not in block_with, (
        "matched path's diff bytes leaked into the retry prompt despite Channel B filter"
    )
    assert "x = 2" in block_without and "x = 2" in block_with

    # REGRESSION: filtering only `candidates` would let three other branches of
    # staged_change_block still emit mandatory `git show :<matched-path>` instructions —
    # the `not_shown` clause under `if inlined:`, the `elif deltas:` fallback, and the
    # `elif changed or added:` fallback. Assert the matched path never appears in the
    # block at all: no row in the STAGED SET table, no delta command, no re-read
    # instruction. Channel A prose (in the commit prompt) already tells the reviewer WHY
    # matched paths are being skimmed on this audit.
    assert "data/big.jsonl" not in block_with, (
        "matched path is mentioned in the retry block (row or fetch instruction); "
        "Channel B should make it invisible to every downstream branch: %r" % block_with
    )
    # And `src.py` (unmatched) should still appear with its normal CHANGED row.
    assert "src.py" in block_with


def test_channel_b_matched_only_retry_does_not_force_git_show(tmp_path, monkeypatch):
    """A retry whose ONLY staged change is a matched (ignored) path must not tell the
    reviewer to `git show :<matched-path>`. Reviewer specifically flagged that filtering
    only `candidates` leaves the `elif deltas` and `elif changed or added` fallback
    branches emitting mandatory fetches — this test drives exactly that path (nothing to
    inline, nothing in deltas, one matched change) and pins the fix."""
    from reviewer_ignore_match import IgnoreMatcher

    proj = tmp_path / "matched_only_repo"
    proj.mkdir()
    _init_repo(proj)
    (proj / "reviewerAgent.md").write_text("strict reviewer\n")
    (proj / "data").mkdir()
    (proj / "data/only.jsonl").write_text('{"round1": true}\n')
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"], check=True)

    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(str(proj))
    rl.set_project_backend(str(proj), "codex")

    # Round 1: change and re-stage; record the audit so the block will fire on next call.
    (proj / "data/only.jsonl").write_text('{"round1_changed": true}\n')
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)
    rl.record_audited_stage(str(proj), claude_session_id="c1", codex_session_id="sid-1")

    # Round 2 (retry): change ONLY the matched file. changed will be [data/only.jsonl];
    # after Channel B filter it must be [], so no downstream branch has any path to fetch.
    (proj / "data/only.jsonl").write_text('{"round2_changed": true}\n')
    subprocess.run(["git", "-C", str(proj), "add", "."], check=True)

    from reviewer_ignore import _SelfExemptedMatcher
    wrapped = _SelfExemptedMatcher(IgnoreMatcher([(1, "data/**")]))
    block = rl.staged_change_block(str(proj), "c1", "sid-1", ignore_matcher=wrapped)

    assert "git show :data/only.jsonl" not in block, (
        "elif-deltas or elif-changed-or-added fallback emitted a mandatory `git show` "
        "for a matched path: %r" % block
    )
    assert "data/only.jsonl" not in block, (
        "matched path leaked into retry block despite Channel B filter: %r" % block
    )
    # And critically: the block must NOT falsely claim NOTHING MOVED. The index really did
    # move (a data file changed); only the *audit-relevant* set is empty. Reviewer must
    # get the skimmed-only phrasing instead.
    assert "NOTHING MOVED" not in block, (
        "matched-only retry falsely reported NOTHING MOVED; the index did move (data file "
        "changed) and only the audit-relevant set is empty"
    )
    assert "only skimmed paths" in block, (
        "matched-only retry did not emit the skimmed-only annotation: %r" % block
    )


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
