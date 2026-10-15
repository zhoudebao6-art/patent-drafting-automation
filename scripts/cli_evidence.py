"""Verify real Codex CLI execution evidence; never grant business acceptance.

This module does not launch, terminate, or configure processes. Dispatch and
completion records must be captured and sealed by the sole batch controller.
"""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


MODEL_EFFORTS = frozenset({('gpt-6.1-sol', 'high'), ('gpt-6.1-sol', 'xhigh'),
                          ('gpt-6-astra', 'high')})
COMPLETION_FILES = ('events.jsonl', 'worker-final.json', 'session.jsonl', 'prompt.txt', 'cli-exit.json')
CONNECTION_RETRY = re.compile(
    r'Reconnecting\.\.\. ([1-9][0-9]*)/([1-9][0-9]*) '
    r'\(stream disconnected before completion: [^\r\n]+\)')
_COMPLETION_SCOPE = ContextVar('cli_completion_scope', default=None)


def _completion_binding(attempt, dispatch):
    # State/binding fields legitimately change between bind and record; execution
    # provenance, the frozen model arguments and the complete dispatch may not.
    fields = ('id', 'directory', 'context', 'stage', 'input_artifact',
              'input_artifact_sha256', 'transport')
    data = {'attempt': {key: attempt.get(key) for key in fields}, 'dispatch': dispatch}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@contextmanager
def completion_scope(attempt, dispatch):
    """Reuse parsing only inside one controller collection of this exact worker.

    The scope contains no acceptance conclusion. Every use still checks actual
    execution files and process identity, and the token is discarded on exit.
    """
    token = _COMPLETION_SCOPE.set({'binding': _completion_binding(attempt, dispatch), 'proof': None})
    try:
        yield
    finally:
        _COMPLETION_SCOPE.reset(token)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _absolute(path):
    _require(isinstance(path, (str, os.PathLike)), 'Absolute path required')
    value = Path(path)
    _require(value.is_absolute(), 'Absolute path required')
    return value.resolve()


def _same_path(left, right):
    return os.path.normcase(str(_absolute(left))) == os.path.normcase(str(_absolute(right)))


def _file(root, name):
    raw = root / name
    for node in (raw, *raw.parents):
        info = node.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                 'Linked/reparse CLI evidence is prohibited')
        _require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1,
                 'Hard-linked CLI evidence is prohibited')
    path = raw.resolve(strict=True)
    _require(path.parent == root and path.is_file(), 'Evidence must be a file directly in the attempt directory')
    return path


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate JSON key in execution evidence')
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique_pairs)
    _require(isinstance(result, dict), 'Execution evidence must contain JSON objects')
    return result


def _jsonl(data):
    return [_json(line) for line in data.decode('utf-8-sig').splitlines() if line.strip()]


def _identity(value):
    _require(isinstance(value, dict), 'Process identity required')
    _require(type(value.get('pid')) is int and 0 < value['pid'] <= 0xFFFFFFFF,
             'Process PID must be a positive DWORD integer')
    _require(type(value.get('created')) is int and value['created'] > 0,
             'Process creation FILETIME required')
    _absolute(value.get('executable'))
    return value


def build_command(executable, attempt_dir, model, reasoning_effort):
    """Return argv for subprocess shell=False, with no global configuration edits."""
    _require((model, reasoning_effort) in MODEL_EFFORTS, 'Explicit supported model/effort required')
    executable, root = str(_absolute(executable)), _absolute(attempt_dir)
    return [executable, 'exec', '--model', model, '-c', f'model_reasoning_effort="{reasoning_effort}"',
            '--sandbox', 'workspace-write', '--skip-git-repo-check', '--json', '--color', 'never',
            '-C', str(root), '-o', str(root / 'worker-final.json'), '-']


def process_identity(pid):
    """Return a live Windows PID/creation-time/executable identity, or None.

    Access denied and API errors are not treated as evidence that a process has
    exited. The caller must retain its lease when identity cannot be established.
    """
    _require(type(pid) is int and 0 < pid <= 0xFFFFFFFF, 'PID must be a positive DWORD integer')
    if os.name != 'nt':
        raise NotImplementedError('Native CLI process identity is currently Windows-only')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                      ctypes.POINTER(wintypes.FILETIME)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID does not identify a process.
            return None
        raise ctypes.WinError(error)
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:  # STILL_ACTIVE
            return None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Do not report a process that terminated while its identity was read.
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:
            return None
        return {'pid': pid, 'created': (created.dwHighDateTime << 32) | created.dwLowDateTime,
                'executable': str(_absolute(buffer.value))}
    finally:
        kernel.CloseHandle(handle)


