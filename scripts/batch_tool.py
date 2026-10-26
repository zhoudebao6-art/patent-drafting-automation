#!/usr/bin/env python3
"""Serial batch ledger. The controller invokes models; dispatch logs are not attestations."""
from __future__ import annotations
import argparse, copy, ctypes, hashlib, json, os, re, shutil, stat, sys, tempfile, time, uuid, zipfile
from datetime import datetime
from pathlib import Path, PureWindowsPath
from xml.etree import ElementTree as ET

class BatchError(ValueError): pass
TERMINAL = {'accepted', 'needs_input', 'failed', 'delivered'}
CHECKS = {'source_support', 'antecedents', 'claims_alignment', 'formulas', 'figures', 'rendered_pages'}
POLICY = Path(__file__).resolve().parent.parent / 'references' / 'batch-policy.json'
RULES = Path(__file__).resolve().parent.parent / 'references' / 'rules.json'

def shared_reader(path):
    """A reader sees one complete version without blocking atomic replacement."""
    if os.name != 'nt':
        return Path(path).open('r', encoding='utf-8-sig')
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]; close.restype = ctypes.c_int
    # GENERIC_READ; FILE_SHARE_READ | WRITE | DELETE; OPEN_EXISTING.
    for retry in range(8):
        handle = create(str(Path(path)), 0x80000000, 7, None, 3, 0x80, None)
        if handle != ctypes.c_void_p(-1).value:
            break
        error = ctypes.get_last_error()
        if error not in {32, 33} or retry == 7:
            exc = ctypes.WinError(error); exc.filename = str(path)
            raise exc
        time.sleep(.005)  # A replacing handle briefly owns the new file exclusively.
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(fd, 'r', encoding='utf-8-sig')
    except BaseException:
        os.close(fd)
        raise


def read(path):
    with shared_reader(path) as stream:
        return json.load(stream)
def atomic(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.batch-', suffix='.tmp')
    backup = None
    committed = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        if os.name == 'nt' and path.exists():
            # MoveFileEx (os.replace) can reject an open destination even with
            # shared DELETE. ReplaceFile supports these snapshot readers and
            # preserves the existing file's ACLs; never ignore merge errors.
            backup = path.with_name('.batch-previous-'+uuid.uuid4().hex+'.tmp')
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            replace = kernel.ReplaceFileW
            replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
            replace.restype = ctypes.c_int
            for retry in range(8):
                if replace(str(path), str(name), str(backup), 0, None, None):
                    break
                error = ctypes.get_last_error()
                if error not in {32, 33} or retry == 7:
                    # 1176/1177 can partially move a replacement. Never retry
                    # those or permission errors; preserve both versions.
                    exc = ctypes.WinError(error); exc.filename = str(path); exc.filename2 = str(name)
                    raise exc
                time.sleep(.005)
        else:
            os.replace(name, path)
        committed = True
    finally:
        if backup is None and os.path.exists(name): os.unlink(name)
        if committed and backup is not None and backup.exists():
            try: backup.unlink()
            except OSError: pass  # A reader may still hold this obsolete version.
def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''): h.update(block)
    return h.hexdigest()
def _comparison_path(path):
    value = Path(path).resolve().as_posix()
    # Windows realpath can retain the extended prefix on only one operand
    # during ReplaceFile. Compare equivalent DOS/UNC spellings; keep other
    # device namespaces distinct and leave actual file paths unchanged.
    if os.name == "nt":
        if value[:8].lower() == "//?/unc/": value = "//" + value[8:]
        elif re.match(r"^//\?/[A-Za-z]:/", value): value = value[4:]
    return Path(value)

def inside(path, root):
    try: _comparison_path(path).relative_to(_comparison_path(root)); return True
    except ValueError: return False
def linked(path):
    s = Path(path).lstat()
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s, 'st_file_attributes', 0) & 0x400)
def clean_path(path, root):
    p, r = Path(path).absolute(), Path(root).resolve()
    if not inside(p, r): raise BatchError('Path escapes allowed directory')
    for part in [p, *p.parents]:
        if (part.exists() or part.is_symlink()) and linked(part): raise BatchError('Links and reparse points are prohibited')
        if part == r: break
    # All components were checked for links above. Keep the requested name:
    # Windows handle-based resolve() can follow a concurrently replaced file
    # to its temporary backup name, which is not a stable future read target.
    return Path(os.path.abspath(p))
