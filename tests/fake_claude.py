#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test stub standing in for the `claude` CLI, so no test spends a real model call.

It mimics the shapes the real CLI was measured producing under
`--output-format json`: an ARRAY of events whose final `type: "result"` entry carries the
verdict, plus the `rate_limit_event` entry that appears when usage limits bite.

  FAKE_CLAUDE_RESULT       the verdict text (default "APPROVED")
  FAKE_CLAUDE_EXIT         process exit code (default "0")
  FAKE_CLAUDE_RATE_LIMITED emit a rate-limit event and the CLI's limit message
  FAKE_CLAUDE_SINGLE       return one object instead of an array (the non-verbose shape)
  FAKE_CLAUDE_ARGV_FILE    append this invocation's argv to that file as one JSON line
  FAKE_CLAUDE_STDIN_FILE   write this invocation's stdin there, so a test can assert on the
                           prompt -- the staged patch is delivered that way and nowhere else
  FAKE_CLAUDE_SLEEP        seconds to sleep before answering, for timeout and cancellation
"""
import json
import os
import sys
import time

argv = sys.argv[1:]

try:
    stdin_text = sys.stdin.read()
except Exception:
    stdin_text = ""

dump = os.environ.get("FAKE_CLAUDE_STDIN_FILE")
if dump:
    with open(dump, "w", encoding="utf-8") as handle:
        handle.write(stdin_text)

argv_dump = os.environ.get("FAKE_CLAUDE_ARGV_FILE")
if argv_dump:
    with open(argv_dump, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(argv) + "\n")

sleep = os.environ.get("FAKE_CLAUDE_SLEEP")
if sleep:
    time.sleep(float(sleep))


def session_id():
    for flag in ("--session-id", "--resume"):
        if flag in argv:
            return argv[argv.index(flag) + 1]
    return "00000000-0000-0000-0000-000000000000"


# MEASURED against the real CLI: a rate_limit_event accompanies EVERY run, carrying
# `status: "allowed"` on an ordinary one. The stub emits it too, because a stub that only
# produced it when exhausted is precisely what let a detector keyed on its mere presence pass
# every test and then block a live APPROVED verdict.
ALLOWED_LIMIT_EVENT = {
    "type": "rate_limit_event", "session_id": session_id(),
    "rate_limit_info": {"status": "allowed", "resetsAt": 1787361000,
                        "rateLimitType": "five_hour", "overageStatus": "rejected",
                        "isUsingOverage": False},
}

if os.environ.get("FAKE_CLAUDE_RATE_LIMITED"):
    events = [
        {"type": "rate_limit_event", "session_id": session_id(),
         "rate_limit_info": {"status": "exhausted", "resetsAt": 1787361000,
                             "rateLimitType": "five_hour"}},
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "result": "Claude usage limit reached. Your limit will reset at 8pm.",
         "session_id": session_id()},
    ]
    sys.stdout.write(json.dumps(events))
    sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "1")))

result_entry = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": os.environ.get("FAKE_CLAUDE_RESULT", "APPROVED"),
    "session_id": session_id(),
    "num_turns": 1,
    "permission_denials": [],
    "total_cost_usd": 0.0,
}

if os.environ.get("FAKE_CLAUDE_SINGLE"):
    sys.stdout.write(json.dumps(result_entry))
else:
    sys.stdout.write(json.dumps([
        {"type": "system", "subtype": "init", "session_id": session_id()},
        ALLOWED_LIMIT_EVENT,
        {"type": "assistant", "session_id": session_id(),
         "message": {"role": "assistant", "content": [{"type": "text", "text": "..."}]}},
        result_entry,
    ]))

sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
