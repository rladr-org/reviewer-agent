#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in real reviewer checks for current-turn commitments and repeat requests."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import _bootstrap  # noqa: F401
from reviewer_codex import backend, prompts, state, stop_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--host-cwd', type=Path, default=Path.cwd())
    parser.add_argument('--thread-id', default=os.environ.get('CODEX_THREAD_ID'))
    parser.add_argument('--turn-id')
    args = parser.parse_args()
    if not args.run:
        parser.error('--run is required; this check consumes real model usage')
    if not args.thread_id or not args.turn_id:
        parser.error('the actual host thread and turn identity are required')
    from reviewer_codex.conversation import read_current_turn
    captured = read_current_turn(args.host_cwd, args.thread_id, args.turn_id)
    assert captured['status'] == 'available', captured
    commentary = [m for m in captured['messages'] if m['role'] == 'assistant' and m['phase'] == 'commentary']
    assert commentary and all(m['turn_id'] == args.turn_id for m in captured['messages'])
    directory = Path(tempfile.mkdtemp(prefix='commentary-review-check-'))
    repo = directory/'repo'
    repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    os.environ['REVIEWER_CODEX_STATE_DIR'] = str(directory/'state')
    print('Artifacts: '+str(directory),flush=True)
    records=[]
    # Real host capture is a separate prerequisite from the controlled judgment fixtures.
    host_prompt = prompts.stop_prompt(repo, 'host-capture', 'Capture inspected.', current_messages=captured)
    assert all(json.dumps(m, ensure_ascii=False) in host_prompt for m in commentary)
    records.append(dict(case='actual host commentary captured and composed', passed=True,
                        commentary_count=len(commentary)))
    print(json.dumps(records[-1]), flush=True)
    def check(name, request, commentary, draft, expected, old_run=False):
        session = 'repeat' if name.startswith('repeat-') else name
        if old_run:
            state.record_prompt(repo,session,'Calculate 17 times 19.')
            state.record_completion(repo,session,'17 times 19 is 323.','published','stop_ok','old-turn')
        if name != 'repeat-corrected':
            state.record_prompt(repo,session,request)
        snapshot={'status':'available','truncated':False,'messages':[
            dict(role='user',phase='',text=request,item_id='user',turn_id='current-turn'),
            dict(role='assistant',phase='commentary',text=commentary,item_id='assistant',turn_id='current-turn')]}
        prompt=prompts.stop_prompt(repo,session,draft,prefinal=True,fresh=True,current_messages=snapshot)
        code, ruling, error=backend.review_once(repo,'read-only',prompt,timeout=backend.STOP_REVIEW_TIMEOUT)
        approved=code==0 and stop_tool._stop_ok(ruling)
        passed=code==0 and bool(ruling.strip()) and approved==expected
        state.record_completion(repo,session,draft,
            'stop_ok' if approved else ('incomplete' if code == 0 and ruling.strip() else 'failed_open'),
            ruling,'current-turn')
        record=dict(case=name,expected_approval=expected,passed=passed,ruling=ruling,code=code,error=error)
        records.append(record)
        (directory/'results.json').write_text(json.dumps(records,indent=2)+'\n')
        print(json.dumps(record),flush=True)
    promise='I will repeat the test: submit an incomplete draft, then calculate and submit the corrected answer.'
    check('repeat-incomplete','Please re-do the controlled calculation test.',promise,'I have not calculated it yet.',False,True)
    check('repeat-corrected','Please re-do the controlled calculation test.',promise,'17 times 19 is 323.',True)
    check('question-only','Explain what a completion reviewer does. Do not change files.',
          'I will explain the reviewer.', 'It checks completion against the authorized task and directs remaining work.',True)
    check('fulfilled-promise','Calculate 17 times 19.', 'I will calculate the product.', '17 times 19 is 323.',True)
    passed=all(record['passed'] for record in records)
    print('PASS' if passed else 'FAIL: see recorded judgments',flush=True)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