def root_path(path, required=False):
    # Inspect the original spelling before resolve() can hide junctions or links.
    p = Path(path).absolute()
    for part in [p, *p.parents]:
        if (part.exists() or part.is_symlink()) and linked(part): raise BatchError('Links and reparse points are prohibited')
    if required and not p.is_dir(): raise BatchError('Input directory does not exist')
    if p.exists() and not p.is_dir(): raise BatchError('Directory required')
    return p.resolve()
def safe(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:100]
    if not name: name = '项目'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name): name = '_' + name
    return name

def desktop_folder():
    if os.name != 'nt': raise BatchError('Use --desktop on non-Windows hosts')
    class GUID(ctypes.Structure):
        _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16), ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]
    raw = uuid.UUID('b4bfcc3a-db2c-424c-b029-7fe99a87c641').bytes_le
    guid = GUID.from_buffer_copy(raw); out = ctypes.c_wchar_p()
    call = ctypes.windll.shell32.SHGetKnownFolderPath
    call.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    if call(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0: raise BatchError('Cannot resolve Desktop Known Folder')
    try: return Path(out.value)
    finally: ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
def scan(root):
    root = root_path(root, required=True); candidates, pending = [], []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not linked(Path(base)/d))
        for name in sorted(files):
            p = Path(base)/name
            if name.startswith('~$') or linked(p): continue
            ext = p.suffix.lower()
            if ext not in {'.docx', '.pdf', '.doc', '.zip', '.rar'}: continue
            row = {'relative_path': str(p.relative_to(root)), 'original_path': str(p.resolve()), 'sha256': sha(p), 'requires_explicit_selection': True}
            archived = any(re.search(r'定稿|归档|档案|范例|案例|已完成', x) for x in p.relative_to(root).parts)
            row['classification'] = 'reference_or_archive' if archived else ('docx_candidate' if ext == '.docx' else 'needs_preprocessing')
            (candidates if ext == '.docx' and not archived else pending).append(row)
    return {'input': str(root), 'candidates': candidates, 'pending': pending, 'projects': []}