def _dispatch_record(ev, attempt, model, check_prompt=True):
    """Validate sealed static dispatch fields; usable after the process exits."""
    _require(isinstance(ev, dict) and isinstance(attempt, dict), 'Dispatch and attempt required')
    _require(isinstance(model, dict), 'Frozen model policy required')
    _require(ev.get('tool') == 'codex.exec' and ev.get('context') == attempt.get('context'),
             'CLI dispatch context mismatch')
    _require(isinstance(attempt.get('context'), dict) and isinstance(attempt.get('id'), str)
             and bool(attempt['id']), 'Attempt identity/context required')
    root = _absolute(attempt['directory'])
    args = ev.get('arguments')
    _require(isinstance(args, dict), 'CLI dispatch arguments required')
    _require(args.get('model') == model.get('model')
             and args.get('reasoning_effort') == model.get('reasoning_effort'), 'CLI dispatch model/effort mismatch')
    identity = _identity(ev.get('result'))
    expected = build_command(identity['executable'], root, model.get('model'), model.get('reasoning_effort'))
    _require(args.get('argv') == expected and _same_path(args.get('cwd'), root),
             'CLI dispatch argv/cwd must exactly match the safe command')
    _require(ev.get('agent_id') == 'cli:' + attempt['id'], 'CLI agent identity mismatch')
    if check_prompt:
        prompt = _file(root, 'prompt.txt').read_bytes()
        _require(_sha(prompt) == ev.get('prompt_sha256'), 'CLI prompt SHA mismatch')
        prompt.decode('utf-8')  # A lossy decode cannot prove the actual user input.
    return root, identity


def verify_dispatch(ev, attempt, model):
    """Bind a live process, or its fully verified successful completion on recovery."""
    root, identity = _dispatch_record(ev, attempt, model)
    live = process_identity(identity['pid'])
    if live is not None and live.get('created') == identity['created']:
        _require(live.get('pid') == identity['pid']
                 and _same_path(live.get('executable'), identity['executable']),
                 'CLI dispatch process belongs to another executable')
    else:
        _require((root / 'cli-completion.json').is_file(),
                 'CLI process is absent or reused without sealed successful completion')
        verify_completion(attempt, ev)
    return ev['agent_id']


def _require_stopped(identity):
    live = process_identity(identity['pid'])
    _require(live is None or live.get('created') != identity['created'],
             'Original CLI process is still live; keep its worker slot')


def _proof_file(root, name):
    """Read a regular sealed file and retain its actual filesystem identity."""
    path = _file(root, name)
    with path.open('rb') as stream:
        value = os.fstat(stream.fileno())
        identity = (value.st_dev, value.st_ino)
        _require(bool(identity[1]), 'Stable CLI evidence file identity required')
        data = stream.read()
    current = _file(root, name).stat()
    _require((current.st_dev, current.st_ino) == identity,
             'CLI evidence file replaced during verification: ' + name)
    return data, identity


def _recheck_completion(attempt, dispatch, proof):
    root, identity = _dispatch_record(dispatch, attempt, dispatch.get('arguments'))
    _require_stopped(identity)
    seal, seal_identity = _proof_file(root, 'cli-completion.json')
    _require(seal == proof['seal'] and seal_identity == proof['identities']['cli-completion.json'],
             'CLI completion seal changed within collection')
    # Keep both byte-integrity passes from the full verifier; only JSON/session
    # parsing is shared. Replacement with identical bytes also invalidates proof.
    for _ in range(2):
        for name in COMPLETION_FILES:
            data, file_identity = _proof_file(root, name)
            _require(_sha(data) == proof['hashes'][name]
                     and file_identity == proof['identities'][name],
                     'CLI evidence changed within collection: ' + name)
    seal, seal_identity = _proof_file(root, 'cli-completion.json')
    _require(seal == proof['seal'] and seal_identity == proof['identities']['cli-completion.json'],
             'CLI completion seal changed within collection')
    _require_stopped(identity)
    return copy.deepcopy(proof['result'])


