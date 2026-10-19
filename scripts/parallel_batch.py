#!/usr/bin/env python3
"""Persistent rolling queue. Dispatch evidence is controller evidence, not model attestation."""
from __future__ import annotations
import argparse, copy, hashlib, json, os, shutil, stat, sys, tempfile, time, uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
import batch_tool as b
import render_docx_checked as renderer

BatchError = b.BatchError
POLICY = Path(__file__).resolve().parent.parent / 'references' / 'batch-policy.json'
RULES = Path(__file__).resolve().parent.parent / 'references' / 'rules.json'
NEXT = {'pending': 'draft', 'drafted': 'review', 'revised': 'review', 'fix_required': 'revise'}
TERMINAL = b.TERMINAL
IMMEDIATE_STAGES = 'overlapped_pipeline_v29'
THREE_POOLS = 'separate_stage_pools_v35'
NONBLOCKING_POOLS = 'nonblocking_handoff_v36'


def nonblocking_pools(policy):
    return policy.get('pool_execution') == NONBLOCKING_POOLS


def immediate_stages(policy):
    return policy.get('stage_scheduling') in {IMMEDIATE_STAGES, THREE_POOLS}


class NoReadyWork(BatchError):
    def __init__(self, plan):
        self.plan = plan
        super().__init__(plan.get('reason', plan['action']))


class PreparationError(BatchError):
    def __init__(self, case_id, reason):
        self.case_id = case_id
        super().__init__('Case '+case_id+' preparation failed: '+reason)

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def file_path(path, root):
    p = b.clean_path(path, root)
    attributes = p.stat()
    if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
        raise BatchError('Regular independent file required; hardlinks prohibited')
    return p