def validate_policy(p, *, legacy_snapshot=False):
    if not isinstance(p, dict): raise BatchError('Invalid model policy')
    marker = p.get('model_policy')
    if marker not in (None, 'sol_review_xhigh_v20'): raise BatchError('Unsupported model policy')
    reviewer = ('gpt-6.1-sol', 'xhigh') if marker else ('gpt-6-astra', 'high')
    expected = {'draft': ('gpt-6.1-sol', 'high'), 'review': reviewer, 'revise': reviewer}
    if p.get('schema_version') != 1: raise BatchError('Unsupported policy')
    if p.get('execution') != 'serial_per_project' or p.get('fallback_model_allowed') is not False: raise BatchError('Invalid execution or fallback policy')
    models = p.get('models')
    if not isinstance(models, dict): raise BatchError('Invalid model policy')
    for stage, (model, effort) in expected.items():
        actual = models.get(stage)
        # Only a verified version-2 batch may retain its explicit historical
        # draft null. init never enables this compatibility path.
        if legacy_snapshot and marker is None and stage == 'draft' and actual == {'model': model, 'reasoning_effort': None}: continue
        if actual != {'model': model, 'reasoning_effort': effort}: raise BatchError('Invalid model policy')
    delivery = p.get('delivery')
    expected_delivery = {'folder_prefix': '专利撰写完成', 'accepted_subdir': '已完成', 'pending_subdir': '待处理'}
    flat_delivery = {'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}
    input_folder_delivery = {'name_source': 'input_folder', 'folder_suffix': '终稿', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}
    if not isinstance(delivery, dict) or delivery not in (expected_delivery, dict(expected_delivery, timing='after_each_acceptance'), flat_delivery, input_folder_delivery):
        raise BatchError('Invalid delivery policy')
def model_label(policy, stage):
    """Describe the frozen stage policy, including historical unspecified effort."""
    config = policy['models'][stage]
    name = config['model'].replace('gpt-', 'GPT-', 1)
    effort = config['reasoning_effort']
    return name+'/'+effort if effort is not None else name+'（旧批次冻结策略未指定推理强度）'

def init(root, work, selection, desktop=None):
    root, work = root_path(root, required=True), root_path(work)
    desk = root_path(desktop if desktop else desktop_folder())
    policy = read(POLICY)
    if isinstance(policy, dict) and policy.get('execution') == 'rolling_parallel':
        raise BatchError('New rolling_parallel batches must use parallel_batch.py init; existing serial manifests remain supported here')
    validate_policy(policy)
    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
    if policy['delivery'].get('name_source') == 'input_folder':
        import flat_delivery
        flat_delivery.destination({'policy': policy, 'input': str(root), 'desktop': str(desk), 'batch_id': batch_id})
    if inside(work, root) or inside(desk, root): raise BatchError('Working and delivery roots must be outside input')
    rules_bytes = RULES.read_bytes(); rules = json.loads(rules_bytes.decode('utf-8-sig'))
    if not isinstance(rules.get('version'), int) or not isinstance(rules.get('rules'), list): raise BatchError('Invalid rule snapshot')
    projects = selection.get('projects')
    if not isinstance(projects, list) or not projects: raise BatchError('Explicit nonempty selection required')
    rows, primaries = [], set()
    for i, item in enumerate(projects, 1):
        title = item['title']
        if not isinstance(title, str) or not title.strip(): raise BatchError('Title required')
        sources = []
        for rel in item['source_files']:
            if Path(rel).is_absolute() or PureWindowsPath(rel).drive: raise BatchError('Relative source required')
            p = clean_path(root/rel, root)
            if not p.is_file() or p.name.startswith('~$'): raise BatchError('Invalid source')
            sources.append({'relative_path': str(p.relative_to(root)), 'original_path': str(p), 'sha256': sha(p)})
        if Path(item['primary_source']).is_absolute() or PureWindowsPath(item['primary_source']).drive: raise BatchError('Relative primary required')
        primary = clean_path(root/item['primary_source'], root)
        if primary.suffix.lower() != '.docx' or str(primary) not in [s['original_path'] for s in sources]: raise BatchError('Primary must be selected DOCX')
        key = os.path.normcase(str(primary))
        if key in primaries: raise BatchError('Duplicate primary')
        primaries.add(key)
        rows.append({'id': f'{i:03d}', 'title': title, 'primary_source': str(primary), 'sources': sources, 'status': 'pending', 'receipts': []})
    batch = work/batch_id; batch.mkdir(parents=True, exist_ok=False)
    (batch/'rules.snapshot.json').write_bytes(rules_bytes)
    m = {'schema_version': 1, 'batch_id': batch_id, 'batch_dir': str(batch), 'input': str(root), 'desktop': str(desk), 'policy': policy, 'rule_version': rules['version'], 'rules_sha256': hashlib.sha256(rules_bytes).hexdigest(), 'evidence_limit': 'Controller-captured dispatch record; not independent proof of model execution or server attestation', 'projects': rows}
    atomic(batch/'policy.snapshot.json', policy); atomic(batch/'manifest.json', m)
    return batch/'manifest.json'
def verify_inputs(m, cases=None):
    cases = m['projects'] if cases is None else cases
    if not cases: return
    root_path(m['input'], required=True)
    for case in cases:
        for src in case['sources']:
            p = clean_path(src['original_path'], m['input'])
            if sha(p) != src['sha256']: raise BatchError('Input changed')
def load_manifest(path):
    m = read(path)
    root_path(m['batch_dir'], required=True)
    if clean_path(path, m['batch_dir']) != Path(m['batch_dir'])/'manifest.json': raise BatchError('Manifest location mismatch')
    if read(clean_path(Path(m['batch_dir'])/'policy.snapshot.json', m['batch_dir'])) != m['policy']: raise BatchError('Policy snapshot changed')
    snapshot = clean_path(Path(m['batch_dir'])/'rules.snapshot.json', m['batch_dir'])
    if sha(snapshot) != m.get('rules_sha256') or read(snapshot).get('version') != m.get('rule_version'): raise BatchError('Rule snapshot changed')
    validate_policy(m['policy'], legacy_snapshot=m.get('rule_version') == 2)
    return m
def verify_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None or '[Content_Types].xml' not in z.namelist(): raise BatchError('Invalid DOCX package')
            if ET.fromstring(z.read('word/document.xml')).tag != '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document': raise BatchError('Invalid DOCX document root')
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError) as e:
        raise BatchError('Unreadable DOCX package') from e
def current_case(m):
    return next((c for c in m['projects'] if c['status'] not in TERMINAL), None)
