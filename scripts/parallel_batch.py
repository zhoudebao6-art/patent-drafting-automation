#!/usr/bin/env python3
"""Persistent rolling queue. Dispatch evidence is controller evidence, not model attestation."""
from __future__ import annotations
import argparse, copy, hashlib, json, os, shutil, sys, tempfile, time, uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
import batch_tool as b
import render_docx_checked as renderer

BatchError = b.BatchError
POLICY = Path(__file__).resolve().parent.parent / 'references' / 'batch-policy.json'
RULES = Path(__file__).resolve().parent.parent / 'references' / 'rules.json'
NEXT = {'pending': 'draft', 'drafted': 'review', 'revised': 'review', 'fix_required': 'revise'}
TERMINAL = b.TERMINAL

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def file_path(path, root):
    p = b.clean_path(path, root)
    if not p.is_file() or p.stat().st_nlink != 1:
        raise BatchError('Regular independent file required; hardlinks prohibited')
    return p

def validate_policy(p):
    if not isinstance(p, dict) or p.get('schema_version') != 2 or p.get('execution') != 'rolling_parallel':
        raise BatchError('Parallel entry requires schema 2 rolling_parallel policy; serial batches use batch_tool.py')
    legacy = dict(p, schema_version=1, execution='serial_per_project')
    b.validate_policy(legacy)
    c = p.get('concurrency', {})
    if (set(c) != {'target_workers', 'max_in_flight_cases', 'min_draft_slots'}
            or any(type(v) is not int for v in c.values())
            or not 1 <= c['target_workers'] <= 64
            or not c['target_workers'] <= c['max_in_flight_cases'] <= 256
            or not 1 <= c['min_draft_slots'] <= c['target_workers']):
        raise BatchError('Invalid concurrency policy')

@contextmanager
def locked(path, timeout=10):
    """Kernel lock is released on process death; never delete a possibly live lock."""
    root = b.root_path(Path(path).parent, required=True)
    lock = b.clean_path(root / '.controller.lock', root)
    with lock.open('a+b') as f:
        if os.fstat(f.fileno()).st_nlink != 1:
            raise BatchError('Hardlinked lock prohibited')
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        until = time.monotonic() + timeout
        while True:
            try:
                f.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise BatchError('Controller lock busy; no live lock was removed')
                time.sleep(.05)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def init(root, work, selection, desktop=None, controller='controller'):
    root, work = b.root_path(root, required=True), b.root_path(work)
    desk = b.root_path(desktop if desktop else b.desktop_folder())
    if b.inside(work, root) or b.inside(desk, root):
        raise BatchError('Working and delivery roots must be outside input')
    if not isinstance(controller, str) or not controller.strip():
        raise BatchError('Controller identity required')
    policy = b.read(POLICY); validate_policy(policy)
    rules_bytes = RULES.read_bytes(); rules = json.loads(rules_bytes.decode('utf-8-sig'))
    if type(rules.get('version')) is not int or not isinstance(rules.get('rules'), list):
        raise BatchError('Invalid rule snapshot')
    projects = selection.get('projects')
    if not isinstance(projects, list) or not projects:
        raise BatchError('Explicit nonempty selection required')
    rows, primaries = [], set()
    for i, item in enumerate(projects, 1):
        title = item['title']; sources = []
        if not isinstance(title, str) or not title.strip(): raise BatchError('Title required')
        for rel in item['source_files']:
            if Path(rel).is_absolute() or PureWindowsPath(rel).drive:
                raise BatchError('Relative source required')
            p = file_path(root / rel, root)
            if p.name.startswith('~$'): raise BatchError('Invalid source')
            if any(s['original_path'] == str(p) for s in sources): raise BatchError('Duplicate selected source')
            sources.append({'relative_path': str(p.relative_to(root)), 'original_path': str(p), 'sha256': b.sha(p)})
        rel = item['primary_source']
        if Path(rel).is_absolute() or PureWindowsPath(rel).drive: raise BatchError('Relative primary required')
        primary = file_path(root / rel, root)
        if primary.suffix.lower() != '.docx' or str(primary) not in [s['original_path'] for s in sources]:
            raise BatchError('Primary must be selected DOCX')
        key = os.path.normcase(str(primary))
        if key in primaries: raise BatchError('Duplicate primary')
        primaries.add(key)
        rows.append({'id': f'{i:03d}', 'title': title, 'primary_source': str(primary), 'sources': sources,
                     'status': 'pending', 'attempts': [], 'receipts': [], 'active_attempt': None})
    batch_id = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
    batch = work / batch_id; batch.mkdir(parents=True, exist_ok=False)
    (batch / 'rules.snapshot.json').write_bytes(rules_bytes)
    b.atomic(batch / 'policy.snapshot.json', policy)
    for case in rows:
        source_dir = batch / 'cases' / case['id'] / 'sources'; source_dir.mkdir(parents=True)
        for j, src in enumerate(case['sources'], 1):
            target = source_dir / (f'{j:03d}_' + b.safe(Path(src['original_path']).name))
            shutil.copyfile(src['original_path'], target)
            if b.sha(target) != src['sha256'] or b.sha(file_path(src['original_path'], root)) != src['sha256']:
                raise BatchError('Source changed while snapshotting')
            target.chmod(0o444); src['snapshot_path'] = str(target)
        case['source_digest'] = digest(case['sources'])
        b.atomic(source_dir / 'verification.json', {'case_id': case['id'], 'sources': case['sources'], 'source_digest': case['source_digest']})
    m = {'schema_version': 2, 'execution': 'rolling_parallel', 'batch_id': batch_id, 'batch_dir': str(batch),
         'input': str(root), 'desktop': str(desk), 'controller': controller,
         'policy': policy, 'rule_version': rules['version'], 'rules_sha256': hashlib.sha256(rules_bytes).hexdigest(),
         'evidence_limit': 'Controller-captured records, not independent model/server attestations; simulations are scheduling regressions only',
         'sequence': 0, 'peak_reserved_slots': 0, 'peak_bound_workers': 0, 'projects': rows}
    b.atomic(batch / 'manifest.json', m)
    return batch / 'manifest.json'

