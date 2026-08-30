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
# Defaults: the N2 built-in was raised from 600 to 900 s to clear observed healthy gaps of
# 300-370 s in large-repo audits with ~2.4x margin instead of the earlier ~1.6x.
# --------------------------------------------------------------------------
def test_default_idle_timeout_is_900():
    assert rl.DEFAULT_IDLE_TIMEOUT == 900


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
# Idle thresholds: a hung attempt is killed and retried while budget remains and
# MAX_IDLE_RETRIES has room. A total-cap timeout is never retried.
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
    # idle_retried is now an integer count, not True/False.
    assert rl.last_invocation().get("idle_retried") == 1
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
    assert rl.last_invocation().get("idle_retried") == 1


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_multiple_idle_kills_retry_until_success(tmp_path, monkeypatch):
    """Attempts 0 AND 1 hang; attempt 2 lands the verdict. The single-retry cap is gone —
    the loop retries any idle kill while budget remains and MAX_IDLE_RETRIES has room."""
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_DELAY_ONLY_ATTEMPT", "0,1")
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, verdict, _err = _audit(tmp_path, timeout=60, first_output_timeout=1, idle_timeout=30)
    assert (code, verdict) == (0, "APPROVED")
    assert rl.last_invocation().get("idle_retried") == 2
    assert int(counter.read_text()) == 3


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_max_idle_retries_caps_pathological_loop(tmp_path, monkeypatch):
    """Every attempt hangs and the budget is generous — MAX_IDLE_RETRIES stops the loop, not the
    budget. Budget is picked at MAX_IDLE_RETRIES+2 x N1 so a broken cap only over-runs by ~1 s
    per missing bound rather than the ~1 hour the earlier draft risked."""
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    # Per attempt: N1 kill at 1 s + subprocess spawn overhead ≈ 1.5 s. Budget must cover
    # MAX_IDLE_RETRIES+1 attempts with slack, but stay small enough that a missing cap does not
    # let the test run for minutes. N1 is kept ≥ 1 s so a slow interpreter startup still lets the
    # fake_codex process reach its counter-file write before it is killed.
    budget = (rl.MAX_IDLE_RETRIES + 1) * 2.0   # ~18 s at MAX_IDLE_RETRIES=8
    code, _v, _e = _audit(tmp_path, timeout=budget, first_output_timeout=1, idle_timeout=30)
    assert code == rl.TIMEOUT_EXIT
    assert int(counter.read_text()) == rl.MAX_IDLE_RETRIES + 1  # initial + N retries
    assert rl.last_invocation().get("idle_retried") == rl.MAX_IDLE_RETRIES


@pytest.mark.skipif(os.name != "posix", reason="select()-based idle detection is POSIX-only")
def test_streaming_audit_retries_stop_when_budget_exhausts_before_cap(tmp_path, monkeypatch):
    """The total-budget deadline is unchanged: when it runs out before MAX_IDLE_RETRIES, the loop
    stops on budget. Each attempt takes ~1 s (killed at N1=1); 4 s budget yields > 2 attempts (so
    the old single-retry cap of n=2 is not enough to pass) and < MAX_IDLE_RETRIES+1 (so it did
    not cap-out either). The window guards against both regressions at once."""
    counter = tmp_path / "attempts"
    monkeypatch.setenv("FAKE_CODEX_COUNTER_FILE", str(counter))
    monkeypatch.setenv("FAKE_CODEX_FIRST_DELAY", "8")
    monkeypatch.setenv("FAKE_CODEX_VERDICT", "APPROVED")
    code, _v, err = _audit(tmp_path, timeout=4, first_output_timeout=1, idle_timeout=30)
    assert code == rl.TIMEOUT_EXIT
    n = int(counter.read_text())
    # n must be > 2 (the old single-retry cap) AND < MAX_IDLE_RETRIES+1 (the count cap).
    assert n > 2, "single-retry regression: n=%d" % n
    assert n < rl.MAX_IDLE_RETRIES + 1, "cap did not fire before budget: n=%d" % n
    assert "idle" in err or "review timed out" in err


def test_streaming_audit_total_cap_timeout_is_never_retried(tmp_path, monkeypatch):
    """A total-cap kill's reason string carries 'review timed out after Ns' (no 'idle'). The retry
    loop must recognise this and exit after the FIRST attempt. Uses a monkeypatched
    _run_streaming so the reason string is deterministic — a fake-codex process cannot itself
    produce the total-cap reason on demand."""
    import reviewer_backends as rb
    calls = []

    def fake_run_streaming(argv, timeout=None, on_cancel=None,
                          first_output_timeout=None, idle_timeout=None,
                          scratch_dir=None):
        calls.append(argv)
        return rl.TIMEOUT_EXIT, "", "review timed out after 1.0s"

    monkeypatch.setattr(rb, "_run_streaming", fake_run_streaming)
    code, verdict, err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == rl.TIMEOUT_EXIT and verdict == ""
    assert "idle" not in err
    assert len(calls) == 1, "total-cap timeout must not retry (attempts=%d)" % len(calls)


