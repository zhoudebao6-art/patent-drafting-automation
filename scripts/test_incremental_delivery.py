"""Delivery fixtures exercise publication, not model or visual-review quality."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import incremental_delivery as inc
import test_batch_tool as serial
import test_parallel_batch as parallel


class SerialIncrementalTests(unittest.TestCase):
    setUp = serial.BatchTests.setUp
    receipt = serial.BatchTests.receipt
    complete = serial.BatchTests.complete

    def test_partial_then_append_same_directory_without_rewriting_first(self):
        self.complete('001')
        second = copy.deepcopy(b.read(self.manifest)['projects'][1])
        dest = Path(b.deliver(self.manifest)); one = next(dest.rglob('*.docx'))
        original = (one.read_bytes(), one.stat().st_mtime_ns)
        m = b.read(self.manifest)
        self.assertFalse(m['delivery_closed']); self.assertEqual(m['projects'][1], second)
        self.assertEqual(b.next_case(m)['case']['id'], '002')
        self.assertEqual(b.deliver(self.manifest), str(dest))
        self.complete('002'); self.assertEqual(b.deliver(self.manifest), str(dest))
        self.assertEqual(len(list(dest.rglob('*.docx'))), 2)
        self.assertEqual((one.read_bytes(), one.stat().st_mtime_ns), original)
        m = b.read(self.manifest); self.assertTrue(m['delivery_closed'])
        self.assertEqual(m, b.read(dest / '批次清单.json'))
        before = {str(f): (f.read_bytes(), f.stat().st_mtime_ns) for f in dest.rglob('*') if f.is_file()}
        b.deliver(self.manifest)
        self.assertEqual(before, {str(f): (f.read_bytes(), f.stat().st_mtime_ns) for f in dest.rglob('*') if f.is_file()})

    def test_draft_and_revision_are_not_published(self):
        self.complete('001'); b.record(self.manifest, '002', self.receipt('draft', '2'))
        b.record(self.manifest, '002', self.receipt('review', '2', verdict='fix_required'))
        b.record(self.manifest, '002', self.receipt('revise', '2'))
        dest = Path(b.deliver(self.manifest))
        self.assertEqual(len(list(dest.rglob('*.docx'))), 1)
        self.assertEqual(b.read(self.manifest)['projects'][1]['status'], 'revised')

    def test_final_pending_note_after_partial(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest))
        b.defer(self.manifest, '002', 'needs_input', 'missing formula source')
        b.deliver(self.manifest)
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 1)
        self.assertTrue(b.read(self.manifest)['delivery_closed'])

    def test_partial_copy_failure_retry(self):
        self.complete('001')
        with patch.object(inc.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        self.assertFalse(list((self.root / 'desktop').rglob('*.docx')))
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'accepted')
        self.assertEqual(len(list(Path(b.deliver(self.manifest)).rglob('*.docx'))), 1)

    def test_case_publish_then_manifest_commit_crash_recovers(self):
        self.complete('001'); original = b.atomic
        def fail(path, value):
            if Path(path) == self.manifest and value.get('delivery_path'): raise OSError('commit interrupted')
            return original(path, value)
        with patch.object(b, 'atomic', side_effect=fail):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        one = next((self.root / 'desktop').rglob('*.docx')); timestamp = one.stat().st_mtime_ns
        # Other case progress can be recorded after the failure. Recovery must
        # never restore the old work manifest from the desktop metadata.
        b.record(self.manifest, '002', self.receipt('draft', '2'))
        dest = Path(b.deliver(self.manifest))
        self.assertEqual(b.read(self.manifest)['projects'][1]['status'], 'drafted')
        self.assertEqual(one.stat().st_mtime_ns, timestamp)
        self.assertEqual(b.read(self.manifest), b.read(dest / '批次清单.json'))

    def test_summary_replacement_crash_recovers(self):
        self.complete('001'); original = inc.write_text_atomic; calls = []
        def fail(path, content):
            calls.append(path)
            if len(calls) == 2: raise OSError('interrupted summary write')
            return original(path, content)
        with patch.object(inc, 'write_text_atomic', side_effect=fail):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        self.assertTrue((self.batch / 'incremental-delivery.pending.json').exists())
        b.record(self.manifest, '002', self.receipt('draft', '2'))
        dest = Path(b.deliver(self.manifest))
        self.assertFalse((self.batch / 'incremental-delivery.pending.json').exists())
        self.assertEqual(b.read(self.manifest)['projects'][1]['status'], 'drafted')
        self.assertEqual(b.read(self.manifest), b.read(dest / '批次清单.json'))

    def test_foreign_case_in_owned_root_not_overwritten(self):
        self.complete('001')
        with patch.object(inc.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        dest = next((self.root / 'desktop').glob('专利撰写完成_*'))
        case = dest / '已完成' / '001_同名'; case.mkdir()
        sentinel = case / '同名.docx'; sentinel.write_bytes(b'user file')
        with self.assertRaises((b.BatchError, FileNotFoundError)): b.deliver(self.manifest)
        self.assertEqual(sentinel.read_bytes(), b'user file')

    def test_foreign_root_and_foreign_case_not_overwritten(self):
        self.complete('001'); m = b.read(self.manifest)
        dest = self.root / 'desktop' / ('专利撰写完成_' + m['batch_id']); dest.mkdir(parents=True)
        sentinel = dest / 'keep.txt'; sentinel.write_text('keep')
        with self.assertRaises((b.BatchError, FileNotFoundError)): b.deliver(self.manifest)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertFalse(list(dest.rglob('*.docx')))

    def test_user_changed_delivered_file_is_preserved(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest))
        one = next(dest.rglob('*.docx')); one.write_bytes(b'user edited')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertEqual(one.read_bytes(), b'user edited')

    def test_user_changed_summary_is_preserved(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest))
        summary = dest / '交付清单.txt'; summary.write_text('user note')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertEqual(summary.read_text(), 'user note')

    def test_missing_delivery_is_not_silently_recreated(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest))
        next(dest.rglob('*.docx')).unlink()
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)

    def test_accepted_artifact_tampering_still_blocks_partial(self):
        self.complete('001'); c = b.read(self.manifest)['projects'][0]
        serial.docx(c['artifact'], 'tampered')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertFalse((self.root / 'desktop').exists())

    def test_policy_timing_legacy_and_new_valid_unknown_rejected(self):
        b.validate_policy(serial.POLICY)
        new = copy.deepcopy(serial.POLICY); new['delivery']['timing'] = 'after_each_acceptance'
        b.validate_policy(new)
        for value in ('after_batch', None, '', True):
            new['delivery']['timing'] = value
            with self.assertRaises(b.BatchError): b.validate_policy(new)


class ParallelIncrementalTests(unittest.TestCase):
    setUp = parallel.ParallelTests.setUp
    reserve = parallel.ParallelTests.reserve
    receipt = parallel.ParallelTests.receipt
    record = parallel.ParallelTests.record

    def interrupted_publication(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        original = b.atomic
        def fail(path, value):
            if Path(path) == self.manifest and value.get('delivery_path'):
                raise OSError('commit interrupted after case publication')
            return original(path, value)
        with patch.object(b, 'atomic', side_effect=fail):
            with self.assertRaises(OSError): p.deliver(self.manifest)
        return next((self.root / 'desktop').rglob('*.docx'))

    def test_recover_published_case_before_quarantining_later_source_change(self):
        one = self.interrupted_publication()
        original = (one.read_bytes(), one.stat().st_mtime_ns)
        serial.docx(self.src / '1.docx', 'changed after publication')
        self.record(self.reserve(1))
        second = copy.deepcopy(b.read(self.manifest)['projects'][1])
        dest = Path(p.deliver(self.manifest)); m = b.read(self.manifest)
        self.assertEqual(m['projects'][0]['status'], 'delivered')
        self.assertEqual(m['projects'][1], second)
        self.assertEqual((one.read_bytes(), one.stat().st_mtime_ns), original)
        self.assertFalse(list(dest.rglob('待处理说明.txt')))
        self.assertTrue(m['delivery_recovery_events'][0]['original_inputs_changed'])
        self.assertEqual(m, b.read(dest / '批次清单.json'))
        p.deliver(self.manifest)
        self.assertEqual(len(b.read(self.manifest)['delivery_recovery_events']), 1)

    def test_recovery_still_quarantines_unpublished_changed_case(self):
        self.interrupted_publication()
        self.record(self.reserve(1)); self.record(self.reserve(1))
        serial.docx(self.src / '1.docx', 'published source changed')
        serial.docx(self.src / '2.docx', 'unpublished source changed')
        dest = Path(p.deliver(self.manifest)); m = b.read(self.manifest)
        self.assertEqual([c['status'] for c in m['projects'][:2]], ['delivered', 'needs_input'])
        self.assertEqual(len(list(dest.rglob('*.docx'))), 1)
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 1)

    def test_recovery_rejects_changed_published_file_or_acceptance_evidence(self):
        one = self.interrupted_publication()
        original = one.read_bytes(); one.write_bytes(b'edited by user')
        with self.assertRaises(b.BatchError): p.deliver(self.manifest)
        self.assertEqual(one.read_bytes(), b'edited by user')
        one.write_bytes(original)
        c = b.read(self.manifest)['projects'][0]
        (Path(c['receipts'][-1]['render']['evidence']).parent / 'page-1.png').write_bytes(b'changed')
        with self.assertRaises(b.BatchError): p.deliver(self.manifest)
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'accepted')

    def test_deliver_while_other_worker_active_then_continue(self):
        first, second = self.reserve(), self.reserve()
        self.record(first); review = self.reserve(); self.record(review)
        before = copy.deepcopy(p.case_by_id(b.read(self.manifest), second['case_id']))
        dest = Path(p.deliver(self.manifest)); original = next(dest.rglob('*.docx')).stat().st_mtime_ns
        m = b.read(self.manifest)
        self.assertEqual(p.case_by_id(m, second['case_id']), before)
        self.assertFalse(m['delivery_closed']); self.assertEqual(p.summary(self.manifest)['active_workers'], 1)
        self.record(second); review2 = self.reserve(); self.record(review2)
        self.assertEqual(p.deliver(self.manifest), str(dest))
        self.assertEqual(len(list(dest.rglob('*.docx'))), 2)
        self.assertEqual(next(dest.rglob('*.docx')).stat().st_mtime_ns, original)
        self.assertEqual(self.reserve()['attempt']['stage'], 'draft')

    def test_partial_final_completion_closes_only_when_all_cases_terminal(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        dest = Path(p.deliver(self.manifest))
        for c in b.read(self.manifest)['projects'][1:]: p.defer(self.manifest, c['id'], 'needs_input', 'fixture')
        self.assertEqual(p.deliver(self.manifest), str(dest))
        self.assertTrue(p.summary(self.manifest)['delivery_closed'])
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 9)
        with self.assertRaisesRegex(b.BatchError, 'closed'): p.reserve(self.manifest, 1)
        self.assertEqual(p.deliver(self.manifest), str(dest))

    def test_changed_accepted_source_quarantines_only_affected_case(self):
        for _ in range(4): self.record(self.reserve(1))
        serial.docx(self.src / '1.docx', 'changed source')
        dest = Path(p.deliver(self.manifest)); files = list(dest.rglob('*.docx'))
        self.assertEqual(len(files), 1); self.assertTrue(files[0].parent.name.startswith('002_'))
        self.assertEqual(p.case_by_id(b.read(self.manifest), '001')['status'], 'needs_input')
        self.assertEqual(p.plan(self.manifest, 1)['action'], 'reserve')

    def test_source_change_during_copy_blocks_affected_case_then_retry(self):
        for _ in range(4): self.record(self.reserve(1))
        original = inc.shutil.copyfileobj; changed = []
        def change(src, target):
            result = original(src, target)
            if not changed:
                changed.append(True); serial.docx(self.src / '1.docx', 'changed during copy')
            return result
        with patch.object(inc.shutil, 'copyfileobj', side_effect=change):
            with self.assertRaisesRegex(b.BatchError, 'quarantined'): p.deliver(self.manifest)
        self.assertFalse(list((self.root / 'desktop').rglob('*.docx')))
        files = list(Path(p.deliver(self.manifest)).rglob('*.docx'))
        self.assertEqual(len(files), 1); self.assertTrue(files[0].parent.name.startswith('002_'))

    def test_corrupted_render_still_blocks_partial(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        c = b.read(self.manifest)['projects'][0]
        (Path(c['receipts'][-1]['render']['evidence']).parent / 'page-1.png').write_bytes(b'bad image')
        with self.assertRaises(b.BatchError): p.deliver(self.manifest)
        self.assertFalse((self.root / 'desktop').exists())

    def test_unknown_controller_cannot_incrementally_deliver(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        with self.assertRaises(b.BatchError): p.deliver(self.manifest, controller='other')


if __name__ == '__main__': unittest.main()