def next_case(m, *, verify_history=True):
    c = current_case(m)
    if c:
        verify_inputs(m, [c])
        if verify_history and c.get('retry_origin'): verify_retry_origin(m, c)
        return {'case': c, 'next_stage': {'pending': 'draft', 'drafted': 'review', 'revised': 'review', 'fix_required': 'revise'}[c['status']]}
    return {'case': None, 'next_stage': None}
def defer(path, case_id, status, reason):
    """Controller event only: never claims a model dispatch or successful review."""
    m = load_manifest(path)
    c = apply_defer(m, case_id, status, reason)
    atomic(path, m); return c
def apply_defer(m, case_id, status, reason):
    c = current_case(m)
    if not c or c['id'] != case_id: raise BatchError('Must defer earliest unfinished project')
    if status not in {'failed', 'needs_input'} or not isinstance(reason, str) or not reason.strip(): raise BatchError('Terminal status and concrete reason required')
    c['receipts'].append({'event': 'controller_defer', 'previous_status': c['status'], 'verdict': status, 'reason': reason.strip()})
    c['status'] = status; return c
def used_agents(c):
    return sorted({r['agent_id'] for r in c['receipts'] if r.get('agent_id')} | set(c.get('retry_origin', {}).get('source_agent_ids', [])))
def record(path, case_id, receipt):
    m = load_manifest(path)
    c = apply_record(m, case_id, receipt)
    atomic(path, m); return c
def apply_record(m, case_id, receipt, *, verify_history=True):
    current = next_case(m, verify_history=verify_history)
    if not current['case'] or current['case']['id'] != case_id: raise BatchError('Must process earliest unfinished project serially')
    c = current['case']; stage = receipt.get('stage')
    if stage != current['next_stage']: raise BatchError('Stage out of order')
    expected = m['policy']['models'][stage]
    if receipt.get('model') != expected['model'] or 'reasoning_effort' not in receipt or receipt.get('reasoning_effort') != expected['reasoning_effort']: raise BatchError('Wrong model or effort')
    if not receipt.get('agent_id') or not receipt.get('dispatch_evidence'): raise BatchError('Dispatch evidence required')
    evidence_path = clean_path(receipt['dispatch_evidence'], m['batch_dir'])
    evidence = read(evidence_path); args = evidence.get('arguments', {}); result = evidence.get('result', {})
    if not isinstance(args, dict) or not isinstance(result, dict): raise BatchError('Invalid dispatch evidence')
    task = args.get('task_name')
    if (evidence.get('tool') != 'collaboration.spawn_agent' or args.get('fork_turns') != 'none'
            # A historical null policy meant the call omitted the override;
            # do not require controllers to fabricate a missing tool argument.
            or args.get('model') != expected['model'] or args.get('reasoning_effort') != expected['reasoning_effort']
            or not isinstance(task, str) or not re.fullmatch(r'[a-z0-9_]+', task)
            or not isinstance(receipt['agent_id'], str) or receipt['agent_id'].split('/')[-1] != task
            or result.get('task_name') != receipt['agent_id']
            or any(result.get(k) for k in ('error', 'errors', 'isError')) or result.get('success') is False
            or result.get('status') in {'failed', 'error', 'rejected'}
            or evidence.get('context') != {'batch_id': m['batch_id'], 'case_id': case_id, 'stage': stage}): raise BatchError('Dispatch evidence mismatch')
    if any(receipt['agent_id'] in used_agents(row) for row in m['projects']): raise BatchError('Dispatch already used by a stage')
    receipt = dict(receipt); receipt['dispatch_evidence'] = str(evidence_path); receipt['dispatch_evidence_sha256'] = sha(evidence_path); receipt['dispatch_record'] = evidence
    verdict = receipt.get('verdict')
    if verdict in {'failed', 'needs_input'}:
        if not isinstance(receipt.get('reason'), str) or not receipt['reason'].strip(): raise BatchError('Failure reason required')
        c['status'] = verdict
    elif stage in {'draft', 'revise'}:
        if verdict not in {None, 'complete'}: raise BatchError('Invalid writing verdict')
        artifact = clean_path(receipt['artifact'], m['batch_dir'])
        if artifact.suffix.lower() != '.docx' or not artifact.is_file() or inside(artifact, m['input']): raise BatchError('Artifact must be batch DOCX')
        if any(Path(s['original_path']).exists() and os.path.samefile(artifact, s['original_path']) for row in m['projects'] for s in row['sources']): raise BatchError('Source cannot be output')
        for row in m['projects']:
            previous = row['receipts'] + ([row['retry_origin']] if row.get('retry_origin') else [])
            for old in previous:
                if old.get('artifact_sha256'):
                    prior = clean_path(old['artifact'], m['batch_dir'])
                    if os.path.samefile(artifact, prior): raise BatchError('Each writing stage must save a separate artifact')
                    if row is c and sha(prior) != old['artifact_sha256']: raise BatchError('Previously recorded artifact changed')
        verify_docx(artifact)
        c['artifact'] = str(artifact); c['artifact_sha256'] = sha(artifact); c['status'] = 'drafted' if stage == 'draft' else 'revised'
        receipt['artifact'] = str(artifact); receipt['artifact_sha256'] = c['artifact_sha256']
        c.pop('passed_review_sha256', None)
    else:
        actual = sha(clean_path(c['artifact'], m['batch_dir']))
        if receipt.get('reviewed_sha256') != actual or actual != c['artifact_sha256']: raise BatchError('Stale review hash')
        if not isinstance(receipt.get('findings'), list) or not isinstance(receipt.get('checks'), dict): raise BatchError('Review findings and checks required')
        if verdict == 'pass':
            if any(receipt['checks'].get(k) != 'passed' for k in CHECKS) or any(not isinstance(f, dict) or f.get('status') not in {'resolved', 'closed'} for f in receipt['findings']): raise BatchError('Incomplete review or unresolved finding')
            c['status'] = 'accepted'; c['passed_review_sha256'] = actual
        elif verdict == 'fix_required': c['status'] = 'fix_required'
        else: raise BatchError('Invalid review verdict')
    c['receipts'].append(receipt); return c

