# SPDX-License-Identifier: MPL-2.0
"""Operator directives: standing instructions injected into EVERY reviewer prompt.

The problem they solve: the reviewer's standing brief (`reviewerAgent.md`) is read exactly
once per Claude session, by `ensure_session`, and every later call resumes that captured
session id. So editing the brief mid-session changes nothing -- and forcing a re-brief
would discard the reviewer's accumulated context and cost minutes.

Observed failure (2026-07-25): the operator authorized a specific model for a judging run,
but across several commit attempts the reviewer kept rejecting the commit on the grounds
that the authorization "is not present anywhere in the visible thread". There was no way to
tell a live reviewer a durable fact.

Directives take the OTHER injection point: every reviewer call already sends a fresh prompt
(COMMIT_PROMPT, PI_COMMIT_PROMPT, STOP_SUPERVISE_PROMPT, the askq prompt). Text prepended
there lands on the very NEXT call, with no re-brief, for either backend, and -- because it
is re-read from disk per call -- it cannot be lost to session rotation or context
compaction the way a one-off "by the way..." turn could.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
CLI = str(ROOT / "directive_toggle.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

PROJ = "/Users/x/proj"
OTHER = "/Users/x/other"

GO = "Opus 4.6 is authorized for the judging process."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))


@pytest.fixture
def proj(tmp_path):
    """A real directory to run the CLI in -- it keys directives off its own cwd."""
    d = tmp_path / "cliproj"
    d.mkdir()
    return str(d)


def _run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, CLI] + list(args),
        capture_output=True, text=True, env=dict(os.environ), cwd=cwd,
    )


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------
def test_no_directives_adds_no_directive_block():
    # apply_context always prepends the standing capsule now, so the assertion is that
    # no DIRECTIVE block appears and the prompt still terminates the string.
    assert rl.directives_block(PROJ) == ""
    out = rl.apply_context("AUDIT THIS", PROJ)
    assert out.endswith("AUDIT THIS")
    assert "OPERATOR DIRECTIVE" not in out


def test_an_added_directive_appears_in_every_prompt():
    rl.add_directive(PROJ, GO)
    prompt = rl.apply_context("AUDIT THIS", PROJ)
    assert GO in prompt
    assert "AUDIT THIS" in prompt


def test_the_original_prompt_survives_verbatim():
    # The audit contract (the exact APPROVED / REJECTED output shape) lives in the prompt
    # constant; injection must not disturb a single character of it.
    rl.add_directive(PROJ, GO)
    assert rl.COMMIT_PROMPT in rl.apply_context(rl.COMMIT_PROMPT, PROJ)


def test_directives_are_scoped_to_the_project():
    # A directive that relaxes a gate must never leak into an unrelated repo.
    rl.add_directive(PROJ, GO)
    assert GO in rl.directives_block(PROJ)
    assert rl.directives_block(OTHER) == ""


def test_directives_are_numbered_in_the_order_added():
    rl.add_directive(PROJ, "first thing")
    rl.add_directive(PROJ, "second thing")
    block = rl.directives_block(PROJ)
    assert block.index("first thing") < block.index("second thing")
    assert "1." in block and "2." in block


def test_remove_drops_only_that_directive():
    rl.add_directive(PROJ, "keep me")
    rl.add_directive(PROJ, "drop me")
    rl.add_directive(PROJ, "keep me too")
    assert rl.remove_directive(PROJ, 2)["text"] == "drop me"
    texts = [d["text"] for d in rl.load_directives(PROJ)]
    assert texts == ["keep me", "keep me too"]


def test_remove_out_of_range_returns_none_and_changes_nothing():
    rl.add_directive(PROJ, GO)
    assert rl.remove_directive(PROJ, 7) is None
    assert rl.remove_directive(PROJ, 0) is None
    assert len(rl.load_directives(PROJ)) == 1


def test_clear_removes_all_of_them():
    rl.add_directive(PROJ, "a")
    rl.add_directive(PROJ, "b")
    assert rl.clear_directives(PROJ) == 2
    assert rl.load_directives(PROJ) == []
    out = rl.apply_context("AUDIT THIS", PROJ)
    assert out.endswith("AUDIT THIS")
    assert "a" not in rl.directives_block(PROJ)


def test_a_blank_directive_is_rejected():
    with pytest.raises(ValueError):
        rl.add_directive(PROJ, "   \n ")
    assert rl.load_directives(PROJ) == []


def test_a_malformed_directives_file_falls_back_to_none():
    # Same fail-soft rule as config.json: a hand-edited file with a syntax error must never
    # wedge the commit gate.
    f = rl.directives_file(PROJ)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('[{"text": "x",}]', encoding="utf-8")
    assert rl.load_directives(PROJ) == []
    assert rl.apply_context("AUDIT THIS", PROJ).endswith("AUDIT THIS")
    assert rl.directives_block(PROJ) == ""


def test_the_block_marks_directives_as_operator_authored_and_authoritative():
    # The whole point is provenance: the reviewer rejected the earlier commit precisely
    # because the authorization looked self-authored by the agent. The block must say who
    # wrote these and that they carry authority, or the reviewer may argue with them too.
    rl.add_directive(PROJ, GO)
    block = rl.directives_block(PROJ).lower()
    assert "operator" in block
    assert "authoritative" in block


def test_the_block_does_not_grant_blanket_approval():
    # A directive settles the thing it names; it must not read as "approve everything".
    rl.add_directive(PROJ, GO)
    block = rl.directives_block(PROJ).lower()
    assert "does not" in block or "do not otherwise" in block


def test_directives_are_read_fresh_on_every_call():
    # No caching: the hook is a short-lived process, but within one process a directive
    # added after the first read must still be seen (this is what makes "add it and retry
    # the commit" work without restarting anything).
    assert GO not in rl.apply_context("P", PROJ)
    rl.add_directive(PROJ, GO)
    assert GO in rl.apply_context("P", PROJ)


def test_a_worktree_shares_the_main_checkouts_directives(tmp_path):
    # Same canonical-project keying as the on/off flag: set it anywhere in the repo, and
    # every linked worktree sees it.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "b"],
                   check=True)

    rl.add_directive(str(repo), GO)
    assert GO in rl.directives_block(str(wt))


# --------------------------------------------------------------------------
# /reviewerDirective CLI
# --------------------------------------------------------------------------
def test_cli_with_no_arguments_reports_none_yet(proj):
    p = _run_cli([], proj)
    assert p.returncode == 0
    assert "no operator directives" in p.stdout.lower()


def test_cli_adds_a_directive_and_confirms_it(proj):
    p = _run_cli([GO], proj)
    assert p.returncode == 0, p.stderr
    assert GO in p.stdout
    assert [d["text"] for d in rl.load_directives(proj)] == [GO]


def test_cli_lists_directives_numbered(proj):
    _run_cli(["first thing"], proj)
    _run_cli(["second thing"], proj)
    p = _run_cli(["list"], proj)
    assert p.returncode == 0
    assert "1." in p.stdout and "first thing" in p.stdout
    assert "2." in p.stdout and "second thing" in p.stdout


def test_cli_clear_empties_the_list(proj):
    _run_cli([GO], proj)
    p = _run_cli(["clear"], proj)
    assert p.returncode == 0
    assert rl.load_directives(proj) == []
    assert "no operator directives" in _run_cli(["list"], proj).stdout.lower()


def test_cli_removes_by_number(proj):
    _run_cli(["keep me"], proj)
    _run_cli(["drop me"], proj)
    p = _run_cli(["remove", "2"], proj)
    assert p.returncode == 0
    assert "drop me" in p.stdout
    assert [d["text"] for d in rl.load_directives(proj)] == ["keep me"]


def test_cli_rejects_a_bad_remove_index(proj):
    _run_cli([GO], proj)
    p = _run_cli(["remove", "9"], proj)
    assert p.returncode != 0
    assert len(rl.load_directives(proj)) == 1


def test_cli_treats_a_multi_word_directive_as_one_directive(proj):
    # The skill passes $ARGUMENTS unquoted, so the words arrive as separate argv entries.
    p = _run_cli(["Opus", "4.6", "is", "authorized."], proj)
    assert p.returncode == 0
    assert [d["text"] for d in rl.load_directives(proj)] == ["Opus 4.6 is authorized."]


# --------------------------------------------------------------------------
# the directive actually reaches the reviewer
# --------------------------------------------------------------------------
def _repo_with_staged_change(tmp_path):
    repo = tmp_path / "gated"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.py"], check=True)
    return str(repo)


def _sent_prompts(dump):
    """Every prompt argument the hook handed to codex, across all invocations."""
    if not Path(dump).exists():
        return []
    out = []
    for line in Path(dump).read_text(encoding="utf-8").splitlines():
        out.extend(json.loads(line))
    return out


def _run(script, stdin_obj, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / script)],
        input=json.dumps(stdin_obj), capture_output=True, text=True, env=env,
    )


def test_the_commit_audit_prompt_carries_the_directive(tmp_path):
    repo = _repo_with_staged_change(tmp_path)
    rl.set_active(repo)
    rl.add_directive(repo, GO)
    dump = str(tmp_path / "argv.jsonl")
    p = _run(
        "commit_hook.py",
        {"cwd": repo, "session_id": "s1", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m x"}},
        {"REVIEWER_HOME": os.environ["REVIEWER_HOME"],
         "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_ARGV_FILE": dump,
         "FAKE_CODEX_STDOUT": "APPROVED"},
    )
    assert p.returncode == 0
    assert any(GO in arg for arg in _sent_prompts(dump)), _sent_prompts(dump)


def test_a_directive_added_after_the_brief_reaches_the_next_audit(tmp_path):
    # THE point of the feature. The reviewer session is briefed once and cached; a directive
    # recorded afterwards must still land, with no re-brief and no restart.
    repo = _repo_with_staged_change(tmp_path)
    rl.set_active(repo)
    rl.write_session_id(repo, "s1", "already-briefed-session")  # session established earlier
    rl.add_directive(repo, GO)
    dump = str(tmp_path / "argv.jsonl")
    _run(
        "commit_hook.py",
        {"cwd": repo, "session_id": "s1", "tool_name": "Bash",
         "tool_input": {"command": "git commit -m x"}},
        {"REVIEWER_HOME": os.environ["REVIEWER_HOME"],
         "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_ARGV_FILE": dump,
         "FAKE_CODEX_STDOUT": "APPROVED"},
    )
    sent = _sent_prompts(dump)
    assert "already-briefed-session" in sent, sent  # resumed, NOT re-briefed
    assert any(GO in arg for arg in sent), sent


def test_the_stop_supervisor_prompt_carries_the_directive(tmp_path):
    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    subprocess.run(["git", "init", "-q", repo], check=True)
    rl.set_stopcontinue_active(repo)
    rl.add_directive(repo, GO)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    }) + "\n")
    dump = str(tmp_path / "argv.jsonl")
    _run(
        "stop_hook.py",
        {"cwd": repo, "session_id": "s2", "transcript_path": str(transcript),
         "hook_event_name": "Stop", "stop_hook_active": False},
        {"REVIEWER_HOME": os.environ["REVIEWER_HOME"],
         "REVIEWER_CODEX_BIN": FAKE,
         "FAKE_CODEX_ARGV_FILE": dump,
         "FAKE_CODEX_STDOUT": "STOP_OK"},
    )
    assert any(GO in arg for arg in _sent_prompts(dump)), _sent_prompts(dump)
