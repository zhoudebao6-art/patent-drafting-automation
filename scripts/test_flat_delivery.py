"""Flat publication fixtures test filesystem safety, not real model acceptance."""
import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import flat_delivery as flat
import incremental_delivery as inc
import test_batch_tool as serial
import test_parallel_batch as parallel

LEGACY_DELIVERY = {'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}
DELIVERY = {'name_source': 'input_folder', 'folder_suffix': '终稿', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}


def use_flat(test):
    m = b.read(test.manifest); m['policy']['delivery'] = copy.deepcopy(DELIVERY)
    b.atomic(test.manifest, m); b.atomic(test.batch / 'policy.snapshot.json', m['policy'])


def assert_flat(test, dest, count):
    test.assertEqual(len(list(dest.glob('*.docx'))), count)
    test.assertEqual(len(list(dest.rglob('*.docx'))), count)
    test.assertEqual({x.name for x in dest.iterdir() if x.suffix != '.docx'}, {'.delivery-owner.json'})
    test.assertFalse(any(x.is_dir() for x in dest.iterdir()))
    test.assertEqual(b.read(test.manifest), b.read(test.batch / 'flat-delivery' / '批次清单.json'))
    if os.name == 'nt': test.assertTrue((dest / '.delivery-owner.json').stat().st_file_attributes & 0x2)


class SerialFlatTests(unittest.TestCase):
    receipt = serial.BatchTests.receipt
    complete = serial.BatchTests.complete

    def setUp(self):
        serial.BatchTests.setUp(self); use_flat(self)

    def test_policy_exact_new_shape_and_legacy_remain_valid(self):
        b.validate_policy(serial.POLICY)
        m = b.read(self.manifest); b.validate_policy(m['policy'])
        legacy = copy.deepcopy(m); legacy['policy']['delivery'] = copy.deepcopy(LEGACY_DELIVERY)
        b.validate_policy(legacy['policy'])
        self.assertEqual(flat.destination(legacy).name, '专利撰写完成_' + m['batch_id'])
        self.assertEqual(flat.destination(m).name, self.src.name + '_终稿_' + m['batch_id'])
        self.assertEqual(m['policy']['models'], serial.POLICY['models'])
        for extra in ({'accepted_subdir': '已完成'}, {'layout': 'other'}, {'timing': None},
                      {'folder_prefix': '专利撰写完成'}, {'name_source': 'case_title'}, {'folder_suffix': '其他'}):
            value = copy.deepcopy(m['policy']); value['delivery'].update(extra)
            with self.assertRaises(b.BatchError): b.validate_policy(value)

    def test_full_input_name_preserved_independent_of_titles_and_internal_sources(self):
        m = b.read(self.manifest)
        original = '1014周丽2-20261014 SPN项目 ' + '完整名称' * 25
        m['input'] = str(self.root / original)
        expected = original + '_终稿_' + m['batch_id']
        self.assertEqual(flat.folder_name(m), expected)
        m['projects'].reverse()
        m['projects'][0]['title'] = '案件名称不用于目录'
        m['projects'][0]['primary_source'] = str(self.batch / 'cases' / '001' / 'sources' / 'internal.docx')
        self.assertEqual(flat.folder_name(m), expected)
        self.assertNotEqual(flat.folder_name(dict(m, batch_id=m['batch_id'] + 'a')), expected)

    def test_invalid_input_basename_fails_without_sanitization(self):
        m = b.read(self.manifest)
        for name in ('', '坏?', '坏*', '结尾.', '结尾 ', 'NUL', 'con.txt', 'CON .txt', 'CONIN$', 'COM¹', 'LPT9'):
            with self.subTest(name=name), self.assertRaisesRegex(b.BatchError, 'basename|Windows'):
                flat.folder_name(dict(m, input=str(self.root / name) if name else self.root.anchor))

    def test_utf16_name_limit_is_checked_with_suffix_and_surrogate_pair(self):
        m = b.read(self.manifest)
        suffix_units = len(('_终稿_' + m['batch_id']).encode('utf-16-le')) // 2
        source_name = '中' * (255 - suffix_units - 2) + '😀'
        exact = dict(m, input=str(self.root / source_name))
        self.assertEqual(len(flat.folder_name(exact).encode('utf-16-le')) // 2, 255)
        with self.assertRaisesRegex(b.BatchError, '255 UTF-16'):
            flat.folder_name(dict(m, input=str(self.root / (source_name + '中'))))

    def test_serial_and_parallel_init_validate_name_before_creating_batch(self):
        # Virtual roots isolate preflight ordering from host path-length settings.
        original_root_path = b.root_path
        for module, base in ((b, serial.POLICY), (p, parallel.POLICY)):
            policy = copy.deepcopy(base); policy['delivery'] = copy.deepcopy(DELIVERY)
            policy_file = self.root / ('init-policy-' + module.__name__ + '.json')
            b.atomic(policy_file, policy)
            for invalid in (self.root.anchor, str(self.root / ('长' * 240))):
                work = self.root / ('not-created-' + module.__name__)
                def root_path(path, required=False):
                    return Path(invalid) if str(path) == str(self.src) else original_root_path(path, required)
                with self.subTest(module=module.__name__, invalid=invalid), \
                        patch.object(module, 'POLICY', policy_file), patch.object(b, 'root_path', side_effect=root_path):
                    with self.assertRaisesRegex(b.BatchError, 'basename|255 UTF-16'):
                        module.init(self.src, work, {'projects': []}, self.root / 'desktop')
                self.assertFalse(work.exists())

    def test_new_init_freezes_original_named_root_without_extra_manifest_field(self):
        source = self.root / '1014周丽2-20261014 SPN项目'; source.mkdir()
        nested = source / '解压材料'; nested.mkdir(); serial.docx(nested / 'a.docx', 'fixture')
        selection = {'projects': [{'title': '另一个案件标题', 'source_files': ['解压材料/a.docx'], 'primary_source': '解压材料/a.docx'}]}
        for module, base in ((b, serial.POLICY), (p, parallel.POLICY)):
            policy = copy.deepcopy(base); policy['delivery'] = copy.deepcopy(DELIVERY)
            policy_file = self.root / ('named-policy-' + module.__name__ + '.json'); b.atomic(policy_file, policy)
            with patch.object(module, 'POLICY', policy_file), patch.object(module, 'RULES', b.RULES):
                manifest = module.init(source, self.root / ('named-work-' + module.__name__), selection, self.root / 'desktop')
            m = b.read(manifest)
            self.assertEqual(m['input'], str(source.resolve()))
            self.assertEqual(flat.destination(m).name, source.name + '_终稿_' + m['batch_id'])
            self.assertEqual(m['policy']['models'], base['models'])
            self.assertEqual(m['policy']['delivery']['timing'], 'after_each_acceptance')

    def test_partial_append_same_root_stable_names_and_idempotent(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest))
        self.assertEqual(dest.name, self.src.name + '_终稿_' + b.read(self.manifest)['batch_id'])
        one = dest / '同名_001.docx'; before = (one.read_bytes(), one.stat().st_mtime_ns)
        assert_flat(self, dest, 1)
        self.assertFalse(b.read(self.manifest)['delivery_closed'])
        self.complete('002'); self.assertEqual(b.deliver(self.manifest), str(dest))
        self.assertEqual((one.read_bytes(), one.stat().st_mtime_ns), before)
        self.assertTrue((dest / '同名_002.docx').exists()); assert_flat(self, dest, 2)
        before = {x.name: (x.read_bytes(), x.stat().st_mtime_ns) for x in dest.iterdir()}
        b.deliver(self.manifest)
        self.assertEqual(before, {x.name: (x.read_bytes(), x.stat().st_mtime_ns) for x in dest.iterdir()})

    def test_completed_batch_does_not_dispatch_legacy_and_unique_name_is_plain(self):
        m = b.read(self.manifest); m['projects'][0]['title'] = '甲项目'; m['projects'][1]['title'] = '乙项目'
        b.atomic(self.manifest, m)
        self.complete('001'); self.complete('002')
        with patch.object(b, '_deliver_legacy', side_effect=AssertionError('legacy must not run')):
            dest = Path(b.deliver(self.manifest)); b.deliver(self.manifest)
        self.assertEqual({x.name for x in dest.glob('*.docx')}, {'甲项目.docx', '乙项目.docx'})
        assert_flat(self, dest, 2)

    def test_no_pass_never_publishes_and_pending_notes_stay_in_work(self):
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        b.record(self.manifest, '001', self.receipt('draft'))
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        b.defer(self.manifest, '001', 'needs_input', '缺少源公式')
        b.defer(self.manifest, '002', 'failed', '渲染失败')
        dest = Path(b.deliver(self.manifest)); assert_flat(self, dest, 0)
        note = (self.batch / 'flat-delivery' / '交付清单.txt').read_text(encoding='utf-8')
        self.assertIn('缺少源公式', note); self.assertIn('渲染失败', note)
        self.assertTrue(b.read(self.manifest)['delivery_closed'])

    def test_names_handle_windows_case_cleanup_and_second_order_collisions(self):
        cases = [{'id': f'{i:03d}', 'title': title} for i, title in enumerate(
            ['A', 'a', 'a_002', '坏?', '坏*', '正常', 'CON', '_CON'], 1)]
        first = flat.names({'projects': cases}); second = flat.names({'projects': list(reversed(cases))})
        self.assertEqual(first, second)
        self.assertEqual(len({flat.name_key(x) for x in first.values()}), len(cases))
        self.assertEqual(first['003'], 'a_002.docx'); self.assertIn('002', first['002'])
        self.assertEqual(first['006'], '正常.docx')

    def test_copy_failure_retries_without_unaccepted_final(self):
        self.complete('001')
        with patch.object(flat.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        self.assertFalse(list((self.root / 'desktop').rglob('*.docx')))
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'accepted')
        assert_flat(self, Path(b.deliver(self.manifest)), 1)

    def test_unknown_root_is_preserved(self):
        self.complete('001'); m = b.read(self.manifest)
        dest = flat.destination(m); dest.mkdir(parents=True)
        sentinel = dest / 'keep.txt'; sentinel.write_bytes(b'keep')
        with self.assertRaises((b.BatchError, FileNotFoundError)): b.deliver(self.manifest)
        self.assertEqual(sentinel.read_bytes(), b'keep'); self.assertFalse(list(dest.glob('*.docx')))

    def test_foreign_file_even_identical_bytes_is_not_adopted(self):
        self.complete('001')
        with patch.object(flat.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        m = b.read(self.manifest); dest = flat.destination(m); c = m['projects'][0]
        target = dest / flat.names(m)[c['id']]; target.write_bytes(Path(c['artifact']).read_bytes())
        before = target.read_bytes()
        with self.assertRaisesRegex(b.BatchError, 'receipt|Unowned'): b.deliver(self.manifest)
        self.assertEqual(target.read_bytes(), before)

    def test_publish_then_manifest_crash_keeps_newer_other_case_progress(self):
        self.complete('001'); original = b.atomic
        def fail(path, value):
            if Path(path) == self.manifest and value.get('delivery_path'): raise OSError('commit interrupted')
            return original(path, value)
        with patch.object(b, 'atomic', side_effect=fail):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        one = next((self.root / 'desktop').rglob('*.docx')); before = (one.read_bytes(), one.stat().st_mtime_ns)
        b.record(self.manifest, '002', self.receipt('draft', '2'))
        serial.docx(self.src / 'a.docx', 'source edited after publication')
        dest = Path(b.deliver(self.manifest)); assert_flat(self, dest, 1)
        self.assertEqual((one.read_bytes(), one.stat().st_mtime_ns), before)
        m = b.read(self.manifest)
        self.assertEqual(m['projects'][1]['status'], 'drafted')
        self.assertTrue(m['delivery_recovery_events'][0]['original_inputs_changed'])

    def test_staged_publication_interruption_reuses_verified_stage(self):
        self.complete('001')
        with patch.object(flat, 'publish_file', side_effect=OSError('move interrupted')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        m = b.read(self.manifest); record = b.read(flat.receipt_path(m, m['projects'][0]))
        staged = flat.staging_directory(m, flat.destination(m)) / record['staging_file']; identity = flat.file_identity(staged)
        dest = Path(b.deliver(self.manifest)); assert_flat(self, dest, 1)
        self.assertEqual(flat.file_identity(next(dest.glob('*.docx'))), identity)

    def assert_real_process_death_recovers_clean_root(self, boundary):
        self.complete('001')
        program = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import batch_tool as b
import flat_delivery as flat
original = b.atomic
def crash(path, value):
    if Path(path).parent.name == 'cases' and 'publication' in value: os._exit(73)
    return original(path, value)
def crash_copy(*args): os._exit(73)
with patch.object(b, 'atomic', side_effect=crash) if sys.argv[3] == 'before_intent' else patch.object(flat.shutil, 'copyfileobj', side_effect=crash_copy):
    b.deliver(Path(sys.argv[2]))
'''
        run = subprocess.run([sys.executable, '-X', 'utf8', '-c', program, str(Path(__file__).parent),
                              str(self.manifest), boundary], capture_output=True, text=True)
        self.assertEqual(run.returncode, 73, run.stdout + run.stderr)
        m = b.read(self.manifest); dest = flat.destination(m); stage = flat.staging_directory(m, dest)
        orphan = next(stage.glob('.case-*.tmp')); before = (orphan.read_bytes(), flat.file_identity(orphan))
        # A foreign file matching the scratch prefix is deliberately preserved.
        foreign = stage / '.case-001-user.tmp'; foreign.write_bytes(b'user scratch')
        self.assertEqual({q.name for q in dest.iterdir()}, {'.delivery-owner.json'})
        if os.name == 'nt': self.assertTrue(stage.stat().st_file_attributes & 0x2)
        assert_flat(self, Path(b.deliver(self.manifest)), 1)
        self.assertEqual((orphan.read_bytes(), flat.file_identity(orphan)), before)
        self.assertEqual(foreign.read_bytes(), b'user scratch')

    def test_real_process_death_before_intent_leaves_only_hidden_staging_remnant(self):
        self.assert_real_process_death_recovers_clean_root('before_intent')

    def test_real_process_death_before_copy_leaves_only_hidden_staging_remnant(self):
        self.assert_real_process_death_recovers_clean_root('before_copy')

    def test_foreign_staging_owner_is_preserved(self):
        self.complete('001')
        with patch.object(flat.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        m = b.read(self.manifest); stage = flat.staging_directory(m, flat.destination(m))
        marker = stage / '.delivery-owner.json'; b.atomic(marker, {'foreign': True})
        with self.assertRaisesRegex(b.BatchError, 'Staging directory belongs'):
            b.deliver(self.manifest)
        self.assertEqual(b.read(marker), {'foreign': True})

    def test_previous_root_staging_intent_remains_recoverable(self):
        self.complete('001')
        with patch.object(flat, 'publish_file', side_effect=OSError('move interrupted')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        m = b.read(self.manifest); dest = flat.destination(m)
        receipt = flat.receipt_path(m, m['projects'][0]); record = b.read(receipt)
        staged = flat.staging_directory(m, dest) / record['staging_file']
        os.rename(staged, dest / record['staging_file'])
        record.pop('staging_area'); b.atomic(receipt, record)
        assert_flat(self, Path(b.deliver(self.manifest)), 1)

    def test_edited_delivered_file_and_missing_file_are_not_replaced(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest)); one = next(dest.glob('*.docx'))
        one.write_bytes(b'user edited')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertEqual(one.read_bytes(), b'user edited')
        one.unlink()
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertFalse(one.exists())

    def test_tampered_receipt_cannot_unlink_delivered_file(self):
        self.complete('001'); dest = Path(b.deliver(self.manifest)); m = b.read(self.manifest)
        one = next(dest.glob('*.docx')); before = one.read_bytes()
        path = flat.receipt_path(m, m['projects'][0]); record = b.read(path)
        record['staging_file'] = one.name; b.atomic(path, record)
        with self.assertRaisesRegex(b.BatchError, 'Invalid publication'): b.deliver(self.manifest)
        self.assertEqual(one.read_bytes(), before)

    def test_work_summary_crash_recovers_and_user_changes_preserved(self):
        self.complete('001'); original = inc.write_text_atomic; calls = []
        def fail(path, content):
            calls.append(path)
            if len(calls) == 2: raise OSError('summary interrupted')
            return original(path, content)
        with patch.object(inc, 'write_text_atomic', side_effect=fail):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        dest = Path(b.deliver(self.manifest)); assert_flat(self, dest, 1)
        note = self.batch / 'flat-delivery' / '交付清单.txt'; note.write_text('user note')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertEqual(note.read_text(), 'user note')


class ParallelFlatTests(unittest.TestCase):
    reserve = parallel.ParallelTests.reserve
    receipt = parallel.ParallelTests.receipt
    record = parallel.ParallelTests.record

    def setUp(self):
        parallel.ParallelTests.setUp(self); use_flat(self)

    def interrupted_publication(self):
        self.record(self.reserve(1)); self.record(self.reserve(1)); original = b.atomic
        def fail(path, value):
            if Path(path) == self.manifest and value.get('delivery_path'): raise OSError('commit interrupted')
            return original(path, value)
        with patch.object(b, 'atomic', side_effect=fail):
            with self.assertRaises(OSError): p.deliver(self.manifest)
        return next((self.root / 'desktop').rglob('*.docx'))

    def test_out_of_order_acceptance_stable_names_and_active_worker_unchanged(self):
        one, two = self.reserve(), self.reserve()
        self.record(two); self.record(self.reserve())
        before = copy.deepcopy(b.read(self.manifest)['projects'][0])
        dest = Path(p.deliver(self.manifest)); assert_flat(self, dest, 1)
        second = dest / '同名_002.docx'; before_file = (second.read_bytes(), second.stat().st_mtime_ns)
        self.assertEqual(b.read(self.manifest)['projects'][0], before)
        self.record(one); self.record(self.reserve())
        self.assertEqual(p.deliver(self.manifest), str(dest)); assert_flat(self, dest, 2)
        self.assertTrue((dest / '同名_001.docx').exists())
        self.assertEqual((second.read_bytes(), second.stat().st_mtime_ns), before_file)
        self.assertEqual(self.reserve()['attempt']['stage'], 'draft')
        self.assertEqual(dest.name, self.src.name + '_终稿_' + b.read(self.manifest)['batch_id'])
        self.assertEqual([item for item in dest.parent.iterdir() if item.is_dir() and not item.name.startswith('.')], [dest])

    def test_all_terminal_first_delivery_uses_flat_and_no_pending_files(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        for c in b.read(self.manifest)['projects'][1:]: p.defer(self.manifest, c['id'], 'needs_input', 'fixture missing')
        with patch.object(p, '_deliver_legacy', side_effect=AssertionError('legacy must not run')):
            dest = Path(p.deliver(self.manifest)); p.deliver(self.manifest)
        assert_flat(self, dest, 1); self.assertTrue(p.summary(self.manifest)['delivery_closed'])
        with self.assertRaisesRegex(b.BatchError, 'closed'): p.reserve(self.manifest, 1)

    def test_recover_publication_then_quarantine_only_unpublished_changed_source(self):
        one = self.interrupted_publication(); before = (one.read_bytes(), one.stat().st_mtime_ns)
        self.record(self.reserve(1)); self.record(self.reserve(1))
        serial.docx(self.src / '1.docx', 'published source changed')
        serial.docx(self.src / '2.docx', 'unpublished source changed')
        dest = Path(p.deliver(self.manifest)); assert_flat(self, dest, 1)
        m = b.read(self.manifest)
        self.assertEqual([c['status'] for c in m['projects'][:2]], ['delivered', 'needs_input'])
        self.assertTrue(m['delivery_recovery_events'][0]['original_inputs_changed'])
        self.assertEqual((one.read_bytes(), one.stat().st_mtime_ns), before)

    def test_source_change_during_copy_and_render_tampering_keep_gates(self):
        for _ in range(4): self.record(self.reserve(1))
        original = flat.shutil.copyfileobj; changes = []
        def change(src, out):
            result = original(src, out)
            if not changes: changes.append(True); serial.docx(self.src / '1.docx', 'changed during copy')
            return result
        with patch.object(flat.shutil, 'copyfileobj', side_effect=change):
            with self.assertRaisesRegex(b.BatchError, 'quarantined'): p.deliver(self.manifest)
        self.assertFalse(list((self.root / 'desktop').rglob('*.docx')))
        m = b.read(self.manifest); c = m['projects'][1]
        image = Path(c['receipts'][-1]['render']['evidence']).parent / 'page-1.png'
        before = image.read_bytes(); image.write_bytes(b'tampered')
        with self.assertRaises(b.BatchError): p.deliver(self.manifest)
        image.write_bytes(before)
        dest = Path(p.deliver(self.manifest)); assert_flat(self, dest, 1)
        self.assertTrue((dest / '同名_002.docx').exists())

    def test_recovered_publication_requires_unchanged_render_evidence(self):
        one = self.interrupted_publication(); before = one.read_bytes()
        c = b.read(self.manifest)['projects'][0]
        (Path(c['receipts'][-1]['render']['evidence']).parent / 'page-1.png').write_bytes(b'tampered')
        with self.assertRaises(b.BatchError): p.deliver(self.manifest)
        self.assertEqual(one.read_bytes(), before)
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'accepted')

    def test_same_controller_lock_and_foreign_controller_rejection(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        with self.assertRaises(b.BatchError): p.deliver(self.manifest, controller='foreign')
        with p.locked(self.manifest):
            with self.assertRaisesRegex(b.BatchError, 'lock busy'):
                with p.locked(self.manifest, timeout=.05): pass
        assert_flat(self, Path(p.deliver(self.manifest)), 1)


if __name__ == '__main__': unittest.main()
