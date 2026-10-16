"""Receipt preflight is structural only; these fixtures are not patent reviews."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import batch_tool as b
import cli_evidence as ce
import cli_pool as pool
import parallel_batch as queue
import worker_receipt as wr
from test_batch_tool import docx
import test_cli_pool as cli_fixtures


class ReceiptPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_root = self.root/'cases/001'
        self.attempt_root = self.case_root/'stages/current'
        self.attempt_root.mkdir(parents=True)
        self.model = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}
        self.attempt = {'id': 'current', 'directory': str(self.attempt_root), 'stage': 'draft',
                        'input_artifact': None, 'input_artifact_sha256': None,
                        'context': {'case_id': '001', 'attempt': 'current', 'stage': 'draft'}}
        self.m = {'batch_dir': str(self.root), 'policy': {'execution_optimization': wr.MODE,
                   'review_mode': 'review_fix_then_recheck'}}
        self.case = {'id': '001', 'artifact_sha256': None}
        self.artifact = self.attempt_root/'actual-title.docx'; docx(self.artifact, 'output')

    def precheck(self, receipt):
        return wr.precheck_receipt(self.m, self.case, self.attempt, receipt)

    def draft(self):
        return {'verdict': 'complete', 'artifact': str(self.artifact), 'artifact_sha256': b.sha(self.artifact)}

    def review(self):
        previous = self.case_root/'stages/previous'; previous.mkdir(exist_ok=True)
        original = previous/'input.docx'; docx(original, 'original')
        self.attempt.update(stage='review', input_artifact=str(original), input_artifact_sha256=b.sha(original))
        self.attempt['context']['stage'] = 'review'
        self.case['artifact_sha256'] = b.sha(original)
        return {'verdict': 'fix_required', 'reviewed_sha256': b.sha(original),
                'checks': {key: 'not_run' for key in b.CHECKS},
                'findings': [{'status': 'unresolved', 'location': 'body', 'detail': 'actual issue'}]}

    def job(self, reservation_data=None):
        reservation = self.attempt_root/'reservation.json'
        b.atomic(reservation, self.attempt if reservation_data is None else reservation_data)
        self.attempt['reservation_sha256'] = b.sha(reservation)
        reserved = {'attempt': self.attempt, 'spawn_arguments': self.model, 'receipt_template': {},
                    'source_snapshots': [], 'worker_context': {'case_directory': str(self.case_root),
                    'review_mode': 'review_fix_then_recheck', 'prepared': {'pipeline': {
                        'execution_optimization': wr.MODE, 'root': str(self.root),
                        'stage_guide': 'stage', 'math_guide': 'math', 'toolkit_guide': 'toolkit', 'toolkit': 'tool'}}}}
        path = pool.prepare_job(reserved, str(self.root/'codex.exe'))
        job = b.read(path)
        identity = {'pid': 123, 'created': 987654, 'executable': str(self.root/'codex.exe')}
        dispatch = {'tool': 'codex.exec', 'context': self.attempt['context'],
                    'agent_id': 'cli:current', 'result': identity,
                    'prompt_sha256': b.sha(self.attempt_root/'prompt.txt'),
                    'arguments': {'cwd': str(self.attempt_root), **self.model, 'argv': job['argv']}}
        b.atomic(self.attempt_root/'dispatch.json', dispatch)
        return path

    def test_draft_actual_path_and_hash_are_measured_without_semantic_defaults(self):
        job = self.job(); decisions = {'verdict': 'complete', 'artifact': str(self.artifact)}
        result = wr.assemble(job, decisions)
        self.assertEqual(result['artifact_sha256'], b.sha(self.artifact))
        self.assertEqual(result['agent_id'], 'cli:current')
        self.assertEqual(result['context'], self.attempt['context'])
        self.assertNotIn('checks', result); self.assertNotIn('findings', result)
        self.assertNotIn('artifact_sha256', decisions)
        self.assertFalse((self.attempt_root/'receipt.json').exists())
        self.assertFalse((self.attempt_root/'worker-final.json').exists())

    def deferred_job(self):
        original = dict(self.attempt, preparation_state='pending')
        prepared = self.attempt_root/'prepared'
        prepared.mkdir()
        handoff = prepared/'handoff.json'
        b.atomic(handoff, {'context': self.attempt['context']})
        self.attempt.update(preparation_state='ready',
                            prepared_handoff={'path': str(handoff), 'sha256': b.sha(handoff)})
        return self.job(original)

    def test_sealed_deferred_preparation_accepts_only_normal_pending_to_ready(self):
        job = self.deferred_job()
        result = wr.assemble(job, {'verdict': 'complete', 'artifact': str(self.artifact)})
        self.assertEqual(result['artifact_sha256'], b.sha(self.artifact))
        self.assertNotIn('checks', result)
        self.assertEqual(b.read(self.attempt_root/'reservation.json')['preparation_state'], 'pending')

    def test_deferred_preparation_rejects_changed_identity_hash_seal_and_unknown_fields(self):
        job_path = self.deferred_job()
        original = b.read(job_path)
        def context(x): x['attempt']['context']['case_id'] = 'foreign'
        def source(x): x['attempt']['context']['source_digest'] = 'foreign'
        def attempt(x): x['attempt']['id'] = 'foreign'
        def reservation_sha(x): x['attempt']['reservation_sha256'] = '0'*64
        def seal(x): x['attempt']['prepared_handoff']['sha256'] = '0'*64
        def missing_seal(x): x['attempt'].pop('prepared_handoff')
        def unknown(x): x['attempt']['unknown'] = 'value'
        for mutate in (context, source, attempt, reservation_sha, seal, missing_seal, unknown):
            with self.subTest(mutation=mutate.__name__):
                job = copy.deepcopy(original); mutate(job)
                b.atomic(job_path, job)
                b.atomic(self.attempt_root/'cli-launch-intent.json',
                         {'context': job['attempt']['context'], 'job_sha256': b.sha(job_path)})
                with self.assertRaises((b.BatchError, ValueError)):
                    wr.assemble(job_path, {'verdict': 'complete', 'artifact': str(self.artifact)})

    def test_deferred_preparation_rejects_resealed_foreign_handoff_context(self):
        job_path = self.deferred_job()
        job = b.read(job_path)
        handoff = self.attempt_root/'prepared'/'handoff.json'
        b.atomic(handoff, {'context': {'case_id': 'foreign'}})
        job['attempt']['prepared_handoff']['sha256'] = b.sha(handoff)
        b.atomic(job_path, job)
        b.atomic(self.attempt_root/'cli-launch-intent.json',
                 {'context': job['attempt']['context'], 'job_sha256': b.sha(job_path)})
        with self.assertRaisesRegex(b.BatchError, 'preparation context changed'):
            wr.assemble(job_path, {'verdict': 'complete', 'artifact': str(self.artifact)})

    def test_real_deferred_preparation_job_and_assembly_preserve_frozen_evidence(self):
        source_dir = self.root/'source'; source_dir.mkdir()
        original = source_dir/'source.docx'; docx(original, 'synthetic source')
        original_sha = b.sha(original)
        selection = {'projects': [{'title': '合成回执准备链', 'source_files': ['source.docx'],
                                   'primary_source': 'source.docx'}]}
        manifest = queue.init(source_dir, self.root/'work', selection, self.root/'desktop')
        initial = queue.load_manifest(manifest)
        self.assertTrue(queue.nonblocking_pools(initial['policy']))
        self.assertEqual(initial['policy']['execution_optimization'], wr.MODE)
        snapshot_path = Path(initial['skill_snapshot']['path'])
        snapshot_sha = b.sha(snapshot_path)
        reserved = queue.reserve(manifest, 1, transport='codex_cli', defer_preparation=True)
        attempt = reserved['attempt']; root = Path(attempt['directory'])
        reservation = root/'reservation.json'; reservation_bytes = reservation.read_bytes()
        self.assertEqual(attempt['preparation_state'], 'pending')
        self.assertEqual(b.sha(reservation), attempt['reservation_sha256'])
        prepared = queue.prepare_reserved(manifest, reserved['case_id'], attempt['id'])
        ready = queue.finish_preparation(manifest, prepared)
        self.assertEqual(ready['attempt']['preparation_state'], 'ready')
        self.assertEqual(ready['attempt']['context'], attempt['context'])
        self.assertEqual(ready['attempt']['reservation_sha256'], attempt['reservation_sha256'])
        handoff = b.read(Path(ready['attempt']['prepared_handoff']['path']))
        self.assertEqual(handoff['status'], 'prepared_data_only')
        self.assertEqual(handoff['context'], attempt['context'])
        self.assertEqual(len(handoff['source_inspections']), 1)
        self.assertEqual(handoff['pipeline']['root'], str(snapshot_path.parent))
        self.assertIsInstance(handoff['prepared_files'], list)
        summary = handoff['source_inspections'][0]
        self.assertEqual(Path(summary['input']), Path(ready['source_snapshots'][0]['snapshot_path']))
        self.assertEqual(b.read(Path(summary['inspection']))['sha256'], original_sha)
        # Real preparation and job creation; only CLI dispatch identity is synthetic.
        # No executable is launched and no acceptance or visual verdict is supplied.
        executable = str(self.root/'codex.exe')
        job_path = pool.prepare_job(ready, executable)
        job = b.read(job_path)
        model = {key: ready['spawn_arguments'][key] for key in ('model', 'reasoning_effort')}
        dispatch = {'tool': 'codex.exec', 'context': attempt['context'],
                    'agent_id': 'cli:'+attempt['id'],
                    'result': {'pid': 123, 'created': 987654, 'executable': executable},
                    'prompt_sha256': b.sha(root/'prompt.txt'),
                    'arguments': {'cwd': str(root), **model, 'argv': job['argv']}}
        b.atomic(root/'dispatch.json', dispatch)
        artifact = root/'actual-title.docx'; docx(artifact, 'synthetic draft')
        manifest_bytes = manifest.read_bytes()
        result = wr.assemble(job_path, {'verdict': 'complete', 'artifact': str(artifact)})
        self.assertEqual(result['artifact_sha256'], b.sha(artifact))
        self.assertEqual(result['context'], attempt['context'])
        self.assertEqual(result['agent_id'], dispatch['agent_id'])
        self.assertEqual({key: result[key] for key in model}, model)
        self.assertNotIn('checks', result); self.assertNotIn('findings', result)
        self.assertEqual(reservation.read_bytes(), reservation_bytes)
        self.assertEqual(b.read(reservation)['preparation_state'], 'pending')
        self.assertEqual(b.sha(reservation), attempt['reservation_sha256'])
        self.assertEqual(b.sha(original), original_sha)
        for source in ready['source_snapshots']:
            self.assertEqual(b.sha(Path(source['snapshot_path'])), source['sha256'])
        self.assertEqual(b.sha(snapshot_path), snapshot_sha)
        self.assertEqual(manifest.read_bytes(), manifest_bytes)
        self.assertFalse((root/'receipt.json').exists())
        self.assertFalse((root/'worker-final.json').exists())
        self.assertFalse((self.root/'desktop').exists())

    def test_deferred_preparation_rejects_external_handoff_with_identical_bytes(self):
        job_path = self.deferred_job(); job = b.read(job_path)
        handoff = Path(job['attempt']['prepared_handoff']['path'])
        foreign = self.root/'foreign-handoff.json'; foreign.write_bytes(handoff.read_bytes())
        self.assertEqual(b.sha(foreign), b.sha(handoff))
        job['attempt']['prepared_handoff']['path'] = str(foreign)
        b.atomic(job_path, job)
        b.atomic(self.attempt_root/'cli-launch-intent.json',
                 {'context': job['attempt']['context'], 'job_sha256': b.sha(job_path)})
        with self.assertRaisesRegex(b.BatchError, 'preparation seal changed'):
            wr.assemble(job_path, {'verdict': 'complete', 'artifact': str(self.artifact)})

    def test_deferred_preparation_rejects_unknown_reservation_field_with_matching_sha(self):
        job_path = self.deferred_job(); job = b.read(job_path)
        path = self.attempt_root/'reservation.json'; reservation = b.read(path)
        reservation['unknown'] = 'not present in the job attempt'
        b.atomic(path, reservation)
        job['attempt']['reservation_sha256'] = b.sha(path)
        b.atomic(job_path, job)
        b.atomic(self.attempt_root/'cli-launch-intent.json',
                 {'context': job['attempt']['context'], 'job_sha256': b.sha(job_path)})
        with self.assertRaisesRegex(b.BatchError, 'reservation changed'):
            wr.assemble(job_path, {'verdict': 'complete', 'artifact': str(self.artifact)})

    def test_missing_verdict_is_caught_without_guessing_complete(self):
        with self.assertRaisesRegex(wr.CaseReceiptError, 'verdict'):
            wr.assemble(self.job(), {'artifact': str(self.artifact)})

    def test_review_not_run_and_unresolved_findings_are_preserved(self):
        decisions = self.review(); del decisions['reviewed_sha256']
        result = wr.assemble(self.job(), decisions)
        self.assertEqual(result['checks'], decisions['checks'])
        self.assertEqual(result['findings'], decisions['findings'])
        self.assertEqual(result['reviewed_sha256'], self.attempt['input_artifact_sha256'])

    def test_revised_requires_location_and_actual_correction_evidence(self):
        receipt = dict(self.review(), verdict='revised', artifact=str(self.artifact), artifact_sha256=b.sha(self.artifact))
        with self.assertRaisesRegex(wr.CaseReceiptError, 'closed findings'):
            self.precheck(receipt)
        receipt['findings'] = [{'status': 'resolved', 'location': 'body', 'correction_evidence': 'actual correction'}]
        self.precheck(receipt)

    def test_invalid_or_missing_checks_and_wrong_verdict_are_local_errors(self):
        receipt = self.review()
        for change in ({'verdict': 'complete'}, {'checks': {}}, {'findings': None}):
            with self.subTest(change=change), self.assertRaises(wr.CaseReceiptError):
                self.precheck(dict(receipt, **change))

    def test_missing_and_same_case_stale_paths_are_local_errors(self):
        receipt = self.draft()
        receipt['artifact'] = str(self.attempt_root/'missing.docx')
        with self.assertRaisesRegex(wr.CaseReceiptError, 'does not exist'): self.precheck(receipt)
        old = self.case_root/'stages/old'; old.mkdir()
        docx(old/'old.docx', 'old')
        receipt.update(artifact=str(old/'old.docx'), artifact_sha256=b.sha(old/'old.docx'))
        with self.assertRaisesRegex(wr.CaseReceiptError, 'current attempt'): self.precheck(receipt)

    def test_hash_mismatch_remains_fatal_even_with_wrong_verdict(self):
        receipt = dict(self.draft(), artifact_sha256='0'*64, verdict='invalid')
        with self.assertRaises(b.BatchError) as raised: self.precheck(receipt)
        self.assertNotIsInstance(raised.exception, wr.CaseReceiptError)

    def test_local_missing_artifact_cannot_mask_known_identity_or_foreign_path_errors(self):
        receipt=dict(self.review(),artifact=str(self.attempt_root/'missing.docx'))
        foreign=self.root/'foreign.json';foreign.write_text('{}')
        render=self.attempt_root/'render.json';render.write_text('{}')
        mutations=[{'reviewed_sha256':'0'*64},
                   {'render':{'artifact_sha256':'0'*64}},
                   {'render':{'evidence':str(foreign)}},
                   {'render':{'evidence':str(render),'evidence_sha256':'0'*64}}]
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(b.BatchError) as raised:
                self.precheck(dict(receipt,**change))
            self.assertNotIsInstance(raised.exception,wr.CaseReceiptError)

    def test_missing_hash_and_stale_artifact_cannot_mask_foreign_render(self):
        receipt=self.review()
        foreign=self.root/'foreign.json';foreign.write_text('{}')
        receipt.update(artifact=self.attempt['input_artifact'],artifact_sha256='',
                       render={'evidence':str(foreign)})
        with self.assertRaises(b.BatchError) as raised:self.precheck(receipt)
        self.assertNotIsInstance(raised.exception,wr.CaseReceiptError)

    def test_parent_traversal_is_integrity_error_not_relative_path_typo(self):
        with self.assertRaises(b.BatchError) as raised:self.precheck(dict(self.draft(),artifact='../foreign.docx'))
        self.assertNotIsInstance(raised.exception,wr.CaseReceiptError)

    def test_foreign_path_and_changed_reviewed_input_remain_fatal(self):
        receipt = self.draft(); foreign = self.root/'foreign.docx'; docx(foreign, 'foreign')
        receipt.update(artifact=str(foreign), artifact_sha256=b.sha(foreign))
        with self.assertRaises(b.BatchError) as raised: self.precheck(receipt)
        self.assertNotIsInstance(raised.exception, wr.CaseReceiptError)
        receipt = self.review(); docx(self.attempt['input_artifact'], 'changed')
        with self.assertRaises(b.BatchError) as raised: self.precheck(receipt)
        self.assertNotIsInstance(raised.exception, wr.CaseReceiptError)

    def test_assembly_rejects_changed_identity_or_launch_intent(self):
        job = self.job()
        with self.assertRaisesRegex(b.BatchError, 'contradicts actual identity'):
            wr.assemble(job, dict(self.draft(), agent_id='another-agent'))
        b.atomic(self.attempt_root/'cli-launch-intent.json', {})
        with self.assertRaisesRegex(b.BatchError, 'launch intent changed'): wr.assemble(job, self.draft())

    def test_pass_is_not_inferred_and_requires_final_render(self):
        receipt = dict(self.review(), verdict='pass')
        with self.assertRaisesRegex(wr.CaseReceiptError, 'all six'): self.precheck(receipt)
        receipt.update(checks={key: 'passed' for key in b.CHECKS}, findings=[])
        with self.assertRaisesRegex(wr.CaseReceiptError, 'render'): self.precheck(receipt)

    def test_stale_same_case_render_is_detected_before_exit(self):
        receipt = self.review(); old = self.case_root/'stages/old-review/render'; old.mkdir(parents=True)
        b.atomic(old/'render.json', {})
        receipt['render'] = {'evidence': str(old/'render.json'), 'evidence_sha256': b.sha(old/'render.json'),
                             'artifact_sha256': self.attempt['input_artifact_sha256']}
        with self.assertRaisesRegex(wr.CaseReceiptError, 'older review'): self.precheck(receipt)

    def test_pass_assembly_measures_render_identity_without_claiming_visual_review(self):
        from PIL import Image
        from pypdf import PdfWriter
        receipt = self.review(); render = self.attempt_root/'render'; render.mkdir()
        image = render/'page-1.png'; Image.new('RGB', (32, 32), 'white').save(image)
        pdf = render/'output.pdf'; writer = PdfWriter(); writer.add_blank_page(width=32, height=32)
        with pdf.open('wb') as stream: writer.write(stream)
        evidence = render/'render.json'
        b.atomic(evidence, {'status': 'rendered_pending_visual_review',
                           'input_sha256': self.attempt['input_artifact_sha256'],
                           'pdf': {'file': pdf.name, 'sha256': b.sha(pdf)},
                           'pages': [{'file': image.name, 'sha256': b.sha(image)}]})
        receipt.update(verdict='pass', checks={key: 'passed' for key in b.CHECKS}, findings=[],
                       render={'evidence': str(evidence)})
        result = wr.assemble(self.job(), receipt)
        self.assertEqual(result['render']['evidence_sha256'], b.sha(evidence))
        self.assertEqual(result['render']['artifact_sha256'], self.attempt['input_artifact_sha256'])
        self.assertEqual(result['checks'], receipt['checks'])
        self.assertEqual(b.read(evidence)['status'], 'rendered_pending_visual_review')


class LeanPoolIsolationTests(unittest.TestCase):
    """Run against a real synthetic frozen queue and real execution verification."""
    setUp = cli_fixtures.CliPoolTests.setUp
    receipt = cli_fixtures.CliPoolTests.receipt
    fixture_cli = cli_fixtures.CliPoolTests.fixture_cli

    def enable_cli(self):
        cli_fixtures.CliPoolTests.enable_cli(self)
        m = b.read(self.manifest)
        policy = copy.deepcopy(m['policy'])
        policy.update(execution_optimization=wr.MODE, worker_preparation='frozen_stage_packet_v1',
                      delivery={'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files',
                                'timing': 'after_each_acceptance'})
        path = self.root/'lean-policy.json'; b.atomic(path, policy)
        selection = {'projects': [{'title': '同名', 'source_files': [f'{i}.docx'],
                                  'primary_source': f'{i}.docx'} for i in range(1, 11)]}
        with patch.object(queue, 'POLICY', path):
            self.manifest = queue.init(self.src, self.root/'lean-work', selection, self.root/'lean-desktop')
        self.batch = self.manifest.parent
        (self.batch/'cli-pool').mkdir(exist_ok=True)

    def rewrite_true_final(self, reserved, receipt):
        root = Path(reserved['attempt']['directory'])
        for name in ('events.jsonl', 'session.jsonl'):
            rows = [json.loads(line) for line in (root/name).read_text(encoding='utf-8').splitlines()]
            for row in rows:
                if row.get('type') == 'item.completed': row['item']['text'] = json.dumps(receipt)
                if row.get('payload', {}).get('phase') == 'final_answer':
                    row['payload']['content'][0]['text'] = json.dumps(receipt)
            (root/name).write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        b.atomic(root/'worker-final.json', receipt)
        seal = b.read(root/'cli-completion.json')
        seal['files'] = {name: b.sha(root/name) for name in ce.COMPLETION_FILES}
        b.atomic(root/'cli-completion.json', seal)

    def test_contract_error_isolates_case_and_allows_other_case_and_next_reserve(self):
        self.enable_cli()
        bad = queue.reserve(self.manifest, 4, transport='codex_cli')
        good = queue.reserve(self.manifest, 4, transport='codex_cli')
        receipt, _, _ = self.fixture_cli(bad); self.fixture_cli(good)
        receipt['verdict'] = 'wrong-enum'; self.rewrite_true_final(bad, receipt)
        with patch.object(ce, 'process_identity', return_value=None), patch.object(pool, 'emit'):
            changed, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        m = queue.load_manifest(self.manifest)
        self.assertEqual(m['projects'][0]['status'], 'failed')
        self.assertEqual(m['projects'][1]['status'], 'drafted')
        next_job = queue.reserve(self.manifest, 4, transport='codex_cli')
        self.assertNotEqual(next_job['case_id'], bad['case_id'])
        rejection = b.read(Path(bad['attempt']['directory'])/'cli-rejection.json')
        self.assertEqual(rejection['scope'], 'case'); self.assertFalse(rejection['automatic_retry'])

    def test_tampered_receipt_hash_still_halts_even_when_verdict_is_wrong(self):
        self.enable_cli(); reserved = queue.reserve(self.manifest, 4, transport='codex_cli')
        receipt, _, _ = self.fixture_cli(reserved)
        receipt.update(verdict='wrong-enum', artifact_sha256='0'*64)
        self.rewrite_true_final(reserved, receipt)
        with patch.object(ce, 'process_identity', return_value=None), patch.object(pool, 'emit'):
            changed, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(changed); self.assertTrue(halted)
        self.assertEqual(b.read(Path(reserved['attempt']['directory'])/'cli-rejection.json')['scope'], 'pool')

    def test_unknown_error_is_not_reclassified_by_message_text(self):
        self.enable_cli(); reserved = queue.reserve(self.manifest, 4, transport='codex_cli')
        self.fixture_cli(reserved)
        with patch.object(ce, 'process_identity', return_value=None), patch.object(pool, 'emit'), \
                patch.object(queue, 'record', side_effect=ValueError('Invalid writing verdict')):
            _, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(halted)

    def test_recovery_can_collect_valid_review_without_publication(self):
        self.enable_cli(); draft=queue.reserve(self.manifest,1,transport='codex_cli')
        self.fixture_cli(draft)
        with patch.object(ce,'process_identity',return_value=None),patch.object(pool,'emit'):
            changed,halted=pool.collect(self.manifest,'controller')
        self.assertTrue(changed);self.assertFalse(halted)
        review=queue.reserve(self.manifest,1,transport='codex_cli')
        receipt,_,_=self.fixture_cli(review);receipt.update(verdict='pass',findings=[])
        self.rewrite_true_final(review,receipt)
        with patch.object(ce,'process_identity',return_value=None),patch.object(pool,'emit'), \
             patch.object(queue,'deliver',side_effect=AssertionError('Publishing prohibited during failed history audit')):
            changed,halted=pool.collect(self.manifest,'controller',suppress_delivery=True)
        self.assertTrue(changed);self.assertFalse(halted)
        self.assertEqual(queue.load_manifest(self.manifest)['projects'][0]['status'],'accepted')
        self.assertFalse(list((self.root/'lean-desktop').rglob('*.docx')))

    def test_lean_prompt_prefers_nonoverlapping_parts_and_real_receipt_helper(self):
        self.enable_cli(); reserved = queue.reserve(self.manifest, 4, transport='codex_cli')
        pipeline = reserved['worker_context']['prepared']['pipeline']
        pipeline['stage_read_parts'] = [{'path': 'part-one', 'sha256': 'fixture', 'characters': 1}]
        prompt = pool.worker_prompt(reserved)
        self.assertIn('不重叠规则块一次', prompt)
        self.assertIn('worker_receipt.py', prompt)
        self.assertNotIn('先完整读取一次本批冻结阶段清单', prompt)


if __name__ == '__main__': unittest.main()
