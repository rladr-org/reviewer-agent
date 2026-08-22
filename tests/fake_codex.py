#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test stub that stands in for the real codex binary.

Behavior is controlled entirely by environment variables so tests can simulate
codex success/failure without invoking the real reviewer:

  FAKE_CODEX_SLEEP       seconds a resume/audit sleeps before answering, so a test can
                         provoke a timeout or cancel a review that is still running
  FAKE_CODEX_BRIEF_SLEEP seconds the --json briefing turn sleeps before emitting its session
                         id, so a test can cancel a review that is still BRIEFING
  FAKE_CODEX_STDOUT      text written to stdout for a resume/audit (default "CANNED CODEX OUTPUT")
  FAKE_CODEX_STDERR      text written to stderr (default "")
  FAKE_CODEX_EXIT        process exit code      (default "0")
  FAKE_CODEX_SESSION_ID  session id emitted by the --json brief (default "fake-session-0001")
  FAKE_CODEX_ARGV_FILE   if set, append this invocation's argv to that file as one JSON line,
                         so a test can assert on the PROMPT the hook actually sent
  FAKE_CODEX_STDIN_FILE  if set, write this invocation's STDIN to that file. The brief is piped
                         (`-`), not passed in argv, so this is the only place a test can prove
                         WHICH brief text reached codex.

Two invocation shapes are recognized:
  * `codex exec --json ... -`  (the session-capturing brief): emit a single JSONL
    `session_meta` event carrying the session id, mirroring real codex --json output.
  * everything else (a `resume` audit/answer): emit FAKE_CODEX_STDOUT.

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
