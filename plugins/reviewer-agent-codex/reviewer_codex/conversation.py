# SPDX-License-Identifier: MPL-2.0
"""Read visible current-turn messages through the documented local app-server API."""
import json
import os
import queue
import subprocess
import threading
import time

from . import backend, state

MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


class _Unavailable(Exception):
    pass


def _messages(items, turn_id):
    if not isinstance(items, list):
        raise _Unavailable('malformed turn items')
    visible = []
    truncated = False
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        kind = item.get('type')
        if kind == 'agentMessage':
            phase = item.get('phase') or ''
            if phase not in ('', 'commentary', 'partial_answer', 'final_answer'):
                continue
            role, text = 'assistant', item.get('text')
        elif kind == 'userMessage':
            content = item.get('content')
            if not isinstance(content, list):
                continue
            role, phase = 'user', ''
            text = '\n'.join(part['text'] for part in content if isinstance(part, dict)
                             and part.get('type') == 'text' and isinstance(part.get('text'), str))
        else:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        truncated = truncated or len(text) > 2000
        visible.append(dict(role=role, phase=phase, text=text[:2000],
                            item_id=item['id'][:128], turn_id=turn_id))
    kept, total = [], 0
    for item in reversed(visible):
        if len(kept) >= 40 or total + len(item['text']) > 12000:
            truncated = True
            break
        kept.append(item)
        total += len(item['text'])
    return dict(status='available', messages=list(reversed(kept)), truncated=truncated)


def read_current_turn(cwd, session_id, turn_id, cancel_event=None, timeout=8):
    """One metadata read and one bounded turn page; never resume or start work.

    The caller supplies host-bound identities from the claimed review ticket.
    Failure is explicit evidence unavailability, never a completion verdict.
    """
    process, reader = None, None
    try:
        if not all(isinstance(v, str) and v.strip() for v in (session_id, turn_id)):
            raise _Unavailable('missing host identity')
        if cancel_event is not None and cancel_event.is_set():
            raise _Unavailable('cancelled')
        root_key = state.project_key(cwd)
        if not root_key:
            raise _Unavailable('missing repository identity')
        deadline = time.monotonic() + timeout
        process = subprocess.Popen([backend.codex_bin(), 'app-server'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=backend.child_environment(), start_new_session=(os.name == 'posix'))
        incoming = queue.Queue(maxsize=8)
        overflow = threading.Event()

        def pump():
            total = 0
            try:
                while True:
                    line = process.stdout.readline(MAX_LINE_BYTES + 1)
                    total += len(line)
                    if len(line) > MAX_LINE_BYTES or total > MAX_TOTAL_BYTES:
                        value = _Unavailable('host response exceeds byte limit')
                    elif not line:
                        value = _Unavailable('host closed the connection')
                    else:
                        value = json.loads(line)
                    incoming.put_nowait(value)
                    if isinstance(value, _Unavailable):
                        return
            except (ValueError, OSError, queue.Full):
                overflow.set()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        def send(message):
            process.stdin.write((json.dumps(message) + '\n').encode())
            process.stdin.flush()

        def rpc(request_id, method, params):
            send(dict(id=request_id, method=method, params=params))
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise _Unavailable('cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Unavailable('host read timed out')
                if overflow.is_set():
                    raise _Unavailable('invalid or excessive host output')
                try:
                    value = incoming.get(timeout=min(.05, remaining))
                except queue.Empty:
                    continue
                if isinstance(value, _Unavailable):
                    raise value
                if not isinstance(value, dict) or value.get('id') != request_id:
                    continue
                if value.get('error') or not isinstance(value.get('result'), dict):
                    raise _Unavailable('host API unavailable')
                return value['result']

        rpc(1, 'initialize', {'clientInfo': {'name': 'reviewer_agent_context', 'version': '1'}})
        send({'method': 'initialized', 'params': {}})
        metadata = rpc(2, 'thread/read', {'threadId': session_id}).get('thread')
        if (not isinstance(metadata, dict) or metadata.get('id') != session_id
                or not isinstance(metadata.get('cwd'), str)
                or state.project_key(metadata['cwd']) != root_key):
            raise _Unavailable('thread or repository identity mismatch')
        turns = rpc(3, 'thread/turns/list', {'threadId': session_id, 'limit': 3,
                    'sortDirection': 'desc', 'itemsView': 'full'}).get('data')
        if isinstance(turns, list):
            for turn in turns:
                if isinstance(turn, dict) and turn.get('id') == turn_id:
                    return _messages(turn.get('items'), turn_id)
        raise _Unavailable('current turn not available in bounded history')
    except _Unavailable as error:
        return dict(status='unavailable', reason=str(error))
    except (OSError, ValueError, TypeError):
        return dict(status='unavailable', reason='host message read failed')
    finally:
        if process is not None:
            backend._terminate(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            if reader is not None:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
