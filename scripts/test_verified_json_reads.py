"""Single-read hash/parse boundaries; local fixtures, never semantic acceptance."""
import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import inspect_docx as inspector
import prepare_handoff as handoff
import stage_bundle as bundle
import batch_tool as batch
import parallel_batch as queue
import incremental_review as reuse
import worker_receipt as receipts
import test_worker_receipt as receipt_fixtures
import test_nonblocking_pools as pool_fixtures
from test_batch_tool import docx
from test_parallel_batch import POLICY


class VerifiedJsonReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root/'batch'
        self.snapshot_root = self.batch/'skill-snapshot'
        self.snapshot_root.mkdir(parents=True)
        self.runtime = self.snapshot_root/'runtime.py'
        self.runtime.write_text('# frozen fixture', encoding='utf-8')
        self.policy = {'worker_preparation': bundle.MODE}
        self.snapshot = self.snapshot_root/'snapshot.json'
        self.write_json(self.snapshot, {
            'mode': bundle.MODE, 'rules_sha256': 'a'*64,
            'policy_sha256': hashlib.sha256(bundle.canonical(self.policy).encode()).hexdigest(),
            'files': {'runtime.py': self.sha(self.runtime)}})
        self.manifest = {'batch_dir': str(self.batch), 'policy': self.policy,
                         'rules_sha256': 'a'*64,
                         'skill_snapshot': {'path': str(self.snapshot), 'sha256': self.sha(self.snapshot)}}
        self.attempt_root = self.batch/'cases'/'001'/'stages'/'attempt'
        prepared = self.attempt_root/'prepared'; prepared.mkdir(parents=True)
        self.handoff = prepared/'handoff.json'
        self.handoff_data = {'context': {'case_id': '001'}, 'prepared_files': [],
                             'source_inspections': [], 'input_inspection': None}
        self.write_json(self.handoff, self.handoff_data)
        self.handoff_manifest = {'batch_dir': str(self.batch), 'policy': {}}
        self.case = {'id': '001', 'sources': []}
        self.attempt = {'directory': str(self.attempt_root), 'context': {'case_id': '001'},
                        'prepared_handoff': {'path': str(self.handoff), 'sha256': self.sha(self.handoff)}}
        self.source = self.root/'source.docx'
        docx(self.source, '本地单次读取验证样例。')
        self.summary = handoff.seal_inspection(inspector.run_inspection(
            self.source, self.root/'cache', 'proposal', True, True))
        self.cache = Path(self.summary['output_directory'])
        self.report = Path(self.summary['inspection'])
        self.identity = inspector.cache_identity(self.source, 'proposal', True)

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def read_once(self, target, function):
        original = Path.open
        reads = Counter()
        def tracked(path, *args, **kwargs):
            reads[path] += 1
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', new=tracked):
            result = function()
        self.assertEqual(reads[target], 1)
        return result

    def replace_after_read(self, target, value, function):
        original = Path.read_bytes
        changed = []
        def replacing(path):
            data = original(path)
            if path == target and not changed:
                self.write_json(target, value)
                changed.append(True)
            return data
        with patch.object(Path, 'read_bytes', new=replacing):
            result = function()
        self.assertTrue(changed)
        return result

    def verify_handoff(self):
        return handoff.verify_prepared(self.handoff_manifest, self.case, self.attempt)

    def verify_cache(self):
        return inspector.verified_cache(self.cache, self.identity)

    def _case_snapshot_normal_result_and_one_read(self):
        self.assertEqual(self.read_once(self.snapshot, lambda: bundle.verify(self.manifest)), self.snapshot_root)

    def _case_handoff_normal_result_and_one_read(self):
        self.assertEqual(self.read_once(self.handoff, self.verify_handoff),
                         {'handoff': self.handoff_data, 'input_report': None})

    def _case_inspection_normal_result_and_one_read(self):
        expected = json.loads(self.report.read_text(encoding='utf-8'))
        self.assertEqual(self.read_once(self.report, self.verify_cache), expected)

    def _case_snapshot_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        altered = json.loads(self.snapshot.read_text(encoding='utf-8'))
        altered['rules_sha256'] = 'b'*64
        self.assertEqual(self.replace_after_read(self.snapshot, altered,
                         lambda: bundle.verify(self.manifest)), self.snapshot_root)
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            bundle.verify(self.manifest)

    def _case_handoff_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        altered = dict(self.handoff_data, context={'case_id': '002'})
        result = self.replace_after_read(self.handoff, altered, self.verify_handoff)
        self.assertEqual(result['handoff'], self.handoff_data)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            self.verify_handoff()

    def _case_report_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        expected = json.loads(self.report.read_text(encoding='utf-8'))
        altered = dict(expected, unverified_marker=True)
        self.assertEqual(self.replace_after_read(self.report, altered, self.verify_cache), expected)
        with self.assertRaisesRegex(ValueError, 'Cache artifact changed'):
            self.verify_cache()

    def _case_each_snapshot_check_still_verifies_runtime_and_identity(self):
        bundle.verify(self.manifest)
        self.runtime.write_text('# changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'file changed'):
            bundle.verify(self.manifest)
        wrong = copy.deepcopy(self.manifest); wrong['rules_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            bundle.verify(wrong, full=False)

    def _case_foreign_handoff_path_and_resigned_context_are_rejected(self):
        foreign = self.root/'handoff.json'; foreign.write_bytes(self.handoff.read_bytes())
        self.attempt['prepared_handoff']['path'] = str(foreign)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            self.verify_handoff()
        self.attempt['prepared_handoff']['path'] = str(self.handoff)
        self.write_json(self.handoff, dict(self.handoff_data, context={'case_id': '002'}))
        self.attempt['prepared_handoff']['sha256'] = self.sha(self.handoff)
        with self.assertRaisesRegex(ValueError, 'context mismatch'):
            self.verify_handoff()

    def _case_resigned_foreign_report_and_changed_cache_metadata_are_rejected(self):
        metadata = self.cache/'cache.json'
        altered = json.loads(self.report.read_text(encoding='utf-8'))
        altered['input'] = str(self.root/'other.docx')
        self.write_json(self.report, altered)
        values = json.loads(metadata.read_text(encoding='utf-8'))
        next(row for row in values['outputs'] if row['file'] == 'inspection.json')['sha256'] = self.sha(self.report)
        self.write_json(metadata, values)
        with self.assertRaisesRegex(ValueError, 'source/status mismatch'):
            self.verify_cache()
        with self.assertRaisesRegex(ValueError, 'cache metadata changed'):
            handoff.verify_inspection(self.summary, self.source, self.identity['source_sha256'],
                                      'proposal', [self.root/'cache'])

    def test_valid_json_family_reads_and_returns_each_object_once(self):
        # Independent error paths retain their assertions; setup is shared within this family.
        for name in ('_case_snapshot_normal_result_and_one_read', '_case_handoff_normal_result_and_one_read', '_case_inspection_normal_result_and_one_read'):
            with self.subTest(case=name):
                getattr(self, name)()

    def test_hash_parse_race_family_uses_verified_bytes_and_rechecks_next_call(self):
        # Independent error paths retain their assertions; setup is shared within this family.
        for name in ('_case_snapshot_parses_hashed_bytes_and_next_call_rechecks_actual_file', '_case_handoff_parses_hashed_bytes_and_next_call_rechecks_actual_file', '_case_report_parses_hashed_bytes_and_next_call_rechecks_actual_file'):
            with self.subTest(case=name):
                getattr(self, name)()

    def test_integrity_family_rejects_tamper_foreign_identity_and_resealing(self):
        # Independent error paths retain their assertions; setup is shared within this family.
        for name in ('_case_each_snapshot_check_still_verifies_runtime_and_identity', '_case_foreign_handoff_path_and_resigned_context_are_rejected', '_case_resigned_foreign_report_and_changed_cache_metadata_are_rejected'):
            with self.subTest(case=name):
                getattr(self, name)()


class RuleSnapshotReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root/'input'; source.mkdir()
        docx(source/'case.docx', '规则快照读取测试。')
        policy = self.root/'policy.json'; batch.atomic(policy, POLICY)
        rules = self.root/'rules.json'; batch.atomic(rules, {'version': 4, 'rules': []})
        with patch.object(queue, 'POLICY', policy), patch.object(queue, 'RULES', rules):
            self.manifest = queue.init(source, self.root/'work',
                {'projects': [{'title': '测试', 'source_files': ['case.docx'], 'primary_source': 'case.docx'}]},
                self.root/'desktop')
        self.rules = self.manifest.parent/'rules.snapshot.json'

    def test_rule_hash_and_version_share_one_read_without_cross_call_cache(self):
        raw = Path.read_bytes
        shared = batch.shared_reader
        hash_file = batch.sha
        reads = []
        def read_bytes(path):
            if path == self.rules: reads.append('bytes')
            return raw(path)
        def shared_reader(path):
            if Path(path) == self.rules: reads.append('shared')
            return shared(path)
        def file_sha(path):
            if Path(path) == self.rules: reads.append('sha')
            return hash_file(path)
        with patch.object(Path, 'read_bytes', read_bytes), patch.object(batch, 'shared_reader', shared_reader), \
                patch.object(batch, 'sha', file_sha):
            for call in range(1, 3):
                self.assertEqual(queue.load_manifest(self.manifest)['rule_version'], 4)
                self.assertEqual(len(reads), call)

    def test_rule_parse_uses_hashed_bytes_and_next_call_rejects_replacement(self):
        original = Path.read_bytes
        replaced = []
        def read_then_replace(path):
            data = original(path)
            if path == self.rules and not replaced:
                batch.atomic(self.rules, {'version': 999, 'rules': []})
                replaced.append(True)
            return data
        with patch.object(Path, 'read_bytes', read_then_replace):
            self.assertEqual(queue.load_manifest(self.manifest)['rule_version'], 4)
        self.assertTrue(replaced)
        with self.assertRaisesRegex(batch.BatchError, 'Rule snapshot changed'):
            queue.load_manifest(self.manifest)

    def test_rule_bom_version_and_hash_guards_are_preserved(self):
        for label, raw, resign, error in [
            ('bom', b'\xef\xbb\xbf'+b'{"version":4,"rules":[]}', True, None),
            ('version', b'{"version":5,"rules":[]}', True, batch.BatchError),
            ('hash_before_decode', b'\xff', False, batch.BatchError),
            ('invalid_json', b'{', True, json.JSONDecodeError),
        ]:
            with self.subTest(case=label):
                self.rules.write_bytes(raw)
                manifest = batch.read(self.manifest)
                if resign: manifest['rules_sha256'] = hashlib.sha256(raw).hexdigest()
                batch.atomic(self.manifest, manifest)
                if error:
                    with self.assertRaises(error): queue.load_manifest(self.manifest)
                else:
                    self.assertEqual(queue.load_manifest(self.manifest)['rule_version'], 4)


def assert_single_json_read(test, target, function):
    """Count hash/parse reads, including the Windows shared-reader path."""
    raw, read_json, hash_file = Path.read_bytes, batch.read, batch.sha
    reads = []
    def observe(label, original):
        def run(path, *args, **kwargs):
            if Path(path) == target:
                reads.append(label)
            return original(path, *args, **kwargs)
        return run
    with patch.object(Path, 'read_bytes', observe('bytes', raw)), \
            patch.object(batch, 'read', observe('json', read_json)), \
            patch.object(batch, 'sha', observe('sha', hash_file)):
        result = function()
    test.assertEqual(reads, ['bytes'])
    return result


def assert_bound_read_and_fresh_next_call(test, target, replacement, function, expected, error):
    """Replacing a file after its read cannot change the parsed verified object."""
    raw = Path.read_bytes
    original_bytes = raw(target)
    changed = []
    def replace_after_read(path):
        data = raw(path)
        if path == target and not changed:
            target.write_text(json.dumps(replacement), encoding='utf-8')
            changed.append(True)
        return data
    try:
        with patch.object(Path, 'read_bytes', replace_after_read):
            test.assertEqual(function(), expected)
        test.assertTrue(changed)
        with test.assertRaisesRegex(batch.BatchError, error):
            function()
    finally:
        target.write_bytes(original_bytes)


class IncrementalPlanReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.prepared = self.root/'cases/001/stages/review/prepared'
        self.prepared.mkdir(parents=True)
        self.plan_path = self.prepared/'review-reuse.json'
        self.handoff_path = self.prepared/'handoff.json'
        self.context = {'case_id': '001', 'stage': 'review', 'attempt': 'review'}
        self.attempt = {'directory': str(self.prepared.parent), 'context': self.context,
                        'input_artifact_sha256': 'a'*64}
        self.plan = {'schema_version': 1, 'context': self.context, 'input_sha256': 'a'*64}
        self.write_plan(self.plan)

    def write_plan(self, value, bom=False):
        self.plan_path.write_text(json.dumps(value), encoding='utf-8-sig' if bom else 'utf-8')
        self.pointer = {'path': str(self.plan_path), 'plan_sha256': batch.sha(self.plan_path)}
        self.handoff = {'context': self.context, 'review_reuse': self.pointer}
        self.handoff_path.write_text(json.dumps(self.handoff), encoding='utf-8-sig' if bom else 'utf-8')
        self.attempt['prepared_handoff'] = {'path': str(self.handoff_path),
                                            'sha256': batch.sha(self.handoff_path)}

    def load(self):
        return reuse.plan_pointer(self.attempt)

    def test_plan_and_handoff_each_read_once_with_bom_preserved(self):
        self.write_plan(self.plan, bom=True)
        for target in (self.plan_path, self.handoff_path):
            with self.subTest(file=target.name):
                self.assertEqual(assert_single_json_read(self, target, self.load),
                                 (self.pointer, self.plan))

    def test_plan_and_handoff_parse_only_hashed_bytes_and_recheck_next_call(self):
        for target, value, error in ((self.plan_path, self.plan, 'review plan changed'),
                                     (self.handoff_path, self.handoff, 'handoff changed')):
            with self.subTest(file=target.name):
                assert_bound_read_and_fresh_next_call(self, target,
                    dict(value, context={'case_id': 'foreign'}), self.load,
                    (self.pointer, self.plan), error)

    def test_resealed_foreign_context_input_and_path_are_still_rejected(self):
        for update, error in (({'context': {'case_id': 'foreign'}}, 'plan context changed'),
                              ({'input_sha256': 'b'*64}, 'plan input changed')):
            with self.subTest(update=update):
                self.write_plan(dict(self.plan, **update))
                with self.assertRaisesRegex(batch.BatchError, error): self.load()
        self.write_plan(self.plan)
        foreign = self.root/'review-reuse.json'
        foreign.write_bytes(self.plan_path.read_bytes())
        changed_pointer = dict(self.pointer, path=str(foreign))
        with self.assertRaises(batch.BatchError):
            reuse.load_plan(changed_pointer, self.attempt)


class ReceiptIdentityReadTests(unittest.TestCase):
    # Reuse setup helpers only; do not inherit and re-run their entire test suite.
    setUp = receipt_fixtures.ReceiptPreflightTests.setUp
    job = receipt_fixtures.ReceiptPreflightTests.job
    deferred_job = receipt_fixtures.ReceiptPreflightTests.deferred_job

    def prepare(self, bom=False):
        self.job_path = self.deferred_job()
        self.reservation = self.attempt_root/'reservation.json'
        self.handoff = self.attempt_root/'prepared/handoff.json'
        if bom:
            for path in (self.reservation, self.handoff):
                path.write_bytes(b'\xef\xbb\xbf'+path.read_bytes())
            self.reseal_job()
            self.job_path.write_bytes(b'\xef\xbb\xbf'+self.job_path.read_bytes())
            job = batch.read(self.job_path)
            batch.atomic(self.attempt_root/'cli-launch-intent.json',
                         {'context': job['attempt']['context'], 'job_sha256': batch.sha(self.job_path)})
        self.decisions = {'verdict': 'complete', 'artifact': str(self.artifact)}
        return receipts.assemble(self.job_path, self.decisions)

    def reseal_job(self):
        job = batch.read(self.job_path)
        job['attempt']['reservation_sha256'] = batch.sha(self.reservation)
        job['attempt']['prepared_handoff']['sha256'] = batch.sha(self.handoff)
        batch.atomic(self.job_path, job)
        batch.atomic(self.attempt_root/'cli-launch-intent.json',
                     {'context': job['attempt']['context'], 'job_sha256': batch.sha(self.job_path)})

    def assemble(self):
        return receipts.assemble(self.job_path, self.decisions)

    def test_job_reservation_and_handoff_each_read_once_with_bom_preserved(self):
        expected = self.prepare(bom=True)
        for target in (self.job_path, self.reservation, self.handoff):
            with self.subTest(file=target.name):
                self.assertEqual(assert_single_json_read(self, target, self.assemble), expected)

    def test_job_reservation_and_handoff_parse_hashed_bytes_and_recheck_next_call(self):
        expected = self.prepare()
        for target, error in ((self.job_path, 'launch intent changed'),
                               (self.reservation, 'reservation changed'),
                               (self.handoff, 'preparation seal changed')):
            with self.subTest(file=target.name):
                changed = dict(batch.read(target), context={'case_id': 'foreign'})
                assert_bound_read_and_fresh_next_call(self, target, changed, self.assemble, expected, error)

    def test_resealed_foreign_handoff_context_and_reservation_are_rejected(self):
        self.prepare()
        originals = {path: path.read_bytes() for path in (self.reservation, self.handoff)}
        for target, error in ((self.reservation, 'reservation changed'),
                               (self.handoff, 'preparation context changed')):
            with self.subTest(file=target.name):
                for path, value in originals.items(): path.write_bytes(value)
                batch.atomic(target, dict(batch.read(target), context={'case_id': 'foreign'}))
                self.reseal_job()
                with self.assertRaisesRegex(batch.BatchError, error): self.assemble()


class DeferredPreparationReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        source = self.root/'input'; source.mkdir()
        docx(source/'source.docx', '本地准备交接样例。')
        policy = self.root/'policy.json'; batch.atomic(policy, pool_fixtures.policy())
        rules = self.root/'rules.json'; batch.atomic(rules, {'version': 36, 'rules': []})
        with patch.object(queue, 'POLICY', policy), patch.object(queue, 'RULES', rules):
            self.manifest = queue.init(source, self.root/'work',
                {'projects': [{'title': '准备交接样例', 'source_files': ['source.docx'],
                               'primary_source': 'source.docx'}]}, self.root/'desktop')
        self.reserved = queue.reserve(self.manifest, 1, transport='codex_cli', defer_preparation=True)
        self.result = queue.prepare_reserved(self.manifest, self.reserved['case_id'], self.reserved['attempt']['id'])
        self.handoff = Path(self.result['seal']['path'])

    def finish(self):
        return queue.finish_preparation(self.manifest, self.result)

    def test_finish_consumes_verified_handoff_without_reopening(self):
        ready = assert_single_json_read(self, self.handoff, self.finish)
        self.assertEqual(ready['worker_context']['prepared'], self.result['prepared'])
        self.assertEqual(ready['attempt']['preparation_state'], 'ready')
        self.handoff.write_bytes(b'{"context":"changed"}')
        current = queue.load_manifest(self.manifest)
        case = queue.case_by_id(current, self.reserved['case_id'])
        attempt = queue.attempt_by_id(case, ready['attempt']['id'])
        with self.assertRaisesRegex(ValueError, 'Prepared handoff changed'):
            handoff.verify_prepared(current, case, attempt)

    def test_finish_still_rejects_unsealed_payload_and_keeps_pending(self):
        self.result['prepared']['context'] = {'case_id': 'foreign'}
        before = self.manifest.read_bytes()
        with self.assertRaisesRegex(batch.BatchError, 'payload differs from sealed handoff'):
            self.finish()
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((Path(self.reserved['attempt']['directory'])/'cli-launch-intent.json').exists())


if __name__ == '__main__':
    unittest.main()