def load_manifest(path):
    m = b.read(file_path(path, Path(path).parent))
    if m.get('schema_version') != 2 or m.get('execution') != 'rolling_parallel':
        raise BatchError('Serial batch must use batch_tool.py; no implicit migration')
    root = b.root_path(m['batch_dir'], required=True)
    if file_path(path, root) != root / 'manifest.json': raise BatchError('Manifest location mismatch')
    validate_policy(m['policy'])
    if b.read(file_path(root / 'policy.snapshot.json', root)) != m['policy']:
        raise BatchError('Policy snapshot changed')
    rules = file_path(root / 'rules.snapshot.json', root)
    if b.sha(rules) != m['rules_sha256'] or b.read(rules).get('version') != m['rule_version']:
        raise BatchError('Rule snapshot changed')
    return m

def case_by_id(m, ident):
    c = next((c for c in m['projects'] if c['id'] == ident), None)
    if c is None: raise BatchError('Case not found')
    return c

def case_root(m, c):
    return b.clean_path(Path(m['batch_dir']) / 'cases' / c['id'], m['batch_dir'])

def verify_original(m, c):
    for s in c['sources']:
        if b.sha(file_path(s['original_path'], m['input'])) != s['sha256']:
            raise BatchError('Original input changed; defer this case without blocking others')

def quarantine_changed_input(m, c):
    try:
        verify_original(m, c)
    except (BatchError, OSError) as e:
        c['receipts'].append({'event': 'controller_input_change', 'previous_status': c['status'],
                              'verdict': 'needs_input', 'reason': str(e), 'source_digest': c['source_digest']})
        c['status'] = 'needs_input'; c.pop('passed_review_sha256', None)
        return True
    return False

