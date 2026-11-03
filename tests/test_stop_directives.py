# SPDX-License-Identifier: MPL-2.0
"""Stop directives: standing operator rules read only by the stop supervisor
(docs/plans/stopcontinue_directive.md).

Observed failure (2026-11-03): the operator had approved a list of five tasks with "ok". The agent
finished one, wrote "Next on the approved list is the survey box rewording", and the supervisor
answered STOP_OK, judging the stop against the newest request alone. The operator had no way to
give the supervisor a standing rule about stopping without also sending it to every commit audit,
which is where `/reviewerDirective` text goes.
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
SKILLS = _BASE / "skills"
UPS = str(ROOT / "userpromptsubmit_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

PROJ = "/Users/x/proj"
SENTINEL = "<<reviewer-agent stop directive>>"
RULE = "Items on HANDOVER.md's approved list are approved; do not stop between them."
FACT = "Opus 4.6 is authorized for the judging process."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    for var in ("REVIEWER_STATE_DIR", "REVIEWER_BACKEND",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    return str(path)


def _sent_prompts(dump):
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


def _fake_env(dump, stdout):
    return {"REVIEWER_HOME": os.environ["REVIEWER_HOME"], "REVIEWER_CODEX_BIN": FAKE,
            "FAKE_CODEX_ARGV_FILE": dump, "FAKE_CODEX_STDOUT": stdout}


def _run_ups(cwd, prompt):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = cwd
    stdin = {"cwd": cwd, "session_id": "claude-dir", "prompt": prompt,
             "hook_event_name": "UserPromptSubmit"}
    p = subprocess.run([sys.executable, UPS], input=json.dumps(stdin),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


def _stop_texts(proj):
    return [d["text"] for d in rl.load_directives(proj, kind="stop")]


# --------------------------------------------------------------------------
# where the rule reaches
# --------------------------------------------------------------------------
def test_a_stop_directive_reaches_the_stop_supervisor(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    rl.set_stopcontinue_active(repo)
    rl.add_directive(repo, RULE, kind="stop")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    }) + "\n")
    dump = str(tmp_path / "argv.jsonl")
    _run("stop_hook.py",
         {"cwd": repo, "session_id": "s2", "transcript_path": str(transcript),
          "hook_event_name": "Stop", "stop_hook_active": False},
         _fake_env(dump, "STOP_OK"))
    assert any(RULE in arg for arg in _sent_prompts(dump)), _sent_prompts(dump)


def test_a_stop_directive_never_reaches_a_commit_audit(tmp_path):
    repo = _git_repo(tmp_path / "gated")
    (Path(repo) / "reviewerAgent.md").write_text("strict reviewer\n")
    (Path(repo) / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", repo, "add", "f.py"], check=True)
    rl.set_active(repo)
    rl.add_directive(repo, RULE, kind="stop")
    rl.add_directive(repo, FACT)
    dump = str(tmp_path / "argv.jsonl")
    p = _run("commit_hook.py",
             {"cwd": repo, "session_id": "s1", "tool_name": "Bash",
              "tool_input": {"command": "git commit -m x"}},
             _fake_env(dump, "APPROVED"))
    assert p.returncode == 0, p.stderr
    sent = _sent_prompts(dump)
    assert any(FACT in arg for arg in sent), sent  # the audit did run, with its own directives
    assert not any(RULE in arg for arg in sent), sent


def test_apply_context_adds_stop_directives_only_for_the_stop_role():
    rl.add_directive(PROJ, RULE, kind="stop")
    assert RULE not in rl.apply_context("AUDIT THIS", PROJ)
    assert RULE not in rl.apply_context("AUDIT THIS", PROJ, commit_audit=True)
    stop = rl.apply_context("JUDGE THIS STOP", PROJ, role=rl.ROLE_STOP)
    assert RULE in stop
    assert stop.endswith("JUDGE THIS STOP")


def test_the_stop_supervisor_still_gets_reviewer_directives_too():
    rl.add_directive(PROJ, FACT)
    rl.add_directive(PROJ, RULE, kind="stop")
    stop = rl.apply_context("JUDGE THIS STOP", PROJ, role=rl.ROLE_STOP)
    assert FACT in stop and RULE in stop


# --------------------------------------------------------------------------
# the two lists are separate
# --------------------------------------------------------------------------
def test_the_two_lists_do_not_share_entries():
    rl.add_directive(PROJ, FACT)
    rl.add_directive(PROJ, RULE, kind="stop")
    assert [d["text"] for d in rl.load_directives(PROJ)] == [FACT]
    assert _stop_texts(PROJ) == [RULE]
    assert RULE not in rl.directives_block(PROJ)
    assert FACT not in rl.directives_block(PROJ, kind="stop")


def test_removing_or_clearing_one_list_leaves_the_other():
    rl.add_directive(PROJ, FACT)
    rl.add_directive(PROJ, RULE, kind="stop")
    assert rl.remove_directive(PROJ, 1, kind="stop")["text"] == RULE
    assert [d["text"] for d in rl.load_directives(PROJ)] == [FACT]
    rl.add_directive(PROJ, RULE, kind="stop")
    assert rl.clear_directives(PROJ) == 1
    assert _stop_texts(PROJ) == [RULE]


def test_a_worktree_shares_the_main_checkouts_stop_directives(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    (Path(repo) / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", repo, "worktree", "add", "-q", str(wt), "-b", "b"], check=True)
    rl.add_directive(repo, RULE, kind="stop")
    assert RULE in rl.directives_block(str(wt), kind="stop")


# --------------------------------------------------------------------------
# what the block tells the supervisor
# --------------------------------------------------------------------------
def test_the_block_names_its_command_and_its_authority():
    rl.add_directive(PROJ, RULE, kind="stop")
    block = rl.directives_block(PROJ, kind="stop")
    assert "/stopContinueDirective" in block
    assert "/reviewerDirective" not in block
    lower = block.lower()
    assert "operator" in lower and "authoritative" in lower
    assert "stop" in lower
    assert "do not otherwise" in lower or "does not otherwise" in lower


def test_no_stop_directives_adds_no_block():
    assert rl.directives_block(PROJ, kind="stop") == ""
    assert "STOP DIRECTIVES" not in rl.apply_context("P", PROJ, role=rl.ROLE_STOP)


# --------------------------------------------------------------------------
# the command, handled by the UserPromptSubmit hook
# --------------------------------------------------------------------------
@pytest.mark.parametrize("command", ["/reviewer-agent:stopContinueDirective",
                                     "/stopContinueDirective", SENTINEL])
def test_the_hook_records_the_rule_verbatim_and_blocks(tmp_path, command):
    repo = _git_repo(tmp_path / "repo")
    pwned = tmp_path / "PWNED_marker"
    text = '$(touch {}) stop only when "every" task is committed'.format(pwned)
    out = _run_ups(repo, command + " " + text)
    assert not pwned.exists(), "shell command substitution executed -- not injection-proof"
    assert _stop_texts(repo) == [text]
    assert rl.load_directives(repo) == []
    assert out.get("decision") == "block", out
    first = out["reason"].splitlines()[0]
    assert "stopContinueDirective SUCCEEDED" in first, out
    assert out["systemMessage"] == out["reason"], out

    listed = _run_ups(repo, command + " list")
    assert listed.get("decision") == "block", listed
    assert text in listed["reason"], listed


def test_the_hook_reports_a_bad_remove_as_failed(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    out = _run_ups(repo, "/stopContinueDirective remove 99")
    first = out["reason"].splitlines()[0]
    assert "stopContinueDirective FAILED:" in first, out
    assert "/stopContinueDirective list" in out["reason"], out


def test_an_empty_list_names_the_stop_command(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    out = _run_ups(repo, "/stopContinueDirective")
    assert out.get("decision") == "block", out
    assert "/stopContinueDirective <text>" in out["reason"], out
    assert "/reviewerDirective" not in out["reason"], out


def test_the_hook_ignores_a_longer_command_name(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    out = _run_ups(repo, "/stopContinueDirectives list")
    assert out.get("decision") != "block", out
    assert _stop_texts(repo) == []


def test_a_reviewer_directive_records_nothing_in_the_stop_list(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    _run_ups(repo, "/reviewerDirective " + FACT)
    assert [d["text"] for d in rl.load_directives(repo)] == [FACT]
    assert _stop_texts(repo) == []


def test_the_skill_carries_the_sentinel_and_no_shell_line():
    body = (SKILLS / "stopContinueDirective" / "SKILL.md").read_text(encoding="utf-8")
    assert SENTINEL in body and "$ARGUMENTS" in body
    assert not any(ln.strip().startswith("!`") for ln in body.splitlines())
    assert "disable-model-invocation: true" in body


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------
def test_the_context_log_line_counts_stop_directives(monkeypatch):
    monkeypatch.setenv("REVIEWER_LOG_OPERATOR_CONTEXT", "1")
    rl.add_directive(PROJ, RULE, kind="stop")
    rl.add_directive(PROJ, "a second stop rule", kind="stop")
    line = rl.context_provenance(PROJ, None)
    assert "directives=0" in line and "stop_directives=2" in line, line
