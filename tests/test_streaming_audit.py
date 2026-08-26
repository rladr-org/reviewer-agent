# SPDX-License-Identifier: MPL-2.0
"""run_codex_streaming_audit: the schema-constrained, streamed codex commit audit.

Drives the fake codex binary directly (not the whole commit hook) so the retry, fail-closed, and
reconstruction behavior can be exercised deterministically and fast. See
docs/plans/codex_jsonl_streaming.md, Task 4.
"""
import json
import os
from pathlib import Path

import pytest

import reviewer_lib as rl

FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    for var in ("FAKE_CODEX_FIRST_DELAY", "FAKE_CODEX_MID_DELAY",
                "FAKE_CODEX_DELAY_ONLY_ATTEMPT", "FAKE_CODEX_VERDICT_JSON",
                "FAKE_CODEX_NO_VERDICT_FILE", "FAKE_CODEX_VERDICT",
                "FAKE_CODEX_FINDINGS", "FAKE_CODEX_SUGGESTIONS", "FAKE_CODEX_EXIT",
                "REVIEWER_CODEX_MODEL", "REVIEWER_CODEX_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    rl.reset_last_invocation()


def _audit(tmp_path, **kw):
    """Run the streaming audit against the fake, returning (code, verdict_text, err)."""
    return rl.run_codex_streaming_audit(
        "sid-1", str(tmp_path), "workspace-write", "AUDIT PROMPT", **kw)


# --------------------------------------------------------------------------
# Verdict reconstruction: the strict JSON on the -o file becomes canonical APPROVED/REJECTED text.
# --------------------------------------------------------------------------
def test_streaming_audit_approves(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert (code, verdict) == (0, "APPROVED")
    assert rl.is_approved(verdict)


def test_streaming_audit_approved_with_suggestions_reconstructs(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    monkeypatch.setenv("FAKE_CODEX_SUGGESTIONS", "cache the loop||inline the helper")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == 0
    assert verdict == "APPROVED\nSUGGESTIONS:\n1. cache the loop\n2. inline the helper"
    assert rl.is_approved(verdict)


def test_streaming_audit_rejects_with_findings_and_suggestions(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "REJECTED")
    monkeypatch.setenv("FAKE_CODEX_FINDINGS", "off-by-one at a.py:10||leak at b.py:3")
    monkeypatch.setenv("FAKE_CODEX_SUGGESTIONS", "rename z")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == 0
    assert verdict == ("REJECTED:\n1. off-by-one at a.py:10\n2. leak at b.py:3\n"
                       "SUGGESTIONS:\n1. rename z")
    assert not rl.is_approved(verdict)


def test_streaming_audit_captures_turn_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, _verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == 0
    usage = rl.last_invocation().get("turn_usage")
    assert usage is not None
    assert usage["output_tokens"] == 340 and usage["input_tokens"] == 1200


# --------------------------------------------------------------------------
# Fail-closed: exit 0 with a non-schema-conforming -o file returns NONZERO + empty verdict.
# --------------------------------------------------------------------------
def test_streaming_audit_malformed_verdict_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_VERDICT_JSON", "this is not json")
    monkeypatch.setenv("FAKE_CODEX_EXIT", "0")
    code, verdict, err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == rl.SCHEMA_VERDICT_MISSING and code != 0
    assert verdict == ""
    assert "no schema-conforming verdict" in err


def test_streaming_audit_empty_verdict_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_NO_VERDICT_FILE", "1")  # exit 0 but write nothing
    monkeypatch.setenv("FAKE_CODEX_EXIT", "0")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == rl.SCHEMA_VERDICT_MISSING and verdict == ""


# --------------------------------------------------------------------------
# Idle thresholds: a hung attempt is killed and retried ONCE; a persistent hang fails closed.
# --------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_first_output_timeout_retries_then_succeeds(tmp_path, monkeypatch):
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_DELAY_ONLY_ATTEMPT", "0")  # only the FIRST attempt hangs
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")         # silent past N1 on attempt 0
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=1, idle_timeout=30)
    assert (code, verdict) == (0, "APPROVED")
    assert rl.last_invocation().get("idle_retried") is True
    assert int(counter.read_text()) == 2  # two attempts ran


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_between_output_timeout_retries_then_succeeds(tmp_path, monkeypatch):
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_DELAY_ONLY_ATTEMPT", "0")
    monkeypatch.setenv("FAKE_CODEX_MID_DELAY", "8")           # silent AFTER first event on attempt 0
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=1)
    assert (code, verdict) == (0, "APPROVED")
    assert rl.last_invocation().get("idle_retried") is True


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_persistent_idle_fails_closed_without_a_third_attempt(tmp_path,
                                                                              monkeypatch):
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")         # EVERY attempt hangs
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, verdict, err = _audit(tmp_path, timeout=20, first_output_timeout=1, idle_timeout=30)
    assert code == rl.TIMEOUT_EXIT and verdict == ""
    assert "idle" in err
    assert int(counter.read_text()) == 2  # exactly one retry, never a third attempt


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_uses_a_fresh_o_file_per_attempt(tmp_path, monkeypatch):
    # finding 6: a retry must NEVER read the previous attempt's -o file. Prove the two attempts
    # were handed DIFFERENT -o paths.
    counter = tmp_path / "attempts"
    argv_file = tmp_path / "argv.jsonl"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_ARGV_FILE", str(argv_file))
    monkeypatch.setenv("FAKE_CODEX_DELAY_ONLY_ATTEMPT", "0")
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, _verdict, _err = _audit(tmp_path, timeout=30, first_output_timeout=1, idle_timeout=30)
    assert code == 0

    def _o_of(argv):
        return argv[argv.index("-o") + 1]

    argvs = [json.loads(line) for line in argv_file.read_text().splitlines() if line.strip()]
    assert len(argvs) == 2, argvs
    assert _o_of(argvs[0]) != _o_of(argvs[1]), "the retry reused the first attempt's -o file"