def verify_case(m, c, original=False):
    root = case_root(m, c)
    if digest(c['sources']) != c['source_digest']: raise BatchError('Source manifest changed')
    expected = {'case_id': c['id'], 'sources': c['sources'], 'source_digest': c['source_digest']}
    if b.read(file_path(root / 'sources' / 'verification.json', root)) != expected:
        raise BatchError('Source verification snapshot changed')
    for s in c['sources']:
        if b.sha(file_path(s['snapshot_path'], root / 'sources')) != s['sha256']:
            raise BatchError('Source snapshot changed')
    if original: verify_original(m, c)
    for r in c['receipts']:
        if r.get('artifact') and b.sha(file_path(r['artifact'], root)) != r['artifact_sha256']:
            raise BatchError('Previously recorded artifact changed')
    for a in c['attempts']:
        expected_dir = root / 'stages' / a['id']
        if b.clean_path(a['directory'], root) != expected_dir:
            raise BatchError('Attempt directory belongs to another case')
        if a.get('context') != context(m, c, a):
            raise BatchError('Attempt provenance changed')
        if b.sha(file_path(expected_dir / 'reservation.json', expected_dir)) != a['reservation_sha256']:
            raise BatchError('Reservation evidence changed')
        if a.get('dispatch_evidence') and b.sha(file_path(a['dispatch_evidence'], expected_dir)) != a['dispatch_sha256']:
            raise BatchError('Dispatch evidence changed')
        if a.get('stop_evidence') and b.sha(file_path(a['stop_evidence'], expected_dir)) != a['stop_sha256']:
            raise BatchError('Worker stop evidence changed')
        if a['state'] == 'completed':
            r = b.read(file_path(expected_dir / 'receipt.json', expected_dir))
            if digest(r) != a['receipt_sha256'] or r not in c['receipts']:
                raise BatchError('Recorded receipt changed')

def attempt_by_id(c, ident):
    a = next((a for a in c['attempts'] if a['id'] == ident), None)
    if a is None or c['active_attempt'] != ident or a['state'] not in {'reserved', 'bound'}:
        raise BatchError('Inactive, cancelled, completed or foreign attempt')
    return a

def context(m, c, a):
    return {'batch_id': m['batch_id'], 'case_id': c['id'], 'stage': a['stage'], 'attempt': a['id'],
            'source_digest': c['source_digest'], 'rules_sha256': m['rules_sha256'],
            'input_artifact_sha256': a['input_artifact_sha256']}

def capacity_value(m, capacity):
    if type(capacity) is not int or not 1 <= capacity <= m['policy']['concurrency']['target_workers']:
        raise BatchError('Explicit actual capacity must be 1..target_workers; controller does not count as worker')

def plan_manifest(m, capacity):
    capacity_value(m, capacity)
    active = [c for c in m['projects'] if c['active_attempt']]
    if len(active) >= capacity:
        return {'action': 'wait', 'reason': 'All actual worker slots occupied', 'active_workers': len(active), 'capacity': capacity, 'ready': [], 'blocked': []}
    in_flight = sum(c['status'] not in TERMINAL and (c['status'] != 'pending' or c['active_attempt'] is not None) for c in m['projects'])
    drafts, followups, blocked = [], [], []
    for c in m['projects']:
        if c['status'] not in NEXT or c['active_attempt']: continue
        if c['status'] == 'pending' and in_flight >= m['policy']['concurrency']['max_in_flight_cases']: continue
        try:
            verify_case(m, c, original=True)
        except (BatchError, OSError) as e:
            blocked.append({'case_id': c['id'], 'reason': str(e)}); continue
        row = {'case_id': c['id'], 'stage': NEXT[c['status']]}
        (drafts if row['stage'] == 'draft' else followups).append(row)
    # Every waiting stage has a monotonic ready sequence: revised/revise stages
    # take precedence, but an older first review cannot be starved indefinitely.
    def priority(row):
        c = case_by_id(m, row['case_id'])
        age = m['sequence'] - c.get('ready_sequence', 0)
        urgent = row['stage'] == 'revise' or c['status'] == 'revised'
        overdue = age >= 2 * m['policy']['concurrency']['max_in_flight_cases']
        return (0 if overdue else 1,
                c.get('ready_sequence', 0) if overdue else (0 if urgent else 1),
                c.get('ready_sequence', 0), c['id'])
    followups.sort(key=priority)
    active_drafts = sum(attempt_by_id(c, c['active_attempt'])['stage'] == 'draft' for c in active)
    free = capacity - len(active)
    # One worker alternates available draft and follow-up work. Larger pools
    # reserve the last free position for drafting, while draining reviews first.
    prefer_draft = bool(drafts) and active_drafts < m['policy']['concurrency']['min_draft_slots'] and (
        (capacity == 1 and m.get('last_reserved_stage') != 'draft') or
        (capacity > 1 and free <= m['policy']['concurrency']['min_draft_slots'] - active_drafts))
    ready = (drafts + followups) if prefer_draft else (followups + drafts)
    return {'action': 'reserve' if ready else ('wait' if active else 'done' if all(c['status'] in TERMINAL for c in m['projects']) else 'blocked'),
            'active_workers': len(active), 'capacity': capacity, 'in_flight_cases': in_flight, 'ready': ready, 'blocked': blocked}