# --------------------------------------------------------------------------
# Opaque --json failure recovery: codex 0.149.1 exits nonzero SILENTLY on a provider rejection
# (bad model, refused effort, exhausted quota) under `--json` -- nothing on stdout OR stderr,
# verified live. The human-readable error is emitted only in NON-json mode. So an opaque,
# non-timeout failure is re-probed once without --json to recover the provider's own sentence,
# which the model-rejection / usage-limit diagnostic downstream then matches.
# --------------------------------------------------------------------------
def test_streaming_audit_opaque_json_failure_reprobes_without_json(tmp_path, monkeypatch):
    import reviewer_backends as rb

    def opaque_json_failure(argv, timeout=None, on_cancel=None,
                            first_output_timeout=None, idle_timeout=None, scratch_dir=None):
        return 1, "", ""  # nonzero; nothing on either stream -- the --json silent failure

    provider_error = (
        "OpenAI Codex v0.149.1\nmodel: bad-model-xyz\n"
        'ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",'
        '"message":"The \'bad-model-xyz\' model is not supported when using Codex with a '
        'ChatGPT account."}}'
    )
    probe_argvs = []

    def non_json_probe(argv, input_bytes=None, timeout=None, cwd=None, on_cancel=None,
                       scratch_dir=None):
        probe_argvs.append(argv)
        return 1, "", provider_error

    monkeypatch.setattr(rb, "_run_streaming", opaque_json_failure)
    monkeypatch.setattr(rb, "_run", non_json_probe)

    code, verdict, err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)

    assert code == 1 and verdict == ""
    assert probe_argvs, "an opaque --json failure must trigger a non-json diagnostic probe"
    assert "--json" not in probe_argvs[-1], "the diagnostic probe must run WITHOUT --json"
    assert rl.model_rejection_detail("codex", err), "the recovered stderr must be diagnosable"
    assert "is not supported" in err


def test_streaming_audit_timeout_is_not_reprobed(tmp_path, monkeypatch):
    """A TIMEOUT_EXIT kill (idle/total) is NOT re-probed: its remedy is a bigger budget, and a
    second run would only waste more of it."""
    import reviewer_backends as rb

    def timed_out(argv, timeout=None, on_cancel=None,
                  first_output_timeout=None, idle_timeout=None, scratch_dir=None):
        return rl.TIMEOUT_EXIT, "", "review timed out after 1.0s"

    probe_calls = []

    def probe(argv, input_bytes=None, timeout=None, cwd=None, on_cancel=None, scratch_dir=None):
        probe_calls.append(argv)
        return 1, "", "should not be called"

    monkeypatch.setattr(rb, "_run_streaming", timed_out)
    monkeypatch.setattr(rb, "_run", probe)
    code, _v, err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == rl.TIMEOUT_EXIT
    assert not probe_calls, "a timeout must not be re-probed"
    assert "timed out" in err


def test_streaming_audit_diagnosable_stderr_is_not_reprobed(tmp_path, monkeypatch):
    """If the streaming failure already carries a diagnosable provider sentence, don't waste a
    second codex call re-probing for what is already in hand."""
    import reviewer_backends as rb

    def has_error(argv, timeout=None, on_cancel=None,
                  first_output_timeout=None, idle_timeout=None, scratch_dir=None):
        return 1, "", "ERROR: model_not_found: no such model 'x'"

    probe_calls = []

    def probe(argv, input_bytes=None, timeout=None, cwd=None, on_cancel=None, scratch_dir=None):
        probe_calls.append(argv)
        return 1, "", "unused"

    monkeypatch.setattr(rb, "_run_streaming", has_error)
    monkeypatch.setattr(rb, "_run", probe)
    code, _v, err = _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)
    assert code == 1
    assert not probe_calls, "an already-diagnosable failure must not be re-probed"
    assert "model_not_found" in err


def test_streaming_audit_reprobe_preserves_audit_invocation(tmp_path, monkeypatch):
    """The diagnostic re-probe runs through `_run`, which rebinds `_LAST_INVOCATION` to the
    probe's own argv and duration. The audit record must still describe the FAILED AUDIT, so
    the audit saves and restores the invocation around the probe."""
    import reviewer_backends as rb

    def opaque_audit(argv, timeout=None, on_cancel=None,
                     first_output_timeout=None, idle_timeout=None, scratch_dir=None):
        rb._record_invocation(["codex", "AUDIT", "--json"], 0.0)
        return 1, "", ""  # opaque --json failure -> triggers the probe

    def probe(argv, input_bytes=None, timeout=None, cwd=None, on_cancel=None, scratch_dir=None):
        rb._record_invocation(["codex", "PROBE"], 0.0)
        return 1, "", "ERROR: the 'x' model is not supported"

    monkeypatch.setattr(rb, "_run_streaming", opaque_audit)
    monkeypatch.setattr(rb, "_run", probe)
    _audit(tmp_path, timeout=30, first_output_timeout=5, idle_timeout=10)

    inv = rb.last_invocation()
    assert inv is not None and "AUDIT" in inv["argv"], (
        "the re-probe overwrote the audit's invocation record: %r" % inv)
    assert "PROBE" not in inv["argv"], "probe argv leaked into the audit record"


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