def retry_event(origin):
    return {k: origin[k] for k in ('source_batch_id', 'source_case_id', 'source_manifest_sha256', 'reason', 'resume_status')} | {'event': 'controller_retry'}

def verify_retry_origin(m, c, seen=()):
    """Validate inherited state without representing old calls as new dispatches."""
    if m['batch_id'] in seen or len(seen) >= 32: raise BatchError('Retry lineage cycle or depth limit')
    origin = c['retry_origin']; path = Path(origin['source_manifest'])
    if sha(path) != origin['source_manifest_sha256']: raise BatchError('Retry source manifest changed')
    prior = load_manifest(path)
    if prior['batch_id'] != origin['source_batch_id'] or prior['batch_id'] == m['batch_id']: raise BatchError('Retry source batch mismatch')
    old = next((x for x in prior['projects'] if x['id'] == origin['source_case_id']), None)
    if old is None: raise BatchError('Retry source case missing')
    state = recoverable_state(prior, old, (*seen, m['batch_id']))
    if (m['input'] != prior['input'] or m['policy'] != prior['policy']
            or m['rules_sha256'] != prior['rules_sha256'] or m['rule_version'] != prior['rule_version']
            or any(c[k] != old[k] for k in ('title', 'sources', 'primary_source'))
            or origin['source_receipts'] != old['receipts'] or origin['source_status'] != old['status']
            or origin['source_agent_ids'] != used_agents(old)
            or origin['resume_status'] != state['status']): raise BatchError('Retry inherited state mismatch')
    if not isinstance(origin['reason'], str) or not origin['reason'].strip(): raise BatchError('Retry reason required')
    event = retry_event(origin)
    if not c['receipts'] or c['receipts'][0] != event: raise BatchError('Retry controller event mismatch')
    if state.get('artifact'):
        artifact = clean_path(origin['artifact'], m['batch_dir'])
        if origin.get('artifact_sha256') != state['artifact_sha256'] or sha(artifact) != state['artifact_sha256']: raise BatchError('Inherited artifact changed')
        verify_docx(artifact)
        state.update(artifact=str(artifact), artifact_sha256=state['artifact_sha256'])
    elif origin.get('artifact') or origin.get('artifact_sha256'): raise BatchError('Unexpected inherited artifact')
    state.update(id=c['id'], receipts=[event], retry_origin=copy.deepcopy(origin))
    return state

