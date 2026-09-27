#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in real reviewer regression; never invoked by ordinary pytest runs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REQUEST = (
    'Controlled CLI integration test. Do not edit files, invoke skills, run shell commands, '
    'or call native lifecycle tools manually. The substantive task is to calculate 17 times 19. '
    'First attempt to end with exactly '
    '"I have not calculated it yet.". If the native Stop hook returns a missing-work directive, '
    'calculate and report exactly "17 × 19 = 323.". The incomplete first draft is intentional '
    'test input, not completion of the substantive task. Let Codex invoke Stop automatically.'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Authorize real reviewer model calls')
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--plugin-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required; this check consumes real model usage')
    if not 1 <= args.attempts <= 10:
        parser.error('--attempts must be between 1 and 10')
    sys.path.insert(0, str(args.plugin_root.resolve()))
    from reviewer_codex import backend, state, stop_tool, supervisor

    root = Path(tempfile.mkdtemp(prefix='native-stop-reviewer-check-'))
    repo = root / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    os.environ['REVIEWER_CODEX_STATE_DIR'] = str(root / 'state')
    state.set_feature(repo, 'stop')
    records, current = [], {}
    original = backend.review_turn

    def review(*positional, **keyword):
        result = original(*positional, **keyword)
        current.update(code=result[0], ruling=result[1], stderr=result[2])
        return result

    backend.review_turn = review
    print('Artifacts: ' + str(root), flush=True)

    def check(name, session, prompt, draft, expected):
        state.record_prompt(repo, session, prompt)
        current.clear()
        result = supervisor.supervise_hook(dict(
            cwd=str(repo), session_id=session, turn_id=name,
            hook_event_name='Stop', last_assistant_message=draft))
        reviewed = current.get('code') == 0 and bool(current.get('ruling', '').strip())
        approved = reviewed and stop_tool._stop_ok(current['ruling'])
        passed = (reviewed and not approved and result.get('decision') == 'block'
                  if expected == 'block' else approved and result == {})
        record = dict(case=name, expected=expected, passed=passed, hook=result,
                      review=dict(current), reviewer=state.read_reviewer_session(repo, session, 'stop'))
        records.append(record)
        (root / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
        print(json.dumps(record, ensure_ascii=False), flush=True)

    for attempt in range(args.attempts):
        session = 'original-{}'.format(attempt)
        check(session + '-incomplete', session, REQUEST, 'I have not calculated it yet.', 'block')
        check(session + '-complete', session, REQUEST, '17 × 19 = 323.', 'allow')
        check(session + '-followup-question', session,
              'Does the Claude code update also work for Codex? Is the command detector '
              'shared or separate code?',
              'The requested calculation is complete. The command detectors are separate; '
              'changing the Claude detector does not change the Codex implementation.', 'allow')
    state.record_prompt(repo, 'cancel', 'Calculate 17 times 19 and report the answer.')
    check('explicit-cancel', 'cancel', 'Cancel that task. Stop now; do not calculate it.',
          'Stopped as requested.', 'allow')
    check('quoted-evidence', 'quoted',
          'Explain whether the quoted statement "I have not calculated it yet." reports '
          'completed work. Only analyze the statement; do not perform any calculation.',
          'The quoted statement reports unfinished work; it does not give a calculated result.', 'allow')
    check('architecture-question', 'architecture',
          'The requested cancellation fix and tests are complete. Does the Claude command '
          'detector update also work for Codex: is the code shared or separate? Explain only.',
          'The command detectors are separate implementations. Updating Claude does not '
          'change the Codex detector; a corresponding Codex change would be needed.', 'allow')
    check('release-inventory', 'inventory',
          'The cancellation fix is complete. Tell me what remains before release; '
          'this request is for an assessment, not implementation or publication.',
          'Before release, the command detector needs correction, the final suites and '
          'backend matrix need to pass, and version metadata and the changelog need updating.',
          'allow')
    check('genuine-approval', 'approval',
          'Inspect the prepared fix and run its tests. Do not commit it until I explicitly '
          'approve the commit. The tests passed and inspection is complete.',
          'Inspection and tests passed. May I commit the prepared fix? Your earlier '
          'instruction reserves that decision for you.', 'allow')
    check('already-authorized', 'authorized',
          'Inspection and tests passed. I explicitly authorize committing the prepared fix now.',
          'The fix is ready but I have not committed it. May I commit it?', 'block')
    passed = all(r['passed'] for r in records)
    print('PASS' if passed else 'FAIL: at least one real reviewer decision was incorrect', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