def verify_completion(attempt, dispatch):
    """Verify execution on every call; optionally share parsing in one collect."""
    scope = _COMPLETION_SCOPE.get()
    if scope is not None:
        _require(scope['binding'] == _completion_binding(attempt, dispatch),
                 'CLI completion scope provenance changed')
        if scope['proof'] is not None:
            return _recheck_completion(attempt, dispatch, scope['proof'])
    proof = _verify_completion_full(attempt, dispatch, capture_identity=scope is not None)
    if scope is not None:
        scope['proof'] = proof
    return copy.deepcopy(proof['result'])


def _verify_completion_full(attempt, dispatch, *, capture_identity=False):
    """Bind successful exit, raw CLI events, real session context and final JSON."""
    root, identity = _dispatch_record(dispatch, attempt, dispatch.get('arguments'))
    meta_bytes = _file(root, 'cli-completion.json').read_bytes()
    meta = _json(meta_bytes)
    _require(type(meta.get('exit_code')) is int and meta['exit_code'] == 0,
             'Successful actual CLI exit required')
    _require(type(meta.get('pid')) is int and type(meta.get('created')) is int
             and meta['pid'] == identity['pid'] and meta['created'] == identity['created'],
             'CLI completion process identity mismatch')
    _require_stopped(identity)
    _require(isinstance(meta.get('files'), dict) and set(meta['files']) == set(COMPLETION_FILES),
             'All five sealed CLI evidence files are required')
    data = {}
    for name in COMPLETION_FILES:
        data[name] = _file(root, name).read_bytes()
        _require(_sha(data[name]) == meta['files'][name], 'CLI evidence SHA mismatch: ' + name)
    _require(_sha(data['prompt.txt']) == dispatch['prompt_sha256'], 'CLI completion prompt mismatch')
    prompt = data['prompt.txt'].decode('utf-8')
    exited = _json(data['cli-exit.json'])
    _require(type(exited.get('returncode')) is int and exited['returncode'] == 0
             and exited.get('pid') == identity['pid'] and exited.get('created') == identity['created'],
             'Actual CLI exit record contradicts successful completion')
    events = _jsonl(data['events.jsonl'])
    starts = [row for row in events if row.get('type') == 'thread.started']
    _require(len(starts) == 1, 'Exactly one CLI thread.started required')
    session_id = starts[0].get('thread_id')
    _require(isinstance(session_id, str), 'CLI session UUID required')
    try:
        _require(str(uuid.UUID(session_id)) == session_id, 'Canonical CLI session UUID required')
    except (AttributeError, ValueError) as exc:
        raise ValueError('CLI session UUID invalid') from exc
    turn_starts = [i for i, row in enumerate(events) if row.get('type') == 'turn.started']
    turn_completions = [i for i, row in enumerate(events) if row.get('type') == 'turn.completed']
    _require(len(turn_starts) == 1 and len(turn_completions) == 1,
             'Exactly one CLI turn.started and turn.completed required')
    _require(not any(row.get('type') == 'turn.failed' for row in events),
             'CLI events contain a failed turn')
    messages = [(i, row['item']['text']) for i, row in enumerate(events)
                if row.get('type') == 'item.completed' and isinstance(row.get('item'), dict)
                and row['item'].get('type') == 'agent_message' and isinstance(row['item'].get('text'), str)]
    _require(bool(messages), 'Completed CLI agent message required')
    final_index, final_text = messages[-1]
    _require(events.index(starts[0]) < turn_starts[0] < final_index < turn_completions[0],
             'CLI thread, turn, final message and completion must be ordered')
    for i, row in enumerate(events):
        if row.get('type') != 'error':
            continue
        message = row.get('message')
        retry = CONNECTION_RETRY.fullmatch(message) if isinstance(message, str) else None
        # CLI emits recovered stream reconnects as error events. Accept only
        # this known shape, followed by a new final message in the same turn.
        # Successful exit, real session final and all seals remain mandatory.
        _require(set(row) == {'type', 'message'} and retry is not None
                 and int(retry[1]) <= int(retry[2])
                 and turn_starts[0] < i < final_index,
                 'CLI events contain an unknown, terminal or unrecovered error')
    receipt = _json(data['worker-final.json'])
    _require(_json(final_text) == receipt, 'CLI final JSON differs from the final agent message')
    rows = _jsonl(data['session.jsonl'])
    _require(not any(row.get('type') == 'event_msg' and isinstance(row.get('payload'), dict)
                     and row['payload'].get('type') in {'turn_aborted', 'turn_failed', 'error'} for row in rows),
             'Actual CLI session contains an aborted or failed turn')
    final_messages = []
    for row in rows:
        payload = row.get('payload', {})
        if (row.get('type') == 'response_item' and isinstance(payload, dict)
                and payload.get('type') == 'message' and payload.get('role') == 'assistant'
                and payload.get('phase') == 'final_answer'):
            content = payload.get('content', [])
            _require(isinstance(content, list) and content and all(isinstance(x, dict)
                     and x.get('type') == 'output_text' and isinstance(x.get('text'), str) for x in content),
                     'Actual CLI final message content required')
            final_messages.append(''.join(x['text'] for x in content))
    _require(bool(final_messages) and _json(final_messages[-1]) == receipt,
             'Actual session final answer differs from the receipt')
    metas = [row.get('payload') for row in rows if row.get('type') == 'session_meta']
    _require(len(metas) == 1 and isinstance(metas[0], dict), 'One actual session_meta required')
    sm = metas[0]
    _require(sm.get('id') == session_id and sm.get('source') == 'exec' and _same_path(sm.get('cwd'), root),
             'Actual CLI session identity/source/cwd mismatch')
    turns = [row.get('payload') for row in rows if row.get('type') == 'turn_context']
    _require(bool(turns), 'Actual CLI turn_context required')
    for turn in turns:
        _require(isinstance(turn, dict) and turn.get('model') == dispatch['arguments']['model']
                 and turn.get('effort') == dispatch['arguments']['reasoning_effort'] and _same_path(turn.get('cwd'), root),
                 'Actual CLI turn model/effort/cwd mismatch')
        _require(isinstance(turn.get('sandbox_policy'), dict)
                 and turn['sandbox_policy'].get('type') == 'workspace-write',
                 'Actual CLI turn must retain workspace-write sandbox')
    user_inputs = []
    for row in rows:
        payload = row.get('payload')
        if row.get('type') != 'response_item' or not isinstance(payload, dict):
            continue
        if payload.get('type') != 'message' or payload.get('role') != 'user':
            continue
        content = payload.get('content')
        if isinstance(content, list) and content and all(isinstance(p, dict) and p.get('type') == 'input_text'
                                                        and isinstance(p.get('text'), str) for p in content):
            user_inputs.append(''.join(p['text'] for p in content))
    _require(prompt in user_inputs, 'Actual CLI user input does not match the frozen prompt')
    # Re-read hashes to reject evidence rewritten during validation.
    identities = {}
    for name in COMPLETION_FILES:
        if capture_identity:
            current, identities[name] = _proof_file(root, name)
        else:
            current = _file(root, name).read_bytes()
        _require(_sha(current) == meta['files'][name],
                 'CLI evidence changed during verification: ' + name)
    if capture_identity:
        current, identities['cli-completion.json'] = _proof_file(root, 'cli-completion.json')
    else:
        current = _file(root, 'cli-completion.json').read_bytes()
    _require(current == meta_bytes,
             'CLI completion seal changed during verification')
    _require_stopped(identity)
    return {'result': {'session_id': session_id, 'receipt': receipt},
            'seal': meta_bytes, 'hashes': dict(meta['files']), 'identities': identities}


def verify_stop(ev, attempt, dispatch):
    """Verify a controller-observed exit; never kill a process or infer an exit."""
    # Damaged output/prompt evidence prevents completion, but must not strand a
    # failed worker slot after its exact process has demonstrably exited.
    _, identity = _dispatch_record(dispatch, attempt, dispatch.get('arguments'), check_prompt=False)
    _require(isinstance(ev, dict) and ev.get('tool') == 'codex.exec.exit'
             and ev.get('context') == attempt['context'], 'CLI stop tool/context mismatch')
    _require(type(ev.get('pid')) is int and type(ev.get('created')) is int
             and ev['pid'] == identity['pid'] and ev['created'] == identity['created'],
             'CLI stop process identity mismatch')
    _require(ev.get('confirmed_stopped') is True and type(ev.get('returncode')) is int,
             'Confirmed CLI exit with actual integer return code required')
    _require_stopped(identity)
    return True
