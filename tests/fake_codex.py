#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test stub that stands in for the real codex binary.

Behavior is controlled entirely by environment variables so tests can simulate
codex success/failure without invoking the real reviewer:

  FAKE_CODEX_SLEEP       seconds a plain (non --json) resume/answer sleeps before replying, so a
                         test can provoke a timeout or cancel a review that is still running
  FAKE_CODEX_BRIEF_SLEEP seconds the --json briefing turn sleeps before emitting its session
                         id, so a test can cancel a review that is still BRIEFING
  FAKE_CODEX_STDOUT      text written to stdout for a plain resume/answer (default "CANNED CODEX OUTPUT")
  FAKE_CODEX_STDERR      text written to stderr (default "")
  FAKE_CODEX_EXIT        process exit code      (default "0")
  FAKE_CODEX_SESSION_ID  session id emitted by the --json brief (default "fake-session-0001")
  FAKE_CODEX_ARGV_FILE   if set, append this invocation's argv to that file as one JSON line,
                         so a test can assert on the PROMPT the hook actually sent (and on the
                         per-attempt `-o` path)
  FAKE_CODEX_STDIN_FILE  if set, write this invocation's STDIN to that file. The brief is piped
                         (`-`), not passed in argv, so this is the only place a test can prove
                         WHICH brief text reached codex.
  FAKE_CODEX_DUMP_TMPDIR if set, write this invocation's TMPDIR env value to that file, so a test
                         can prove the audit subprocess ran with a scratch-scoped TMPDIR.

The STREAMING COMMIT AUDIT (`--json ... resume <id> <prompt>`) is driven by these extra knobs:

  FAKE_CODEX_VERDICT      "APPROVED" (default) or "REJECTED", written to the `-o` schema file.
  FAKE_CODEX_FINDINGS     "||"-separated required fixes. Forced to [] when APPROVED and defaulted
                          to one item when REJECTED, so the fixture always satisfies the strict
                          parser's verdict/findings consistency rule.
  FAKE_CODEX_SUGGESTIONS  "||"-separated non-blocking suggestions (default none).
  FAKE_CODEX_VERDICT_JSON if present, its RAW value is written to `-o` verbatim (so a test can
                          inject malformed or empty JSON to exercise the fail-closed path).
  FAKE_CODEX_NO_VERDICT_FILE  if present, the `-o` file is left as codex created it (empty) --
                          simulates a turn that exits 0 but writes no verdict.
  FAKE_CODEX_FIRST_DELAY  seconds to sleep BEFORE any --json event (exercises N1, first-output).
  FAKE_CODEX_MID_DELAY    seconds to sleep AFTER `thread.started`, before the rest (exercises N2,
                          between-output).
  FAKE_CODEX_COUNTER_FILE  if set, a per-invocation attempt counter (0-based) is read+incremented
                          here, so a test can scope the delays to a single attempt.
  FAKE_CODEX_DELAY_ONLY_ATTEMPT  if set (with a counter file), FIRST/MID delays apply ONLY on that
                          attempt index -- e.g. "0" makes only the first attempt hang, so the
                          retry then succeeds. Unset means the delays apply on every attempt.

Three invocation shapes are recognized:
  * `codex exec --json ... resume <id> <prompt>`  (the STREAMING commit audit): emit the JSONL
    event stream (with optional delays) and write the schema verdict JSON to the `-o` file.
  * `codex exec --json ... -`  (the session-capturing brief): emit a leading `thread.started`
    event carrying the session id, mirroring real codex --json output.
  * everything else (a plain `resume` audit/answer): emit FAKE_CODEX_STDOUT.

