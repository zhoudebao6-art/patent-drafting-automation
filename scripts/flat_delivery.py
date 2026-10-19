"""Publish final DOCX files in one flat, owned directory.

Only the immutable hidden owner file accompanies files in the final directory.
Per-case publication intents, receipts and summaries remain in the work batch.
Copies use a hidden, owned sibling directory on the destination filesystem.
Unrecorded crash remnants stay there; unknown files are never swept or adopted.
Callers hold the controller lock and revalidate frozen acceptance evidence.
"""
from __future__ import annotations
import copy
import ctypes
import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
from pathlib import Path
import batch_tool as b
import incremental_delivery as inc

MODE = 'flat_final_files_v1'


def enabled(m):
    return m['policy']['delivery'].get('layout') == 'flat_final_files'


def name_key(name):
    return unicodedata.normalize('NFC', name).rstrip(' .').casefold()


def names(m):
    """Resolve all batch names before any case finishes, independent of order."""
    import output_contract
    if output_contract.enabled(m):
        result={c['id']:output_contract.filename(c['title']) for c in m['projects']}
        if len(result)!=len(m['projects']) or len({name_key(n) for n in result.values()})!=len(result):
            raise b.BatchError('Duplicate official titles cannot share one flat folder without changing names; resolve this selection before drafting')
        return result
    originals = {c['id']: b.safe(c['title']) + '.docx' for c in m['projects']}
    if len(originals) != len(m['projects']): raise b.BatchError('Duplicate case identity')
    groups = {}
    for ident, name in originals.items(): groups.setdefault(name_key(name), []).append(ident)
    result = {ids[0]: originals[ids[0]] for ids in groups.values() if len(ids) == 1}
    occupied = {name_key(name) for name in result.values()}
    for ident in sorted(originals):
        if ident in result: continue
        if b.safe(ident) != ident: raise b.BatchError('Invalid case identity')
        stem = Path(originals[ident]).stem
        candidate = stem + '_' + ident + '.docx'
        while name_key(candidate) in occupied:
            candidate = Path(candidate).stem + '_' + ident + '.docx'
        result[ident] = candidate; occupied.add(name_key(candidate))
    return result


def folder_name(m):
    """Use the frozen input root, never a case title or internal source snapshot."""
    policy = m['policy']['delivery']
    if policy.get('name_source') != 'input_folder':
        return policy['folder_prefix'] + '_' + m['batch_id']
    source_name = Path(m['input']).name
    if not source_name:
        raise b.BatchError('Input directory has no basename; select a named input folder for delivery')
    if (source_name in {'.', '..'} or source_name.endswith((' ', '.'))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', source_name)
            or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])', source_name.split('.')[0].rstrip(' '))):
        raise b.BatchError('Input folder name is not a valid Windows directory name; it will not be renamed or sanitized')
    name = source_name + '_' + policy['folder_suffix'] + '_' + m['batch_id']
    try:
        units = len(name.encode('utf-16-le')) // 2
    except UnicodeEncodeError as exc:
        raise b.BatchError('Input folder name contains invalid Unicode; it will not be renamed') from exc
    if units > 255:
        raise b.BatchError('Input folder name plus delivery suffix exceeds 255 UTF-16 units; choose a shorter input folder name before drafting')
    return name


def destination(m):
    desk = b.root_path(m['desktop'])
    dest = b.clean_path(desk / folder_name(m), desk)
    if b.inside(dest, m['input']): raise b.BatchError('Delivery cannot be inside input')
    return dest


def owner(m):
    return {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id'],
            'batch_dir': m['batch_dir'], 'rules_sha256': m['rules_sha256'], 'mode': MODE,
            'filenames': names(m)}


def hidden(path):
    if os.name == 'nt':
        call = ctypes.windll.kernel32.SetFileAttributesW
        call.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        call.restype = ctypes.c_int
        if not call(str(path), 0x2): raise ctypes.WinError()