def plan(path, capacity):
    with locked(path): return plan_manifest(load_manifest(path), capacity)

def mutate(path, controller, operation):
    with locked(path):
        m = load_manifest(path)
        if controller != m['controller']: raise BatchError('Only the batch controller may write the manifest')
        if m.get('delivery_path'): raise BatchError('Delivered batch is closed')
        result = operation(m)
        m['sequence'] += 1; b.atomic(path, m)
        return result

def reserve(path, capacity, case_id=None, stage=None, controller='controller'):
    def apply(m):
        p = plan_manifest(m, capacity)
        if not p['ready']: raise BatchError(p.get('reason', p['action']))
        row = p['ready'][0]
        if case_id is not None and case_id != row['case_id']: raise BatchError('Reserve scheduler-selected case to preserve fairness')
        if stage is not None and stage != row['stage']: raise BatchError('Stage out of order')
        c = case_by_id(m, row['case_id']); verify_case(m, c, original=True)
        ident = uuid.uuid4().hex
        folder = case_root(m, c) / 'stages' / ident; folder.mkdir(parents=True, exist_ok=False)
        a = {'id': ident, 'stage': row['stage'], 'state': 'reserved', 'directory': str(folder),
             'input_artifact': c.get('artifact'), 'input_artifact_sha256': c.get('artifact_sha256'),
             'task_name': 'p_' + m['batch_id'].split('_')[-1] + '_' + c['id'] + '_' + row['stage'] + '_' + ident}
        c['attempts'].append(a); c['active_attempt'] = ident
        m['peak_reserved_slots'] = max(m.get('peak_reserved_slots', 0), sum(bool(row['active_attempt']) for row in m['projects']))
        m['last_reserved_stage'] = row['stage']
        a['context'] = context(m, c, a)
        b.atomic(folder / 'reservation.json', a)
        a['reservation_sha256'] = b.sha(folder / 'reservation.json')
        latest_review = next((r for r in reversed(c['receipts']) if r.get('stage') == 'review'), None)
        model = m['policy']['models'][row['stage']]
        receipt_template = dict(model, context=a['context'], stage=row['stage'], agent_id='<successful spawn result.task_name>')
        if row['stage'] in {'draft', 'revise'}:
            receipt_template.update(verdict='complete', artifact=str(folder / 'output.docx'), artifact_sha256='<SHA256 of output.docx>')
        else:
            receipt_template.update(verdict='<pass or fix_required>', reviewed_sha256=a['input_artifact_sha256'],
                                    checks={k: '<passed or concrete failure>' for k in sorted(b.CHECKS)}, findings=[],
                                    render={'artifact_sha256': a['input_artifact_sha256'], 'evidence': str(folder / 'render' / 'render.json'), 'evidence_sha256': '<SHA256 of render.json>'})
        return {'case_id': c['id'], 'attempt': copy.deepcopy(a), 'spawn_arguments': dict(m['policy']['models'][row['stage']], fork_turns='none', task_name=a['task_name']),
                'source_snapshots': copy.deepcopy(c['sources']), 'receipt_template': receipt_template,
                'worker_context': {'title': c['title'], 'context': a['context'], 'case_directory': str(case_root(m, c)),
                                   'writable_directory': str(folder), 'rules_snapshot': str(Path(m['batch_dir']) / 'rules.snapshot.json'),
                                   'primary_snapshot': next(s['snapshot_path'] for s in c['sources'] if s['original_path'] == c['primary_source']),
                                   'input_artifact': a['input_artifact'], 'input_artifact_sha256': a['input_artifact_sha256'],
                                   'latest_review': copy.deepcopy(latest_review),
                                   'receipt_path': str(folder / 'receipt.json'), 'dispatch_path': str(folder / 'dispatch.json'),
                                   'boundary': 'Read only source/rules/prior outputs; write only this attempt directory; never write manifest; render evidence does not substitute visual six-check acceptance'}}
    return mutate(path, controller, apply)

