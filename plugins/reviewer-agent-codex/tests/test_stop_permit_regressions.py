# SPDX-License-Identifier: MPL-2.0
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from reviewer_codex import state, stop_tool, supervisor

ROOT = Path(__file__).resolve().parents[1]


def payload(repo, **changes):
    result = dict(cwd=str(repo), session_id='root', turn_id='turn-1',
                  last_assistant_message='I am done.')
    result.update(changes)
    return result


def approve(repo, monkeypatch, ruling=(0, 'STOP_OK', '')):
    state.set_feature(repo, 'stop')
    monkeypatch.setattr(stop_tool.backend, 'review_turn', lambda *a, **k: ruling)
    ticket = state.create_stop_ticket(repo, 'root', 'I am done.', turn_id='turn-1')
    return stop_tool.review_stop(repo, ticket)


def new_prompt(repo):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/hook_prompt.py')],
        input=json.dumps(dict(cwd=str(repo), session_id='root', turn_id='turn-2',
                             prompt='Continue the authorized implementation.')),
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_new_operator_prompt_invalidates_approval(git_repo, isolated_state, monkeypatch):
    approve(git_repo, monkeypatch)
    new_prompt(git_repo)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


@pytest.mark.parametrize('message', [
    'Completion check passed. The evaluation is complete; the plan needs the four revisions identified above.',
    'The completion check passed. No further work was requested.',
    'Completion approved.',
])
def test_reworded_acknowledgment_consumes_approval(git_repo, isolated_state, monkeypatch, message):
    approve(git_repo, monkeypatch)
    result = supervisor.supervise(payload(git_repo, turn_id='host-continuation',
                                         last_assistant_message=message))
    assert 'approved' in result['systemMessage']
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def pre_tool(repo, name):
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/hook_pre_tool.py')],
        input=json.dumps(dict(cwd=str(repo), session_id='root', hook_event_name='PreToolUse',
                             tool_name=name, tool_input={}, tool_use_id='work-1')),
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('tool', ['Bash', 'update_plan', 'apply_patch', 'mcp__example__lookup'])
def test_resumed_tool_work_invalidates_approval(git_repo, isolated_state, monkeypatch, tool):
    approve(git_repo, monkeypatch)
    pre_tool(git_repo, tool)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_review_tool_itself_does_not_break_completion(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    ticket = state.create_stop_ticket(git_repo, 'root', 'I am done.', turn_id='turn-1')
    pre_tool(git_repo, 'mcp__reviewer_agent__stop_continue')
    monkeypatch.setattr(stop_tool.backend, 'review_turn', lambda *a, **k: (0, 'STOP_OK', ''))
    assert stop_tool.review_stop(git_repo, ticket)['status'] == 'stop_ok'
    assert 'approved' in supervisor.supervise(payload(git_repo, turn_id='host-turn',
                                        last_assistant_message='Completion approved.'))['systemMessage']


def test_work_during_review_invalidates_late_approval(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    ticket = state.create_stop_ticket(git_repo, 'root', 'I am done.', turn_id='turn-1')
    def review(*args, **kwargs):
        pre_tool(git_repo, 'apply_patch')
        return 0, 'STOP_OK', ''
    monkeypatch.setattr(stop_tool.backend, 'review_turn', review)
    stop_tool.review_stop(git_repo, ticket)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_matching_approval_is_one_use(git_repo, isolated_state, monkeypatch):
    approve(git_repo, monkeypatch)
    assert 'approved' in supervisor.supervise(payload(git_repo))['systemMessage']
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_old_review_cannot_approve_new_operator_prompt(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, 'stop')
    ticket = state.create_stop_ticket(git_repo, 'root', 'I am done.', turn_id='turn-1')
    def review(*args, **kwargs):
        new_prompt(git_repo)
        return 0, 'STOP_OK', ''
    monkeypatch.setattr(stop_tool.backend, 'review_turn', review)
    stop_tool.review_stop(git_repo, ticket)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


@pytest.mark.parametrize('ruling', [(1, '', 'reviewer failed'), (0, '', '')])
def test_failed_open_is_not_reported_as_approval(git_repo, isolated_state, monkeypatch, ruling):
    assert approve(git_repo, monkeypatch, ruling)['status'] == 'failed_open'
    message = supervisor.supervise(payload(git_repo))['systemMessage'].lower()
    assert 'approved' not in message
    assert 'failed' in message


def test_legacy_permit_is_not_an_approval(git_repo, isolated_state):
    state.set_feature(git_repo, 'stop')
    target = state._stop_permit_file(git_repo, 'root')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('old-permit-token\n')
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_expired_permit_requires_review(git_repo, isolated_state, monkeypatch):
    approve(git_repo, monkeypatch)
    now = state.time.time()
    monkeypatch.setattr(state.time, 'time', lambda: now + 3601)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_subagent_start_invalidates_approval(git_repo, isolated_state, monkeypatch):
    approve(git_repo, monkeypatch)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/hook_subagent.py'), 'start'],
        input=json.dumps(dict(cwd=str(git_repo), session_id='root', agent_id='child-1')),
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def _require_flock():
    if state.fcntl is None:
        pytest.skip("the mutation lock is a no-op without fcntl")


def test_invalidate_serializes_on_mutation_lock(git_repo, isolated_state, monkeypatch):
    # Finding 1: once consume renames the permit to a claim, invalidate's unlink is a
    # no-op, so the two operations must not interleave -- invalidate must take the lock.
    _require_flock()
    approve(git_repo, monkeypatch)
    permit = state._stop_permit_file(git_repo, 'root')
    assert permit.exists()
    finished = threading.Event()

    def worker():
        state.invalidate_stop_permits(git_repo, 'root')
        finished.set()

    with state._mutation_lock():
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "invalidate did not serialize on the mutation lock"
        assert permit.exists(), "permit revoked before the lock was acquired"
    thread.join(timeout=2.0)
    assert finished.is_set()
    assert not permit.exists()


def test_consume_serializes_on_mutation_lock(git_repo, isolated_state, monkeypatch):
    # Finding 1: consume's rename + generation compare must be atomic w.r.t. invalidate.
    _require_flock()
    approve(git_repo, monkeypatch)
    outcome = {}

    def worker():
        outcome['value'] = state.consume_stop_permit(git_repo, 'root')

    with state._mutation_lock():
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "consume did not serialize on the mutation lock"
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert outcome.get('value') == 'stop_ok'


def test_invalidate_revokes_permit_when_generation_write_fails(git_repo, isolated_state, monkeypatch):
    # Finding 2: a failed generation write must still revoke the permit, or a stale
    # permit whose generation is unchanged will approve after new operator input.
    approve(git_repo, monkeypatch)
    permit = state._stop_permit_file(git_repo, 'root')
    assert permit.exists()

    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(state, '_atomic_write', boom)
    with pytest.raises(OSError):
        state.invalidate_stop_permits(git_repo, 'root')
    assert not permit.exists(), "a failed generation write must still revoke the permit"


def test_stop_generation_survives_read_error(git_repo, isolated_state, monkeypatch):
    # Finding 3: a non-missing read error must fail open to "", not escape.
    real = Path.read_text

    def boom(self, *args, **kwargs):
        if 'stop-generations' in str(self) or 'stop-work-generations' in str(self):
            raise PermissionError("locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', boom)
    assert state.stop_generation(git_repo, 'root') == ''
    assert state.stop_generation(git_repo, 'root', work=True) == ''


def test_review_snapshot_survives_generation_read_error(git_repo, isolated_state, monkeypatch):
    # Finding 3: the pre-review work-generation snapshot is outside the fail-open try,
    # so a read error there must not escape as a bare exception.
    state.set_feature(git_repo, 'stop')
    ticket = state.create_stop_ticket(git_repo, 'root', 'I am done.', turn_id='turn-1')
    monkeypatch.setattr(stop_tool.backend, 'review_turn', lambda *a, **k: (0, 'STOP_OK', ''))
    real = Path.read_text

    def boom(self, *args, **kwargs):
        if 'stop-generations' in str(self) or 'stop-work-generations' in str(self):
            raise PermissionError("locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', boom)
    result = stop_tool.review_stop(git_repo, ticket)
    assert result['status'] in ('stop_ok', 'failed_open')


def test_failed_generation_write_during_review_denies_regranted_stop(git_repo, isolated_state, monkeypatch):
    # Reviewer follow-up on finding 2: if invalidate's generation write fails mid-review,
    # removing the permit is not enough -- the running review regrants a permit from its
    # pre-review snapshot, and an unchanged generation still matches it. Deleting the
    # generation on write failure makes the snapshot mismatch, so consume fails closed.
    state.set_feature(git_repo, 'stop')
    # A non-empty baseline generation, as the operator prompt and tool calls establish.
    state.invalidate_stop_permits(git_repo, 'root')
    state.invalidate_stop_permits(git_repo, 'root', work=True)
    ticket = state.create_stop_ticket(git_repo, 'root', 'I am done.', turn_id='turn-1')
    real_atomic = state._atomic_write

    def review(*args, **kwargs):
        def boom(target, text):
            if 'stop-generations' in str(target) or 'stop-work-generations' in str(target):
                raise OSError("no space left on device")
            return real_atomic(target, text)
        # New operator input arrives during the review, but its generation write fails.
        monkeypatch.setattr(state, '_atomic_write', boom)
        try:
            state.invalidate_stop_permits(git_repo, 'root')
        except OSError:
            pass
        monkeypatch.setattr(state, '_atomic_write', real_atomic)
        return 0, 'STOP_OK', ''

    monkeypatch.setattr(stop_tool.backend, 'review_turn', review)
    stop_tool.review_stop(git_repo, ticket)
    assert supervisor.supervise(payload(git_repo))['decision'] == 'block'


def test_consume_survives_claim_unlink_failure(git_repo, isolated_state, monkeypatch):
    # Finding 3: consume's finally unlink is unguarded; a non-missing unlink error must
    # not escape and turn a decided outcome into a bare exception.
    approve(git_repo, monkeypatch)
    real_unlink = Path.unlink

    def boom(self, *args, **kwargs):
        if self.name.endswith('.claim'):
            raise PermissionError("cannot remove claim")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', boom)
    assert state.consume_stop_permit(git_repo, 'root') == 'stop_ok'