def verify_owner(m, dest):
    if b.read(inc.regular(dest / '.delivery-owner.json', dest)) != owner(m):
        raise b.BatchError('Delivery directory belongs to another transaction; preserved')


def staging_directory(m, dest, *, create=False):
    """Keep pre-intent process-death remnants outside the final-file directory."""
    root = b.clean_path(dest.parent / ('.patent-stage-' + m['delivery_transaction_id']), dest.parent)
    expected = dict(owner(m), purpose='publication_staging')
    if not root.exists() and create:
        initial = Path(tempfile.mkdtemp(prefix='.patent-stage-init-', dir=dest.parent))
        try:
            hidden(initial)
            b.atomic(initial / '.delivery-owner.json', expected)
            # A competing or foreign directory is never replaced on Windows.
            if root.exists(): raise b.BatchError('Staging destination created concurrently')
            os.rename(initial, root)
        finally:
            if initial.exists():
                b.clean_path(initial, dest.parent); shutil.rmtree(initial)
    if b.read(inc.regular(root / '.delivery-owner.json', root)) != expected:
        raise b.BatchError('Staging directory belongs to another transaction; preserved')
    return root


def work_directory(m):
    path = b.clean_path(Path(m['batch_dir']) / 'flat-delivery', m['batch_dir'])
    path.mkdir(exist_ok=True)
    return path


def receipt_path(m, c):
    if b.safe(c['id']) != c['id']: raise b.BatchError('Invalid case identity')
    root = work_directory(m)
    receipts = b.clean_path(root / 'cases', root); receipts.mkdir(exist_ok=True)
    return b.clean_path(receipts / (c['id'] + '.json'), root)


def file_identity(path):
    value = path.stat()
    if not value.st_ino: raise b.BatchError('Filesystem must provide stable file identity')
    return {'device': value.st_dev, 'inode': value.st_ino}


def same_file(path, identity):
    return path.exists() and file_identity(path) == identity


def target_path(dest, filename):
    # Explicit Windows-style comparison also protects case-sensitive test hosts.
    for p in dest.iterdir():
        if name_key(p.name) == name_key(filename) and p.name != filename:
            raise b.BatchError('Case-insensitive delivery filename conflict; preserved')
    return b.clean_path(dest / filename, dest)


