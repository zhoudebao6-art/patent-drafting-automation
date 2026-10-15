"""Append accepted cases to one owned delivery directory, with recoverable metadata.

The caller is the sole manifest writer and verifies acceptance before calling.
Parallel callers additionally hold the existing controller kernel lock.
Case directories are immutable publications; the work ledger remains authoritative.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
import batch_tool as b

MODE = 'incremental_v1'


def regular(path, root):
    path = b.clean_path(path, root)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise b.BatchError('Independent delivery file required')
    return path


def current_hash(path, root):
    path = b.clean_path(path, root)
    return b.sha(regular(path, root)) if path.exists() else None


def write_text_atomic(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.delivery-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content.encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def sync_metadata(m, dest, texts):
    """Write-ahead intent permits mixed old/new summary files after a crash.

    Recovery only completes owned summary files. It never restores an old work
    manifest, so another case's progress since the failed delivery is preserved.
    """
    root = Path(m['batch_dir'])
    state_path = b.clean_path(root / 'incremental-delivery.state.json', root)
    pending_path = b.clean_path(root / 'incremental-delivery.pending.json', root)
    identity = {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id']}
    state = b.read(regular(state_path, root)) if state_path.exists() else dict(identity, hashes={})
    if any(state.get(k) != v for k, v in identity.items()): raise b.BatchError('Delivery metadata owner mismatch')

    def apply(intent):
        if any(intent.get(k) != v for k, v in identity.items()): raise b.BatchError('Delivery metadata intent mismatch')
        if set(intent['texts']) != {'批次清单.json', '交付清单.txt'}: raise b.BatchError('Invalid delivery metadata names')
        hashes = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in intent['texts'].items()}
        for name, new_hash in hashes.items():
            if current_hash(dest / name, dest) not in {intent['before'].get(name), new_hash}:
                raise b.BatchError('Delivery summary changed by another writer; preserved')
        for name, content in intent['texts'].items():
            if current_hash(dest / name, dest) != hashes[name]:
                write_text_atomic(dest / name, content)
        result = dict(identity, hashes=hashes)
        b.atomic(state_path, result)
        pending_path.unlink()
        return result

    if pending_path.exists(): state = apply(b.read(regular(pending_path, root)))
    for name in texts:
        if current_hash(dest / name, dest) != state['hashes'].get(name):
            raise b.BatchError('Delivery summary changed or disappeared; preserved')
    hashes = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in texts.items()}
    if hashes == state['hashes']: return
    intent = dict(identity, before=state['hashes'], texts=texts)
    b.atomic(pending_path, intent)
    apply(intent)


def publish_case(m, c, dest, verify):
    accepted = c['status'] in {'accepted', 'delivered'}
    relative = Path(m['policy']['delivery']['accepted_subdir' if accepted else 'pending_subdir']) / (c['id'] + '_' + b.safe(c['title']))
    folder = b.clean_path(dest / relative, dest)
    filename = b.safe(c['title']) + '.docx' if accepted else '待处理说明.txt'
    reason = c['receipts'][-1].get('reason', '请查看本案校核记录。') if c['receipts'] else '尚未验收'
    note = f"项目：{c['title']}\n状态：{c['status']}\n原因：{reason}\n该项目尚未形成已验收成稿。\n"
    expected = c['passed_review_sha256'] if accepted else hashlib.sha256(note.encode('utf-8')).hexdigest()
    receipt = {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id'], 'case_id': c['id'],
               'kind': 'accepted' if accepted else 'pending', 'file': filename, 'sha256': expected}
    if folder.exists():
        if b.read(regular(folder / '.delivery-case.json', folder)) != receipt:
            raise b.BatchError('Existing case delivery has different ownership; preserved')
        if b.sha(regular(folder / filename, folder)) != expected:
            raise b.BatchError('Delivered artifact changed or incomplete; preserved')
    else:
        if c['status'] == 'delivered' or c['id'] in m.get('delivery_items', {}):
            raise b.BatchError('Recorded case delivery disappeared; preserved')
        folder.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.case-' + c['id'] + '-', dir=folder.parent))
        try:
            if accepted:
                with regular(c['artifact'], m['batch_dir']).open('rb') as src, (staging / filename).open('xb') as out:
                    shutil.copyfileobj(src, out)
                if b.sha(staging / filename) != expected: raise b.BatchError('Copy verification failed')
                verify(c)  # Recheck source/artifact/render immediately before publication.
            else: (staging / filename).write_bytes(note.encode('utf-8'))
            b.atomic(staging / '.delivery-case.json', receipt)
            b.clean_path(folder, dest)
            if folder.exists(): raise b.BatchError('Case delivery destination created concurrently')
            os.rename(staging, folder)
        finally:
            if staging.exists():
                b.clean_path(staging, dest); shutil.rmtree(staging)
    return {'relative_path': str(relative / filename), 'sha256': expected, 'kind': receipt['kind']}


def recover_published_cases(m, verify):
    """Recover immutable case publications before checking mutable source inputs.

    A case directory is published only after verification. A crash can leave its
    work status accepted; later source edits must not undo the publication fact.
    The caller verifies frozen acceptance evidence and records source changes.
    """
    if m.get('delivery_mode') != MODE: return []
    desk = b.root_path(m['desktop'])
    dest = b.clean_path(desk / (m['policy']['delivery']['folder_prefix'] + '_' + m['batch_id']), desk)
    if not dest.exists(): return []
    owner = {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id'],
             'batch_dir': m['batch_dir'], 'rules_sha256': m['rules_sha256'], 'mode': MODE}
    if b.read(regular(dest / '.delivery-owner.json', dest)) != owner:
        raise b.BatchError('Delivery directory belongs to another transaction; preserved')
    recovered = []
    for c in m['projects']:
        if c['status'] != 'accepted': continue
        relative = Path(m['policy']['delivery']['accepted_subdir']) / (c['id'] + '_' + b.safe(c['title']))
        if not b.clean_path(dest / relative, dest).exists(): continue
        candidate = copy.deepcopy(c)
        candidate['status'] = 'delivered'
        verify(candidate)
        entry = publish_case(m, candidate, dest, verify)
        old = m.get('delivery_items', {}).get(c['id'])
        if old is not None and old != entry: raise b.BatchError('Published case identity changed')
        c.update(status='delivered', delivered_artifact=str(dest / entry['relative_path']))
        m.setdefault('delivery_items', {})[c['id']] = entry
        recovered.append(c)
    return recovered


def deliver(path, m, verify):
    selected = [c for c in m['projects'] if c['status'] in {'accepted', 'delivered'}]
    if not selected and m.get('delivery_mode') != MODE:
        raise b.BatchError('No accepted case is ready for incremental delivery')
    for c in selected: verify(c)
    desk = b.root_path(m['desktop'])
    dest = b.clean_path(desk / (m['policy']['delivery']['folder_prefix'] + '_' + m['batch_id']), desk)
    if b.inside(dest, m['input']): raise b.BatchError('Delivery cannot be inside input')
    if m.get('delivery_path') and Path(m['delivery_path']) != dest: raise b.BatchError('Delivery location changed')
    if m.get('delivery_path') and not dest.exists(): raise b.BatchError('Recorded delivery directory disappeared')
    if m.get('delivery_mode') != MODE:
        m.update(delivery_mode=MODE, delivery_transaction_id=uuid.uuid4().hex, delivery_closed=False, delivery_items={})
        b.atomic(path, m)
    owner = {'batch_id': m['batch_id'], 'transaction_id': m['delivery_transaction_id'],
             'batch_dir': m['batch_dir'], 'rules_sha256': m['rules_sha256'], 'mode': MODE}
    if not dest.exists():
        desk.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.patent-' + m['batch_id'] + '-', dir=desk))
        try:
            b.atomic(staging / '.delivery-owner.json', owner)
            if dest.exists(): raise b.BatchError('Delivery destination created concurrently')
            os.rename(staging, dest)
        finally:
            if staging.exists():
                b.clean_path(staging, desk); shutil.rmtree(staging)
    if b.read(regular(dest / '.delivery-owner.json', dest)) != owner:
        raise b.BatchError('Delivery directory belongs to another transaction; preserved')
    final = copy.deepcopy(m)
    for c in final['projects']:
        if c['status'] not in b.TERMINAL or c.get('active_attempt'): continue
        entry = publish_case(m, c, dest, verify)
        old = final['delivery_items'].get(c['id'])
        if old is not None and old != entry: raise b.BatchError('Published case identity changed')
        final['delivery_items'][c['id']] = entry
        if entry['kind'] == 'accepted':
            c.update(status='delivered', delivered_artifact=str(dest / entry['relative_path']))
    final.update(delivery_path=str(dest), delivery_closed=all(c['status'] in b.TERMINAL and not c.get('active_attempt') for c in final['projects']))
    draft = b.model_label(m['policy'], 'draft')
    review = b.model_label(m['policy'], 'review')
    labels = {'delivered': '已完成', 'needs_input': '待补材料', 'failed': '执行失败', 'pending': '待撰写',
              'drafted': '待复查', 'revised': '待重验', 'fix_required': '待修正', 'accepted': '待交付'}
    lines = ['专利批次交付清单', f'已完成稿件均经 {draft} 撰写、{review} 最终复查。',
             '批次已结束。' if final['delivery_closed'] else '逐案交付中；其它项目继续处理，未经最终验收的稿件不交付。']
    lines.extend(f"{c['id']} {c['title']}：{labels[c['status']]}" for c in final['projects'])
    sync_metadata(final, dest, {'批次清单.json': json.dumps(final, ensure_ascii=False, indent=2), '交付清单.txt': '\n'.join(lines)})
    if final != m: b.atomic(path, final)
    return str(dest)
