# SPDX-License-Identifier: MPL-2.0
"""Red tests for the injection-safe command split (docs/plans/reviewer_cmd_dispatcher.md).

Free-text reviewerDirective moves to a UserPromptSubmit hook (JSON stdin = no shell = injection
proof); the six constrained commands route through one dispatcher `reviewer_cmd.py` so operators add
a single scoped permission rule. These are RED: `reviewer_cmd.py`, `directive_toggle.run(args,
proj)`, the hook's sentinel branch, and the rewired skills do not exist yet.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
sys.path.insert(0, str(ROOT))
import reviewer_lib as rl  # noqa: E402

pytestmark = pytest.mark.smoke  # install-time subset (tests/test_smoke_subset.py)

UPS = str(ROOT / "userpromptsubmit_hook.py")
REVIEWER_CMD = str(ROOT / "reviewer_cmd.py")
SKILLS = _BASE / "skills"

# The literal the reviewerDirective skill emits (no bang) and the hook detects. Green must use this
# exact string in both places; test_skill_wiring pins them together.
SENTINEL = "<<reviewer-agent directive>>"

# subcommand -> the toggle MODULE NAME the dispatcher must delegate to.
DISPATCH = {
    "agent": "toggle",
    "history": "reviewer_history_toggle",
    "model": "reviewer_model_toggle",
    "session": "reviewer_session_toggle",
    "stopcontinue": "stopcontinue_toggle",
    "watchdog": "stopcontinue_watchdog_toggle",
}
# skill dir -> its dispatcher subcommand.
SIX_SKILLS = {
    "reviewerAgent": "agent",
    "reviewerHistory": "history",
    "reviewerModel": "model",
    "reviewerSession": "session",
    "stopContinue": "stopcontinue",
    "stopContinueWatchdog": "watchdog",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in ("REVIEWER_STATE_DIR", "REVIEWER_BACKEND",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return str(repo)


def run_ups(cwd, prompt, home, session_id="claude-dir"):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = cwd
    stdin = {"cwd": cwd, "session_id": session_id, "prompt": prompt,
             "hook_event_name": "UserPromptSubmit"}
    return subprocess.run([sys.executable, UPS], input=json.dumps(stdin),
                          capture_output=True, text=True, env=env)


# --------------------------------------------------------------------------
# Dispatcher: routing, quoted-arg re-split, unknown subcommand
# --------------------------------------------------------------------------
def test_dispatcher_routes_each_subcommand_to_its_toggle(monkeypatch):
    import reviewer_cmd                                   # ModuleNotFoundError today = red
    for sub, mod_name in DISPATCH.items():
        mod = __import__(mod_name)
        calls = {}
        monkeypatch.setattr(mod, "main", lambda argv, _c=calls: (_c.update(argv=argv) or 0))
        rc = reviewer_cmd.main(["reviewer_cmd.py", sub, "status"])
        assert rc == 0, (sub, rc)
        assert calls.get("argv", [None])[1:] == ["status"], (sub, calls)


def test_dispatcher_resplits_quoted_multitoken_args(monkeypatch):
    """The skill passes "$ARGUMENTS" quoted, so `on acceptRisk` arrives as ONE token; the dispatcher
    must re-split it so the toggle sees the two tokens it always has."""
    import reviewer_cmd
    import stopcontinue_toggle
    calls = {}
    monkeypatch.setattr(stopcontinue_toggle, "main", lambda argv: (calls.update(argv=argv) or 0))
    rc = reviewer_cmd.main(["reviewer_cmd.py", "stopcontinue", "on acceptRisk"])
    assert rc == 0
    assert calls.get("argv", [None])[1:] == ["on", "acceptRisk"], calls


def test_dispatcher_unknown_subcommand_exits_nonzero_without_delegating(monkeypatch):
    import reviewer_cmd
    called = []
    for mod_name in set(DISPATCH.values()):
        mod = __import__(mod_name)
        monkeypatch.setattr(mod, "main", lambda argv, _n=mod_name: called.append(_n) or 0)
    rc = reviewer_cmd.main(["reviewer_cmd.py", "bogus", "x"])
    assert rc != 0
    assert called == [], "unknown subcommand must not delegate to any toggle: %r" % called


# --------------------------------------------------------------------------
# directive_toggle.run(args, proj): honor the passed project, not os.getcwd()
# --------------------------------------------------------------------------
def test_directive_run_records_under_passed_project_not_process_cwd(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    import directive_toggle
    proj = make_repo(tmp_path)
    # run(args, proj) does not exist yet -> AttributeError = red. The process cwd is the test's cwd,
    # deliberately different from `proj`, so a getcwd-based impl would record under the wrong repo.
    code, msg = directive_toggle.run(["hello", "world"], proj)
    assert code == 0, msg
    assert any("hello world" in d["text"] for d in rl.load_directives(proj))


# --------------------------------------------------------------------------
# reviewerDirective hook: injection-proof + blocks the model turn
# --------------------------------------------------------------------------
def test_directive_hook_is_injection_proof_and_blocks(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    pwned = tmp_path / "PWNED_marker"
    assert not pwned.exists()                                 # guard: not stale
    text = '$(touch {}) plan -> commit (green) and a "quote"'.format(pwned)
    p = run_ups(repo, SENTINEL + " " + text, home, "claude-dir")
    assert p.returncode == 0, p.stderr
    assert not pwned.exists(), "shell command substitution executed -- not injection-proof"
    monkeypatch.setenv("REVIEWER_HOME", home)
    texts = [d["text"] for d in rl.load_directives(repo)]
    assert texts == [text], \
        "directive not recorded EXACTLY verbatim under the payload cwd: %r" % texts
    out = json.loads(p.stdout or "{}")
    # Repo blocking contract is TOP-LEVEL decision + reason + systemMessage (stop_block_output).
    assert out.get("decision") == "block", out
    assert out.get("reason"), "block response missing `reason` (outcome shown to the operator)"
    assert out.get("systemMessage"), "block response missing `systemMessage`"


def test_directive_hook_passes_non_sentinel_prompts_through(tmp_path, monkeypatch):
    """Regression guard: an ordinary prompt must NOT be treated as a directive, and adding the
    directive branch must leave the existing watchdog re-arm path fully intact. Arm the watchdog so
    re-arm SHOULD fire, submit a NON-sentinel prompt, and assert the re-arm context still comes back
    (so a change that drops/corrupts re-arm, or that swallows ordinary prompts, fails here)."""
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.set_stopcontinue_watchdog_active(repo, 1)
    rl.set_watchdog_teardown_ready(repo, "claude-x")            # re-arm should fire this prompt
    p = run_ups(repo, "please refactor the parser", home, "claude-x")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    assert out.get("decision") != "block", \
        "an ordinary prompt must NOT be blocked by the directive branch: %r" % out
    ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert rl.WATCHDOG_MARKER in ctx, \
        "watchdog re-arm context was lost for an ordinary prompt: %r" % out
    monkeypatch.setenv("REVIEWER_HOME", home)
    assert rl.load_directives(repo) == []


# --------------------------------------------------------------------------
# Skill wiring
# --------------------------------------------------------------------------
def test_skill_wiring_dispatcher_bangs_and_directive_sentinel():
    for skill, sub in SIX_SKILLS.items():
        body = (SKILLS / skill / "SKILL.md").read_text()
        bangs = [ln for ln in body.splitlines() if ln.strip().startswith("!`")]
        assert bangs, "%s has no bang line" % skill
        line = bangs[0]
        assert "reviewer_cmd.py" in line and sub in line, (skill, line)
        assert '"$ARGUMENTS"' in line, (skill, line)         # quoted, not bare $ARGUMENTS
    d = (SKILLS / "reviewerDirective" / "SKILL.md").read_text()
    assert not any(ln.strip().startswith("!`") for ln in d.splitlines()), \
        "reviewerDirective must not use a bang pre-exec (it moves to the hook)"
    assert SENTINEL in d and "$ARGUMENTS" in d


# Claude Code hands UserPromptSubmit the prompt AS TYPED -- `/reviewer-agent:reviewerDirective list`
# (verified on 2.1.285 with a hook that dumped its input) -- before the skill body that carries the
# sentinel is expanded. Matching only the sentinel meant the command never ran.
@pytest.mark.parametrize("command", ["/reviewer-agent:reviewerDirective", "/reviewerDirective"])
def test_directive_hook_handles_the_typed_slash_command(tmp_path, monkeypatch, command):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    pwned = tmp_path / "PWNED_marker"
    text = '$(touch {}) plan -> commit (green) and a "quote"'.format(pwned)
    p = run_ups(repo, command + " " + text, home)
    assert p.returncode == 0, p.stderr
    assert not pwned.exists(), "shell command substitution executed -- not injection-proof"
    assert [d["text"] for d in rl.load_directives(repo)] == [text]
    out = json.loads(p.stdout or "{}")
    assert out.get("decision") == "block", out

    listed = json.loads(run_ups(repo, command + " list", home).stdout or "{}")
    assert listed.get("decision") == "block", listed
    assert text in listed.get("reason", ""), listed


# Claude Code titles every blocked prompt "UserPromptSubmit operation blocked by hook", which reads
# as a failure even when the directive was recorded. The reason must lead with the real outcome.
def test_directive_hook_reason_leads_with_success_or_failure(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)

    ok = json.loads(run_ups(repo, "/reviewerDirective tests must exercise behavior", home).stdout)
    first = ok["reason"].splitlines()[0]
    assert "SUCCEEDED" in first and "FAILED" not in first, ok
    assert "blocked" in first, "the success line must explain the 'blocked' heading: %r" % first
    assert ok["systemMessage"] == ok["reason"], ok

    bad = json.loads(run_ups(repo, "/reviewerDirective remove 99", home).stdout)
    first = bad["reason"].splitlines()[0]
    assert "FAILED" in first and "SUCCEEDED" not in first, bad
    assert "no directive numbered" in bad["reason"], bad


def test_directive_hook_ignores_a_longer_command_name(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = make_repo(tmp_path)
    p = run_ups(repo, "/reviewerDirectives list", home)
    assert json.loads(p.stdout or "{}").get("decision") != "block"
    assert rl.load_directives(repo) == []
