# SPDX-License-Identifier: MPL-2.0
"""Real trusted hook -> MCP review -> Stop state, with controlled reviewer rulings."""
import json
import pytest
from reviewer_codex import backend, liveness, mcp_server, state, supervisor
from reviewer_codex.cancellation import Cancellation
from test_hook_scripts import _run

SESSION, TURN, DRAFT = 'prefinal-session', 'turn-1', 'Implemented and verified the change.'

def event(repo, name='Stop', **changes):
    return dict(dict(cwd=str(repo), session_id=SESSION, turn_id=TURN,
                hook_event_name=name, last_assistant_message=DRAFT), **changes)

@pytest.fixture
def reviewed(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    calls, outcomes = [], ['STOP_OK']
    def review(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, outcomes.pop(0) if len(outcomes) > 1 else outcomes[0], ''
    monkeypatch.setattr(backend, 'review_turn', review)
    return calls, outcomes

def bind(repo, storage, draft=DRAFT, **arguments):
    args = dict(dict(cwd=str(repo), proposed_response=draft), **arguments)
    result = _run('hook_pre_tool.py', event(repo, 'PreToolUse',
                  tool_name='mcp__reviewer_agent__review_completion', tool_input=args), storage)
    assert result.returncode == 0, result.stderr
    assert result.stdout, 'completion call needs trusted host binding'
    output = json.loads(result.stdout)
    assert 'systemMessage' not in output
    specific = output['hookSpecificOutput']
    assert specific['permissionDecision'] == 'allow'
    return specific['updatedInput']

def call(args, cancel=None):
    response = mcp_server.handle_message({'id': 1, 'method': 'tools/call',
        'params': {'name': 'review_completion', 'arguments': args}}, cancel_event=cancel)
    assert 'error' not in response, response
    return response['result']['structuredContent']

def test_enabled_prompt_supplies_model_context_without_ui_warning(git_repo, isolated_state, reviewed):
    result = _run('hook_prompt.py', event(git_repo, 'UserPromptSubmit', prompt='Finish this task'), isolated_state)
    assert result.returncode == 0
    assert result.stdout, 'enabled sessions must learn the pre-final protocol'
    output = json.loads(result.stdout)
    assert set(output) == {'hookSpecificOutput'}
    context = output['hookSpecificOutput']['additionalContext']
    assert 'review_completion' in context and 'proposed_response' in context and 'final' in context

def test_disabled_stays_silent_and_does_not_review(git_repo, isolated_state, monkeypatch):
    monkeypatch.setattr(backend, 'review_turn', lambda *a, **k: pytest.fail('disabled reviewer ran'))
    result = _run('hook_prompt.py', event(git_repo, 'UserPromptSubmit', prompt='Finish'), isolated_state)
    assert result.stdout == ''
    assert call(dict(cwd=str(git_repo), proposed_response=DRAFT))['status'] == 'disabled'

def test_rejected_draft_retry_and_stop_stay_in_one_turn(git_repo, isolated_state, reviewed):
    calls, outcomes = reviewed
    outcomes[:] = ['Run the missing check.', 'STOP_OK']
    first = call(bind(git_repo, isolated_state, 'I still need to run the check.'))
    assert first['status'] == 'incomplete' and first['directive'] == 'Run the missing check.'
    assert 'decision' not in first and 'reason' not in first
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert call(bind(git_repo, isolated_state))['status'] == 'stop_ok'
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert len(calls) == 2, 'Stop must consume approval without a third review'
    assert calls[-1][0][1:4] == (SESSION, 'stop', 'read-only') and DRAFT in calls[-1][0][4]
    assert not state.consume_stop_permit(git_repo, SESSION)

def test_binding_replaces_forged_token_and_scope(git_repo, isolated_state, reviewed):
    forged = state.create_stop_ticket(git_repo, 'other-session', 'forged')
    args = bind(git_repo, isolated_state, review_token=forged, cwd='/tmp', session_id='other', turn_id='other')
    assert set(args) == {'cwd', 'proposed_response', 'review_token'}
    assert args['cwd'] == str(git_repo) and args['review_token'] != forged
    assert call(args)['status'] == 'stop_ok'
    assert reviewed[0][0][0][1] == SESSION
    assert supervisor.supervise_hook(event(git_repo)) == {}

@pytest.mark.parametrize('change', ['draft', 'turn', 'work', 'input'])
def test_changed_completion_requires_fallback(git_repo, isolated_state, reviewed, change):
    calls, outcomes = reviewed
    assert call(bind(git_repo, isolated_state))['status'] == 'stop_ok'
    data = event(git_repo)
    if change == 'draft': data['last_assistant_message'] = 'Unreviewed claims.'
    elif change == 'turn': data['turn_id'] = 'turn-2'
    else: state.invalidate_stop_permits(git_repo, SESSION, work=change == 'work')
    outcomes[:] = ['Review the changed work.']
    assert supervisor.supervise_hook(data)['decision'] == 'block'
    assert len(calls) == 2

def test_surrounding_whitespace_does_not_repeat_review(git_repo, isolated_state, reviewed):
    assert call(bind(git_repo, isolated_state))['status'] == 'stop_ok'
    assert supervisor.supervise_hook(event(git_repo, last_assistant_message='\n'+DRAFT+'\n')) == {}
    assert len(reviewed[0]) == 1

@pytest.mark.parametrize('kind', ['missing', 'legacy', 'changed-draft', 'replay'])
def test_unbound_or_replayed_call_cannot_approve(git_repo, isolated_state, reviewed, kind):
    args = dict(cwd=str(git_repo), proposed_response=DRAFT)
    if kind == 'legacy': args['review_token'] = state.create_stop_ticket(git_repo, SESSION, DRAFT, turn_id=TURN)
    elif kind in ('changed-draft', 'replay'):
        args = bind(git_repo, isolated_state)
        if kind == 'changed-draft': args['proposed_response'] = 'Changed after binding'
        else:
            assert call(args)['status'] == 'stop_ok'
            assert supervisor.supervise_hook(event(git_repo)) == {}
    before = len(reviewed[0])
    assert call(args)['status'] == 'error'
    assert len(reviewed[0]) == before and not state.consume_stop_permit(git_repo, SESSION)

def test_shared_budget_bounds_retries_and_fallback(git_repo, isolated_state, reviewed, monkeypatch):
    monkeypatch.setenv('REVIEWER_STOP_MAX_NUDGES', '2')
    calls, outcomes = reviewed
    outcomes[:] = ['Finish the check.']
    assert supervisor.supervise_hook(event(git_repo))['decision'] == 'block'
    assert call(bind(git_repo, isolated_state))['status'] == 'incomplete'
    assert call(bind(git_repo, isolated_state))['status'] == 'budget_exhausted'
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert len(calls) == 2 and state.read_nudges(git_repo, SESSION) == 2

def test_backend_failure_is_reported_and_allows_stop(git_repo, isolated_state, reviewed, monkeypatch):
    def broken(*a, **k): raise RuntimeError('review unavailable')
    monkeypatch.setattr(backend, 'review_turn', broken)
    assert call(bind(git_repo, isolated_state))['status'] == 'failed_open'
    result = supervisor.supervise_hook(event(git_repo))
    assert 'decision' not in result and 'failed' in result['systemMessage'].lower()
    assert not state.consume_stop_permit(git_repo, SESSION)

def test_cancel_grants_only_interrupt_bypass(git_repo, isolated_state, reviewed, monkeypatch):
    token = Cancellation()
    def cancelled(*a, **k):
        assert k['cancel_event'] is token
        token.cancel()
        return backend.CANCELLED_EXIT, '', ''
    monkeypatch.setattr(backend, 'review_turn', cancelled)
    assert call(bind(git_repo, isolated_state), token)['status'] == 'cancelled'
    assert not state.consume_stop_permit(git_repo, SESSION)
    assert supervisor.supervise_hook(event(git_repo)) == {}
    assert not state.consume_stop_abort_bypass(git_repo, SESSION, TURN)

@pytest.mark.parametrize('work', [False, True])
def test_change_during_review_returns_tool_directive(git_repo, isolated_state, reviewed, monkeypatch, work):
    def changed(*a, **k):
        state.invalidate_stop_permits(git_repo, SESSION, work=work)
        return 0, 'STOP_OK', ''
    monkeypatch.setattr(backend, 'review_turn', changed)
    result = call(bind(git_repo, isolated_state))
    assert result['status'] == 'incomplete' and 'decision' not in result
    assert not state.consume_stop_permit(git_repo, SESSION)

def test_background_work_is_reviewed_and_cleared(git_repo, isolated_state, reviewed):
    liveness.mark(git_repo, SESSION, 'subagent', 'worker', 'pending worker')
    assert call(bind(git_repo, isolated_state))['status'] == 'stop_ok'
    assert '- subagent: pending worker' in reviewed[0][0][0][4]
    assert not liveness.outstanding(git_repo, SESSION)
    assert supervisor.supervise_hook(event(git_repo)) == {}

@pytest.mark.parametrize('field', ['session_id', 'turn_id'])
def test_missing_host_identity_never_accepts_supplied_token(git_repo, isolated_state, reviewed, field):
    forged = state.create_stop_ticket(git_repo, SESSION, DRAFT, turn_id=TURN)
    data = event(git_repo, 'PreToolUse', tool_name='mcp__reviewer_agent__review_completion',
                 tool_input=dict(cwd=str(git_repo), proposed_response=DRAFT, review_token=forged))
    data[field] = ''
    result = _run('hook_pre_tool.py', data, isolated_state)
    assert result.returncode == 0 and result.stdout
    args = json.loads(result.stdout)['hookSpecificOutput']['updatedInput']
    assert not args.get('review_token')
    assert call(args)['status'] == 'error'
    assert reviewed[0] == []


def test_tool_schema_and_instructions_explain_prefinal_protocol():
    tools = mcp_server.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
    completion = next((t for t in tools if t['name'] == 'review_completion'), None)
    assert completion is not None
    assert completion['inputSchema']['required'] == ['cwd', 'proposed_response']
    assert not {'session_id', 'turn_id'} & set(completion['inputSchema']['properties'])
    instructions = mcp_server.handle_message({'id': 2, 'method': 'initialize'})['result']['instructions']
    assert 'review_completion' in instructions and 'final' in instructions