def recoverable_state(m, c, seen=()):
    """Replay recorded transitions and recover only the state before the final block."""
    if m['batch_id'] in seen or len(seen) >= 32: raise BatchError('Retry lineage cycle or depth limit')
    if not m.get('delivery_path') or any(x['status'] not in TERMINAL for x in m['projects']): raise BatchError('Deliver the closed source batch before retry')
    if c['status'] not in {'needs_input', 'failed'}: raise BatchError('Only failed or needs_input cases can be retried')
    verify_inputs(m, [c])
    state = {k: copy.deepcopy(c[k]) for k in ('id', 'title', 'primary_source', 'sources')}
    state.update(status='pending', receipts=[])
    start = 0
    if c.get('retry_origin'):
        state = verify_retry_origin(m, c, seen); start = 1
    replay = dict(m, projects=[state]); before = None
    for receipt in c['receipts'][start:]:
        before = copy.deepcopy(state)
        if receipt.get('event') == 'controller_defer':
            if receipt.get('previous_status') != state['status']: raise BatchError('Historical defer state mismatch')
            apply_defer(replay, c['id'], receipt['verdict'], receipt['reason'])
            if state['receipts'][-1] != receipt: raise BatchError('Historical defer receipt changed')
        elif receipt.get('event'):
            raise BatchError('Unexpected historical controller event')
        else:
            apply_record(replay, c['id'], receipt, verify_history=False)
            if state['receipts'][-1] != receipt: raise BatchError('Historical dispatch or artifact evidence changed')
    if before is None or before['status'] in TERMINAL: raise BatchError('No recoverable blocked stage')
    for key in ('status', 'artifact', 'artifact_sha256', 'passed_review_sha256'):
        if state.get(key) != c.get(key): raise BatchError('Historical state does not match receipts')
    # Carry no old receipt or passed flag into the new batch's stage history.
    before['receipts'] = []; before.pop('retry_origin', None); before.pop('passed_review_sha256', None)
    return before

def retry(path, case_id, reason, work=None, desktop=None):
    if not isinstance(reason, str) or not reason.strip(): raise BatchError('Concrete retry reason required')
    source_hash = sha(path); m = load_manifest(path)
    c = next((x for x in m['projects'] if x['id'] == case_id), None)
    if c is None: raise BatchError('Retry case not found')
    state = recoverable_state(m, c)
    if state['status'] == 'pending':
        # Historical unspecified draft effort may describe inherited work,
        # but must never become the policy for a fresh drafting invocation.
        validate_policy(m['policy'])
    work = root_path(work if work else Path(m['batch_dir']).parent)
    desk = root_path(desktop if desktop else m['desktop'])
    if inside(work, m['input']) or inside(desk, m['input']): raise BatchError('Working and delivery roots must be outside input')
    if any(inside(p, r) for p in (work, desk) for r in (m['batch_dir'], m['delivery_path'])): raise BatchError('Retry cannot modify source batch or delivery')
    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
    if m['policy']['delivery'].get('name_source') == 'input_folder':
        import flat_delivery
        flat_delivery.destination(dict(m, batch_id=batch_id, desktop=str(desk)))
    batch = work/batch_id
    # Publish manifest last. An interrupted copy leaves no runnable partial batch.
    batch.mkdir(parents=True, exist_ok=False)
    origin = {'source_manifest': str(Path(path).resolve()), 'source_manifest_sha256': source_hash,
              'source_batch_id': m['batch_id'], 'source_case_id': c['id'], 'source_status': c['status'],
              'source_receipts': copy.deepcopy(c['receipts']), 'source_agent_ids': used_agents(c),
              'reason': reason.strip(), 'resume_status': state['status']}
    if state.get('artifact'):
        target = batch/'inherited.docx'; shutil.copyfile(state['artifact'], target)
        if sha(target) != state['artifact_sha256']: raise BatchError('Inherited copy verification failed')
        state['artifact'] = str(target); origin.update(artifact=str(target), artifact_sha256=state['artifact_sha256'])
    state.update(id='001', retry_origin=origin, receipts=[retry_event(origin)])
    result = {k: copy.deepcopy(m[k]) for k in ('schema_version', 'input', 'policy', 'rule_version', 'rules_sha256', 'evidence_limit')}
    result.update(batch_id=batch_id, batch_dir=str(batch), desktop=str(desk), projects=[state])
    shutil.copyfile(Path(m['batch_dir'])/'rules.snapshot.json', batch/'rules.snapshot.json')
    shutil.copyfile(Path(m['batch_dir'])/'policy.snapshot.json', batch/'policy.snapshot.json')
    if sha(path) != source_hash: raise BatchError('Source batch changed during retry')
    verify_retry_origin(result, state)
    atomic(batch/'manifest.json', result)
    return batch/'manifest.json'