def bind(path, case_id, attempt, dispatch, controller='controller'):
    def apply(m):
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        if a['state'] != 'reserved': raise BatchError('Attempt already bound')
        evp = file_path(dispatch, a['directory']); ev = b.read(evp)
        expected = dict(m['policy']['models'][a['stage']], fork_turns='none', task_name=a['task_name'])
        args, result = ev.get('arguments'), ev.get('result')
        agent = result.get('task_name') if isinstance(result, dict) else None
        if (ev.get('tool') != 'collaboration.spawn_agent' or ev.get('context') != context(m, c, a)
                or not isinstance(args, dict) or any(args.get(k) != v for k, v in expected.items())
                or not isinstance(agent, str) or agent.split('/')[-1] != a['task_name']
                or any(result.get(k) for k in ('error', 'errors', 'isError')) or result.get('success') is False
                or result.get('status') in {'failed', 'error', 'rejected'}):
            raise BatchError('Dispatch model/high/none/task/context/result mismatch')
        if any(old.get('agent_id') == agent for row in m['projects'] for old in row['attempts']):
            raise BatchError('Worker dispatch already used')
        verify_case(m, c)
        a.update(state='bound', agent_id=agent, dispatch_evidence=str(evp), dispatch_sha256=b.sha(evp), dispatch_record=ev)
        m['peak_bound_workers'] = max(m.get('peak_bound_workers', 0), sum(old['state'] == 'bound' for row in m['projects'] for old in row['attempts']))
        return copy.deepcopy(a)
    return mutate(path, controller, apply)

def verify_render(m, c, a, render):
    if not isinstance(render, dict) or render.get('artifact_sha256') != a['input_artifact_sha256']:
        raise BatchError('Render must bind the current reviewed artifact SHA')
    if not isinstance(render.get('evidence'), str) or not isinstance(render.get('evidence_sha256'), str):
        raise BatchError('Canonical render evidence path and SHA required')
    evidence_path = file_path(render['evidence'], case_root(m, c))
    if evidence_path.name != 'render.json' or b.sha(evidence_path) != render.get('evidence_sha256'):
        raise BatchError('Canonical render.json evidence SHA mismatch')
    allowed = [Path(a['directory']), Path(a['input_artifact']).parent]
    if not any(b.inside(evidence_path, root) for root in allowed): raise BatchError('Render belongs to stale attempt')
    evidence = b.read(evidence_path)
    if not isinstance(evidence.get('pdf'), dict) or not isinstance(evidence.get('pages'), list):
        raise BatchError('Canonical PDF and complete page list required')
    entries = [evidence['pdf'], *evidence['pages']]
    for f in entries:
        if not isinstance(f, dict) or not isinstance(f.get('file'), str): raise BatchError('Render file entry required')
        p = file_path(evidence_path.parent / f['file'], evidence_path.parent)
        if p.parent != evidence_path.parent: raise BatchError('Render file must be directly in render directory')
    try:
        renderer.verify_render(evidence_path.parent, a['input_artifact'])
    except (ValueError, OSError, KeyError, TypeError) as e:
        raise BatchError('Canonical render verification failed: ' + str(e)) from e
    return copy.deepcopy(render)

