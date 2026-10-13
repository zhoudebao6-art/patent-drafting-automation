import copy
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import batch_tool as b

POLICY = {'schema_version': 1, 'execution': 'serial_per_project', 'fallback_model_allowed': False, 'models': {'draft': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}, 'review': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'}, 'revise': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'}}, 'delivery': {'folder_prefix': '专利撰写完成', 'accepted_subdir': '已完成', 'pending_subdir': '待处理'}}
def docx(path, text):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>')
class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.src = self.root/'input'; self.src.mkdir()
        for name in ('a.docx', 'b.docx'): docx(self.src/name, name)
        self.original = {p.name: p.read_bytes() for p in self.src.iterdir()}
        policy = self.root/'policy.json'; b.atomic(policy, POLICY)
        self.patcher = patch.object(b, 'POLICY', policy); self.patcher.start(); self.addCleanup(self.patcher.stop)
        rules = self.root/'rules.json'; b.atomic(rules, {'version': 2, 'rules': []})
        self.rules_patcher = patch.object(b, 'RULES', rules); self.rules_patcher.start(); self.addCleanup(self.rules_patcher.stop)
        selection = {'projects': [{'title': '同名', 'source_files': [n], 'primary_source': n} for n in ('a.docx', 'b.docx')]}
        self.manifest = b.init(self.src, self.root/'work', selection, self.root/'desktop')
        self.batch = self.manifest.parent
    def receipt(self, stage, suffix='', **extra):
        policy = POLICY['models'][stage]
        agent = 'agent_' + stage + suffix
        evidence = self.batch/(agent + '.json')
        m = b.read(self.manifest); c = b.current_case(m)
        b.atomic(evidence, {'tool': 'collaboration.spawn_agent', 'context': {'batch_id': m['batch_id'], 'case_id': c['id'], 'stage': stage}, 'arguments': dict(policy, fork_turns='none', task_name=agent), 'result': {'task_name': agent}})
        r = dict(policy, stage=stage, agent_id=agent, dispatch_evidence=str(evidence))
        if stage in ('draft', 'revise'):
            artifact = self.batch/(agent + '.docx'); docx(artifact, agent); r['artifact'] = str(artifact)
        else:
            c = b.next_case(b.read(self.manifest))['case']; r.update(reviewed_sha256=b.sha(c['artifact']), verdict='pass', findings=[], checks={k: 'passed' for k in b.CHECKS})
        r.update(extra); return r
    def complete(self, ident):
        b.record(self.manifest, ident, self.receipt('draft', ident))
        b.record(self.manifest, ident, self.receipt('review', ident))
    def test_serial_and_complete_delivery_same_titles(self):
        with self.assertRaises(b.BatchError): b.record(self.manifest, '002', self.receipt('draft'))
        self.complete('001'); self.assertEqual(b.next_case(b.read(self.manifest))['case']['id'], '002')
        self.complete('002'); dest = Path(b.deliver(self.manifest))
        outputs = list(dest.rglob('*.docx')); self.assertEqual(len(outputs), 2)
        self.assertNotEqual(outputs[0].parent, outputs[1].parent)
        for n, data in self.original.items(): self.assertEqual((self.src/n).read_bytes(), data)
        self.assertEqual(b.deliver(self.manifest), str(dest))
    def test_model_effort_evidence_rejections(self):
        for change in ({'model': 'gpt-6-astra'}, {'reasoning_effort': None}, {'reasoning_effort': 'low'}, {'dispatch_evidence': None}):
            with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('draft', **change))
        b.record(self.manifest, '001', self.receipt('draft'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('review', reasoning_effort='low'))
        r = self.receipt('review'); ev = b.read(r['dispatch_evidence']); ev['arguments']['model'] = 'gpt-6-sol'; b.atomic(r['dispatch_evidence'], ev)
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
    def test_policy_requires_explicit_high_for_every_new_stage(self):
        b.validate_policy(POLICY)
        for stage in ('draft', 'review', 'revise'):
            for effort in (None, 'low', 'medium', 'xhigh', 'missing'):
                with self.subTest(stage=stage, effort=effort):
                    policy = copy.deepcopy(POLICY)
                    if effort == 'missing': del policy['models'][stage]['reasoning_effort']
                    else: policy['models'][stage]['reasoning_effort'] = effort
                    with self.assertRaises(b.BatchError): b.validate_policy(policy)
        for policy in ({}, dict(POLICY, models=None), dict(POLICY, models={})):
            with self.assertRaises(b.BatchError): b.validate_policy(policy)
    def test_every_stage_rejects_missing_null_low_receipt_and_dispatch_effort(self):
        for stage, suffix in (('draft', 'd'), ('review', 'r1'), ('revise', 'v'), ('review', 'r2')):
            for target in ('receipt', 'dispatch'):
                for effort in (None, 'low', 'missing'):
                    with self.subTest(stage=stage, round=suffix, target=target, effort=effort):
                        r = self.receipt(stage, suffix)
                        ev = b.read(r['dispatch_evidence'])
                        data = r if target == 'receipt' else ev['arguments']
                        if effort == 'missing': del data['reasoning_effort']
                        else: data['reasoning_effort'] = effort
                        if target == 'dispatch': b.atomic(r['dispatch_evidence'], ev)
                        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
            r = self.receipt(stage, suffix)
            if suffix == 'r1': r.update(verdict='fix_required', findings=[{'status': 'unresolved'}])
            b.record(self.manifest, '001', r)
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'accepted')
    def test_legacy_policy_only_restored_from_verified_version_two_snapshot(self):
        legacy = copy.deepcopy(POLICY); legacy['models']['draft']['reasoning_effort'] = None
        b.atomic(b.POLICY, legacy)
        selection = {'projects': [{'title': 'legacy', 'source_files': ['a.docx'], 'primary_source': 'a.docx'}]}
        with self.assertRaises(b.BatchError): b.init(self.src, self.root/'newwork', selection, self.root/'desktop')
        m = b.read(self.manifest); m['policy'] = legacy; b.atomic(self.manifest, m)
        with self.assertRaises(b.BatchError): b.load_manifest(self.manifest)
        b.atomic(self.batch/'policy.snapshot.json', legacy)
        self.assertIsNone(b.load_manifest(self.manifest)['policy']['models']['draft']['reasoning_effort'])
        b.atomic(b.POLICY, POLICY); b.atomic(b.RULES, {'version': 3, 'rules': []})
        self.assertEqual(b.load_manifest(self.manifest)['rule_version'], 2)
        r = self.receipt('draft', reasoning_effort=None)
        ev = b.read(r['dispatch_evidence']); ev['arguments']['reasoning_effort'] = None; b.atomic(r['dispatch_evidence'], ev)
        missing = dict(r); del missing['reasoning_effort']
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', missing)
        del ev['arguments']['reasoning_effort']; b.atomic(r['dispatch_evidence'], ev)
        # Original version-2 calls omitted the override. Keep that real
        # dispatch unchanged instead of fabricating an explicit null argument.
        b.record(self.manifest, '001', r)
        b.record(self.manifest, '001', self.receipt('review'))
        b.defer(self.manifest, '002', 'needs_input', 'Legacy second case deferred')
        dest = Path(b.deliver(self.manifest))
        note = (dest/'交付清单.txt').read_text(encoding='utf-8')
        self.assertIn('旧批次冻结策略未指定推理强度', note); self.assertNotIn('GPT-6.1-sol/high', note)
    def test_version_three_frozen_policy_cannot_downgrade_to_legacy_null(self):
        b.atomic(self.batch/'rules.snapshot.json', {'version': 3, 'rules': []})
        m = b.read(self.manifest); m.update(rule_version=3, rules_sha256=b.sha(self.batch/'rules.snapshot.json'))
        b.atomic(self.manifest, m); b.load_manifest(self.manifest)
        m['policy']['models']['draft']['reasoning_effort'] = None
        b.atomic(self.manifest, m); b.atomic(self.batch/'policy.snapshot.json', m['policy'])
        with self.assertRaises(b.BatchError): b.load_manifest(self.manifest)
    def test_stale_review_after_revision(self):
        b.record(self.manifest, '001', self.receipt('draft'))
        old = self.receipt('review'); b.record(self.manifest, '001', dict(old, verdict='fix_required', findings=[{'status': 'unresolved'}]))
        b.record(self.manifest, '001', self.receipt('revise'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', old)
        b.record(self.manifest, '001', self.receipt('review', 'new'))
    def test_no_pass_no_delivery(self):
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        b.record(self.manifest, '001', self.receipt('draft'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('review', checks={}))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('review', findings=[{'status': 'unresolved'}]))
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
    def test_input_changed(self):
        (self.src/'a.docx').write_bytes(b'changed')
        with self.assertRaises(b.BatchError): b.next_case(b.read(self.manifest))
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
    def test_scan_not_auto_select_archive_pdf(self):
        archive = self.src/'定稿档案'; archive.mkdir(); (archive/'reference.docx').write_bytes(b'x')
        (self.src/'extra.pdf').write_bytes(b'x'); (self.src/'~$a.docx').write_bytes(b'x')
        result = b.scan(self.src)
        self.assertEqual(len(result['candidates']), 2); self.assertEqual(len(result['pending']), 2); self.assertEqual(result['projects'], [])
    def test_output_and_root_boundary(self):
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('draft', artifact=str(self.src/'a.docx')))
        selection = {'projects': [{'title': 'x', 'source_files': ['../outside.docx'], 'primary_source': '../outside.docx'}]}
        with self.assertRaises(b.BatchError): b.init(self.src, self.root/'work2', selection, self.root/'desktop')
        with self.assertRaises(b.BatchError): b.init(self.src, self.src/'work', {'projects': []}, self.root/'desktop')
    def test_pending_delivery_only_note(self):
        b.record(self.manifest, '001', self.receipt('draft', verdict='needs_input', reason='Missing source values'))
        b.record(self.manifest, '002', self.receipt('draft', '2', verdict='failed', reason='Worker could not render'))
        dest = Path(b.deliver(self.manifest)); self.assertEqual(list(dest.rglob('*.docx')), [])
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 2)
        note = next(dest.rglob('待处理说明.txt')).read_text(encoding='utf-8')
        self.assertIn('原因：', note); self.assertNotIn('agent_id', note)
        self.assertIn('待补材料', (dest/'交付清单.txt').read_text(encoding='utf-8'))
    def test_dispatch_failure_missing_context_and_reuse_rejected(self):
        for change in ({'error': 'Unavailable'}, {'success': False}, {'status': 'failed'}, {'task_name': 'different'}):
            r = self.receipt('draft'); ev = b.read(r['dispatch_evidence']); ev['result'].update(change); b.atomic(r['dispatch_evidence'], ev)
            with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
        for change in ({'case_id': '002'}, {'stage': 'review'}, {'batch_id': 'other'}):
            r = self.receipt('draft'); ev = b.read(r['dispatch_evidence']); ev['context'].update(change); b.atomic(r['dispatch_evidence'], ev)
            with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
        r = self.receipt('draft'); ev = b.read(r['dispatch_evidence']); del ev['arguments']['task_name']; b.atomic(r['dispatch_evidence'], ev)
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
        b.record(self.manifest, '001', self.receipt('draft'))
        r = self.receipt('review'); b.record(self.manifest, '001', dict(r, verdict='fix_required'))
        b.record(self.manifest, '001', self.receipt('revise'))
        r = self.receipt('review')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
    def test_revision_requires_recheck_and_separate_file(self):
        r = self.receipt('draft'); b.record(self.manifest, '001', r)
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('revise'))
        b.record(self.manifest, '001', self.receipt('review', verdict='fix_required'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('revise', artifact=r['artifact']))
        b.record(self.manifest, '001', self.receipt('revise'))
        self.assertEqual(b.next_case(b.read(self.manifest))['next_stage'], 'review')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '002', self.receipt('draft', '2'))
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
    def test_invalid_docx_and_writing_verdict_rejected(self):
        r = self.receipt('draft', verdict='pass')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
        r = self.receipt('draft'); Path(r['artifact']).write_bytes(b'not a docx')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
        r = self.receipt('draft')
        with zipfile.ZipFile(r['artifact'], 'w') as z: z.writestr('fake.txt', 'x')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
    def test_passed_artifact_tamper_blocks_delivery(self):
        self.complete('001'); self.complete('002')
        c = b.read(self.manifest)['projects'][0]; docx(c['artifact'], 'changed after review')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        self.assertFalse((self.root/'desktop').exists())
    def test_defer_changed_source_and_continue_without_model_claim(self):
        (self.src/'a.docx').write_bytes(b'changed')
        with self.assertRaises(b.BatchError): b.next_case(b.read(self.manifest))
        with self.assertRaises(b.BatchError): b.defer(self.manifest, '002', 'failed', 'out of order')
        c = b.defer(self.manifest, '001', 'needs_input', 'Source changed; new confirmation required')
        self.assertNotIn('model', c['receipts'][-1]); self.assertNotIn('agent_id', c['receipts'][-1])
        self.complete('002'); dest = Path(b.deliver(self.manifest))
        self.assertEqual(len(list(dest.rglob('*.docx'))), 1)
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 1)
    def test_defer_missing_source_and_continue(self):
        (self.src/'a.docx').unlink()
        b.defer(self.manifest, '001', 'needs_input', 'Source was moved; please restore it')
        self.complete('002'); dest = Path(b.deliver(self.manifest))
        self.assertEqual(len(list(dest.rglob('*.docx'))), 1)
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'needs_input')
    def test_delivery_copy_failure_recovers_without_partial_final(self):
        self.complete('001'); self.complete('002')
        with patch.object(b.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        self.assertEqual(list((self.root/'desktop').iterdir()), [])
        self.assertEqual([c['status'] for c in b.read(self.manifest)['projects']], ['accepted', 'accepted'])
        dest = Path(b.deliver(self.manifest)); self.assertEqual(len(list(dest.rglob('*.docx'))), 2)
    def test_delivery_manifest_commit_failure_recovers(self):
        self.complete('001'); self.complete('002'); original_atomic = b.atomic
        def fail_commit(path, data):
            if Path(path) == self.manifest and data.get('delivery_path'): raise OSError('manifest commit interrupted')
            return original_atomic(path, data)
        with patch.object(b, 'atomic', side_effect=fail_commit):
            with self.assertRaises(OSError): b.deliver(self.manifest)
        self.assertNotIn('delivery_path', b.read(self.manifest))
        dest = Path(b.deliver(self.manifest)); self.assertEqual(len(list(dest.rglob('*.docx'))), 2)
        self.assertEqual(b.read(self.manifest), b.read(dest/'批次清单.json'))
    def test_existing_delivery_is_not_overwritten(self):
        self.complete('001'); self.complete('002'); m = b.read(self.manifest)
        dest = self.root/'desktop'/('专利撰写完成_' + m['batch_id']); dest.mkdir(parents=True)
        sentinel = dest/'keep.txt'; sentinel.write_text('Keep', encoding='utf-8')
        with self.assertRaises((b.BatchError, OSError)): b.deliver(self.manifest)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'Keep')
    def test_delivered_tamper_not_accepted_by_retry(self):
        self.complete('001'); self.complete('002'); dest = Path(b.deliver(self.manifest))
        next(dest.rglob('*.docx')).write_bytes(b'changed')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
    def test_rules_frozen_and_snapshot_tamper_rejected(self):
        m = b.load_manifest(self.manifest); self.assertEqual(m['rule_version'], 2)
        b.atomic(b.RULES, {'version': 3, 'rules': []})
        self.assertEqual(b.load_manifest(self.manifest)['rule_version'], 2)
        b.atomic(self.batch/'rules.snapshot.json', {'version': 3, 'rules': []})
        with self.assertRaises(b.BatchError): b.load_manifest(self.manifest)
    def test_scan_cli_cannot_overwrite_input(self):
        with patch.object(sys, 'argv', ['batch_tool.py', 'scan', '--input', str(self.src), '--out', str(self.src/'a.docx')]), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(b.main(), 2)
        self.assertEqual((self.src/'a.docx').read_bytes(), self.original['a.docx'])
    def test_root_reparse_rejected_before_resolve(self):
        actual_linked = b.linked
        with patch.object(b, 'linked', side_effect=lambda p: Path(p) == self.src or actual_linked(p)):
            with self.assertRaises(b.BatchError): b.scan(self.src)
            with self.assertRaises(b.BatchError): b.init(self.src, self.root/'work2', {'projects': []}, self.root/'desktop')
    def test_artifact_hardlink_to_source_rejected(self):
        r = self.receipt('draft'); artifact = Path(r['artifact']); artifact.unlink()
        os.link(self.src/'a.docx', artifact)
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', r)
    def test_windows_reserved_title_sanitized(self):
        self.assertEqual(b.safe('CON.txt'), '_CON.txt')
    def close_for_retry(self, stage='review', controller=False):
        if stage != 'draft': b.record(self.manifest, '001', self.receipt('draft'))
        if stage == 'revise': b.record(self.manifest, '001', self.receipt('review', verdict='fix_required', findings=[{'status': 'open', 'id': 'ISSUE-1'}]))
        if controller: b.defer(self.manifest, '001', 'failed', 'Renderer unavailable')
        else: b.record(self.manifest, '001', self.receipt(stage, 'blocked', verdict='needs_input', reason='Renderer unavailable'))
        b.defer(self.manifest, '002', 'needs_input', 'Second case deliberately excluded')
        return Path(b.deliver(self.manifest))
    def use_retry(self):
        self.manifest = b.retry(self.manifest, '001', 'Environment repaired')
        self.batch = self.manifest.parent
        return b.load_manifest(self.manifest)
    def test_retry_review_preserves_history_and_requires_new_review(self):
        dest = self.close_for_retry()
        before = {p: p.read_bytes() for root in (self.batch, dest) for p in root.rglob('*') if p.is_file()}
        old_manifest = self.manifest; old = b.read(old_manifest)
        m = self.use_retry(); c = m['projects'][0]
        self.assertEqual(b.next_case(m)['next_stage'], 'review')
        self.assertEqual(c['artifact_sha256'], old['projects'][0]['artifact_sha256'])
        self.assertEqual(c['retry_origin']['source_receipts'], old['projects'][0]['receipts'])
        self.assertEqual(len(c['receipts']), 1); self.assertNotIn('agent_id', c['receipts'][0])
        self.assertNotIn('passed_review_sha256', c); self.assertNotIn('delivery_path', m)
        self.assertEqual(m['policy'], old['policy']); self.assertEqual(m['rules_sha256'], old['rules_sha256'])
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('draft', 'spurious'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', old['projects'][0]['receipts'][-1])
        # Even a newly written context file cannot re-label a historical worker
        # as a fresh invocation in the continuation batch.
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('review', 'blocked'))
        b.record(self.manifest, '001', self.receipt('review', 'recovered'))
        delivered = Path(b.deliver(self.manifest))
        self.assertEqual(len(list(delivered.rglob('*.docx'))), 1)
        for p, data in before.items(): self.assertEqual(p.read_bytes(), data)
    def test_retry_controller_defer_restores_review_and_freezes_old_rules(self):
        self.close_for_retry(controller=True)
        b.atomic(b.RULES, {'version': 99, 'rules': []})
        b.atomic(b.POLICY, {})
        m = self.use_retry()
        self.assertEqual(m['rule_version'], 2); self.assertEqual(b.next_case(m)['next_stage'], 'review')
    def test_retry_draft_failure_restarts_only_draft(self):
        self.close_for_retry('draft'); m = self.use_retry()
        self.assertEqual(b.next_case(m)['next_stage'], 'draft')
        self.assertNotIn('artifact', m['projects'][0])
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', {'stage': 'review'})
        self.complete('001'); self.assertEqual(len(list(Path(b.deliver(self.manifest)).rglob('*.docx'))), 1)
    def test_retry_revision_failure_preserves_findings_and_requires_rereview(self):
        self.close_for_retry('revise'); m = self.use_retry()
        self.assertEqual(b.next_case(m)['next_stage'], 'revise')
        prior = m['projects'][0]['retry_origin']['source_receipts']
        self.assertEqual(prior[1]['findings'][0]['id'], 'ISSUE-1')
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('review', 'skip_revision'))
        with self.assertRaises(b.BatchError): b.record(self.manifest, '001', self.receipt('revise', artifact=m['projects'][0]['artifact']))
        b.record(self.manifest, '001', self.receipt('revise', 'fixed'))
        self.assertEqual(b.next_case(b.read(self.manifest))['next_stage'], 'review')
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        b.record(self.manifest, '001', self.receipt('review', 'new'))
        b.deliver(self.manifest)
    def test_retry_rejects_changed_source_artifact_dispatch_and_manifest_state(self):
        self.close_for_retry(); m = b.read(self.manifest); c = m['projects'][0]
        paths = [self.src/'a.docx', Path(c['artifact']), Path(c['receipts'][0]['dispatch_evidence'])]
        for path in paths:
            with self.subTest(path=path.name):
                data = path.read_bytes(); path.write_bytes(b'changed')
                with self.assertRaises((b.BatchError, ValueError)): b.retry(self.manifest, '001', 'Retry')
                path.write_bytes(data)
        original = self.manifest.read_bytes()
        for field, value in [('artifact_sha256', '0' * 64), ('status', 'failed')]:
            changed = copy.deepcopy(m); changed['projects'][0][field] = value; b.atomic(self.manifest, changed)
            with self.assertRaises(b.BatchError): b.retry(self.manifest, '001', 'Retry')
            self.manifest.write_bytes(original)
    def test_retry_lineage_tamper_blocks_next_and_delivery(self):
        self.close_for_retry(); old_manifest = self.manifest
        m = self.use_retry(); origin = m['projects'][0]['retry_origin']
        inherited = Path(origin['artifact']); data = inherited.read_bytes(); inherited.write_bytes(b'changed')
        with self.assertRaises(b.BatchError): b.next_case(b.read(self.manifest))
        inherited.write_bytes(data)
        b.record(self.manifest, '001', self.receipt('review', 'new'))
        old_data = old_manifest.read_bytes(); old = b.read(old_manifest); old['projects'][0]['receipts'][-1]['reason'] += ' changed'; b.atomic(old_manifest, old)
        with self.assertRaises(b.BatchError): b.deliver(self.manifest)
        old_manifest.write_bytes(old_data); b.deliver(self.manifest)
    def test_retry_can_resume_twice_without_inventing_drafts(self):
        self.close_for_retry(); self.use_retry()
        b.record(self.manifest, '001', self.receipt('review', 'blocked_again', verdict='failed', reason='Renderer failed again'))
        b.deliver(self.manifest)
        m = self.use_retry()
        self.assertEqual(b.next_case(m)['next_stage'], 'review')
        self.assertEqual([r.get('stage') for r in m['projects'][0]['receipts']], [None])
        b.record(self.manifest, '001', self.receipt('review', 'final')); b.deliver(self.manifest)
    def test_retry_rejects_active_accepted_unknown_and_unsafe_destination(self):
        with self.assertRaises(b.BatchError): b.retry(self.manifest, '001', 'Active batch')
        self.complete('001'); b.defer(self.manifest, '002', 'failed', 'Failed'); dest = Path(b.deliver(self.manifest))
        for ident in ('001', '999'):
            with self.assertRaises(b.BatchError): b.retry(self.manifest, ident, 'Retry')
        for work in (self.src/'nested', self.batch/'nested', dest/'nested'):
            with self.assertRaises(b.BatchError): b.retry(self.manifest, '002', 'Retry', work)
            with self.assertRaises(b.BatchError): b.retry(self.manifest, '002', 'Retry', desktop=work)
        with self.assertRaises(b.BatchError): b.retry(self.manifest, '002', ' ')
    def test_retry_cannot_start_new_draft_with_legacy_unspecified_effort(self):
        legacy = copy.deepcopy(POLICY); legacy['models']['draft']['reasoning_effort'] = None
        m = b.read(self.manifest); m['policy'] = legacy
        b.atomic(self.manifest, m); b.atomic(self.batch/'policy.snapshot.json', legacy)
        b.defer(self.manifest, '001', 'failed', 'Draft unavailable')
        b.defer(self.manifest, '002', 'needs_input', 'Excluded'); b.deliver(self.manifest)
        with self.assertRaises(b.BatchError): b.retry(self.manifest, '001', 'Environment restored')
    def test_retry_cli_and_lineage_depth_guard(self):
        self.close_for_retry()
        with patch.object(sys, 'argv', ['batch_tool.py', 'retry', '--manifest', str(self.manifest), '--case', '001', '--reason', 'Environment restored']), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(b.main(), 0)
        m = b.load_manifest(b.json.loads(output.getvalue()))
        with self.assertRaises(b.BatchError): b.verify_retry_origin(m, m['projects'][0], (m['batch_id'],))
        with self.assertRaises(b.BatchError): b.verify_retry_origin(m, m['projects'][0], tuple(str(n) for n in range(32)))
if __name__ == '__main__': unittest.main()