def deliver(path):
    import incremental_delivery as incremental
    import flat_delivery as flat
    m = load_manifest(path)
    if flat.enabled(m):
        # New flat serial batches share the same kernel publication lock.
        from parallel_batch import locked
        with locked(path):
            m = load_manifest(path)
            def quarantine(c):
                try: verify_inputs(m, [c])
                except (BatchError, OSError) as exc:
                    c['receipts'].append({'event': 'controller_input_change', 'previous_status': c['status'],
                                          'verdict': 'needs_input', 'reason': str(exc)})
                    c['status'] = 'needs_input'; c.pop('passed_review_sha256', None)
                    return True
                return False
            def verify(c):
                original = next(x for x in m['projects'] if x['id'] == c['id'])
                if c['status'] == 'accepted' and quarantine(original):
                    atomic(path, m)
                    raise BatchError('Input changed during delivery; case quarantined; retry delivers remaining cases')
                if c.get('retry_origin'): verify_retry_origin(m, c)
                artifact = clean_path(c['artifact'], m['batch_dir']); verify_docx(artifact)
                if sha(artifact) != c.get('passed_review_sha256') or sha(artifact) != c.get('artifact_sha256'):
                    raise BatchError('Artifact changed after review')
            recovered = flat.recover_published_cases(m, verify)
            for c in recovered:
                event = {'event': 'controller_delivery_recovery', 'case_id': c['id'],
                         'artifact_sha256': c['passed_review_sha256'], 'original_inputs_changed': False}
                try: verify_inputs(m, [c])
                except (BatchError, OSError) as exc: event.update(original_inputs_changed=True, reason=str(exc))
                m.setdefault('delivery_recovery_events', []).append(event)
            changed = False
            for c in m['projects']:
                if c['status'] == 'accepted': changed = quarantine(c) or changed
            if changed or recovered: atomic(path, m)
            return flat.deliver(path, m, verify)
    if m.get('delivery_mode') != incremental.MODE and all(c['status'] in TERMINAL for c in m['projects']):
        return _deliver_legacy(path)
    def verify(c):
        verify_inputs(m, [c])
        if c.get('retry_origin'): verify_retry_origin(m, c)
        artifact = clean_path(c['artifact'], m['batch_dir']); verify_docx(artifact)
        if sha(artifact) != c.get('passed_review_sha256') or sha(artifact) != c.get('artifact_sha256'):
            raise BatchError('Artifact changed after review')
    return incremental.deliver(path, m, verify)