def record(path, case_id, attempt, receipt, controller='controller'):
    def apply(m):
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        if a['state'] != 'bound': raise BatchError('Successful dispatch must be bound before recording')
        verify_case(m, c)
        if b.sha(file_path(a['dispatch_evidence'], a['directory'])) != a['dispatch_sha256']:
            raise BatchError('Dispatch evidence changed')
        if receipt.get('context') != context(m, c, a): raise BatchError('Cross-case, source, rules or attempt receipt mismatch')
        model = m['policy']['models'][a['stage']]
        if any(receipt.get(k) != v for k, v in model.items()) or receipt.get('agent_id') != a['agent_id'] or receipt.get('stage') != a['stage']:
            raise BatchError('Receipt model/high/worker/stage mismatch')
        r = copy.deepcopy(receipt); verdict = r.get('verdict')
        if verdict in {'failed', 'needs_input'}:
            if not isinstance(r.get('reason'), str) or not r['reason'].strip(): raise BatchError('Concrete failure reason required')
            c['status'] = verdict
        elif a['stage'] in {'draft', 'revise'}:
            if verdict not in {None, 'complete'}: raise BatchError('Invalid writing verdict')
            artifact = file_path(r['artifact'], a['directory'])
            if artifact.suffix.lower() != '.docx': raise BatchError('Output DOCX required')
            b.verify_docx(artifact); actual = b.sha(artifact)
            if r.get('artifact_sha256') != actual: raise BatchError('Artifact SHA required and must match')
            c.update(artifact=str(artifact), artifact_sha256=actual, status='drafted' if a['stage'] == 'draft' else 'revised')
            c.pop('passed_review_sha256', None)
            r['artifact'] = str(artifact)
        else:
            actual = b.sha(file_path(c['artifact'], case_root(m, c)))
            if r.get('reviewed_sha256') != actual or actual != c['artifact_sha256'] or actual != a['input_artifact_sha256']:
                raise BatchError('Stale review hash')
            if not isinstance(r.get('checks'), dict) or not isinstance(r.get('findings'), list): raise BatchError('Six checks and findings required')
            if verdict == 'pass':
                if any(r['checks'].get(k) != 'passed' for k in b.CHECKS) or any(not isinstance(f, dict) or f.get('status') not in {'resolved', 'closed'} for f in r['findings']):
                    raise BatchError('Incomplete six-check acceptance or unresolved finding')
                r['render'] = verify_render(m, c, a, r.get('render'))
                c.update(status='accepted', passed_review_sha256=actual)
            elif verdict == 'fix_required': c['status'] = 'fix_required'
            else: raise BatchError('Invalid review verdict')
        b.atomic(Path(a['directory']) / 'receipt.json', r)
        a.update(state='completed', receipt_sha256=digest(r)); c['receipts'].append(r)
        c['active_attempt'] = None; c['ready_sequence'] = m['sequence'] + 1
        quarantine_changed_input(m, c)
        return copy.deepcopy(c)
    return mutate(path, controller, apply)

def cancel(path, case_id, attempt, reason, stop_evidence=None, controller='controller'):
    def apply(m):
        if not isinstance(reason, str) or not reason.strip(): raise BatchError('Concrete cancellation reason required')
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        if a['state'] == 'bound':
            if stop_evidence is None: raise BatchError('Bound worker requires confirmed stop evidence before release')
            evp = file_path(stop_evidence, a['directory']); ev = b.read(evp)
            if (ev.get('tool') != 'collaboration.interrupt_agent' or ev.get('context') != context(m, c, a)
                    or ev.get('arguments') != {'target': a['agent_id']} or ev.get('confirmed_stopped') is not True
                    or not isinstance(ev.get('result'), dict) or any(ev['result'].get(k) for k in ('error', 'errors', 'isError'))
                    or ev['result'].get('success') is False
                    or ev['result'].get('status') in {'failed', 'error', 'rejected'}):
                raise BatchError('Confirmed worker stop evidence mismatch')
            a['stop_evidence'] = str(evp); a['stop_sha256'] = b.sha(evp)
        a.update(state='cancelled', reason=reason.strip()); c['active_attempt'] = None
        return copy.deepcopy(a)
    return mutate(path, controller, apply)

