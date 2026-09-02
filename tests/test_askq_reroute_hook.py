# SPDX-License-Identifier: MPL-2.0
"""RED tests for the AskUserQuestion PreToolUse reroute hook (askq_reroute_hook.py).

The hook DENIES AskUserQuestion at PreToolUse -- but only when stopContinue is active -- and hands
the agent the serialized question with an instruction to re-ask it as plain prose and stop, so the
stopContinue Stop-hook supervisor answers it (established) or defers it to the operator (genuine).
When stopContinue is off it stays out of the way so the widget renders normally. Fail-open: it
never wedges a question. Replaces the dead afkTimeoutMs answerer (askq_answer_hook.py).
See docs/plans/askq_stopcontinue_reroute.md.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
HOOK = str(ROOT / "askq_reroute_hook.py")
HOOKS_JSON = _BASE / "hooks" / "hooks.json"

PROJ = "/Users/x/proj"

TOOL_INPUT = {
    "questions": [
        {
            "question": "Ship it now or wait?",
            "header": "Release",
            "options": [
                {"label": "Ship", "description": "release immediately"},
                {"label": "Wait", "description": "hold for review"},
            ],
        }
    ]
}


def _event(cwd=PROJ, session_id="claude-Q", tool_name="AskUserQuestion", tool_input=None):
    return {
        "cwd": cwd,
        "session_id": session_id,
        "tool_name": tool_name,
        "hook_event_name": "PreToolUse",
        "tool_input": TOOL_INPUT if tool_input is None else tool_input,
    }


def _run(stdin_text, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, HOOK],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
    )


def run_hook(stdin_obj, env_extra):
    return _run(json.dumps(stdin_obj), env_extra)


def run_hook_raw(stdin_text, env_extra):
    return _run(stdin_text, env_extra)


# --------------------------------------------------------------------------
# gate: deny ONLY when stopContinue is active
# --------------------------------------------------------------------------
def test_denies_when_stopcontinue_active(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_stopcontinue_active(PROJ)
    p = run_hook(_event(), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    # the reason must carry the actual question + options and tell the agent to prose + stop
    assert "Ship it now or wait?" in reason
    assert "Ship" in reason and "Wait" in reason
    assert "prose" in reason.lower()
    assert "stop" in reason.lower()


def test_allows_when_stopcontinue_inactive(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    # stopContinue NOT set -> the hook must stay out of the way so the widget renders
    p = run_hook(_event(), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


def test_reason_embeds_the_full_serialized_question(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_stopcontinue_active(PROJ)
    p = run_hook(_event(), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    reason = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    # exact same rendering the reviewer/answerer path uses, so the agent re-emits faithfully
    assert rl.serialize_questions(TOOL_INPUT) in reason


# --------------------------------------------------------------------------
# safety: never wedge a question
# --------------------------------------------------------------------------
def test_fail_open_on_malformed_stdin(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_stopcontinue_active(PROJ)  # active -> it WOULD deny on valid input
    p = run_hook_raw("this is not json {", {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""  # emit nothing rather than crash-wedge the question


def test_non_askuserquestion_is_a_noop(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    rl.set_stopcontinue_active(PROJ)
    p = run_hook(_event(tool_name="Read"), {"REVIEWER_HOME": home})
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""


# --------------------------------------------------------------------------
# manifest: the PreToolUse reroute is wired and the PostToolUse answerer is gone
# --------------------------------------------------------------------------
def _entry_refs(entry):
    parts = []
    for h in entry.get("hooks", []) or []:
        parts.append(h.get("command", ""))
        parts.extend(h.get("args", []) or [])
    return " ".join(parts)


def test_hooks_json_wires_pretooluse_reroute_and_drops_the_answerer():
    hooks = json.loads(HOOKS_JSON.read_text())["hooks"]
    pre = hooks.get("PreToolUse", []) or []
    askq = [e for e in pre if e.get("matcher") == "AskUserQuestion"]
    assert askq, "no PreToolUse matcher for AskUserQuestion"
    assert any("askq_reroute_hook.py" in _entry_refs(e) for e in askq)
    # the dead PostToolUse answerer is fully removed from the manifest
    assert "askq_answer_hook.py" not in json.dumps(hooks)