def _deliver_legacy(path):
    m = load_manifest(path)
    if any(c['status'] not in TERMINAL for c in m['projects']): raise BatchError('All projects must finish before delivery')
    verify_inputs(m, [c for c in m['projects'] if c['status'] in {'accepted', 'delivered'}])
    for c in m['projects']:
        if c['status'] in {'accepted', 'delivered'}:
            if c.get('retry_origin'): verify_retry_origin(m, c)
            artifact = clean_path(c['artifact'], m['batch_dir']); verify_docx(artifact)
            if sha(artifact) != c.get('passed_review_sha256'): raise BatchError('Artifact changed after review')
    root_path(m['desktop'])
    dest = Path(m['desktop'])/(m['policy']['delivery']['folder_prefix'] + '_' + m['batch_id'])
    if inside(dest, m['input']): raise BatchError('Delivery cannot be inside input')
    clean_path(dest, m['desktop'])
    if not m.get('delivery_transaction_id'):
        m['delivery_transaction_id'] = uuid.uuid4().hex; atomic(path, m)
    completed = copy.deepcopy(m)
    draft_label = model_label(m['policy'], 'draft')
    review_label = model_label(m['policy'], 'review')
    lines = ['专利批次交付清单', f'已完成稿件均经 {draft_label} 撰写、{review_label} 最终复查。']
    labels = {'delivered': '已完成', 'needs_input': '待补材料', 'failed': '执行失败'}
    for c in completed['projects']:
        folder = dest/(m['policy']['delivery']['accepted_subdir'] if c['status'] in {'accepted', 'delivered'} else m['policy']['delivery']['pending_subdir'])/(c['id'] + '_' + safe(c['title']))
        if c['status'] in {'accepted', 'delivered'}:
            c['delivered_artifact'] = str(folder/(safe(c['title']) + '.docx')); c['status'] = 'delivered'
        reason = c['receipts'][-1].get('reason', '') if c['receipts'] else ''
        lines.append(f"{c['id']} {c['title']}：{labels[c['status']]}" + (f"；{reason}" if reason else ''))
    completed['delivery_path'] = str(dest)
    if dest.exists():
        # Recover a rename that succeeded before the work manifest was committed.
        if read(clean_path(dest/'批次清单.json', m['desktop'])) != completed: raise BatchError('Delivery directory already exists and does not match this transaction')
        for c in completed['projects']:
            if c['status'] == 'delivered' and sha(clean_path(c['delivered_artifact'], dest)) != c['passed_review_sha256']: raise BatchError('Delivered artifact changed')
        atomic(path, completed); return str(dest)
    if m.get('delivery_path'): raise BatchError('Recorded delivery directory is missing')
    Path(m['desktop']).mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.patent-' + m['batch_id'] + '-', dir=m['desktop']))
    try:
        for c in completed['projects']:
            folder = staging/(m['policy']['delivery']['accepted_subdir'] if c['status'] == 'delivered' else m['policy']['delivery']['pending_subdir'])/(c['id'] + '_' + safe(c['title']))
            folder.mkdir(parents=True, exist_ok=False)
            if c['status'] == 'delivered':
                target = folder/(safe(c['title']) + '.docx')
                with clean_path(c['artifact'], m['batch_dir']).open('rb') as src, target.open('xb') as out: shutil.copyfileobj(src, out)
                if sha(target) != c['passed_review_sha256']: raise BatchError('Copy verification failed')
            else:
                reason = c['receipts'][-1].get('reason', '请查看本案校核记录中的具体缺口。')
                (folder/'待处理说明.txt').write_text(f"项目：{c['title']}\n状态：{labels[c['status']]}\n原因：{reason}\n该项目尚未形成已验收成稿。\n", encoding='utf-8')
        (staging/'交付清单.txt').write_text('\n'.join(lines), encoding='utf-8')
        atomic(staging/'批次清单.json', completed)
        verify_inputs(m, [c for c in m['projects'] if c['status'] == 'accepted'])
        clean_path(dest, m['desktop'])
        if dest.exists(): raise BatchError('Delivery destination was created concurrently')
        os.rename(staging, dest)
        atomic(path, completed); return str(dest)
    finally:
        if staging.exists():
            clean_path(staging, m['desktop']); shutil.rmtree(staging)
def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan'); s.add_argument('--input', required=True); s.add_argument('--out', required=True)
    s = sub.add_parser('init'); s.add_argument('--input', required=True); s.add_argument('--work-root', required=True); s.add_argument('--selection', required=True); s.add_argument('--desktop')
    s = sub.add_parser('next'); s.add_argument('--manifest', required=True)
    s = sub.add_parser('record'); s.add_argument('--manifest', required=True); s.add_argument('--case', required=True); s.add_argument('--receipt', required=True)
    s = sub.add_parser('defer'); s.add_argument('--manifest', required=True); s.add_argument('--case', required=True); s.add_argument('--status', choices=['needs_input', 'failed'], required=True); s.add_argument('--reason', required=True)
    s = sub.add_parser('retry'); s.add_argument('--manifest', required=True); s.add_argument('--case', required=True); s.add_argument('--reason', required=True); s.add_argument('--work-root'); s.add_argument('--desktop')
    s = sub.add_parser('deliver'); s.add_argument('--manifest', required=True)
    a = p.parse_args()
    try:
        if a.command == 'scan':
            result = scan(a.input)
            out = Path(a.out).absolute(); root_path(out.parent)
            if inside(out, a.input) or (out.exists() and linked(out)): raise BatchError('Scan output must be outside input and cannot be a link')
            atomic(out, result)
        elif a.command == 'init': result = str(init(a.input, a.work_root, read(a.selection), a.desktop))
        elif a.command == 'next': result = next_case(load_manifest(a.manifest))
        elif a.command == 'record': result = record(a.manifest, a.case, read(a.receipt))
        elif a.command == 'defer': result = defer(a.manifest, a.case, a.status, a.reason)
        elif a.command == 'retry': result = str(retry(a.manifest, a.case, a.reason, a.work_root, a.desktop))
        else: result = deliver(a.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (BatchError, OSError, ValueError, KeyError, TypeError) as e:
        print(f'批处理拒绝：{e}', file=sys.stderr); return 2
if __name__ == '__main__': raise SystemExit(main())

