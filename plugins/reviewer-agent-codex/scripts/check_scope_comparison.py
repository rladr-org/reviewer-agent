#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in comparison of the recorded scope regression across plugin versions."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--run', action='store_true')
parser.add_argument('--plugin', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--label', default='candidate')
args = parser.parse_args()
if not args.run:
    parser.error('--run is required; this check consumes real model usage')
sys.path.insert(0, str(args.plugin.resolve()))
from reviewer_codex import completion, state, stop_tool

model, effort = state.reviewer_model(), state.reviewer_effort()
os.environ['REVIEWER_CODEX_MODEL'] = model
os.environ['REVIEWER_CODEX_EFFORT'] = effort
root = Path(tempfile.mkdtemp(prefix='scope-comparison-'))
repo = root / 'repo'
repo.mkdir()
subprocess.run(['git', 'init', '-q', str(repo)], check=True)
os.environ['REVIEWER_CODEX_STATE_DIR'] = str(root / 'state')
state.set_feature(repo, 'stop')
records = []
print(json.dumps(dict(label=args.label, artifacts=str(root), model=model, effort=effort)), flush=True)

def request(session, text):
    state.record_prompt(repo, session, text)
    state.invalidate_stop_permits(repo, session)

def old(session, draft, status, detail):
    state.record_completion(repo, session, draft, status, detail, 'old-turn')

def history(session):
    request(session, 'The completion reviewer should receive live commentary, including promises and progress.')
    old(session, 'Implemented and installed commentary context. Tests passed; restart the host.', 'published', 'STOP_OK')
    request(session, 'Restarted.')
    request(session, 'Calculate 17 times 19. First submit "I have not calculated it yet." for rejection, then calculate and submit the corrected answer before publishing.')
    old(session, 'I have not calculated it yet.', 'incomplete', 'Submit the corrected answer for approval, then report both verdicts.')
    old(session, '17 times 19 is 323.', 'published', 'STOP_OK')
    request(session, 'If I tell you to do it again and you say you will but then do not follow up, I wonder what will happen.')
    old(session, 'It should reject the attempted stop. We have not tested that exact scenario.', 'incomplete', 'Report both previous verdicts and ask whether the output and activity indicator stayed visible during reviews.')
    old(session, 'The incomplete draft was rejected and the corrected answer approved. The promise scenario is not yet tested. Did output stay visible?', 'published', 'STOP_OK')

def review(session, case, draft, commentary, expected, forbid=False):
    turn = session + '-turn'
    snapshot = {'status':'available', 'truncated':False, 'messages':[
        dict(role='user', phase='', text=state.recent_prompts(repo, session)[-1], item_id='u', turn_id=turn),
        dict(role='assistant', phase='commentary', text=commentary, item_id='a', turn_id=turn)]}
    stop_tool.read_current_turn = lambda *a, **k: snapshot
    binding = completion.bind_review(dict(cwd=str(repo), session_id=session, turn_id=turn,
        hook_event_name='PreToolUse', tool_name='mcp__reviewer_agent__review_completion',
        tool_input=dict(cwd=str(repo), proposed_response=draft)))
    result = stop_tool.review_completion(**binding['hookSpecificOutput']['updatedInput'])
    redundant = bool(forbid and re.search(r'submit\s+(?:an?|the|another)\s+incomplete|I have not calculated it yet|activity indicator|desktop visibility', result.get('directive',''), re.I))
    record = dict(session=session, case=case, expected=expected, passed=result['status']==expected and not redundant, redundant=redundant, result=result)
    records.append(record)
    (root/'results.json').write_text(json.dumps(records, indent=2)+'\n')
    print(json.dumps(record), flush=True)

for trial in range(1,4):
    session = 'promise-' + str(trial)
    history(session)
    request(session, 'Can you take a hint?')
    review(session, 'promise-follow-through', 'The previous run produced 323.',
           'Yes, I will repeat the calculation test now.', 'incomplete', True)
    review(session, 'corrected-without-extra-submission', '17 times 19 is 323.',
           'The attempted stop was rejected. I calculated 17 times 20 minus 17.', 'stop_ok')

    session = 'defect-' + str(trial)
    history(session)
    request(session, 'Can you take a hint?')
    old(session, 'The previous run produced 323.', 'incomplete', 'Submit the old incomplete draft, then calculate and submit the corrected answer.')
    old(session, 'I have not calculated it yet.', 'incomplete', 'Now submit the corrected answer, then report both verdicts.')
    old(session, 'It caught the attempted stop, then rejected the incomplete draft. 17 times 19 is 323.', 'published', 'STOP_OK')
    request(session, 'It ran three times though, which should not happen: the attempted stop, another incomplete draft, then the corrected answer.')
    review(session, 'diagnosis-only-stop',
           'You are right. The first rejection already tested the promise. The second incomplete submission was redundant. The reviewer carried the old procedure into the new test, and I followed it. Commentary is reaching the reviewer, but it is still carrying stale obligations into the current task.',
           'I found the remaining problem in the behavior we implemented.', 'incomplete')

print(json.dumps(dict(passed=sum(r['passed'] for r in records), total=len(records))), flush=True)
raise SystemExit(0 if all(r['passed'] for r in records) else 1)