def defer(path, case_id, status, reason, controller='controller'):
    def apply(m):
        c = case_by_id(m, case_id)
        if c['active_attempt']: raise BatchError('Active attempt must be cancelled with confirmed stop before defer')
        if c['status'] in TERMINAL or status not in {'failed', 'needs_input'} or not isinstance(reason, str) or not reason.strip():
            raise BatchError('Unfinished case and concrete terminal reason required')
        c['receipts'].append({'event': 'controller_defer', 'previous_status': c['status'], 'reason': reason.strip(), 'verdict': status})
        c['status'] = status; return copy.deepcopy(c)
    return mutate(path, controller, apply)

def deliver(path, controller='controller'):
    with locked(path):
        m = load_manifest(path)
        if controller != m['controller']: raise BatchError('Only batch controller may deliver')
        if any(c['status'] not in TERMINAL or c['active_attempt'] for c in m['projects']): raise BatchError('Close all cases before consolidated delivery')
        quarantined = False
        for c in m['projects']:
            if c['status'] == 'accepted': quarantined = quarantine_changed_input(m, c) or quarantined
        if quarantined: b.atomic(path, m)
        for c in m['projects']:
            if c['status'] in {'accepted', 'delivered'}:
                verify_case(m, c)
                if b.sha(file_path(c['artifact'], case_root(m, c))) != c.get('passed_review_sha256'): raise BatchError('Artifact changed after acceptance')
                r = c['receipts'][-1]
                a = next(x for x in c['attempts'] if x['id'] == r['context']['attempt'])
                verify_render(m, c, a, r.get('render'))
        desk = b.root_path(m['desktop']); desk.mkdir(parents=True, exist_ok=True)
        dest = b.clean_path(desk / (m['policy']['delivery']['folder_prefix'] + '_' + m['batch_id']), desk)
        if not m.get('delivery_transaction_id'):
            m['delivery_transaction_id'] = uuid.uuid4().hex; b.atomic(path, m)
        final = copy.deepcopy(m); final['delivery_path'] = str(dest)
        for c in final['projects']:
            if c['status'] in {'accepted', 'delivered'}:
                c['status'] = 'delivered'
                c['delivered_artifact'] = str(dest / m['policy']['delivery']['accepted_subdir'] / (c['id'] + '_' + b.safe(c['title'])) / (b.safe(c['title']) + '.docx'))
        if dest.exists():
            if b.read(file_path(dest / '批次清单.json', dest)) != final: raise BatchError('Existing delivery transaction mismatch')
            for c in final['projects']:
                if c['status'] == 'delivered' and b.sha(file_path(c['delivered_artifact'], dest)) != c['passed_review_sha256']: raise BatchError('Delivered artifact changed')
            b.atomic(path, final); return str(dest)
        if m.get('delivery_path'): raise BatchError('Recorded delivery missing')
        staging = Path(tempfile.mkdtemp(prefix='.patent-' + m['batch_id'] + '-', dir=desk))
        try:
            lines = ['专利批次交付清单', '已完成稿经 GPT-6.1-sol/high 撰写、GPT-6-astra/high 最终六项复查。']
            for c in final['projects']:
                completed = c['status'] == 'delivered'
                folder = staging / m['policy']['delivery']['accepted_subdir' if completed else 'pending_subdir'] / (c['id'] + '_' + b.safe(c['title']))
                folder.mkdir(parents=True)
                if completed:
                    output = folder / (b.safe(c['title']) + '.docx'); shutil.copyfile(c['artifact'], output)
                    if b.sha(output) != c['passed_review_sha256']: raise BatchError('Copy verification failed')
                else:
                    (folder / '待处理说明.txt').write_text(c['receipts'][-1].get('reason', '尚未验收'), encoding='utf-8')
                lines.append(c['id'] + ' ' + c['title'] + '：' + c['status'])
            (staging / '交付清单.txt').write_text('\n'.join(lines), encoding='utf-8')
            b.atomic(staging / '批次清单.json', final)
            changed_during_copy = False
            for c in m['projects']:
                if c['status'] == 'accepted': changed_during_copy = quarantine_changed_input(m, c) or changed_during_copy
            if changed_during_copy:
                b.atomic(path, m)
                raise BatchError('Inputs changed during delivery; affected cases quarantined; retry delivers remaining cases')
            if dest.exists(): raise BatchError('Delivery destination created concurrently')
            os.rename(staging, dest); b.atomic(path, final); return str(dest)
        finally:
            if staging.exists(): shutil.rmtree(staging)