def publish_case(m, c, dest, verify):
    filename = names(m)[c['id']]
    target = target_path(dest, filename)
    expected = {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id'],
                'case_id': c['id'], 'file': filename, 'sha256': c['passed_review_sha256']}
    record_path = receipt_path(m, c)
    if record_path.exists():
        record = b.read(inc.regular(record_path, m['batch_dir']))
        if record.get('publication') != expected: raise b.BatchError('Publication receipt changed; preserved')
        stage_name = record.get('staging_file')
        identity = record.get('file_identity')
        if (not isinstance(stage_name, str) or Path(stage_name).name != stage_name
                or not stage_name.startswith('.case-' + c['id'] + '-') or not stage_name.endswith('.tmp')
                or not isinstance(identity, dict) or set(identity) != {'device', 'inode'}
                or any(type(value) is not int for value in identity.values()) or not identity['inode']):
            raise b.BatchError('Invalid publication identity; preserved')
        area = record.get('staging_area', 'delivery_root')
        if area not in {'delivery_root', 'owned_sibling'}:
            raise b.BatchError('Invalid publication staging area; preserved')
        stage_root = staging_directory(m, dest) if area == 'owned_sibling' else dest
        staging = b.clean_path(stage_root / record['staging_file'], stage_root)
        if target.exists():
            if not same_file(target, identity): raise b.BatchError('Delivered file identity changed; preserved')
            # POSIX may stop between the no-replace hard link and staging unlink.
            if staging.exists():
                if not same_file(staging, identity): raise b.BatchError('Staging identity changed; preserved')
                staging.unlink()
        elif staging.exists():
            if not same_file(staging, identity): raise b.BatchError('Staging identity changed; preserved')
            if b.sha(inc.regular(staging, stage_root)) != expected['sha256']: raise b.BatchError('Staged file changed; preserved')
            verify(c)
            publish_file(staging, target)
        else:
            raise b.BatchError('Recorded publication disappeared; preserved')
    else:
        if target.exists(): raise b.BatchError('Unowned delivery file already exists; preserved')
        if c['status'] == 'delivered' or c['id'] in m.get('delivery_items', {}):
            raise b.BatchError('Recorded delivery receipt disappeared; preserved')
        stage_root = staging_directory(m, dest, create=True)
        fd, temporary = tempfile.mkstemp(prefix='.case-' + c['id'] + '-', suffix='.tmp', dir=stage_root)
        staging = Path(temporary); identity = file_identity(staging)
        intent_saved = False
        try:
            with os.fdopen(fd, 'wb') as out, inc.regular(c['artifact'], m['batch_dir']).open('rb') as src:
                shutil.copyfileobj(src, out); out.flush(); os.fsync(out.fileno())
            if b.sha(staging) != expected['sha256']: raise b.BatchError('Copy verification failed')
            verify(c)  # source, accepted bytes and rendered pages immediately before publication
            record = {'publication': expected, 'staging_file': staging.name, 'file_identity': identity,
                      'staging_area': 'owned_sibling'}
            b.atomic(record_path, record); intent_saved = True
            target_path(dest, filename)
            publish_file(staging, target)
        finally:
            # Retain a verified intent's stage for recovery after an interrupted move.
            if not intent_saved and same_file(staging, identity): staging.unlink()
    if b.sha(inc.regular(target, dest)) != expected['sha256']:
        raise b.BatchError('Delivered artifact changed; preserved')
    return {'relative_path': filename, 'sha256': expected['sha256'], 'kind': 'accepted'}


def publish_file(staging, target):
    """Atomic no-replace publication, including on POSIX where rename replaces."""
    if os.name == 'nt': os.rename(staging, target)
    else:
        os.link(staging, target)
        staging.unlink()


def recover_published_cases(m, verify):
    if m.get('delivery_mode') != MODE: return []
    dest = destination(m)
    if not dest.exists(): return []
    verify_owner(m, dest)
    recovered = []
    for c in m['projects']:
        if c['status'] != 'accepted': continue
        target = target_path(dest, names(m)[c['id']])
        if not target.exists(): continue
        candidate = copy.deepcopy(c); candidate['status'] = 'delivered'
        verify(candidate)  # frozen evidence only; original sources may have changed after publication
        entry = publish_case(m, candidate, dest, verify)
        old = m.get('delivery_items', {}).get(c['id'])
        if old is not None and old != entry: raise b.BatchError('Published case identity changed')
        c.update(status='delivered', delivered_artifact=str(target))
        m.setdefault('delivery_items', {})[c['id']] = entry
        recovered.append(c)
    return recovered


class QuarantinedInput(b.BatchError):
    """Controller has persisted this case's changed-input quarantine."""
    def __init__(self, case_id):
        self.case_id=case_id
        super().__init__('Original input changed; case quarantined before publication')


