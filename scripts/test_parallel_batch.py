"""Simulated dispatches test queue mechanics, not actual concurrent model execution."""
import copy
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
from test_batch_tool import POLICY as SERIAL, docx

POLICY = dict(copy.deepcopy(SERIAL), schema_version=2, execution='rolling_parallel',
              concurrency={'target_workers': 4, 'max_in_flight_cases': 8, 'min_draft_slots': 1})

def process_record(manifest, case, attempt, receipt, queue):
    try:
        p.record(manifest, case, attempt, receipt)
        queue.put(None)
    except Exception as e:
        queue.put(repr(e))

class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.src = self.root / 'input'; self.src.mkdir()
        for i in range(1, 11): docx(self.src / f'{i}.docx', f'source {i}')
        policy = self.root / 'policy.json'; b.atomic(policy, POLICY)
        rules = self.root / 'rules.json'; b.atomic(rules, {'version': 4, 'rules': []})
        for key, val in (('POLICY', policy), ('RULES', rules)):
            patcher = patch.object(p, key, val); patcher.start(); self.addCleanup(patcher.stop)
        selection = {'projects': [{'title': '同名', 'source_files': [f'{i}.docx'], 'primary_source': f'{i}.docx'} for i in range(1, 11)]}
        self.manifest = p.init(self.src, self.root / 'work', selection, self.root / 'desktop')
        self.batch = self.manifest.parent

    def reserve(self, capacity=4):
        r = p.reserve(self.manifest, capacity); a = r['attempt']
        dispatch = Path(a['directory']) / 'dispatch.json'
        b.atomic(dispatch, {'tool': 'collaboration.spawn_agent', 'context': a['context'],
                            'arguments': r['spawn_arguments'], 'result': {'task_name': '/root/' + a['task_name']}})
        p.bind(self.manifest, r['case_id'], a['id'], dispatch)
        return r

    def receipt(self, r, verdict='complete'):
        a = r['attempt']; stage = a['stage']
        receipt = dict(POLICY['models'][stage], context=a['context'], stage=stage, agent_id='/root/' + a['task_name'], verdict=verdict)
        if stage in {'draft', 'revise'}:
            artifact = Path(a['directory']) / 'output.docx'; docx(artifact, a['id'])
            receipt.update(artifact=str(artifact), artifact_sha256=b.sha(artifact))
        else:
            render = Path(a['directory']) / 'render' / 'page.png'; render.parent.mkdir(exist_ok=True)
            # Decodable one-page fixtures exercise structural verification only;
            # they do not claim actual DOCX conversion or visual acceptance.
            from PIL import Image
            from pypdf import PdfWriter
            render = render.with_name('page-1.png'); Image.new('RGB', (32, 32), 'white').save(render)
            pdf = render.parent / 'output.pdf'; writer = PdfWriter(); writer.add_blank_page(width=32, height=32)
            with pdf.open('wb') as stream: writer.write(stream)
            evidence = render.parent / 'render.json'
            b.atomic(evidence, {'status': 'rendered_pending_visual_review', 'input_sha256': a['input_artifact_sha256'],
                                'pdf': {'file': pdf.name, 'sha256': b.sha(pdf)},
                                'pages': [{'file': render.name, 'sha256': b.sha(render)}]})
            receipt.update(reviewed_sha256=a['input_artifact_sha256'], checks={k: 'passed' for k in b.CHECKS},
                           findings=[] if verdict == 'pass' else [{'status': 'unresolved', 'detail': 'fixture'}],
                           render={'artifact_sha256': a['input_artifact_sha256'], 'evidence': str(evidence), 'evidence_sha256': b.sha(evidence)})
        return receipt

    def record(self, r, verdict=None):
        verdict = verdict or ('pass' if r['attempt']['stage'] == 'review' else 'complete')
        receipt = self.receipt(r, verdict)
        p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)
        return receipt

    def assert_rejected(self, r, receipt):
        with self.assertRaises(b.BatchError): p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)

    def test_five_cases_out_of_order_and_immediate_backfill(self):
        first = [self.reserve() for _ in range(4)]
        self.assertEqual(p.plan(self.manifest, 4)['action'], 'wait')
        self.record(first[2])
        review = self.reserve(); self.assertEqual((review['case_id'], review['attempt']['stage']), ('003', 'review'))
        self.record(first[0]); self.record(first[3]); self.record(first[1])
        refill = self.reserve(); self.assertEqual((refill['case_id'], refill['attempt']['stage']), ('001', 'review'))
        next_review = self.reserve(); self.assertEqual(next_review['attempt']['stage'], 'review')
        draft5 = self.reserve(); self.assertEqual((draft5['case_id'], draft5['attempt']['stage']), ('005', 'draft'))
        self.record(review); self.record(refill, 'fix_required'); self.record(next_review); self.record(draft5)
        self.assertGreaterEqual(len({c['id'] for c in b.read(self.manifest)['projects'] if c['attempts']}), 5)

    def test_serial_one_worker_follows_real_capacity(self):
        draft = self.reserve(1)
        with self.assertRaises(b.BatchError): p.reserve(self.manifest, 1)
        self.record(draft)
        review = self.reserve(1); self.assertEqual(review['attempt']['stage'], 'review')
        self.record(review)
        draft2 = self.reserve(1); self.assertEqual(draft2['attempt']['stage'], 'draft')
        self.assertEqual(p.summary(self.manifest)['active_workers'], 1)

    def test_cancel_late_receipt_rejected_and_new_attempt_unique(self):
        r = p.reserve(self.manifest, 1); a = r['attempt']
        p.cancel(self.manifest, r['case_id'], a['id'], 'spawn failed before a worker started')
        next_r = self.reserve(1); self.assertNotEqual(a['id'], next_r['attempt']['id'])
        late = dict(self.receipt(next_r), context=a['context'])
        with self.assertRaises(b.BatchError): p.record(self.manifest, r['case_id'], a['id'], late)

    def test_active_defer_and_unconfirmed_cancel_are_rejected(self):
        r = self.reserve(1); a = r['attempt']
        with self.assertRaises(b.BatchError): p.defer(self.manifest, r['case_id'], 'failed', 'fixture')
        with self.assertRaises(b.BatchError): p.cancel(self.manifest, r['case_id'], a['id'], 'fixture')
        stop = Path(a['directory']) / 'stop.json'
        ev = {'tool': 'collaboration.interrupt_agent', 'context': a['context'], 'arguments': {'target': '/root/' + a['task_name']}, 'result': {'previous_status': 'running'}, 'confirmed_stopped': False}
        b.atomic(stop, ev)
        with self.assertRaises(b.BatchError): p.cancel(self.manifest, r['case_id'], a['id'], 'fixture', stop)
        ev['confirmed_stopped'] = True; b.atomic(stop, ev)
        late = self.receipt(r); p.cancel(self.manifest, r['case_id'], a['id'], 'confirmed stop', stop)
        self.assert_rejected(r, late)
        p.defer(self.manifest, r['case_id'], 'needs_input', 'source material missing')

    def test_cross_case_artifact_receipt_and_stale_binding_rejected(self):
        one, two = self.reserve(), self.reserve()
        r1, r2 = self.receipt(one), self.receipt(two)
        self.assert_rejected(one, dict(r1, artifact=r2['artifact'], artifact_sha256=r2['artifact_sha256']))
        self.assert_rejected(one, dict(r1, context=two['attempt']['context']))
        for key in ('attempt', 'source_digest', 'rules_sha256'):
            bad = copy.deepcopy(r1); bad['context'][key] = 'stale'
            self.assert_rejected(one, bad)
        self.assert_rejected(one, dict(r1, artifact_sha256='stale'))
        self.record(one)
        self.assert_rejected(one, r1)

    def test_failed_stop_result_cannot_release_bound_worker(self):
        r = self.reserve(1); a = r['attempt']
        stop = Path(a['directory']) / 'stop.json'
        ev = {'tool': 'collaboration.interrupt_agent', 'context': a['context'],
              'arguments': {'target': '/root/' + a['task_name']}, 'confirmed_stopped': True}
        for result in ({'success': False}, {'status': 'failed'}, {'status': 'error'}, {'status': 'rejected'}):
            with self.subTest(result=result):
                b.atomic(stop, dict(ev, result=result))
                with self.assertRaisesRegex(b.BatchError, 'stop evidence mismatch'):
                    p.cancel(self.manifest, r['case_id'], a['id'], 'stop failed', stop)
                self.assertEqual(p.summary(self.manifest)['active_workers'], 1)
        b.atomic(stop, dict(ev, result={'previous_status': 'running'}))
        p.cancel(self.manifest, r['case_id'], a['id'], 'confirmed stopped', stop)
        self.assertEqual(p.summary(self.manifest)['active_workers'], 0)

    def test_cancelled_stop_evidence_integrity_checked_before_retry(self):
        r = self.reserve(1); a = r['attempt']; stop = Path(a['directory']) / 'stop.json'
        b.atomic(stop, {'tool': 'collaboration.interrupt_agent', 'context': a['context'],
                        'arguments': {'target': '/root/' + a['task_name']},
                        'result': {'previous_status': 'running'}, 'confirmed_stopped': True})
        p.cancel(self.manifest, r['case_id'], a['id'], 'confirmed stopped', stop)
        b.atomic(stop, {'tampered': True})
        plan = p.plan(self.manifest, 1)
        self.assertEqual(plan['blocked'][0]['case_id'], r['case_id'])
        self.assertIn('stop evidence changed', plan['blocked'][0]['reason'])
        self.assertEqual(self.reserve(1)['case_id'], '002')

    def test_review_revision_re_review_and_old_hash_rejection(self):
        draft = self.reserve(1); self.record(draft)
        review = self.reserve(1); original_sha = review['attempt']['input_artifact_sha256']; self.record(review, 'fix_required')
        # Capacity 1 reserves a new draft between follow-ups by policy.
        while True:
            r = self.reserve(1)
            if r['attempt']['stage'] == 'revise': break
            self.record(r)
        self.record(r)
        while True:
            r = self.reserve(1)
            if r['case_id'] == draft['case_id'] and r['attempt']['stage'] == 'review': break
            self.record(r)
        receipt = self.receipt(r, 'pass')
        self.assert_rejected(r, dict(receipt, reviewed_sha256=original_sha))
        bad = copy.deepcopy(receipt); bad['render']['artifact_sha256'] = original_sha; self.assert_rejected(r, bad)
        p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)
        case = p.case_by_id(b.read(self.manifest), '001')
        self.assertEqual([x['stage'] for x in case['receipts']], ['draft', 'review', 'revise', 'review'])
        self.assertEqual(case['status'], 'accepted')

    def test_wrong_dispatch_model_high_none_task_and_context_rejected(self):
        r = p.reserve(self.manifest, 1); a = r['attempt']; path = Path(a['directory']) / 'dispatch.json'
        good = {'tool': 'collaboration.spawn_agent', 'context': a['context'], 'arguments': r['spawn_arguments'], 'result': {'task_name': '/root/' + a['task_name']}}
        for field, value in (('model', 'gpt-6-astra'), ('reasoning_effort', None), ('reasoning_effort', 'low'), ('fork_turns', 'all'), ('task_name', 'wrong')):
            bad = copy.deepcopy(good); bad['arguments'][field] = value; b.atomic(path, bad)
            with self.assertRaises(b.BatchError): p.bind(self.manifest, r['case_id'], a['id'], path)
        bad = copy.deepcopy(good); del bad['arguments']['reasoning_effort']; b.atomic(path, bad)
        with self.assertRaises(b.BatchError): p.bind(self.manifest, r['case_id'], a['id'], path)
        b.atomic(path, good); p.bind(self.manifest, r['case_id'], a['id'], path)
        receipt = self.receipt(r)
        for key, value in (('model', 'gpt-6-astra'), ('reasoning_effort', None), ('reasoning_effort', 'low'), ('stage', 'review')):
            self.assert_rejected(r, dict(receipt, **{key: value}))

    def test_source_change_blocks_only_affected_case(self):
        docx(self.src / '1.docx', 'modified original')
        plan = p.plan(self.manifest, 4)
        self.assertEqual(plan['blocked'][0]['case_id'], '001')
        self.assertEqual(self.reserve()['case_id'], '002')
        p.defer(self.manifest, '001', 'needs_input', 'original changed')

    def test_snapshot_change_is_case_local_and_hardlink_rejected(self):
        m = b.read(self.manifest); target = Path(m['projects'][0]['sources'][0]['snapshot_path'])
        target.chmod(0o644); target.write_bytes(b'changed')
        self.assertEqual(self.reserve()['case_id'], '002')
        r = self.reserve(); receipt = self.receipt(r)
        hardlink = Path(r['attempt']['directory']) / 'alias.docx'; os.link(receipt['artifact'], hardlink)
        self.assert_rejected(r, dict(receipt, artifact=str(hardlink)))

    def test_hardlink_sources_rejected(self):
        os.link(self.src / '1.docx', self.src / 'alias.docx')
        with self.assertRaises(b.BatchError):
            p.init(self.src, self.root / 'more', {'projects': [{'title': 'a', 'source_files': ['1.docx'], 'primary_source': '1.docx'}]}, self.root / 'desktop')

    def test_concurrent_process_records_do_not_lose_updates(self):
        rs = [self.reserve() for _ in range(4)]
        ctx = multiprocessing.get_context('spawn'); queue = ctx.Queue()
        processes = [ctx.Process(target=process_record, args=(str(self.manifest), r['case_id'], r['attempt']['id'], self.receipt(r), queue)) for r in rs]
        for child in processes: child.start()
        for child in processes: child.join(15); self.assertEqual(child.exitcode, 0)
        self.assertEqual([queue.get(timeout=2) for _ in processes], [None] * 4)
        m = p.load_manifest(self.manifest)
        self.assertEqual(sum(c['status'] == 'drafted' for c in m['projects']), 4)
        self.assertEqual(sum(len(c['receipts']) for c in m['projects']), 4)

    def test_max_in_flight_limits_and_review_fairness(self):
        # Complete first reviews only as fix_required so 8 cases remain in flight.
        seen_reviews = set()
        for _ in range(80):
            r = self.reserve(1)
            if r['attempt']['stage'] == 'review': seen_reviews.add(r['case_id']); self.record(r, 'fix_required')
            else: self.record(r)
            m = b.read(self.manifest)
            started = [c for c in m['projects'] if c['attempts']]
            self.assertLessEqual(len(started), 8)
            if len(started) == 8 and len(seen_reviews) == 8: break
        self.assertEqual(len(seen_reviews), 8)
        self.assertFalse(any(c['attempts'] for c in m['projects'][8:]))

    def test_controller_and_capacity_enforced(self):
        for capacity in (0, -1, 5, None, True):
            with self.assertRaises(b.BatchError): p.plan(self.manifest, capacity)
        with self.assertRaises(b.BatchError): p.reserve(self.manifest, 4, controller='another')
        self.assertEqual(p.summary(self.manifest)['active_workers'], 0)

    def test_six_checks_and_render_evidence_cannot_be_waived(self):
        r = self.reserve(1); self.record(r); r = self.reserve(1); receipt = self.receipt(r, 'pass')
        for check in b.CHECKS:
            bad = copy.deepcopy(receipt); bad['checks'][check] = 'skipped'; self.assert_rejected(r, bad)
        for change in ({'findings': [{'status': 'open'}]}, {'render': None}, {'render': {'artifact_sha256': r['attempt']['input_artifact_sha256'], 'files': []}}):
            self.assert_rejected(r, dict(receipt, **change))

    def test_render_reuse_requires_current_artifact_and_same_case(self):
        r = self.reserve(1); self.record(r); review = self.reserve(1); receipt = self.receipt(review, 'pass')
        import shutil
        source = Path(receipt['render']['evidence']).parent
        reuse = Path(r['attempt']['directory']) / 'render'; shutil.copytree(source, reuse)
        receipt['render'].update(evidence=str(reuse / 'render.json'), evidence_sha256=b.sha(reuse / 'render.json'))
        p.record(self.manifest, review['case_id'], review['attempt']['id'], receipt)

    def test_delivery_same_titles_nonpass_excluded_and_retry(self):
        # Close the 10-case batch with 2 actual simulated accepted outputs.
        for _ in range(4): self.record(self.reserve(1))
        m = b.read(self.manifest)
        for c in m['projects']:
            if c['status'] not in p.TERMINAL: p.defer(self.manifest, c['id'], 'needs_input', 'fixture missing source')
        dest = Path(p.deliver(self.manifest)); outputs = list(dest.rglob('*.docx'))
        self.assertEqual(len(outputs), 2); self.assertEqual(len({x.parent for x in outputs}), 2)
        self.assertEqual(p.deliver(self.manifest), str(dest))
        self.assertEqual(len(list(dest.rglob('待处理说明.txt'))), 8)

    def test_delivery_failed_copy_retry_and_rename_commit_recovery(self):
        self.record(self.reserve(1)); self.record(self.reserve(1))
        for c in b.read(self.manifest)['projects'][1:]: p.defer(self.manifest, c['id'], 'failed', 'fixture')
        with patch.object(p.shutil, 'copyfile', side_effect=OSError('injected copy failure')):
            with self.assertRaises(OSError): p.deliver(self.manifest)
        self.assertFalse(b.read(self.manifest).get('delivery_path'))
        original = b.atomic
        def fail_final(path, data):
            if Path(path) == self.manifest and data.get('delivery_path'): raise OSError('injected final commit failure')
            return original(path, data)
        with patch.object(b, 'atomic', side_effect=fail_final):
            with self.assertRaises(OSError): p.deliver(self.manifest)
        self.assertTrue(list((self.root / 'desktop').glob('专利撰写完成_*')))
        self.assertTrue(Path(p.deliver(self.manifest)).exists())

    def test_serial_manifest_no_implicit_migration_and_old_init_guidance(self):
        with patch.object(b, 'POLICY', p.POLICY):
            with self.assertRaisesRegex(b.BatchError, 'parallel_batch.py'):
                b.init(self.src, self.root / 'serial', {'projects': [{'title': 'a', 'source_files': ['1.docx'], 'primary_source': '1.docx'}]}, self.root / 'desktop')
        m = b.read(self.manifest); m['execution'] = 'serial_per_project'; b.atomic(self.manifest, m)
        with self.assertRaisesRegex(b.BatchError, 'Serial batch'): p.load_manifest(self.manifest)

    def test_recorded_evidence_corruption_rejected(self):
        r = self.reserve(1); self.record(r)
        path = Path(r['attempt']['directory']) / 'receipt.json'; receipt = b.read(path); receipt['artifact_sha256'] = 'wrong'; b.atomic(path, receipt)
        plan = p.plan(self.manifest, 1)
        self.assertEqual(plan['blocked'][0]['case_id'], '001')
        self.assertEqual(self.reserve(1)['case_id'], '002')

    def test_canonical_render_corruption_and_incomplete_pages_rejected(self):
        r = self.reserve(1); self.record(r); review = self.reserve(1)
        for tamper in ('evidence_sha', 'page_sha', 'empty_pages', 'page_sequence', 'pdf_count', 'undecodable_png', 'hardlink'):
            with self.subTest(tamper=tamper):
                receipt = self.receipt(review, 'pass'); evidence_path = Path(receipt['render']['evidence']); evidence = b.read(evidence_path)
                page = evidence_path.parent / 'page-1.png'; pdf = evidence_path.parent / 'output.pdf'
                if tamper == 'evidence_sha': receipt['render']['evidence_sha256'] = 'stale'
                elif tamper == 'page_sha': evidence['pages'][0]['sha256'] = 'stale'
                elif tamper == 'empty_pages': evidence['pages'] = []
                elif tamper == 'page_sequence':
                    renamed = page.with_name('page-2.png'); page.rename(renamed); evidence['pages'][0]['file'] = renamed.name
                elif tamper == 'pdf_count':
                    from pypdf import PdfWriter
                    writer = PdfWriter(); writer.add_blank_page(width=32, height=32); writer.add_blank_page(width=32, height=32)
                    with pdf.open('wb') as stream: writer.write(stream)
                    evidence['pdf']['sha256'] = b.sha(pdf)
                elif tamper == 'undecodable_png':
                    page.write_bytes(b'\x89PNG\r\n\x1a\nwrong'); evidence['pages'][0]['sha256'] = b.sha(page)
                else:
                    alias = evidence_path.parent / 'alias.png'; os.link(page, alias)
                    self.addCleanup(alias.unlink, missing_ok=True)
                if tamper != 'evidence_sha':
                    b.atomic(evidence_path, evidence); receipt['render']['evidence_sha256'] = b.sha(evidence_path)
                self.assert_rejected(review, receipt)
                if tamper == 'hardlink': alias.unlink()

    def test_live_lock_not_deleted_and_controller_recovers_after_release(self):
        with p.locked(self.manifest):
            with self.assertRaisesRegex(b.BatchError, 'lock busy'):
                with p.locked(self.manifest, timeout=.05): pass
            self.assertTrue((self.batch / '.controller.lock').exists())
        self.assertEqual(p.plan(self.manifest, 1)['action'], 'reserve')

    def test_cli_plan_reserve_bind_record_and_summary(self):
        import json
        script = Path(p.__file__)
        def cli(command, *args):
            result = subprocess.run([sys.executable, '-X', 'utf8', str(script), command, '--manifest', str(self.manifest), *args], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(cli('plan', '--capacity', '1')['action'], 'reserve')
        r = cli('reserve', '--capacity', '1', '--controller', 'controller'); a = r['attempt']
        self.assertEqual(r['worker_context']['primary_snapshot'], r['source_snapshots'][0]['snapshot_path'])
        dispatch = Path(a['directory']) / 'dispatch.json'
        b.atomic(dispatch, {'tool': 'collaboration.spawn_agent', 'context': a['context'], 'arguments': r['spawn_arguments'], 'result': {'task_name': '/root/' + a['task_name']}})
        common = ['--case', r['case_id'], '--attempt', a['id'], '--controller', 'controller']
        cli('bind', *common, '--dispatch', str(dispatch))
        receipt = Path(a['directory']) / 'worker-receipt.json'; b.atomic(receipt, self.receipt(r))
        cli('record', *common, '--receipt', str(receipt))
        self.assertEqual(cli('summary')['counts']['drafted'], 1)

    def test_original_change_during_worker_is_quarantined_with_receipt_preserved(self):
        one, two = self.reserve(), self.reserve()
        docx(self.src / '1.docx', 'changed while worker ran')
        self.record(one); self.record(two)
        case = p.case_by_id(b.read(self.manifest), '001')
        self.assertEqual(case['status'], 'needs_input'); self.assertIsNone(case['active_attempt'])
        self.assertEqual(case['receipts'][0]['stage'], 'draft')
        self.assertEqual(case['receipts'][1]['event'], 'controller_input_change')
        self.assertEqual(p.case_by_id(b.read(self.manifest), '002')['status'], 'drafted')
        self.assertEqual(b.sha(case['sources'][0]['snapshot_path']), case['sources'][0]['sha256'])

    def test_accepted_original_change_delivers_only_unaffected_cases(self):
        for _ in range(4): self.record(self.reserve(1))
        for c in b.read(self.manifest)['projects'][2:]: p.defer(self.manifest, c['id'], 'needs_input', 'fixture')
        docx(self.src / '1.docx', 'changed after acceptance')
        dest = Path(p.deliver(self.manifest)); files = list(dest.rglob('*.docx'))
        self.assertEqual(len(files), 1); self.assertTrue(files[0].parent.name.startswith('002_'))
        case = p.case_by_id(b.read(self.manifest), '001')
        self.assertEqual(case['status'], 'needs_input')
        self.assertEqual(case['receipts'][-2]['verdict'], 'pass')
        self.assertEqual(case['receipts'][-1]['event'], 'controller_input_change')
        self.assertNotIn('passed_review_sha256', case)

    def test_source_changes_while_copying_quarantine_and_retry_without_false_delivery(self):
        for _ in range(4): self.record(self.reserve(1))
        for c in b.read(self.manifest)['projects'][2:]: p.defer(self.manifest, c['id'], 'needs_input', 'fixture')
        original = p.shutil.copyfile; changed = [False]
        def change_input(src, dest):
            result = original(src, dest)
            if not changed[0]:
                changed[0] = True; docx(self.src / '1.docx', 'changed during copy')
            return result
        with patch.object(p.shutil, 'copyfile', side_effect=change_input):
            with self.assertRaisesRegex(b.BatchError, 'quarantined'): p.deliver(self.manifest)
        self.assertFalse(list((self.root / 'desktop').glob('专利撰写完成_*')))
        self.assertEqual(p.case_by_id(b.read(self.manifest), '001')['status'], 'needs_input')
        files = list(Path(p.deliver(self.manifest)).rglob('*.docx'))
        self.assertEqual(len(files), 1); self.assertTrue(files[0].parent.name.startswith('002_'))

    def test_peak_counts_are_ledger_evidence_only(self):
        rs = [self.reserve() for _ in range(4)]
        s = p.summary(self.manifest)
        self.assertEqual(s['peak_reserved_slots'], 4); self.assertEqual(s['peak_bound_workers'], 4)
        self.record(rs[0]); s = p.summary(self.manifest)
        self.assertEqual(s['active_workers'], 3); self.assertEqual(s['peak_bound_workers'], 4)
        self.assertIn('not independent proof', s['concurrency_evidence_scope'])

if __name__ == '__main__': unittest.main()
