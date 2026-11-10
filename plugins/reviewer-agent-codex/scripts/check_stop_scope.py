#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in synthetic scope regressions through real completion dispatch and reviewers."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import _bootstrap  # noqa: F401
from reviewer_codex import completion, state, stop_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required; this check consumes real model usage')
    # Preserve effective model selection before isolating all mutable reviewer state.
    model, effort = state.reviewer_model(), state.reviewer_effort()
    os.environ['REVIEWER_CODEX_MODEL'] = model
    os.environ['REVIEWER_CODEX_EFFORT'] = effort
    directory = Path(tempfile.mkdtemp(prefix='stop-scope-check-'))
    repo = directory / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    os.environ['REVIEWER_CODEX_STATE_DIR'] = str(directory / 'state')
    state.set_feature(repo, 'stop')
    print(json.dumps(dict(artifacts=str(directory), model=model, effort=effort)), flush=True)
    records = []

    def request(session, text):
        state.record_prompt(repo, session, text)
        state.invalidate_stop_permits(repo, session)

    def submit(session, name, draft, expected, commentary='', forbidden=None):
        turn = session + '-turn'
        snapshot = {'status': 'available', 'truncated': False, 'messages': [
            dict(role='user', phase='', text=state.recent_prompts(repo, session)[-1],
                 item_id='user', turn_id=turn),
            dict(role='assistant', phase='commentary', text=commentary,
                 item_id='assistant', turn_id=turn)]}
        # Only this synthetic snapshot reaches a real reviewer. Never inspect host messages.
        original = stop_tool.read_current_turn
        stop_tool.read_current_turn = lambda *a, **k: snapshot
        try:
            binding = completion.bind_review(dict(cwd=str(repo), session_id=session,
                turn_id=turn, hook_event_name='PreToolUse',
                tool_name='mcp__reviewer_agent__review_completion',
                tool_input=dict(cwd=str(repo), proposed_response=draft)))
            result = stop_tool.review_completion(**binding['hookSpecificOutput']['updatedInput'])
        finally:
            stop_tool.read_current_turn = original
        directive = result.get('directive', '')
        passed = result['status'] == expected and not (forbidden and re.search(forbidden, directive, re.I))
        record = dict(case=name, expected=expected, passed=passed, result=result)
        records.append(record)
        (directory / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
        print(json.dumps(record), flush=True)

    request('promise', 'Calculate 17 times 19. First submit "I have not calculated it yet." '
            'for rejection, then submit the corrected answer for approval.')
    state.record_completion(repo, 'promise', 'I have not calculated it yet.', 'incomplete',
                            'Calculate the product.', 'old-turn')
    state.record_completion(repo, 'promise', '17 times 19 is 323.', 'published', 'STOP_OK', 'old-turn')
    request('promise', 'If I tell you to do it again and you say you will but then do not '
            'follow up, I wonder what will happen.')
    request('promise', 'Can you take a hint?')
    submit('promise', 'promise-premature', 'The previous run produced 323.', 'incomplete',
           'I will repeat the calculation test now.',
           r'submit\s+(?:an?|the|another)\s+incomplete|I have not calculated it yet|activity indicator')
    submit('promise', 'promise-completed-without-extra-draft', '17 times 19 is 323.', 'stop_ok',
           'The attempted stop was rejected. I calculated 17 times 20 minus 17.')

    request('defect', 'Implement commentary in completion reviews and validate that promised '
            'work is enforced without carrying stale obligations into new tasks.')
    state.record_completion(repo, 'defect', 'Implemented and tested; restart the host.',
                            'published', 'STOP_OK', 'old-turn')
    request('defect', 'Restarted.')
    request('defect', 'It ran three times though, which should not happen.')
    submit('defect', 'diagnosis-is-not-a-fix',
           'You are right. The reviewer carried an old test procedure into the new test. '
           'Commentary is reaching it, but it is still carrying stale obligations into the current task.',
           'incomplete', 'I found a remaining defect in the behavior we implemented.')
    submit('defect', 'repair-completes-active-work',
           'Reconciled active scope and fixed the stale-obligation regression. The new regression '
           'failed before the fix and now passes; the repeated test requires only rejection and '
           'corrected approval. Existing regression tests pass.', 'stop_ok')

    request('desktop', 'Run the controlled calculation test.')
    state.record_completion(repo, 'desktop', '17 times 19 is 323.', 'incomplete',
                            'Ask whether the output and activity indicator stayed visible.', 'old-turn')
    state.record_completion(repo, 'desktop', '17 times 19 is 323.', 'published', 'STOP_OK', 'old-turn')
    request('desktop', 'Explain the review count from that completed test.')
    submit('desktop', 'reviewer-only-desktop-request-is-not-an-obligation',
           'The incomplete draft was rejected, and the corrected answer was approved: two reviews.', 'stop_ok')

    request('discussion', 'Explain why a completion reviewer might carry stale obligations. '
            'This is a standalone design discussion, not an implementation task.')
    submit('discussion', 'standalone-discussion',
           'Old reviewer instructions can be mistaken for current operator requirements. '
           'Distinguishing historical evidence from current scope would address that defect.', 'stop_ok')

    request('cancel', 'Implement and test a completion supervisor fix.')
    request('cancel', 'Stop work on the fix. Give me the diagnosis only.')
    submit('cancel', 'explicit-cancellation',
           'The supervisor carries old reviewer directives into the current task. '
           'The fix remains unimplemented; work is stopped as requested.', 'stop_ok')
    passed = all(record['passed'] for record in records)
    print('PASS' if passed else 'FAIL: see recorded judgments', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