It drains stdin (the brief is piped in) to avoid broken-pipe errors.
"""
import json
import os
import sys
import time

argv = sys.argv[1:]

try:
    _stdin = sys.stdin.read()
except Exception:
    _stdin = ""

_stdin_dump = os.environ.get("FAKE_CODEX_STDIN_FILE")
if _stdin_dump:
    with open(_stdin_dump, "w", encoding="utf-8") as _fh:
        _fh.write(_stdin)

_dump = os.environ.get("FAKE_CODEX_ARGV_FILE")
if _dump:
    with open(_dump, "a", encoding="utf-8") as _fh:
        _fh.write(json.dumps(argv) + "\n")

_tmpdir_dump = os.environ.get("FAKE_CODEX_DUMP_TMPDIR")
if _tmpdir_dump:
    with open(_tmpdir_dump, "w", encoding="utf-8") as _fh:
        _fh.write(os.environ.get("TMPDIR", ""))


def _emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def _split(raw):
    return [item for item in (raw or "").split("||") if item]


def _o_path():
    if "-o" in argv:
        i = argv.index("-o")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


# STREAMING COMMIT AUDIT: --json AND a `resume` subcommand. Checked BEFORE the brief so the audit
# (which now also passes --json) is not mistaken for a session-capturing brief.
if "--json" in argv and "resume" in argv:
    # Per-attempt counter, so a test can make ONLY the first attempt hang and the retry succeed.
    attempt = None
    _counter = os.environ.get("FAKE_CODEX_COUNTER_FILE")
    if _counter:
        try:
            attempt = int((open(_counter).read().strip() or "0"))
        except (OSError, ValueError):
            attempt = 0
        with open(_counter, "w") as _fh:
            _fh.write(str(attempt + 1))
    _only = os.environ.get("FAKE_CODEX_DELAY_ONLY_ATTEMPT")
    _delays_apply = _only is None or (attempt is not None and attempt == int(_only))

    _first = os.environ.get("FAKE_CODEX_FIRST_DELAY")
    if _first and _delays_apply:
        time.sleep(float(_first))            # silent before ANY event -> exercises N1

    _emit({"type": "thread.started",
           "thread_id": os.environ.get("FAKE_CODEX_SESSION_ID", "fake-session-0001")})

    _mid = os.environ.get("FAKE_CODEX_MID_DELAY")
    if _mid and _delays_apply:
        time.sleep(float(_mid))              # silent AFTER the first event -> exercises N2

    _emit({"type": "turn.started"})
    _emit({"type": "item.completed",
           "item": {"type": "agent_message", "text": "audited the staged diff"}})
    _emit({"type": "turn.completed",
           "usage": {"input_tokens": 1200, "output_tokens": 340,
                     "cached_input_tokens": 100, "reasoning_output_tokens": 88}})

    _out = _o_path()
    if _out is not None and "FAKE_CODEX_NO_VERDICT_FILE" not in os.environ:
        if "FAKE_CODEX_VERDICT_JSON" in os.environ:
            _payload = os.environ["FAKE_CODEX_VERDICT_JSON"]   # raw: may be malformed/empty
        else:
            _verdict = os.environ.get("FAKE_CODEX_VERDICT", "APPROVED")
            if _verdict == "REJECTED":
                _findings = _split(os.environ.get("FAKE_CODEX_FINDINGS", "a required fix"))
                if not _findings:
                    _findings = ["a required fix"]   # a rejection must carry >=1 (consistency)
            else:
                _findings = []                        # APPROVED => no findings (consistency)
            _payload = json.dumps({
                "verdict": _verdict,
                "findings": _findings,
                "suggestions": _split(os.environ.get("FAKE_CODEX_SUGGESTIONS", "")),
            })
        with open(_out, "w", encoding="utf-8") as _fh:
            _fh.write(_payload)

    sys.stderr.write(os.environ.get("FAKE_CODEX_STDERR", ""))
    sys.exit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))

if "--json" in argv:
    _brief_sleep = os.environ.get("FAKE_CODEX_BRIEF_SLEEP")
    if _brief_sleep:
        time.sleep(float(_brief_sleep))
    exit_code = int(os.environ.get("FAKE_CODEX_EXIT", "0"))
    if exit_code != 0:
        # A codex that fails before a session starts emits NO thread.started event, so no
        # session id is capturable (models auth/config errors, etc.).
        sys.stderr.write(os.environ.get("FAKE_CODEX_STDERR", ""))
        sys.exit(exit_code)
    # Mirror the REAL `codex exec --json` stdout: a leading `thread.started` event whose
    # `thread_id` is the resumable session id, followed by turn events.
    sid = os.environ.get("FAKE_CODEX_SESSION_ID", "fake-session-0001")
    sys.stdout.write(json.dumps({"type": "thread.started", "thread_id": sid}) + "\n")
    sys.stdout.write(json.dumps({"type": "turn.started"}) + "\n")
    sys.stdout.write(
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "READY"}}
        )
        + "\n"
    )
    sys.exit(0)

_sleep = os.environ.get("FAKE_CODEX_SLEEP")
if _sleep:
    time.sleep(float(_sleep))

sys.stdout.write(os.environ.get("FAKE_CODEX_STDOUT", "CANNED CODEX OUTPUT"))
sys.stderr.write(os.environ.get("FAKE_CODEX_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CODEX_EXIT", "0")))
