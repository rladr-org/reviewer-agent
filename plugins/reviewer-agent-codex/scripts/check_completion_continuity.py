#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in real judgments through draft dispatch across SessionEnd/resume."""
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
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required; this check consumes real model usage')
    directory = Path(tempfile.mkdtemp(prefix='completion-continuity-'))
    repo = directory / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    os.environ['REVIEWER_CODEX_STATE_DIR'] = str(directory / 'state')
    state.set_feature(repo, 'stop')
    records = []
    print('Artifacts: ' + str(directory), flush=True)

    def hook(script, session, **extra):
        payload = dict(cwd=str(repo), session_id=session, turn_id='test-turn', **extra)
        result = subprocess.run([sys.executable, str(Path(__file__).with_name(script))],
            input=json.dumps(payload), text=True, capture_output=True, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def prompt(session, text):
        hook('hook_prompt.py', session, hook_event_name='UserPromptSubmit', prompt=text)

    def review(session, case, draft, expected):
        binding = hook('hook_pre_tool.py', session, hook_event_name='PreToolUse',
            tool_name='mcp__reviewer_agent__review_completion',
            tool_input=dict(cwd=str(repo), proposed_response=draft))
        response = mcp_server.handle_message({'id': len(records) + 1, 'method': 'tools/call',
            'params': {'name': 'review_completion',
                       'arguments': binding['hookSpecificOutput']['updatedInput']}})
        result = response['result']['structuredContent']
        record = dict(case=case, expected=expected, result=result, passed=result['status'] == expected)
        if result['status'] == 'stop_ok':
            record['stop'] = supervisor.supervise_hook(dict(cwd=str(repo), session_id=session,
                turn_id='test-turn', hook_event_name='Stop', last_assistant_message=draft))
            record['passed'] = record['passed'] and record['stop'] == {}
        records.append(record)
        (directory / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
        print(json.dumps(record), flush=True)

    assignment = ('Calculate 17 times 19. First submit an intentionally incomplete draft for '
        'a controlled completion-review test, then calculate and submit the corrected answer '
        'for approval before publishing it. A status question during the test does not cancel '
        'the calculation. Do not edit files.')
    prompt('resumed', assignment)
    review('resumed', 'initial rejection', 'I have not calculated it yet.', 'incomplete')
    hook('hook_session_end.py', 'resumed', hook_event_name='SessionEnd')
    prompt('resumed', 'Were you hung?')
    records.append(dict(case='assignment survives restart', passed=assignment in state.recent_prompts(repo, 'resumed')))
    review('resumed', 'status does not finish task', 'I was waiting for the reviewer.', 'incomplete')
    review('resumed', 'corrected draft', '17 times 19 is 323.', 'stop_ok')
    hook('hook_session_end.py', 'resumed', hook_event_name='SessionEnd')
    prompt('resumed', 'What was the result?')
    review('resumed', 'completed task stays complete', '17 times 19 is 323.', 'stop_ok')
    prompt('cancelled', assignment)
    prompt('cancelled', 'Cancel the calculation. Stop now.')
    review('cancelled', 'explicit cancellation', 'Stopped as requested.', 'stop_ok')
    prompt('question-only', 'Explain what a completion reviewer does. Do not change code.')
    review('question-only', 'question-only control',
        'It checks whether the requested task is complete and directs remaining authorized work.', 'stop_ok')
    passed = all(record['passed'] for record in records)
    print('PASS' if passed else 'FAIL: see recorded cases', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
