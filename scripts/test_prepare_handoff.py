import unittest
import contextlib
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import inspect_docx as inspector
import prepare_handoff as handoff
import test_parallel_batch as fixtures


class HandoffTests(unittest.TestCase):
    setUp = fixtures.ParallelTests.setUp
    receipt = fixtures.ParallelTests.receipt

    def bind(self, result):
        a = result['attempt']
        dispatch = Path(a['directory'])/'dispatch.json'
        b.atomic(dispatch, {'tool': 'collaboration.spawn_agent', 'context': a['context'],
                            'arguments': result['spawn_arguments'], 'result': {'task_name': '/root/'+a['task_name']}})
        return p.bind(self.manifest, result['case_id'], a['id'], dispatch)

    def test_prepared_reservation_has_real_case_scoped_paths(self):
        r = p.reserve(self.manifest, 1, prepare=True)
        prepared = r['worker_context']['prepared']
        summary = prepared['source_inspections'][0]
        self.assertEqual(summary['cache_status'], 'miss')
        self.assertTrue(Path(summary['inspection']).is_relative_to(Path(r['worker_context']['case_directory'])/'source-cache'))
        index = b.read(summary['index'])
        self.assertEqual(index['sha256'], r['source_snapshots'][0]['sha256'])
        self.assertEqual(b.read(prepared['rules_active'])['snapshot_sha256'], r['attempt']['context']['rules_sha256'])
        self.bind(r)

    def test_next_stage_reuses_sources_and_writer_inspection_without_zip_parse(self):
        draft = p.reserve(self.manifest, 1, prepare=True); self.bind(draft)
        receipt = self.receipt(draft)
        cache = draft['worker_context']['prepared']['draft_inspection_cache']
        original = inspector.run_inspection(receipt['artifact'], cache, 'draft', True, True)
        p.record(self.manifest, draft['case_id'], draft['attempt']['id'], receipt)
        with patch.object(inspector, 'inspect', side_effect=AssertionError('Unexpected DOCX reparse')):
            review = p.reserve(self.manifest, 1, prepare=True)
        prepared = review['worker_context']['prepared']
        self.assertEqual(prepared['source_inspections'][0]['inspection'], draft['worker_context']['prepared']['source_inspections'][0]['inspection'])
        self.assertEqual(prepared['source_inspections'][0]['cache_status'], 'hit')
        self.assertEqual(prepared['input_inspection']['inspection'], original['inspection'])
        self.assertEqual(prepared['input_inspection']['cache_status'], 'hit')
        self.assertEqual(review['attempt']['stage'], 'review')
        self.assertEqual(p.load_manifest(self.manifest)['projects'][0]['status'], 'drafted')

    def test_tampered_active_rules_block_dispatch_binding(self):
        r = p.reserve(self.manifest, 1, prepare=True)
        Path(r['worker_context']['prepared']['rules_active']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(b.BatchError, 'Prepared rule/index file changed'):
            self.bind(r)

    def test_preparation_failure_does_not_commit_a_reservation(self):
        before = self.manifest.read_bytes()
        with patch.object(inspector, 'run_inspection', side_effect=ValueError('bad source')):
            with self.assertRaisesRegex(p.PreparationError, 'bad source'):
                p.reserve(self.manifest, 1, prepare=True)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertTrue(all(c['active_attempt'] is None for c in p.load_manifest(self.manifest)['projects']))

    def test_changed_source_cache_blocks_binding_without_reparse(self):
        r = p.reserve(self.manifest, 1, prepare=True)
        value = r['worker_context']['prepared']['source_inspections'][0]
        Path(value['content']).write_text('damaged', encoding='utf-8')
        with patch.object(inspector, 'inspect', side_effect=AssertionError('No ZIP parse at binding')):
            with self.assertRaisesRegex(b.BatchError, 'Cache artifact changed'):
                self.bind(r)
        self.assertEqual(p.load_manifest(self.manifest)['projects'][0]['attempts'][0]['state'], 'reserved')

    def test_missing_source_cache_blocks_binding(self):
        r = p.reserve(self.manifest, 1, prepare=True)
        Path(r['worker_context']['prepared']['source_inspections'][0]['inspection']).unlink()
        with self.assertRaisesRegex(b.BatchError, 'Invalid prepared handoff'):
            self.bind(r)

    def test_resigned_damaged_cache_blocks_binding(self):
        r = p.reserve(self.manifest, 1, prepare=True)
        value = r['worker_context']['prepared']['source_inspections'][0]
        content = Path(value['content']); content.write_text('damaged', encoding='utf-8')
        metadata = Path(value['output_directory'])/'cache.json'
        data = b.read(metadata)
        for row in data['outputs']:
            if row['file'] == 'content.txt': row['sha256'] = b.sha(content)
        b.atomic(metadata, data)
        with self.assertRaisesRegex(b.BatchError, 'Prepared cache metadata changed'):
            self.bind(r)

    def test_changed_writer_cache_blocks_review_binding(self):
        draft = p.reserve(self.manifest, 1, prepare=True); self.bind(draft)
        receipt = self.receipt(draft)
        inspector.run_inspection(receipt['artifact'], draft['worker_context']['prepared']['draft_inspection_cache'], 'draft', True, True)
        p.record(self.manifest, draft['case_id'], draft['attempt']['id'], receipt)
        review = p.reserve(self.manifest, 1, prepare=True)
        Path(review['worker_context']['prepared']['input_inspection']['content']).write_text('damaged', encoding='utf-8')
        with self.assertRaisesRegex(b.BatchError, 'Cache artifact changed'):
            self.bind(review)

    def test_invalid_docx_cli_refuses_without_reserving(self):
        for kind in ('bad_zip', 'bad_xml'):
            with self.subTest(kind=kind):
                source = self.root/kind; source.mkdir()
                doc = source/'source.docx'
                if kind == 'bad_zip':
                    doc.write_text('not a ZIP', encoding='utf-8')
                else:
                    with zipfile.ZipFile(doc, 'w') as z:
                        z.writestr('word/document.xml', '<unclosed')
                manifest = p.init(source, self.root/'invalid-work', {'projects': [
                    {'title': kind, 'source_files': [doc.name], 'primary_source': doc.name}
                ]}, self.root/'desktop')
                before = manifest.read_bytes(); stderr = io.StringIO()
                with patch.object(sys, 'argv', ['parallel_batch.py', 'reserve', '--manifest', str(manifest),
                                              '--capacity', '1', '--controller', 'controller', '--prepare']):
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(p.main(), 2)
                self.assertIn('Invalid DOCX source', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())
                self.assertEqual(manifest.read_bytes(), before)

    def test_other_case_does_not_reuse_first_case_cache(self):
        first = p.reserve(self.manifest, 2, prepare=True)
        second = p.reserve(self.manifest, 2, prepare=True)
        a = first['worker_context']['prepared']['source_inspections'][0]
        other = second['worker_context']['prepared']['source_inspections'][0]
        self.assertEqual(other['cache_status'], 'miss')
        self.assertNotEqual(a['inspection'], other['inspection'])
        self.assertFalse(Path(a['inspection']).is_relative_to(Path(second['worker_context']['case_directory'])))

    def test_missing_old_inspection_prepares_one_new_attempt_cache(self):
        draft = p.reserve(self.manifest, 1); self.bind(draft)
        receipt = self.receipt(draft)
        p.record(self.manifest, draft['case_id'], draft['attempt']['id'], receipt)
        review = p.reserve(self.manifest, 1, prepare=True)
        value = review['worker_context']['prepared']['input_inspection']
        self.assertEqual(value['cache_status'], 'miss')
        self.assertTrue(Path(value['inspection']).is_relative_to(Path(review['attempt']['directory'])))

    def test_render_candidates_are_only_current_writer_and_never_pass(self):
        draft = p.reserve(self.manifest, 1); self.bind(draft)
        receipt = self.receipt(draft)
        root = Path(receipt['artifact']).parent
        for name, sha in [('render-valid', receipt['artifact_sha256']), ('render-old', 'different')]:
            folder = root/name; folder.mkdir()
            b.atomic(folder/'render.json', {'status': 'rendered_pending_visual_review', 'input': receipt['artifact'], 'input_sha256': sha})
        foreign = root.parent/'stale-review'/'render'; foreign.mkdir(parents=True)
        b.atomic(foreign/'render.json', {'status': 'rendered_pending_visual_review', 'input': receipt['artifact'], 'input_sha256': receipt['artifact_sha256']})
        candidates = handoff.render_candidates(receipt['artifact'], receipt['artifact_sha256'])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['status'], 'candidate_requires_integrity_and_visual_review')
        self.assertTrue(Path(candidates[0]['evidence']).is_relative_to(root))

    def test_cold_and_warm_data_do_not_remove_active_rule_exceptions(self):
        m = p.load_manifest(self.manifest)
        snapshot = Path(m['batch_dir'])/'rules.snapshot.json'
        rules = {'version': m['rule_version'], 'rules': [
            {'id': 'A', 'status': 'active', 'scope': 'global', 'instruction': 'keep', 'exceptions': ['project override'], 'evidence': [{'source': 'user'}]},
            {'id': 'B', 'status': 'retired', 'instruction': 'old'}], 'history': ['irrelevant past']}
        b.atomic(snapshot, rules); m['rules_sha256'] = b.sha(snapshot); b.atomic(self.manifest, m)
        r = p.reserve(self.manifest, 1, prepare=True)
        active = b.read(r['worker_context']['prepared']['rules_active'])
        self.assertEqual(active['rules'], [rules['rules'][0]])
        self.assertNotIn('history', active)

    def test_short_directory_key_collision_does_not_reuse_wrong_identity(self):
        first = self.root/'first.docx'; second = self.root/'second.docx'
        fixtures.docx(first, 'first'); fixtures.docx(second, 'second')
        cache = self.root/'collision-test'
        with patch.object(inspector, 'cache_key', return_value='forced-collision'):
            a = inspector.run_inspection(first, cache, 'proposal', True, True)
            other = inspector.run_inspection(second, cache, 'proposal', True, True)
            with patch.object(inspector, 'inspect', side_effect=AssertionError('No reparse expected')):
                hit = inspector.run_inspection(first, cache, 'proposal', True, True)
        self.assertEqual(other['cache_status'], 'miss')
        self.assertNotEqual(a['inspection'], other['inspection'])
        self.assertEqual(hit['inspection'], a['inspection'])


if __name__ == '__main__':
    unittest.main()