def summary(path):
    with locked(path):
        m = load_manifest(path)
        return {'batch_id': m['batch_id'], 'controller': m['controller'], 'delivery_path': m.get('delivery_path'),
                'counts': {s: sum(c['status'] == s for c in m['projects']) for s in sorted(set(c['status'] for c in m['projects']))},
                'active_workers': sum(bool(c['active_attempt']) for c in m['projects']),
                'peak_reserved_slots': m.get('peak_reserved_slots', 0), 'peak_bound_workers': m.get('peak_bound_workers', 0),
                'concurrency_evidence_scope': 'Ledger reservations and controller-captured successful dispatches only; not independent proof of simultaneous model execution',
                'cases': [{'id': c['id'], 'title': c['title'], 'status': c['status'], 'active_attempt': c['active_attempt']} for c in m['projects']]}

def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan'); s.add_argument('--input', required=True); s.add_argument('--out', required=True)
    s = sub.add_parser('init'); s.add_argument('--input', required=True); s.add_argument('--work-root', required=True); s.add_argument('--selection', required=True); s.add_argument('--desktop'); s.add_argument('--controller', required=True)
    for command in ('plan', 'reserve', 'bind', 'record', 'cancel', 'defer', 'deliver', 'summary'):
        s = sub.add_parser(command); s.add_argument('--manifest', required=True)
        if command not in {'plan', 'summary'}: s.add_argument('--controller', required=True)
        if command in {'plan', 'reserve'}: s.add_argument('--capacity', type=int, required=True)
        if command in {'bind', 'record', 'cancel', 'defer'}: s.add_argument('--case', required=True)
        if command in {'bind', 'record', 'cancel'}: s.add_argument('--attempt', required=True)
        if command == 'bind': s.add_argument('--dispatch', required=True)
        if command == 'record': s.add_argument('--receipt', required=True)
        if command == 'cancel': s.add_argument('--reason', required=True); s.add_argument('--stop-evidence')
        if command == 'defer': s.add_argument('--status', choices=['failed', 'needs_input'], required=True); s.add_argument('--reason', required=True)
    a = p.parse_args()
    try:
        if a.command == 'scan':
            output = Path(a.out).absolute(); b.root_path(output.parent)
            if b.inside(output, a.input) or (output.exists() and (b.linked(output) or output.stat().st_nlink != 1)): raise BatchError('Scan output must be independent and outside input')
            result = b.scan(a.input); b.atomic(output, result)
        elif a.command == 'init': result = str(init(a.input, a.work_root, b.read(a.selection), a.desktop, a.controller))
        elif a.command == 'plan': result = plan(a.manifest, a.capacity)
        elif a.command == 'reserve': result = reserve(a.manifest, a.capacity, controller=a.controller)
        elif a.command == 'bind': result = bind(a.manifest, a.case, a.attempt, a.dispatch, a.controller)
        elif a.command == 'record':
            m = load_manifest(a.manifest); c = case_by_id(m, a.case); attempt = attempt_by_id(c, a.attempt)
            result = record(a.manifest, a.case, a.attempt, b.read(file_path(a.receipt, attempt['directory'])), a.controller)
        elif a.command == 'cancel': result = cancel(a.manifest, a.case, a.attempt, a.reason, a.stop_evidence, a.controller)
        elif a.command == 'defer': result = defer(a.manifest, a.case, a.status, a.reason, a.controller)
        elif a.command == 'deliver': result = deliver(a.manifest, a.controller)
        else: result = summary(a.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (BatchError, OSError, ValueError, KeyError, TypeError) as e:
        print(f'并行队列拒绝：{e}', file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
