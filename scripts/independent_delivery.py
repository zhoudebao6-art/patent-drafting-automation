"""One publication lane, with expensive file checks outside the queue lock."""
import copy
import uuid
from pathlib import Path

import batch_tool as b
import flat_delivery as flat
import parallel_batch as queue


def deliver(path, controller='controller', *, audit_history=False):
    path = Path(path)
    work = path.parent/'delivery-control'
    work.mkdir(exist_ok=True)
    # A different lock directory keeps publication single-writer without
    # holding up case receipts, reservations or preparation commits.
    with queue.locked(work/'owner.json'):
        with queue.locked(path):
            current = queue.load_manifest(path)
            if current['controller'] != controller or not queue.nonblocking_pools(current['policy']):
                raise queue.BatchError('Independent publication is not authorized')
            if not current.get('delivery_mode'):
                if not any(c['status'] == 'accepted' for c in current['projects']) and not all(
                        c['status'] in b.TERMINAL and not c.get('active_attempt') for c in current['projects']):
                    raise queue.BatchError('No accepted case is ready for flat delivery')
                current.update(delivery_mode=flat.MODE, delivery_transaction_id=uuid.uuid4().hex,
                               delivery_closed=False, delivery_items={})
                current['sequence'] += 1
                b.atomic(path, current)
            before = copy.deepcopy(current)
        shadow = work/'publication-snapshot.json'
        b.atomic(shadow, before)
        destination = queue.deliver_snapshot(shadow, copy.deepcopy(before),
                                            audit_history=audit_history, write_metadata=False)
        after = b.read(shadow)
        changed = {c['id']: c for c in after['projects'] if c != queue.case_by_id(before, c['id'])}
        with queue.locked(path):
            current = queue.load_manifest(path)
            if (current['controller'] != controller or current['policy'] != before['policy']
                    or current['rules_sha256'] != before['rules_sha256']
                    or current['delivery_transaction_id'] != before['delivery_transaction_id']):
                raise queue.BatchError('Publication transaction changed; preserve published files for recovery')
            for ident, updated in changed.items():
                target = queue.case_by_id(current, ident)
                if target != queue.case_by_id(before, ident):
                    raise queue.BatchError('Published case changed before merge; recover from publication receipt')
                target.clear(); target.update(copy.deepcopy(updated))
            for ident, entry in after.get('delivery_items', {}).items():
                existing = current['delivery_items'].get(ident)
                if existing is not None and existing != entry:
                    raise queue.BatchError('Publication ledger conflict')
                current['delivery_items'][ident] = copy.deepcopy(entry)
            prior_events = before.get('delivery_recovery_events', [])
            extra = after.get('delivery_recovery_events', [])[len(prior_events):]
            if extra:
                current.setdefault('delivery_recovery_events', []).extend(extra)
            current['delivery_path'] = destination
            # Only an actually performed terminal history audit may close it.
            current['delivery_closed'] = bool(after.get('delivery_closed')) and all(
                c['status'] in b.TERMINAL and not c.get('active_attempt') for c in current['projects'])
            current['sequence'] += 1
            b.atomic(path, current)
            metadata = copy.deepcopy(current)
        # Never write the stale publication snapshot back over peer progress.
        flat.sync_metadata(metadata)
        return destination
