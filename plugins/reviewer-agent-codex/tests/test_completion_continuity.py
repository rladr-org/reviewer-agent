# SPDX-License-Identifier: MPL-2.0
"""Exercise retained task evidence through lifecycle hooks and review dispatch."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from reviewer_codex import backend, liveness, prompts, state, supervisor
from test_completion_drafts import SESSION, TURN, bind, call, event
from test_hook_scripts import _run


def end(repo, isolated_state):
    result = _run('hook_session_end.py', event(repo, 'SessionEnd'), isolated_state)
    assert result.returncode == 0, result.stderr


def test_restart_keeps_assignment_and_plan_but_not_transient_authority(git_repo, isolated_state):
    state.record_prompt(git_repo, SESSION, 'Finish the plugin fix and test it.')
    plan = {'plan': [{'step': 'Fix the regression', 'status': 'in_progress'}]}
    state.record_plan(git_repo, SESSION, plan)
    state.publish_reviewer_session(git_repo, SESSION, 'stop', 'child')
    state.bump_nudges(git_repo, SESSION)
    state.grant_stop_permit(git_repo, SESSION)
    state.grant_stop_abort_bypass(git_repo, SESSION, TURN)
    liveness.mark(git_repo, SESSION, 'shell', 'process', 'test process')
    end(git_repo, isolated_state)
    resumed = _run('hook_prompt.py', event(git_repo, 'UserPromptSubmit', prompt='Were you hung?'), isolated_state)
    assert resumed.returncode == 0
    assert state.recent_prompts(git_repo, SESSION) == ['Finish the plugin fix and test it.', 'Were you hung?']
    assert state.current_plan(git_repo, SESSION) == plan
    assert state.read_reviewer_session(git_repo, SESSION, 'stop') is None
    assert state.read_nudges(git_repo, SESSION) == 0
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert not state.consume_stop_abort_bypass(git_repo, SESSION, TURN)
    assert not liveness.outstanding(git_repo, SESSION)


def test_review_history_distinguishes_approval_from_observed_publication(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'Calculate the product.', ''))
    assert call(bind(git_repo, isolated_state, 'Not calculated.'))['status'] == 'incomplete'
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'STOP_OK', ''))
    args = bind(git_repo, isolated_state)
    assert call(args)['status'] == 'stop_ok'
    records = state.recent_completions(git_repo, SESSION)
    assert [r['status'] for r in records] == ['incomplete', 'stop_ok']
    assert records[0]['draft'] == 'Not calculated.'
    assert records[0]['detail'] == 'Calculate the product.'
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message=args['proposed_response'])) == {}
    assert state.recent_completions(git_repo, SESSION)[-1]['status'] == 'published'
    end(git_repo, isolated_state)
    assert state.recent_completions(git_repo, SESSION)[-1]['draft'] == args['proposed_response']


def test_changed_final_is_not_recorded_as_publication_of_approved_draft(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'STOP_OK', ''))
    assert call(bind(git_repo, isolated_state, 'Original.'))['status'] == 'stop_ok'
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'Finish the task.', ''))
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message='Changed.'))['decision'] == 'block'
    assert 'published' not in [r['status'] for r in state.recent_completions(git_repo, SESSION)]


def test_fresh_reassessment_receives_prior_draft_and_ruling(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'Compute the missing product.', ''))
    for _ in range(2):
        assert call(bind(git_repo, isolated_state, 'Still calculating.'))['status'] == 'incomplete'
    seen = []
    monkeypatch.setattr(backend, 'review_once', lambda *a, **k: (seen.append(a[2]) or 0, 'STOP_OK', ''))
    assert call(bind(git_repo, isolated_state))['status'] == 'stop_ok'
    assert 'Still calculating.' in seen[0] and 'Compute the missing product.' in seen[0]
    assert 'incomplete' in seen[0]


def test_session_end_invalidates_review_already_in_flight(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    def review(*a, **k):
        end(git_repo, isolated_state)
        return 0, 'STOP_OK', ''
    monkeypatch.setattr(backend, 'review_turn', review)
    assert call(bind(git_repo, isolated_state))['status'] == 'incomplete'
    assert not state.consume_stop_permit(git_repo, SESSION)


def test_history_is_bounded_isolated_and_explicitly_clearable(git_repo, isolated_state, tmp_path):
    other = tmp_path / 'other'
    shutil.copytree(git_repo, other)
    for i in range(30):
        state.record_completion(git_repo, SESSION, str(i) + 'x' * 5000, 'incomplete', 'y' * 3000, TURN)
    records = state.recent_completions(git_repo, SESSION)
    assert len(records) == 20 and records[0]['draft'].startswith('10')
    assert all(len(r['draft']) <= 4000 and len(r['detail']) <= 2000 for r in records)
    assert not state.recent_completions(other, SESSION)
    assert not state.recent_completions(git_repo, 'different-session')
    context = prompts.completion_context(git_repo, SESSION)
    assert len(context) < 13000
    state.clear_session_artifacts(SESSION)
    assert not state.recent_completions(git_repo, SESSION)


@pytest.mark.parametrize('value', ['broken json', '{}', '[null, 2, {"status": "stop_ok"}]'])
def test_corrupt_history_is_ignored(git_repo, isolated_state, value):
    path = state.completion_history_file(git_repo, SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    assert state.recent_completions(git_repo, SESSION) == []


def test_history_write_failure_does_not_change_review_result(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    def fail(*a, **k):
        raise OSError('read-only history')
    monkeypatch.setattr(state, 'record_completion', fail)
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (0, 'STOP_OK', ''))
    args = bind(git_repo, isolated_state)
    assert call(args)['status'] == 'stop_ok'
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message=args['proposed_response'])) == {}


@pytest.mark.parametrize('ruling,code,status', [('', 1, 'failed_open'), ('STOP_OK', backend.CANCELLED_EXIT, 'cancelled')])
def test_unsuccessful_review_never_records_approval(git_repo, isolated_state, monkeypatch, ruling, code, status):
    state.set_feature(git_repo, 'stop')
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: (code, ruling, 'unavailable'))
    assert call(bind(git_repo, isolated_state))['status'] == status
    assert [r['status'] for r in state.recent_completions(git_repo, SESSION)] == [status]


def test_live_continuity_check_requires_explicit_opt_in():
    script = Path(__file__).resolve().parents[1] / 'scripts/check_completion_continuity.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 2
    assert '--run is required' in result.stderr
