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
from test_batch_tool import docx


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

    def test_snapshot_normal_result_and_one_read(self):
        self.assertEqual(self.read_once(self.snapshot, lambda: bundle.verify(self.manifest)), self.snapshot_root)

    def test_handoff_normal_result_and_one_read(self):
        self.assertEqual(self.read_once(self.handoff, self.verify_handoff),
                         {'handoff': self.handoff_data, 'input_report': None})

    def test_inspection_normal_result_and_one_read(self):
        expected = json.loads(self.report.read_text(encoding='utf-8'))
        self.assertEqual(self.read_once(self.report, self.verify_cache), expected)

    def test_snapshot_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        altered = json.loads(self.snapshot.read_text(encoding='utf-8'))
        altered['rules_sha256'] = 'b'*64
        self.assertEqual(self.replace_after_read(self.snapshot, altered,
                         lambda: bundle.verify(self.manifest)), self.snapshot_root)
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            bundle.verify(self.manifest)

    def test_handoff_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        altered = dict(self.handoff_data, context={'case_id': '002'})
        result = self.replace_after_read(self.handoff, altered, self.verify_handoff)
        self.assertEqual(result['handoff'], self.handoff_data)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            self.verify_handoff()

    def test_report_parses_hashed_bytes_and_next_call_rechecks_actual_file(self):
        expected = json.loads(self.report.read_text(encoding='utf-8'))
        altered = dict(expected, unverified_marker=True)
        self.assertEqual(self.replace_after_read(self.report, altered, self.verify_cache), expected)
        with self.assertRaisesRegex(ValueError, 'Cache artifact changed'):
            self.verify_cache()

    def test_each_snapshot_check_still_verifies_runtime_and_identity(self):
        bundle.verify(self.manifest)
        self.runtime.write_text('# changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'file changed'):
            bundle.verify(self.manifest)
        wrong = copy.deepcopy(self.manifest); wrong['rules_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            bundle.verify(wrong, full=False)

    def test_foreign_handoff_path_and_resigned_context_are_rejected(self):
        foreign = self.root/'handoff.json'; foreign.write_bytes(self.handoff.read_bytes())
        self.attempt['prepared_handoff']['path'] = str(foreign)
        with self.assertRaisesRegex(ValueError, 'handoff changed'):
            self.verify_handoff()
        self.attempt['prepared_handoff']['path'] = str(self.handoff)
        self.write_json(self.handoff, dict(self.handoff_data, context={'case_id': '002'}))
        self.attempt['prepared_handoff']['sha256'] = self.sha(self.handoff)
        with self.assertRaisesRegex(ValueError, 'context mismatch'):
            self.verify_handoff()

    def test_resigned_foreign_report_and_changed_cache_metadata_are_rejected(self):
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


if __name__ == '__main__':
    unittest.main()
