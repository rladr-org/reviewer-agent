# SPDX-License-Identifier: MPL-2.0
"""Scope provenance must reach the actual reviewer on every completion route."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer_codex import backend, prompts, state, supervisor
from test_completion_drafts import SESSION, bind, call, event


@pytest.mark.parametrize('route', ['draft', 'native', 'fresh'])
def test_latest_request_is_separate_from_old_requests_and_verdicts(
        git_repo, isolated_state, monkeypatch, route):
    state.set_feature(git_repo, 'stop')
    old = 'Run the old incomplete-draft procedure.'
    context = 'Now test promising to calculate and trying to stop before doing it.'
    latest = 'Can you take a hint?'
    for message in (old, context, latest):
        state.record_prompt(git_repo, SESSION, message)
    stale = 'Ask whether the desktop activity indicator stayed visible.'
    state.record_completion(git_repo, SESSION, 'Old answer.', 'incomplete', stale, 'old-turn')
    seen = []
    monkeypatch.setattr(backend, 'review_turn',
                        lambda *a, **k: (seen.append(a[4]) or 0, 'Finish the calculation.', ''))
    monkeypatch.setattr(backend, 'review_once',
                        lambda *a, **k: (seen.append(a[2]) or 0, 'Finish the calculation.', ''))
    if route == 'native':
        assert supervisor.supervise_hook(event(git_repo, last_assistant_message='Old run finished.'))['decision'] == 'block'
    else:
        for _ in range(3 if route == 'fresh' else 1):
            assert call(bind(git_repo, isolated_state, 'Old run finished.'))['status'] == 'incomplete'
    prompt = seen[-1]
    start = prompt.index('OPERATOR SCOPE (host-recorded messages):\n')
    end = prompt.index('\n--- end operator scope ---', start)
    scope = json.loads(prompt[start:].split('\n', 1)[1].split('\n--- end operator scope ---', 1)[0])
    assert scope == {'earlier_messages': [old, context], 'latest_message': latest}
    assert stale not in json.dumps(scope)
    assert prompt.index(stale) < start < end < prompt.index("AGENT'S PROPOSED FINAL MESSAGE:")
    assert 'REGRESSIONS IN ASSIGNED WORK' in prompt
    assert 'RECONCILE EACH NEXT STEP' in prompt


def test_no_operator_history_is_explicit_and_does_not_invent_a_request(git_repo, isolated_state):
    prompt = prompts.stop_prompt(git_repo, 'empty', 'A draft.')
    start = prompt.index('OPERATOR SCOPE (host-recorded messages):\n')
    scope = json.loads(prompt[start:].split('\n', 1)[1].split('\n--- end operator scope ---', 1)[0])
    assert scope == {'earlier_messages': [], 'latest_message': None}


def test_scope_change_does_not_change_commit_operator_context(git_repo, isolated_state):
    state.record_prompt(git_repo, SESSION, 'Review these staged files.')
    prompt = prompts.commit_prompt(git_repo, SESSION)
    assert 'OPERATOR PROMPTS (oldest first):' in prompt
    assert 'Review these staged files.' in prompt
    assert 'OPERATOR SCOPE (host-recorded messages):' not in prompt


def test_live_scope_replay_requires_opt_in():
    script = Path(__file__).resolve().parents[1] / 'scripts/check_stop_scope.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2 and '--run is required' in result.stderr
