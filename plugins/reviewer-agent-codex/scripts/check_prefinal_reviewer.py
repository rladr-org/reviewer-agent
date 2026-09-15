#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Explicit live check; consumes real Codex model usage, outside the unit suite.

Run in a host-enabled amygdala shell: python scripts/check_prefinal_reviewer.py --run
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
    parser.add_argument('--run', action='store_true', help='Authorize real reviewer invocations')
    if not parser.parse_args().run:
        parser.error('--run is required; this live check consumes model usage')
    root = Path(tempfile.mkdtemp(prefix='prefinal-reviewer-check-'))
    repo = root / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    os.environ['REVIEWER_CODEX_STATE_DIR'] = str(root / 'state')
    session, turn = 'live-prefinal-reviewer', 'turn-1'
    state.set_feature(repo, 'stop')
    state.record_prompt(repo, session,
        'Controlled pre-final completion test. First call review_completion with proposed_response '
        'exactly "I still need to calculate 17 times 19 and report the answer." This deliberately '
        'incomplete draft must be reviewed before doing that calculation. After the reviewer '
        'returns the missing-work directive, calculate the result yourself and review the final '
        'draft "17 × 19 = 323." Then publish that approved draft. Do not publish the incomplete '
        'draft as a final message. Do not change files or call native lifecycle tools manually. '
        'Follow the host-supplied review protocol.')
    print('LIVE_ROOT=' + str(root), flush=True)
    def review(draft):
        event = dict(cwd=str(repo), session_id=session, turn_id=turn,
                     hook_event_name='PreToolUse', tool_name='mcp__reviewer_agent__review_completion',
                     tool_input=dict(cwd=str(repo), proposed_response=draft))
        hook = subprocess.run([sys.executable, str(Path(__file__).with_name('hook_pre_tool.py'))],
                              input=json.dumps(event), text=True, capture_output=True, check=True)
        args = json.loads(hook.stdout)['hookSpecificOutput']['updatedInput']
        result = mcp_server.handle_message({'id': 1, 'method': 'tools/call',
                  'params': {'name': 'review_completion', 'arguments': args}})['result']['structuredContent']
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result
    first = review('I still need to calculate 17 times 19 and report the answer.')
    assert first['status'] == 'incomplete', first
    reviewer = state.read_reviewer_session(repo, session, 'stop')
    assert reviewer, 'real reviewer session was not created'
    final = '17 × 19 = 323.'
    second = review(final)
    assert second['status'] == 'stop_ok', second
    assert state.read_reviewer_session(repo, session, 'stop') == reviewer
    assert state.read_nudges(repo, session) == 2
    assert supervisor.supervise_hook(dict(cwd=str(repo), session_id=session, turn_id=turn,
               hook_event_name='Stop', last_assistant_message=final)) == {}
    assert state.read_nudges(repo, session) == 2, 'Stop unexpectedly reviewed again'
    assert not state._stop_permit_file(repo, session).exists()
    print('PASS: incomplete draft rejected; completed draft approved; Stop silent.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