def validate_policy(p):
    if not isinstance(p, dict) or p.get('schema_version') != 2 or p.get('execution') != 'rolling_parallel':
        raise BatchError('Parallel entry requires schema 2 rolling_parallel policy; serial batches use batch_tool.py')
    legacy = dict(p, schema_version=1, execution='serial_per_project')
    b.validate_policy(legacy)
    if p['delivery'].get('name_source') == 'input_folder' and p['delivery'] != {
            'name_source': 'input_folder', 'folder_suffix': '终稿',
            'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}:
        raise BatchError('Input-folder naming requires the exact immediate flat delivery policy')
    if not isinstance(p.get('review_mode', 'review_only'), str) or p.get('review_mode', 'review_only') not in {'review_only', 'review_fix_then_recheck'}:
        raise BatchError('Invalid review_mode policy')
    c = p.get('concurrency', {})
    if (set(c) != {'target_workers', 'max_in_flight_cases', 'min_draft_slots'}
            or any(type(v) is not int for v in c.values())
            or not 1 <= c['target_workers'] <= 64
            or not c['target_workers'] <= c['max_in_flight_cases'] <= 256
            or not 1 <= c['min_draft_slots'] <= c['target_workers']):
        raise BatchError('Invalid concurrency policy')
    transports = p.get('dispatch_transports', ['collaboration'])
    if (not isinstance(transports, list) or not transports or any(not isinstance(t, str) for t in transports)
            or len(transports) != len(set(transports))
            or any(t not in {'collaboration', 'codex_cli'} for t in transports)):
        raise BatchError('Invalid dispatch transports')
    if p.get('worker_preparation') not in (None, 'frozen_stage_packet_v1'):
        raise BatchError('Invalid worker preparation mode')
    if p.get('output_contract') not in (None,'wording_title_v18'):
        raise BatchError('Invalid output contract')
    if p.get('output_contract') and (p.get('worker_preparation')!='frozen_stage_packet_v1' or
                                    p['delivery'].get('layout')!='flat_final_files'):
        raise BatchError('Output contract requires frozen preparation and flat delivery')
    if p.get('prose_contract') not in (None,'clean_prose_v19'):
        raise BatchError('Invalid quotation/bracket contract')
    if p.get('prose_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Quotation/bracket contract requires the frozen final output contract')
    if p.get('benefit_contract') not in (None,'numbered_benefits_v21'):
        raise BatchError('Invalid benefit numbering contract')
    if p.get('benefit_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Benefit numbering contract requires the frozen final output contract')
    if p.get('layout_contract') not in (None,'manual_flow_abstract_v22'):
        raise BatchError('Invalid page flow/abstract figure contract')
    if p.get('layout_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Page flow/abstract figure contract requires the frozen final output contract')
    if p.get('formula_contract') not in (None,'source_math_prose_v23'):
        raise BatchError('Invalid formula/prose contract')
    if p.get('formula_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Formula/prose contract requires the frozen final output contract')
    if p.get('spacing_contract') not in (None,'no_literal_spaces_v26'):
        raise BatchError('Invalid content spacing contract')
    if p.get('spacing_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Content spacing contract requires the frozen final output contract')
    if p.get('invention_contract') not in (None,'claims_mirror_v27'):
        raise BatchError('Invalid invention contract')
    if p.get('invention_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Invention contract requires the frozen final output contract')
    if p.get('figure_description_contract') not in (None,'named_figures_v32'):
        raise BatchError('Invalid figure description contract')
    if p.get('figure_description_contract') and p.get('output_contract')!='wording_title_v18':
        raise BatchError('Figure description contract requires the frozen final output contract')
    if p.get('figure_caption_contract') not in (None,'number_only_v38'):
        raise BatchError('Invalid final figure caption contract')
    if p.get('figure_caption_contract') and (p.get('output_contract')!='wording_title_v18' or
                                            p.get('layout_contract')!='manual_flow_abstract_v22'):
        raise BatchError('Final figure caption contract requires frozen output and page flow contracts')
    if p.get('execution_optimization') not in (None, 'lean_execution_v24'):
        raise BatchError('Invalid execution optimization contract')
    if p.get('execution_optimization') and (p.get('worker_preparation') != 'frozen_stage_packet_v1'
                                           or p['delivery'].get('layout') != 'flat_final_files'):
        raise BatchError('Lean execution requires frozen preparation and flat delivery')
    if p.get('post_review_optimization') not in (None, 'single_closeout_v25'):
        raise BatchError('Invalid post-review optimization contract')
    if p.get('post_review_optimization') and p.get('execution_optimization') != 'lean_execution_v24':
        raise BatchError('Single closeout requires lean execution')
    if p.get('review_reuse') not in (None, 'verified_scope_v1'):
        raise BatchError('Invalid incremental review contract')
    if p.get('review_reuse') and (p.get('worker_preparation') != 'frozen_stage_packet_v1'
                                 or p.get('review_mode') != 'review_only'
                                 or p.get('execution_optimization') != 'lean_execution_v24'):
        raise BatchError('Incremental review requires lean frozen preparation and independent read-only review')
    if p.get('stage_scheduling') not in (None, IMMEDIATE_STAGES, THREE_POOLS):
        raise BatchError('Invalid stage scheduling policy')
    if p.get('stage_scheduling') == THREE_POOLS:
        pools = p.get('stage_pools')
        if (not isinstance(pools, dict) or set(pools) != {'draft', 'review', 'revise'}
                or any(type(v) is not int or v < 1 for v in pools.values())
                or sum(pools.values()) != c['target_workers']):
            raise BatchError('Stage pools require positive draft/review/revise limits summing to target_workers')
        if p.get('review_mode') != 'review_only':
            raise BatchError('Separate stage pools require review_only and an independent revise stage')
    elif 'stage_pools' in p:
        raise BatchError('Stage pools require the separate-stage scheduling policy')
    if p.get('stage_scheduling') and p['delivery'].get('timing') != 'after_each_acceptance':
        raise BatchError('Immediate stages require per-case delivery')
    if p.get('pool_execution') not in (None, NONBLOCKING_POOLS):
        raise BatchError('Invalid pool execution policy')
    if nonblocking_pools(p) and (p.get('stage_scheduling') != THREE_POOLS
                                or p['delivery'].get('layout') != 'flat_final_files'
                                or p.get('worker_preparation') != 'frozen_stage_packet_v1'):
        raise BatchError('Nonblocking pools require prepared three-stage flat delivery')

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
    if not isinstance(controller, str) or not controller.strip():
        raise BatchError('Controller identity required')
    policy = b.read(POLICY); validate_policy(policy)
    batch_id = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
    if policy['delivery'].get('name_source') == 'input_folder':
        import flat_delivery
        flat_delivery.destination({'policy': policy, 'input': str(root), 'desktop': str(desk), 'batch_id': batch_id})
    if b.inside(work, root) or b.inside(desk, root):
        raise BatchError('Working and delivery roots must be outside input')
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
    if policy.get('output_contract'):
        import flat_delivery
        flat_delivery.names({'policy':policy,'projects':rows})
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
    if policy.get('worker_preparation') == 'frozen_stage_packet_v1':
        from stage_bundle import freeze
        m['skill_snapshot'] = freeze(Path(__file__).resolve().parent.parent, batch, rules_bytes, policy)
    b.atomic(batch / 'manifest.json', m)
    return batch / 'manifest.json'

def load_manifest(path):
    expected = os.path.normcase(os.path.abspath(path))
    for retry in range(8):
        try:
            return _load_manifest(path)
        except OSError as exc:
            # ReplaceFileW can briefly move the old name before installing the
            # new one. Retry the entire validation, only for this manifest and
            # these transient Windows errors; never skip any identity checks.
            if (os.name != 'nt' or getattr(exc, 'winerror', None) not in {2, 32, 33}
                    or not exc.filename or os.path.normcase(os.path.abspath(exc.filename)) != expected
                    or retry == 7):
                raise
            time.sleep(.005)


def _load_manifest(path):
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
    if m.get('skill_snapshot') or m['policy'].get('worker_preparation'):
        from stage_bundle import verify
        try:
            verify(m, full=False)
        except (ValueError, OSError) as exc:
            raise BatchError(str(exc)) from exc
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
    if c.get('recovery_evidence'):
        recovery = file_path(c['recovery_evidence'], root)
        if b.sha(recovery) != c.get('recovery_evidence_sha256'):
            raise BatchError('Draft recovery evidence changed')
        imported = b.read(recovery)
        if (imported not in c['receipts'] or imported.get('acceptance_inherited') is not False
                or c.get('recovered_cli_session_ids') != [imported.get('cli_session_id')]):
            raise BatchError('Draft recovery provenance mismatch')
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

def plan_manifest(m, capacity, first_only=False):
    capacity_value(m, capacity)
    active = [c for c in m['projects'] if c['active_attempt']]
    immediate = immediate_stages(m['policy'])
    pools = m['policy'].get('stage_pools') if m['policy'].get('stage_scheduling') == THREE_POOLS else None
    active_by_stage = {stage: sum(attempt_by_id(c, c['active_attempt'])['stage'] == stage for c in active)
                       for stage in ('draft', 'review', 'revise')}
    pool_status = ({stage: {'active': active_by_stage[stage], 'limit': limit} for stage, limit in pools.items()}
                   if pools else {})
    if pools and any(active_by_stage[stage] > limit for stage, limit in pools.items()):
        raise BatchError('Active stages exceed the frozen pool limits')
    accepted = [c['id'] for c in m['projects'] if c['status'] == 'accepted' and not c['active_attempt']]
    if immediate and accepted and not nonblocking_pools(m['policy']):
        return {'action': 'deliver', 'reason': 'Publish accepted cases before reserving another stage',
                'active_workers': len(active), 'capacity': capacity, 'ready': [], 'blocked': [],
                'accepted_case_ids': accepted, 'pool_status': pool_status}
    if len(active) >= capacity:
        return {'action': 'wait', 'reason': 'All actual worker slots occupied', 'active_workers': len(active), 'capacity': capacity, 'ready': [], 'blocked': [], 'pool_status': pool_status}
    in_flight = sum(c['status'] not in TERMINAL and (c['status'] != 'pending' or c['active_attempt'] is not None) for c in m['projects'])
    drafts, followups, blocked = [], [], []
    for c in m['projects']:
        if c['status'] not in NEXT or c['active_attempt']: continue
        if c['status'] == 'pending' and in_flight >= m['policy']['concurrency']['max_in_flight_cases']: continue
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
    active_drafts = active_by_stage['draft']
    free = capacity - len(active)
    # One worker alternates available draft and follow-up work. Larger pools
    # reserve the last free position for drafting, while draining reviews first.
    if pools:
        # Hard per-stage limits; idle stages cannot lend slots beyond those
        # limits. Among ready stages balance occupancy against their shares.
        # Stable ties retain aged/urgent follow-up order before new drafting.
        ordered = [row for row in followups + drafts if active_by_stage[row['stage']] < pools[row['stage']]]
        if capacity > 1:
            ordered.sort(key=lambda row: active_by_stage[row['stage']] / pools[row['stage']])
    elif immediate:
        # Two work-conserving lanes: fill ready review/repair work to its share,
        # then keep drafting in parallel. A lane with no ready work lends its
        # slots; never preempt running attempts. With one slot finish the case.
        draft_target = max(1, capacity // 2)
        review_target = capacity - draft_target
        active_followups = len(active) - active_drafts
        prefer_draft = (capacity > 1 and bool(drafts) and active_drafts < draft_target
                        and active_followups >= review_target)
    else:
        prefer_draft = bool(drafts) and active_drafts < m['policy']['concurrency']['min_draft_slots'] and (
            (capacity == 1 and m.get('last_reserved_stage') != 'draft') or
            (capacity > 1 and free <= m['policy']['concurrency']['min_draft_slots'] - active_drafts))
    if not pools:
        ordered = (drafts + followups) if prefer_draft else (followups + drafts)
    ready = []
    for row in ordered:
        try:
            verify_case(m, case_by_id(m, row['case_id']), original=True)
        except (BatchError, OSError) as e:
            blocked.append({'case_id': row['case_id'], 'reason': str(e)}); continue
        ready.append(row)
        if first_only:
            break  # Reserve needs one verified case, not a re-hash of the entire queue.
    return {'action': 'reserve' if ready else ('wait' if active else 'done' if all(c['status'] in TERMINAL for c in m['projects']) else 'blocked'),
            'active_workers': len(active), 'capacity': capacity, 'in_flight_cases': in_flight, 'ready': ready, 'blocked': blocked,
            'pool_status': pool_status}

def plan(path, capacity):
    with locked(path): return plan_manifest(load_manifest(path), capacity)

def mutate(path, controller, operation):
    with locked(path):
        m = load_manifest(path)
        if controller != m['controller']: raise BatchError('Only the batch controller may write the manifest')
        if m.get('delivery_closed') or (m.get('delivery_path') and m.get('delivery_mode') not in {'incremental_v1', 'flat_final_files_v1'}):
            raise BatchError('Delivered batch is closed')
        result = operation(m)
        m['sequence'] += 1; b.atomic(path, m)
        return result


def case_mutate(path, controller, case_id, operation):
    """Validate one case off the batch lock; merge without losing peer progress."""
    m = load_manifest(path)
    if not nonblocking_pools(m['policy']):
        return mutate(path, controller, operation)
    if controller != m['controller'] or m.get('delivery_closed'):
        raise BatchError('Controller mismatch or closed batch')
    with locked(case_root(m, case_by_id(m, case_id)) / 'case-operation.json'):
        m = load_manifest(path)
        before = copy.deepcopy(case_by_id(m, case_id))
        result = operation(m)
        after = case_by_id(m, case_id)
        with locked(path):
            current = load_manifest(path)
            if (current['controller'] != controller or current.get('delivery_closed')
                    or current['policy'] != m['policy'] or current['rules_sha256'] != m['rules_sha256']
                    or case_by_id(current, case_id) != before):
                raise BatchError('Case changed during independent validation; no stale merge')
            sessions = {a['cli_session_id'] for c in current['projects'] if c['id'] != case_id
                        for a in c['attempts'] if a.get('cli_session_id')}
            sessions.update(s for c in current['projects'] if c['id'] != case_id
                            for s in c.get('recovered_cli_session_ids', []))
            if any(a.get('cli_session_id') in sessions for a in after['attempts']):
                raise BatchError('CLI session already used by another case')
            agents = {a['agent_id'] for c in current['projects'] if c['id'] != case_id
                      for a in c['attempts'] if a.get('agent_id')}
            if any(a.get('agent_id') in agents for a in after['attempts']):
                raise BatchError('Worker dispatch already used by another case')
            if after.get('ready_sequence') != before.get('ready_sequence'):
                after['ready_sequence'] = current['sequence'] + 1
            target = case_by_id(current, case_id)
            target.clear(); target.update(copy.deepcopy(after))
            current['peak_bound_workers'] = max(current.get('peak_bound_workers', 0),
                sum(a['state'] == 'bound' for c in current['projects'] for a in c['attempts']))
            current['sequence'] += 1
            b.atomic(path, current)
            return copy.deepcopy(target) if result.get('id') == case_id else result


def prepare_reserved(path, case_id, attempt, controller='controller'):
    """Local I/O only. No model launch and no write to the shared manifest."""
    from prepare_handoff import prepare as prepare_data, SOURCE_ERRORS
    m = load_manifest(path)
    if controller != m['controller'] or not nonblocking_pools(m['policy']):
        raise BatchError('Deferred preparation is not authorized')
    c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
    if a.get('preparation_state') != 'pending' or a['state'] != 'reserved':
        raise BatchError('Attempt is not awaiting preparation')
    verify_case(m, c, original=True)
    try:
        prepared, seal = prepare_data(m, c, a)
    except SOURCE_ERRORS as exc:
        raise PreparationError(case_id, 'Invalid DOCX source: '+str(exc)) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PreparationError(case_id, str(exc)) from exc
    return {'case_id': case_id, 'attempt': attempt, 'context': a['context'],
            'prepared': prepared, 'seal': seal, 'source_navigation': c.get('source_navigation', {})}


def finish_preparation(path, result, controller='controller'):
    def apply(m):
        from prepare_handoff import verify_prepared
        c = case_by_id(m, result['case_id']); a = attempt_by_id(c, result['attempt'])
        if (not nonblocking_pools(m['policy']) or a.get('preparation_state') != 'pending'
                or a['state'] != 'reserved' or result['context'] != context(m, c, a)):
            raise BatchError('Stale deferred preparation result')
        verify_case(m, c, original=True)
        old = c.get('source_navigation', {})
        if any(result['source_navigation'].get(k) != v for k, v in old.items()):
            raise BatchError('Prepared navigation changed existing evidence')
        c['source_navigation'] = copy.deepcopy(result['source_navigation'])
        a['prepared_handoff'] = copy.deepcopy(result['seal'])
        verify_prepared(m, c, a)
        if b.read(file_path(result['seal']['path'], a['directory'])) != result['prepared']:
            raise BatchError('Preparation payload differs from sealed handoff')
        a['preparation_state'] = 'ready'
        return reservation_payload(m, c, a, result['prepared'])
    return case_mutate(path, controller, result['case_id'], apply)

def reserve(path, capacity, case_id=None, stage=None, controller='controller', prepare=False, transport='collaboration', defer_preparation=False):
    def apply(m):
        if transport not in m['policy'].get('dispatch_transports', ['collaboration']):
            raise BatchError('Transport not authorized in this frozen policy')
        if defer_preparation and not nonblocking_pools(m['policy']):
            raise BatchError('Deferred preparation requires nonblocking pools')
        p = plan_manifest(m, capacity, first_only=True)
        if not p['ready']: raise NoReadyWork(p)
        row = p['ready'][0]
        if case_id is not None and case_id != row['case_id']: raise BatchError('Reserve scheduler-selected case to preserve fairness')
        if stage is not None and stage != row['stage']: raise BatchError('Stage out of order')
        c = case_by_id(m, row['case_id'])  # Verified once under this same controller lock.
        ident = uuid.uuid4().hex
        folder = case_root(m, c) / 'stages' / ident; folder.mkdir(parents=True, exist_ok=False)
        a = {'id': ident, 'stage': row['stage'], 'state': 'reserved', 'directory': str(folder),
             'input_artifact': c.get('artifact'), 'input_artifact_sha256': c.get('artifact_sha256'),
             'task_name': 'p_' + m['batch_id'].split('_')[-1] + '_' + c['id'] + '_' + row['stage'] + '_' + ident}
        if transport == 'codex_cli':
            a['transport'] = transport
        c['attempts'].append(a); c['active_attempt'] = ident
        m['peak_reserved_slots'] = max(m.get('peak_reserved_slots', 0), sum(bool(row['active_attempt']) for row in m['projects']))
        m['last_reserved_stage'] = row['stage']
        a['context'] = context(m, c, a)
        prepared = None
        if defer_preparation:
            a['preparation_state'] = 'pending'
        elif prepare or m['policy'].get('worker_preparation') == 'frozen_stage_packet_v1':
            from prepare_handoff import prepare as prepare_data, SOURCE_ERRORS
            try:
                prepared, a['prepared_handoff'] = prepare_data(m, c, a)
            except SOURCE_ERRORS as exc:
                raise PreparationError(c['id'], 'Invalid DOCX source: '+str(exc)) from exc
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise PreparationError(c['id'], str(exc)) from exc
        b.atomic(folder / 'reservation.json', a)
        a['reservation_sha256'] = b.sha(folder / 'reservation.json')
        return reservation_payload(m, c, a, prepared)
    return mutate(path, controller, apply)


def reservation_payload(m, c, a, prepared=None):
    row = {"stage": a["stage"]}
    folder = Path(a["directory"])
    latest_review = next((r for r in reversed(c['receipts']) if r.get('stage') == 'review'), None)
    model = m['policy']['models'][row['stage']]
    receipt_template = dict(model, context=a['context'], stage=row['stage'], agent_id='<successful spawn result.task_name>')
    if row['stage'] in {'draft', 'revise'}:
        receipt_template.update(verdict='complete', artifact=str(folder / 'output.docx'), artifact_sha256='<SHA256 of output.docx>')
    else:
        receipt_template.update(verdict='<pass or fix_required>', reviewed_sha256=a['input_artifact_sha256'],
                                checks={k: '<passed or concrete failure>' for k in sorted(b.CHECKS)}, findings=[],
                                render={'artifact_sha256': a['input_artifact_sha256'], 'evidence': str(folder / 'render' / 'render.json'), 'evidence_sha256': '<SHA256 of render.json>'})
        if m['policy'].get('review_mode') == 'review_fix_then_recheck':
            receipt_template['verdict'] = '<pass, revised, fix_required or needs_input>'
    return {'case_id': c['id'], 'attempt': copy.deepcopy(a), 'spawn_arguments': dict(m['policy']['models'][row['stage']], fork_turns='none', task_name=a['task_name']),
            'source_snapshots': copy.deepcopy(c['sources']), 'receipt_template': receipt_template,
            'worker_context': {'title': c['title'], 'context': a['context'], 'case_directory': str(case_root(m, c)),
                               'writable_directory': str(folder), 'rules_snapshot': str(Path(m['batch_dir']) / 'rules.snapshot.json'),
                               'primary_snapshot': next(s['snapshot_path'] for s in c['sources'] if s['original_path'] == c['primary_source']),
                               'input_artifact': a['input_artifact'], 'input_artifact_sha256': a['input_artifact_sha256'],
                               'review_mode': m['policy'].get('review_mode', 'review_only'),
                               'review_reuse': m['policy'].get('review_reuse'),
                               'prepared': prepared,
                               'latest_review': copy.deepcopy(latest_review),
                               'receipt_path': str(folder / 'receipt.json'), 'dispatch_path': str(folder / 'dispatch.json'),
                               'boundary': 'Read only source/rules/prior outputs; write only this attempt directory; never write manifest; render evidence does not substitute visual six-check acceptance'}}


def bind(path, case_id, attempt, dispatch, controller='controller'):
    def apply(m):
        if attempt_by_id(case_by_id(m, case_id), attempt).get('preparation_state') == 'pending':
            raise BatchError('Preparation must finish before model dispatch')
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        if a['state'] != 'reserved': raise BatchError('Attempt already bound')
        if a.get('prepared_handoff'):
            from prepare_handoff import verify_prepared
            try:
                verify_prepared(m, c, a)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise BatchError('Invalid prepared handoff: ' + str(exc)) from exc
        evp = file_path(dispatch, a['directory']); ev = b.read(evp)
        if a.get('transport') == 'codex_cli':
            from cli_evidence import verify_dispatch
            try:
                agent = verify_dispatch(ev, a, m['policy']['models'][a['stage']])
            except (ValueError, OSError, KeyError, TypeError) as exc:
                raise BatchError('Invalid CLI dispatch: ' + str(exc)) from exc
        else:
            expected = dict(m['policy']['models'][a['stage']], fork_turns='none', task_name=a['task_name'])
            args, result = ev.get('arguments'), ev.get('result')
            agent = result.get('task_name') if isinstance(result, dict) else None
            if (ev.get('tool') != 'collaboration.spawn_agent' or ev.get('context') != context(m, c, a)
                    or not isinstance(args, dict) or any(args.get(k) != v for k, v in expected.items())
                    or not isinstance(agent, str) or agent.split('/')[-1] != a['task_name']
                    or any(result.get(k) for k in ('error', 'errors', 'isError')) or result.get('success') is False
                    or result.get('status') in {'failed', 'error', 'rejected'}):
                raise BatchError('Dispatch model/effort/none/task/context/result mismatch')
        if any(old.get('agent_id') == agent for row in m['projects'] for old in row['attempts']):
            raise BatchError('Worker dispatch already used')
        verify_case(m, c)
        a.update(state='bound', agent_id=agent, dispatch_evidence=str(evp), dispatch_sha256=b.sha(evp), dispatch_record=ev)
        m['peak_bound_workers'] = max(m.get('peak_bound_workers', 0), sum(old['state'] == 'bound' for row in m['projects'] for old in row['attempts']))
        return copy.deepcopy(a)
    return case_mutate(path, controller, case_id, apply)

def render_validation(m, c, a, render):
    return {'schema_version': 1, 'context': context(m,c,a), 'input': a['input_artifact'],
            'render': copy.deepcopy(render), 'renderer_sha256': b.sha(renderer.__file__),
            'controller_sha256': b.sha(__file__)}


def verify_render(m, c, a, render, *, validation=None):
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
        if validation is not None:
            if (m['policy'].get('post_review_optimization') != 'single_closeout_v25'
                    or validation != render_validation(m,c,a,render)):
                raise BatchError('Controller render validation binding changed')
            renderer.verify_render(evidence_path.parent, a['input_artifact'], check_format=False,
                                   expected_evidence_sha256=render['evidence_sha256'])
        else:
            renderer.verify_render(evidence_path.parent, a['input_artifact'],
                                   expected_evidence_sha256=render['evidence_sha256'])
    except (ValueError, OSError, KeyError, TypeError) as e:
        raise BatchError('Canonical render verification failed: ' + str(e)) from e
    return copy.deepcopy(render)

def record(path, case_id, attempt, receipt, controller='controller'):
    def apply(m):
        if m.get('skill_snapshot'):
            from stage_bundle import verify
            try:
                verify(m)
            except (ValueError, OSError) as exc:
                raise BatchError(str(exc)) from exc
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        if a['state'] != 'bound': raise BatchError('Successful dispatch must be bound before recording')
        verify_case(m, c)
        if b.sha(file_path(a['dispatch_evidence'], a['directory'])) != a['dispatch_sha256']:
            raise BatchError('Dispatch evidence changed')
        if a.get('transport') == 'codex_cli':
            from cli_evidence import verify_completion
            try:
                execution = verify_completion(a, a['dispatch_record'])
            except (ValueError, OSError, KeyError, TypeError) as exc:
                raise BatchError('Invalid CLI completion: ' + str(exc)) from exc
            if execution['receipt'] != receipt:
                raise BatchError('CLI final message differs from submitted receipt')
            if any(old.get('cli_session_id') == execution['session_id'] for row in m['projects'] for old in row['attempts']):
                raise BatchError('CLI session already used; independent stage session required')
            if any(execution['session_id'] in row.get('recovered_cli_session_ids', []) for row in m['projects']):
                raise BatchError('Recovered draft session cannot provide independent review')
            a['cli_session_id'] = execution['session_id']
        if receipt.get('context') != context(m, c, a): raise BatchError('Cross-case, source, rules or attempt receipt mismatch')
        model = m['policy']['models'][a['stage']]
        if any(receipt.get(k) != v for k, v in model.items()) or receipt.get('agent_id') != a['agent_id'] or receipt.get('stage') != a['stage']:
            raise BatchError('Receipt model/effort/worker/stage mismatch')
        if m['policy'].get('execution_optimization') == 'lean_execution_v24':
            from worker_receipt import precheck_receipt
            precheck_receipt(m, c, a, receipt)
        r = copy.deepcopy(receipt); verdict = r.get('verdict')
        # This is controller evidence, never a worker-supplied shortcut.
        if m['policy'].get('post_review_optimization') == 'single_closeout_v25':
            r.pop('render_validation', None)
        r.pop('review_reuse_validation', None)
        if m['policy'].get('review_reuse'):
            import incremental_review
            reuse_validation = incremental_review.validate(m, c, a, r)
            if reuse_validation is not None:
                r['review_reuse_validation'] = reuse_validation
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
            if verdict != 'revised' and any(k in r for k in ('artifact', 'artifact_sha256')):
                raise BatchError('Review acceptance cannot also submit a changed artifact; use revised then a new review')
            if verdict == 'pass':
                if any(r['checks'].get(k) != 'passed' for k in b.CHECKS) or any(not isinstance(f, dict) or f.get('status') not in {'resolved', 'closed'} for f in r['findings']):
                    raise BatchError('Incomplete six-check acceptance or unresolved finding')
                r['render'] = verify_render(m, c, a, r.get('render'))
                if m['policy'].get('post_review_optimization') == 'single_closeout_v25':
                    r['render_validation'] = render_validation(m,c,a,r['render'])
                if m['policy'].get('output_contract'):
                    import output_contract
                    r['document_contract'] = output_contract.verify(m,c,a,r)
                c.update(status='accepted', passed_review_sha256=actual)
            elif verdict == 'revised':
                if m['policy'].get('review_mode', 'review_only') != 'review_fix_then_recheck':
                    raise BatchError('This frozen policy does not allow review-stage revision')
                if any(not isinstance(r['checks'].get(k), str) or not r['checks'][k].strip() for k in b.CHECKS):
                    raise BatchError('Review revision must record all six checks, including not_run where applicable')
                if not r['findings'] or any(
                        not isinstance(f, dict) or f.get('status') not in {'resolved', 'closed'}
                        or any(not isinstance(f.get(k), str) or not f[k].strip() for k in ('location', 'correction_evidence'))
                        for f in r['findings']):
                    raise BatchError('Review revision needs closed findings with location and correction_evidence')
                artifact = file_path(r['artifact'], a['directory'])
                if artifact.suffix.lower() != '.docx': raise BatchError('Revised output DOCX required')
                b.verify_docx(artifact); revised_sha = b.sha(artifact)
                if r.get('artifact_sha256') != revised_sha or revised_sha == actual:
                    raise BatchError('Review revision SHA must match new bytes and differ from reviewed input')
                # The original input must remain unchanged throughout validation.
                if b.sha(file_path(a['input_artifact'], case_root(m, c))) != actual:
                    raise BatchError('Reviewed input changed during revision validation')
                r['artifact'] = str(artifact)
                c.update(artifact=str(artifact), artifact_sha256=revised_sha, status='revised')
                c.pop('passed_review_sha256', None)
            elif verdict == 'fix_required': c['status'] = 'fix_required'
            else: raise BatchError('Invalid review verdict')
        b.atomic(Path(a['directory']) / 'receipt.json', r)
        a.update(state='completed', receipt_sha256=digest(r)); c['receipts'].append(r)
        c['active_attempt'] = None; c['ready_sequence'] = m['sequence'] + 1
        quarantine_changed_input(m, c)
        return copy.deepcopy(c)
    return case_mutate(path, controller, case_id, apply)

def cancel(path, case_id, attempt, reason, stop_evidence=None, controller='controller'):
    def apply(m):
        if not isinstance(reason, str) or not reason.strip(): raise BatchError('Concrete cancellation reason required')
        c = case_by_id(m, case_id); a = attempt_by_id(c, attempt)
        launched_cli = a.get('transport') == 'codex_cli' and (Path(a['directory'])/'cli-launch-intent.json').exists()
        if a['state'] == 'bound' or launched_cli:
            if stop_evidence is None: raise BatchError('Bound worker requires confirmed stop evidence before release')
            evp = file_path(stop_evidence, a['directory']); ev = b.read(evp)
            if a.get('transport') == 'codex_cli':
                from cli_evidence import verify_stop
                dispatch = a.get('dispatch_record') or b.read(file_path(Path(a['directory'])/'dispatch.json', a['directory']))
                try:
                    verify_stop(ev, a, dispatch)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    raise BatchError('Invalid CLI stop: ' + str(exc)) from exc
            elif (ev.get('tool') != 'collaboration.interrupt_agent' or ev.get('context') != context(m, c, a)
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

def deliver(path, controller='controller', *, audit_history=False):
    m = load_manifest(path)
    if nonblocking_pools(m['policy']):
        from independent_delivery import deliver as publish
        return publish(path, controller, audit_history=audit_history)
    import incremental_delivery as incremental
    import flat_delivery as flat
    with locked(path):
        m = load_manifest(path)
        if controller != m['controller']: raise BatchError('Only batch controller may deliver')
        legacy = not flat.enabled(m) and m.get('delivery_mode') != incremental.MODE and all(c['status'] in TERMINAL and not c['active_attempt'] for c in m['projects'])
        if not legacy:
            return deliver_snapshot(path, m, audit_history=audit_history)
    return _deliver_legacy(path, controller)


def deliver_snapshot(path, m, *, audit_history=False, write_metadata=True):
    import incremental_delivery as incremental
    import flat_delivery as flat
    publisher = flat if flat.enabled(m) else incremental
    options = {'write_metadata': write_metadata} if flat.enabled(m) else {}
    def verify(c):
        original = case_by_id(m, c['id'])
        if c['status'] == 'accepted' and quarantine_changed_input(m, original):
            b.atomic(path, m)
            if m['policy'].get('post_review_optimization') == 'single_closeout_v25':
                raise flat.QuarantinedInput(c['id'])
            raise BatchError('Input changed during delivery; case quarantined; retry delivers remaining cases')
        verify_case(m, c)
        if c.get('active_attempt'): raise BatchError('Accepted case has active attempt')
        if b.sha(file_path(c['artifact'], case_root(m, c))) != c.get('passed_review_sha256'):
            raise BatchError('Artifact changed after acceptance')
        r = c['receipts'][-1]
        a = next(x for x in c['attempts'] if x['id'] == r['context']['attempt'])
        validation = (r.get('render_validation') if c['status']=='accepted'
                      and m['policy'].get('post_review_optimization')=='single_closeout_v25' else None)
        verify_render(m, c, a, r.get('render'), validation=validation)
        if m['policy'].get('output_contract'):
            import output_contract
            if output_contract.verify(m,c,a,r) != r.get('document_contract'):
                raise BatchError('Final document title/wording contract changed after acceptance')
    recovered = publisher.recover_published_cases(m, verify)
    for c in recovered:
        event = {'event': 'controller_delivery_recovery', 'case_id': c['id'],
                 'artifact_sha256': c['passed_review_sha256'], 'original_inputs_changed': False}
        try: verify_original(m, c)
        except (BatchError, OSError) as exc:
            event.update(original_inputs_changed=True, reason=str(exc))
        m.setdefault('delivery_recovery_events', []).append(event)
    quarantined = False
    if m['policy'].get('post_review_optimization') != 'single_closeout_v25':
        for c in m['projects']:
            if c['status'] == 'accepted': quarantined = quarantine_changed_input(m, c) or quarantined
    if quarantined or recovered: b.atomic(path, m)
    if audit_history and flat.enabled(m):
        return publisher.deliver(path, m, verify, force_history_audit=True, **options)
    return publisher.deliver(path, m, verify, **options)


def _deliver_legacy(path, controller='controller'):
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
            lines = ['专利批次交付清单', f"已完成稿经 {b.model_label(m['policy'], 'draft')} 撰写、{b.model_label(m['policy'], 'review')} 最终六项复查。"]
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
                'delivery_mode': m.get('delivery_mode'), 'delivery_closed': m.get('delivery_closed', bool(m.get('delivery_path'))),
                'counts': {s: sum(c['status'] == s for c in m['projects']) for s in sorted(set(c['status'] for c in m['projects']))},
                'active_workers': sum(bool(c['active_attempt']) for c in m['projects']),
                'peak_reserved_slots': m.get('peak_reserved_slots', 0), 'peak_bound_workers': m.get('peak_bound_workers', 0),
                'concurrency_evidence_scope': 'Ledger reservations and controller-captured successful dispatches only; not independent proof of simultaneous model execution',
                'cases': [{'id': c['id'], 'title': c['title'], 'status': c['status'], 'active_attempt': c['active_attempt']} for c in m['projects']]}

def compact_result(command, result, manifest):
    """Only shorten the CLI reply; durable receipts and acceptance stay intact."""
    if command in {'record', 'defer'}:
        return {k: result.get(k) for k in ('id', 'title', 'status', 'artifact', 'artifact_sha256', 'active_attempt')} | {'manifest': str(manifest)}
    if command in {'bind', 'cancel'}:
        return {k: result.get(k) for k in ('id', 'stage', 'state', 'agent_id', 'directory')} | {'manifest': str(manifest)}
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan'); s.add_argument('--input', required=True); s.add_argument('--out', required=True)
    s = sub.add_parser('init'); s.add_argument('--input', required=True); s.add_argument('--work-root', required=True); s.add_argument('--selection', required=True); s.add_argument('--desktop'); s.add_argument('--controller', required=True)
    for command in ('plan', 'reserve', 'bind', 'record', 'cancel', 'defer', 'deliver', 'summary'):
        s = sub.add_parser(command); s.add_argument('--manifest', required=True)
        s.add_argument('--compact', action='store_true', help='Short CLI reply; complete evidence stays in the manifest and receipt files')
        if command not in {'plan', 'summary'}: s.add_argument('--controller', required=True)
        if command in {'plan', 'reserve'}: s.add_argument('--capacity', type=int, required=True)
        if command == 'reserve':
            s.add_argument('--prepare', action='store_true', help='Prepare verified case cache paths and active frozen rules before dispatch')
            s.add_argument('--transport', choices=['collaboration', 'codex_cli'], default='collaboration')
        if command in {'bind', 'record', 'cancel', 'defer'}: s.add_argument('--case', required=True)
        if command in {'bind', 'record', 'cancel'}: s.add_argument('--attempt', required=True)
        if command == 'bind': s.add_argument('--dispatch', required=True)
        if command == 'record': s.add_argument('--receipt', required=True)
        if command == 'cancel': s.add_argument('--reason', required=True); s.add_argument('--stop-evidence')
        if command == 'defer': s.add_argument('--status', choices=['failed', 'needs_input'], required=True); s.add_argument('--reason', required=True)
    a = p.parse_args()
    try:
        if getattr(a, 'manifest', None):
            from stage_bundle import redirect_cli
            redirected = redirect_cli(a.manifest, 'parallel_batch.py', sys.argv[1:])
            if redirected is not None:
                return redirected
        if a.command == 'scan':
            output = Path(a.out).absolute(); b.root_path(output.parent)
            if b.inside(output, a.input) or (output.exists() and (b.linked(output) or output.stat().st_nlink != 1)): raise BatchError('Scan output must be independent and outside input')
            result = b.scan(a.input); b.atomic(output, result)
        elif a.command == 'init': result = str(init(a.input, a.work_root, b.read(a.selection), a.desktop, a.controller))
        elif a.command == 'plan': result = plan(a.manifest, a.capacity)
        elif a.command == 'reserve': result = reserve(a.manifest, a.capacity, controller=a.controller, prepare=a.prepare, transport=a.transport)
        elif a.command == 'bind': result = bind(a.manifest, a.case, a.attempt, a.dispatch, a.controller)
        elif a.command == 'record':
            m = load_manifest(a.manifest); c = case_by_id(m, a.case); attempt = attempt_by_id(c, a.attempt)
            result = record(a.manifest, a.case, a.attempt, b.read(file_path(a.receipt, attempt['directory'])), a.controller)
        elif a.command == 'cancel': result = cancel(a.manifest, a.case, a.attempt, a.reason, a.stop_evidence, a.controller)
        elif a.command == 'defer': result = defer(a.manifest, a.case, a.status, a.reason, a.controller)
        elif a.command == 'deliver': result = deliver(a.manifest, a.controller)
        else: result = summary(a.manifest)
        if getattr(a, 'compact', False): result = compact_result(a.command, result, a.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (BatchError, OSError, ValueError, KeyError, TypeError) as e:
        print(f'并行队列拒绝：{e}', file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
