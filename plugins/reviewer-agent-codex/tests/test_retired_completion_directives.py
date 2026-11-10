# SPDX-License-Identifier: MPL-2.0
"""Completed review commands must not be replayed as current obligations."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from reviewer_codex import prompts, state
from test_completion_drafts import SESSION, event
from test_hook_scripts import _run


def projected(repo):
    return [json.loads(line) for line in prompts.completion_context(repo, SESSION).splitlines()
            if line.startswith('{')]


@pytest.mark.parametrize('status', ['stop_ok', 'published'])
def test_completed_prefix_loses_directives_but_keeps_drafts_outcomes_and_audit(
        git_repo, isolated_state, status):
    state.record_completion(git_repo, SESSION, 'Old incomplete answer.', 'incomplete',
                            'Repeat the obsolete procedure.', 'old')
    state.record_completion(git_repo, SESSION, 'Old completed answer.', status,
                            'Old reviewer explanation.', 'old')
    state.record_completion(git_repo, SESSION, 'Current unfinished work.', 'incomplete',
                            'Fix the remaining current defect.', 'new')
    target = state.completion_history_file(git_repo, SESSION)
    before = target.read_bytes()
    records = projected(git_repo)
    assert records[:2] == [
        dict(draft='Old incomplete answer.', status='incomplete', turn_id='old'),
        dict(draft='Old completed answer.', status=status, turn_id='old')]
    assert records[2]['detail'] == 'Fix the remaining current defect.'
    assert target.read_bytes() == before
    assert state.recent_completions(git_repo, SESSION)[0]['detail'] == 'Repeat the obsolete procedure.'


@pytest.mark.parametrize('status', ['stop_wait', 'failed_open', 'cancelled', 'budget_exhausted'])
def test_non_completion_outcomes_do_not_retire_review_feedback(git_repo, isolated_state, status):
    state.record_completion(git_repo, SESSION, 'Unfinished.', 'incomplete', 'Finish the repair.', 'turn')
    state.record_completion(git_repo, SESSION, 'Yielding.', status, 'No completion established.', 'turn')
    assert projected(git_repo) == state.recent_completions(git_repo, SESSION)


def test_latest_completion_retires_only_its_prefix(git_repo, isolated_state):
    for index, status in enumerate(['incomplete', 'stop_ok', 'incomplete', 'published', 'incomplete']):
        state.record_completion(git_repo, SESSION, str(index), status, 'Feedback ' + str(index), str(index))
    records = projected(git_repo)
    assert [r['draft'] for r in records] == ['0', '1', '2', '3', '4']
    assert all('detail' not in r for r in records[:-1])
    assert records[-1]['detail'] == 'Feedback 4'


def test_prompt_hook_tells_agent_to_reconcile_directives_before_acting(git_repo, isolated_state):
    state.set_feature(git_repo, 'stop')
    result = _run('hook_prompt.py', event(git_repo, 'UserPromptSubmit', prompt='Continue the current test.'), isolated_state)
    assert result.returncode == 0, result.stderr
    guidance = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
    assert 'A reviewer directive is not new authorization' in guidance
    assert 'already-satisfied' in guidance
    assert 'current user request' in guidance
    assert 'On stop_ok or stop_wait, publish exactly that draft without further tools' in guidance


def test_comparison_replay_requires_explicit_opt_in():
    script = Path(__file__).resolve().parents[1] / 'scripts/check_scope_comparison.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2 and '--run is required' in result.stderr
