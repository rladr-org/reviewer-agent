#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in draft-review regression using a real reviewer and disposable repository/state.

Exercises the source hook and MCP dispatch, not desktop rendering or host hook discovery.
Run a separate host integration check before claiming those paths are verified.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import _bootstrap  # noqa: F401
from reviewer_codex import mcp_server, state, supervisor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run real reviewer model calls")
    parser.add_argument("--codex", help="Codex executable used for the nested reviewer")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; this check consumes real model usage")
    if args.codex:
        os.environ["REVIEWER_CODEX_BIN"] = args.codex
    directory = Path(tempfile.mkdtemp(prefix="completion-draft-check-"))
    repo = directory / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    os.environ["REVIEWER_CODEX_STATE_DIR"] = str(directory / "state")
    session, turn = "live-draft-review", "turn-1"
    state.set_feature(repo, "stop")
    state.record_prompt(repo, session,
        "Calculate 17 times 19. For this controlled completion-review test, first submit "
        "the incomplete draft 'I have not calculated it yet.' to review_completion. "
        "After its rejection, calculate the product and check '17 times 19 is 323.' "
        "before publishing that exact answer. The first draft is intentionally incomplete; "
        "the substantive task is still the calculation. Do not edit files or perform other work.")
    records = []
    print("Artifacts: " + str(directory), flush=True)

    def review(draft):
        payload = dict(cwd=str(repo), session_id=session, turn_id=turn,
            hook_event_name="PreToolUse", tool_name="mcp__reviewer_agent__review_completion",
            tool_input=dict(cwd=str(repo), proposed_response=draft))
        hook = subprocess.run([sys.executable, str(Path(__file__).with_name("hook_pre_tool.py"))],
                              input=json.dumps(payload), capture_output=True, text=True, check=True)
        arguments = json.loads(hook.stdout)["hookSpecificOutput"]["updatedInput"]
        response = mcp_server.handle_message({"id": len(records) + 1, "method": "tools/call",
            "params": {"name": "review_completion", "arguments": arguments}})
        result = response["result"]["structuredContent"]
        records.append(dict(draft=draft, result=result))
        (directory / "results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result), flush=True)
        return result

    assert review("I have not calculated it yet.")["status"] == "incomplete"
    reviewer = state.read_reviewer_session(repo, session, "stop")
    assert reviewer, "No real reviewer session was created"
    final = "17 times 19 is 323."
    assert review(final)["status"] == "stop_ok"
    assert state.read_reviewer_session(repo, session, "stop") == reviewer
    assert state.read_nudges(repo, session) == 2
    result = supervisor.supervise_hook(dict(cwd=str(repo), session_id=session, turn_id=turn,
        hook_event_name="Stop", last_assistant_message=final))
    assert result == {}, result
    assert state.read_nudges(repo, session) == 2, "Stop launched a duplicate review"
    assert not state.consume_stop_permit(repo, session), "Stop left reusable approval"
    print("PASS: real rejection, corrected approval, and silent Stop without a third review.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