def deliver(path, m, verify, *, force_history_audit=False, write_metadata=True):
    selected = [c for c in m['projects'] if c['status'] in {'accepted', 'delivered'}]
    closed = all(c['status'] in b.TERMINAL and not c.get('active_attempt') for c in m['projects'])
    if m['policy'].get('pool_execution') == 'nonblocking_handoff_v36' and any(
            c['status'] == 'accepted' for c in m['projects']):
        # Publish new independently accepted bytes before the terminal history
        # audit; the next publication tick performs that audit before closure.
        closed = False
    if not selected and not closed and m.get('delivery_mode') != MODE:
        raise b.BatchError('No accepted case is ready for flat delivery')
    lean = m['policy'].get('execution_optimization') == 'lean_execution_v24'
    # New cases are fully verified immediately before atomic publication below.
    # Published cases retain file identity/SHA checks on EVERY delivery. Their full
    # historical evidence is audited once at closure instead of on every append.
    audit_history = force_history_audit or (closed and not m.get('delivery_closed'))
    for c in selected:
        if not lean or (c['status'] == 'delivered' and audit_history):
            verify(c)
    dest = destination(m); desk = dest.parent
    if m.get('delivery_path') and Path(m['delivery_path']) != dest: raise b.BatchError('Delivery location changed')
    if m.get('delivery_path') and not dest.exists(): raise b.BatchError('Recorded delivery directory disappeared')
    if m.get('delivery_mode') != MODE:
        if m.get('delivery_mode') or m.get('delivery_path'): raise b.BatchError('Existing delivery layout cannot be migrated')
        m.update(delivery_mode=MODE, delivery_transaction_id=uuid.uuid4().hex, delivery_closed=False, delivery_items={})
        b.atomic(path, m)
    if not dest.exists():
        desk.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.patent-' + m['batch_id'] + '-', dir=desk))
        try:
            b.atomic(staging / '.delivery-owner.json', owner(m)); hidden(staging / '.delivery-owner.json')
            if dest.exists(): raise b.BatchError('Delivery destination created concurrently')
            os.rename(staging, dest)
        finally:
            if staging.exists():
                b.clean_path(staging, desk); shutil.rmtree(staging)
    verify_owner(m, dest)
    final = copy.deepcopy(m)
    for c in final['projects']:
        if c['status'] not in {'accepted', 'delivered'} or c.get('active_attempt'): continue
        try:
            entry = publish_case(m, c, dest, verify)
        except QuarantinedInput as exc:
            original=next(row for row in m['projects'] if row['id']==c['id'])
            if (m['policy'].get('post_review_optimization')!='single_closeout_v25'
                    or exc.case_id!=c['id'] or original['status']!='needs_input'
                    or original['receipts'][-1].get('event')!='controller_input_change'):
                raise
            c.clear();c.update(copy.deepcopy(original))
            continue
        old = final['delivery_items'].get(c['id'])
        if old is not None and old != entry: raise b.BatchError('Published case identity changed')
        final['delivery_items'][c['id']] = entry
        c.update(status='delivered', delivered_artifact=str(dest / entry['relative_path']))
    final.update(delivery_path=str(dest), delivery_closed=closed)
    if write_metadata:
        sync_metadata(final)
    if final != m: b.atomic(path, final)
    return str(dest)


def sync_metadata(final):
    m = final
    closed = final.get('delivery_closed', False)
    work = work_directory(m)
    labels = {'delivered': '已完成', 'needs_input': '待补材料', 'failed': '执行失败', 'pending': '待撰写',
              'drafted': '待复查', 'revised': '待重验', 'fix_required': '待修正', 'accepted': '待交付'}
    lines = ['专利批次交付清单', '桌面批次文件夹根部仅放验收成稿；交付与校核记录保留在工作目录。',
             '批次已结束。' if closed else '逐案交付中；其他项目继续处理。']
    lines.extend(f"{c['id']} {c['title']}：{labels[c['status']]}" for c in final['projects'])
    lines.extend(f"待处理说明 {c['id']} {c['title']}：{c['receipts'][-1].get('reason', '尚未验收') if c['receipts'] else '尚未验收'}；未形成验收成稿，未交付。"
                 for c in final['projects'] if c['status'] in {'needs_input', 'failed'})
    # Existing transactional summary writer remains recoverable, now wholly in work storage.
    inc.sync_metadata(final, work, {'批次清单.json': json.dumps(final, ensure_ascii=False, indent=2),
                                    '交付清单.txt': '\n'.join(lines)})
