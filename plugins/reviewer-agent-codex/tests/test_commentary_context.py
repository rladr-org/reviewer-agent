# SPDX-License-Identifier: MPL-2.0
"""Host-bound commentary reaches the reviewer without reading private rollouts."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from reviewer_codex import backend, state, stop_tool, supervisor
from test_completion_drafts import SESSION, TURN, bind, call, event

PROMISE = 'I will repeat the test, then submit the corrected answer.'


@pytest.fixture
def server(tmp_path, git_repo, monkeypatch):
    script = tmp_path / 'fake-app-server'
    script.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys, time
from pathlib import Path
Path(os.environ['PROBE_PID']).write_text(str(os.getpid()))
for line in sys.stdin:
    req = json.loads(line)
    with open(os.environ['PROBE_LOG'], 'a') as out: out.write(req['method'] + '\\n')
    if 'id' not in req: continue
    mode = os.environ.get('PROBE_MODE', '')
    if mode == 'hang': time.sleep(30)
    if mode == 'oversize':
        print('x' * 5000000, flush=True); continue
    method = req['method']
    if method == 'initialize': result = {}
    elif method == 'thread/read':
        result = {'thread': {'id': 'other' if mode == 'wrong_thread' else 'session',
            'cwd': '/' if mode == 'wrong_repo' else os.environ['PROBE_CWD']}}
    elif method == 'thread/turns/list':
        if mode == 'unsupported':
            print(json.dumps({'id': req['id'], 'error': {'code': -32601, 'message': 'unsupported'}}), flush=True)
            continue
        items = [
            {'type':'userMessage','id':'u','content':[{'type':'text','text':'Please repeat the test.'}]},
            {'type':'agentMessage','id':'a','phase':'commentary','text':os.environ['PROBE_PROMISE']},
            {'type':'reasoning','id':'r','content':['PRIVATE REASONING']},
            {'type':'mcpToolCall','id':'t','arguments':{'secret':'PRIVATE TOOL DATA'}},
            {'type':'agentMessage','id':'f','phase':'final_answer','text':'A prior visible answer.'}]
        if mode == 'many': items += [{'type':'agentMessage','id':str(i),'phase':'commentary','text':'x'*3000} for i in range(80)]
        result = {'data':[{'id':'other-turn','items':[{'type':'agentMessage','text':'OTHER TURN SECRET'}]},
            {'id':'missing' if mode == 'wrong_turn' else 'turn', 'items':items}]}
    else: raise RuntimeError('Unexpected RPC: ' + method)
    print(json.dumps({'id':req['id'],'result':result}), flush=True)
''')
    script.chmod(0o755)
    for name, value in {'REVIEWER_CODEX_BIN':script, 'PROBE_PID':tmp_path/'pid',
        'PROBE_LOG':tmp_path/'rpc.log', 'PROBE_CWD':git_repo, 'PROBE_PROMISE':PROMISE}.items():
        monkeypatch.setenv(name, str(value))
    return tmp_path


def capture(repo, **kwargs):
    return importlib.import_module('reviewer_codex.conversation').read_current_turn(repo, 'session', 'turn', **kwargs)


def assert_reaped(server):
    pid = int((server/'pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_read_only_api_captures_visible_messages_in_order_and_reaps_helper(git_repo, server):
    result = capture(git_repo)
    assert result['status'] == 'available'
    assert [m['role'] for m in result['messages']] == ['user','assistant','assistant']
    assert result['messages'][1] == dict(role='assistant', phase='commentary', text=PROMISE, item_id='a', turn_id='turn')
    rendered = json.dumps(result)
    assert 'PRIVATE' not in rendered and 'OTHER TURN' not in rendered
    assert not result['truncated']
    assert (server/'rpc.log').read_text().splitlines() == ['initialize','initialized','thread/read','thread/turns/list']
    assert_reaped(server)


@pytest.mark.parametrize('mode', ['wrong_thread','wrong_repo','wrong_turn','unsupported','oversize'])
def test_bad_or_unavailable_scope_never_leaks_messages(git_repo, server, monkeypatch, mode):
    monkeypatch.setenv('PROBE_MODE', mode)
    result = capture(git_repo)
    assert result['status'] == 'unavailable'
    assert not result.get('messages')
    assert_reaped(server)


@pytest.mark.parametrize('cancel', [False, True])
def test_hung_api_is_bounded_and_cancellable(git_repo, server, monkeypatch, cancel):
    monkeypatch.setenv('PROBE_MODE','hang')
    module = importlib.import_module('reviewer_codex.conversation')
    popen, processes = module.subprocess.Popen, []
    def start_process(argv, **kwargs):
        process = popen(argv, **kwargs)
        if len(argv) > 1 and argv[1] == 'app-server':
            processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', start_process)
    token = threading.Event()
    def cancel_after_start():
        deadline = time.monotonic() + 2
        while not (server/'pid').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        token.set()
    worker = threading.Thread(target=cancel_after_start)
    if cancel: worker.start()
    start = time.monotonic()
    try:
        result = capture(git_repo, timeout=5 if cancel else .2, cancel_event=token)
    finally:
        if cancel: worker.join(3)
    assert time.monotonic()-start < 3
    assert result['status'] == 'unavailable'
    assert processes and all(process.poll() is not None for process in processes)


def test_message_bounds_are_reported(git_repo, server, monkeypatch):
    monkeypatch.setenv('PROBE_MODE','many')
    result = capture(git_repo)
    assert result['status'] == 'available' and result['truncated']
    assert len(result['messages']) <= 40
    assert sum(len(m['text']) for m in result['messages']) <= 12000
    assert all(len(m['text']) <= 2000 for m in result['messages'])


@pytest.mark.parametrize('route', ['draft','native','fresh'])
def test_host_commentary_reaches_actual_reviewer_prompt(git_repo, isolated_state, monkeypatch, route):
    state.set_feature(git_repo,'stop')
    seen, scopes = [], []
    def read(cwd, session, turn, **kwargs):
        scopes.append((str(cwd),session,turn))
        return {'status':'available','truncated':False,'messages':[
            dict(role='user',phase='',text='Please repeat the test.',item_id='u',turn_id=TURN),
            dict(role='assistant',phase='commentary',text=PROMISE,item_id='a',turn_id=TURN)]}
    monkeypatch.setattr(stop_tool,'read_current_turn',read,raising=False)
    monkeypatch.setattr(backend,'review_turn',lambda *a,**k:(seen.append(a[4]) or 0,'Finish the promised test.',''))
    monkeypatch.setattr(backend,'review_once',lambda *a,**k:(seen.append(a[2]) or 0,'Finish the promised test.',''))
    if route == 'native':
        assert supervisor.supervise_hook(event(git_repo,last_assistant_message='Not calculated.'))['decision']=='block'
    else:
        for _ in range(3 if route=='fresh' else 1):
            assert call(bind(git_repo,isolated_state,'Not calculated.'))['status']=='incomplete'
    assert scopes and all(s==(str(git_repo),SESSION,TURN) for s in scopes)
    assert PROMISE in seen[-1] and 'Please repeat the test.' in seen[-1]
    assert 'commentary' in seen[-1] and TURN in seen[-1]
    assert 'not operator authorization' in seen[-1]


def test_unavailable_capture_is_visible_to_reviewer_and_does_not_approve(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo,'stop')
    seen=[]
    monkeypatch.setattr(backend,'review_turn',lambda *a,**k:(seen.append(a[4]) or 0,'Finish the test.',''))
    assert call(bind(git_repo,isolated_state))['status']=='incomplete'
    assert 'CURRENT TURN MESSAGES UNAVAILABLE' in seen[0]
    assert not state.consume_stop_permit(git_repo,SESSION)


def test_live_commentary_check_requires_opt_in():
    script=Path(__file__).resolve().parents[1]/'scripts/check_commentary_context.py'
    result=subprocess.run([sys.executable,str(script)],capture_output=True,text=True)
    assert result.returncode==2 and '--run is required' in result.stderr
