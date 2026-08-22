# SPDX-License-Identifier: MPL-2.0
"""Diagnostic: record what the operator relay ACTUALLY contained, per audit.

On 2026-07-26 the reviewer rejected a commit for lacking an authorization the operator had
typed 8 minutes earlier. Establishing why took six forensic steps against a 25MB transcript.
Every fact needed was cheap to record at audit time and nowhere in the log.

OFF by default -- it is a diagnostic, not a feature, and it puts operator text in the log.
Turn it on with `"logOperatorContext": true` in config.json (or REVIEWER_LOG_OPERATOR_CONTEXT).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl
from test_operator_transcript import (_assistant, _queued_attachment, _transcript, _typed)

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

PROJ = "/Users/x/proj"
GO = "Opus 4.6 is authorized for the judging process."


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    for var in ("REVIEWER_LOG_OPERATOR_CONTEXT", "REVIEWER_OPERATOR_TRANSCRIPT",
                "REVIEWER_OPERATOR_TRANSCRIPT_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)


def _enable(tmp_path, monkeypatch, **extra):
    cfg = tmp_path / "config.json"
    payload = {"logOperatorContext": True}
    payload.update(extra)
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(cfg))


# --------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------
def test_provenance_logging_is_off_by_default(tmp_path):
    path = _transcript(tmp_path, _typed(GO))
    assert rl.operator_context_logging_enabled() is False
    assert rl.context_provenance(PROJ, path) == ""


def test_config_turns_provenance_logging_on(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    path = _transcript(tmp_path, _typed(GO))
    assert rl.operator_context_logging_enabled() is True
    assert rl.context_provenance(PROJ, path) != ""


def test_env_turns_provenance_logging_on(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_LOG_OPERATOR_CONTEXT", "1")
    path = _transcript(tmp_path, _typed(GO))
    assert rl.context_provenance(PROJ, path) != ""


# --------------------------------------------------------------------------
# what it records
# --------------------------------------------------------------------------
def test_it_reports_how_many_messages_were_relayed(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    path = _transcript(tmp_path, _typed("one", uuid="u1"), _typed("two", uuid="u2"),
                       _assistant("noise"))
    assert "op_msgs=2/2" in rl.context_provenance(PROJ, path)


def test_it_reports_the_cutoff_so_a_missing_message_is_visible(tmp_path, monkeypatch):
    # THE field that would have ended the 2026-07-26 investigation in one line: the operator
    # compares this against when they remember typing, and a gap is immediately obvious.
    _enable(tmp_path, monkeypatch)
    path = _transcript(
        tmp_path,
        _typed("earlier", uuid="u1", ts="2026-07-26T00:29:02.000Z"),
        _typed("latest", uuid="u2", ts="2026-07-26T00:32:00.000Z"),
    )
    out = rl.context_provenance(PROJ, path)
    assert "2026-07-26T00:32:00" in out
    assert "latest" in out


def test_it_counts_queued_input_too(tmp_path, monkeypatch):
    # Queued messages were the ones being dropped; the diagnostic must see the same set the
    # relay does, or it would have reported "all good" during the outage.
    _enable(tmp_path, monkeypatch)
    path = _transcript(tmp_path, _typed("typed", uuid="u1"),
                       _queued_attachment("queued while busy", uuid="u2"))
    assert "op_msgs=2/2" in rl.context_provenance(PROJ, path)


def test_it_shows_clipping_as_kept_over_total(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    monkeypatch.setenv("REVIEWER_OPERATOR_TRANSCRIPT_MAX_CHARS", "40")
    path = _transcript(tmp_path, _typed("x" * 100, uuid="u1"), _typed("kept", uuid="u2"))
    assert "op_msgs=1/2" in rl.context_provenance(PROJ, path)


def test_it_says_when_the_relay_is_switched_off(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch, operatorTranscript=False)
    path = _transcript(tmp_path, _typed(GO))
    out = rl.context_provenance(PROJ, path)
    assert "relay=off" in out
    assert GO not in out  # nothing relayed -> nothing to quote


def test_it_counts_directives(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    rl.add_directive(PROJ, "one")
    rl.add_directive(PROJ, "two")
    path = _transcript(tmp_path, _typed(GO))
    assert "directives=2" in rl.context_provenance(PROJ, path)


def test_an_empty_transcript_still_reports_rather_than_going_silent(tmp_path, monkeypatch):
    # "No line in the log" must never be ambiguous between "nothing relayed" and "the
    # diagnostic itself broke".
    _enable(tmp_path, monkeypatch)
    path = _transcript(tmp_path, _assistant("only me"))
    assert "op_msgs=0/0" in rl.context_provenance(PROJ, path)


# --------------------------------------------------------------------------
# the hook writes it
# --------------------------------------------------------------------------
def _repo(tmp_path):
    repo = tmp_path / "gated"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "reviewerAgent.md").write_text("strict reviewer\n")
    (repo / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "f.py"], check=True)
    return str(repo)


def _run_commit(tmp_path, repo, env_extra):
    env = dict(os.environ)
    env.update({"REVIEWER_HOME": os.environ["REVIEWER_HOME"],
                "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_STDOUT": "APPROVED"})
    env.update(env_extra)
    path = _transcript(tmp_path, _typed(GO))
    subprocess.run(
        [sys.executable, str(ROOT / "commit_hook.py")],
        input=json.dumps({"cwd": repo, "session_id": "s1", "transcript_path": path,
                          "tool_name": "Bash",
                          "tool_input": {"command": "git commit -m x"}}),
        capture_output=True, text=True, env=env,
    )
    log = Path(os.environ["REVIEWER_HOME"]) / "logs" / "reviewer.log"
    return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""


def test_the_commit_hook_records_provenance_when_enabled(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    rl.set_active(repo)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"logOperatorContext": True}), encoding="utf-8")
    text = _run_commit(tmp_path, repo, {"REVIEWER_CONFIG_FILE": str(cfg)})
    assert "op_msgs=1/1" in text, text[-600:]


def test_the_commit_hook_stays_quiet_by_default(tmp_path):
    repo = _repo(tmp_path)
    rl.set_active(repo)
    text = _run_commit(tmp_path, repo, {})
    assert "[commit]" in text          # the audit did happen
    assert "op_msgs=" not in text      # but no operator text was written to the log


# --------------------------------------------------------------------------
# reading the transcript once per process
# --------------------------------------------------------------------------
def test_the_transcript_is_parsed_once_per_hook_process(tmp_path, monkeypatch):
    # apply_context reads it for the block AND for the watermark, and the diagnostic reads it
    # again -- three full parses of a file that reached 25MB in real use. One snapshot per
    # process is also the correct semantics: every consumer should see the same transcript.
    path = _transcript(tmp_path, _typed("first", uuid="u1"))
    assert [m["text"] for m in rl.operator_messages(path)] == ["first"]
    Path(path).write_text(
        json.dumps(_typed("first", uuid="u1")) + "\n"
        + json.dumps(_typed("second", uuid="u2")) + "\n", encoding="utf-8")
    assert [m["text"] for m in rl.operator_messages(path)] == ["first"], \
        "second read re-parsed the file instead of reusing the process snapshot"
