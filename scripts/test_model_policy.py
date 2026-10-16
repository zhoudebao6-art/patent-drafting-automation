"""Model-policy regressions with synthetic dispatches and files, never paid calls.

These tests verify model/effort routing, frozen evidence and stage isolation.
The tiny DOCX/render fixtures do not establish patent or visual acceptance.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

import batch_tool as b
import cli_evidence as ce
import cli_pool
import parallel_batch as queue
import recover_cli_batch as recovery
import stage_bundle
import test_cli_evidence as cli_fixtures
import test_parallel_batch as queue_fixtures
import test_recover_cli_batch as recovery_fixtures
from test_batch_tool import POLICY as SERIAL_POLICY, docx


SKILL = Path(__file__).resolve().parent.parent
MARKER = 'sol_review_xhigh_v20'
NEW_MODELS = {
    'draft': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'},
    'review': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'},
    'revise': {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'},
}


def current_policy(base):
    result = copy.deepcopy(base)
    result.update(model_policy=MARKER, models=copy.deepcopy(NEW_MODELS))
    return result


class QueueFixture:
    reserve = queue_fixtures.ParallelTests.reserve

    def __init__(self, root, policy=None):
        self.root = Path(root)
        self.root.mkdir(parents=True)
        self.src = self.root / 'input'
        self.src.mkdir()
        docx(self.src / 'source.docx', 'source technical material')
        selection = {'projects': [{'title': '一种处理系统', 'source_files': ['source.docx'],
                                   'primary_source': 'source.docx'}]}
        if policy is None:
            # Use the real new-batch defaults, including frozen preparation and
            # document contracts. Only model execution and rendering are fixtures.
            self.manifest = queue.init(self.src, self.root / 'work', selection, self.root / 'desktop')
        else:
            policy_path = self.root / 'policy.json'
            rules_path = self.root / 'rules.json'
            b.atomic(policy_path, policy)
            b.atomic(rules_path, {'version': 20, 'rules': []})
            with patch.object(queue, 'POLICY', policy_path), patch.object(queue, 'RULES', rules_path):
                self.manifest = queue.init(self.src, self.root / 'work', selection, self.root / 'desktop')
        self.batch = self.manifest.parent

    def receipt(self, reserved, verdict='complete'):
        value = queue_fixtures.ParallelTests.receipt(self, reserved, verdict)
        value.update({key: reserved['spawn_arguments'][key] for key in ('model', 'reasoning_effort')})
        if reserved['attempt']['stage'] in {'draft', 'revise'}:
            self.write_docx(value['artifact'], reserved['attempt']['id'])
            value['artifact_sha256'] = b.sha(value['artifact'])
        else:
            import incremental_review as reuse
            manifest = b.read(self.manifest)
            if reuse.enabled(manifest['policy']):
                case = next(c for c in manifest['projects'] if c['id'] == reserved['case_id'])
                attempt = next(a for a in case['attempts'] if a['id'] == reserved['attempt']['id'])
                pointer, _ = reuse.plan_pointer(attempt)
                # Explicit synthetic coverage is now required by real defaults.
                # Do not turn off the new policy to make an older fixture pass.
                value['review_coverage'] = {'schema_version': 1, 'plan_sha256': pointer['plan_sha256'],
                    'checks': {k: {'fresh': ['*'], 'passed': ['*'], 'inherited': [], 'impact_review': ''}
                               for k in reuse.CHECKS}, 'pages': []}
                if verdict == 'pass':
                    evidence = Path(value['render']['evidence'])
                    metadata = b.read(evidence)
                    metadata.update(input=attempt['input_artifact'], runtime_fingerprint='synthetic-model-policy-fixture')
                    b.atomic(evidence, metadata)
                    value['render']['evidence_sha256'] = b.sha(evidence)
                    value['review_coverage']['pages'] = [{'page': 1, 'status': 'passed', 'mode': 'fresh'}]
                else:
                    value['checks'].update(source_support='synthetic correction required', rendered_pages='not_run')
                    value['review_coverage']['checks']['source_support']['passed'] = []
                    value.pop('render')
        return value

    def write_docx(self, path, token):
        # Keep the model-routing fixture compliant with current content/layout
        # defaults using the same shared authoring helper, not a parallel template.
        import json
        import shutil
        import authoring_toolkit as toolkit
        from test_authoring_toolkit import minimal_spec
        path = Path(path)
        spec = minimal_spec()
        spec.update(project_title='一种处理系统', protected_names=[], invention_from_claims=True)
        spec['sections'][0]['blocks'][0]['text']='本发明提供记录处理。'
        spec['sections'][2]['blocks']=[dict(id='claim1',type='paragraph',role='body',
            text='1.一种处理系统，其特征在于，所述处理系统包括记录模块，所述记录模块用于接收记录，所述记录供回查使用。')]
        lines=[('project_title','一种处理系统'),('heading','技术领域'),('body','本发明涉及记录处理。'),
               ('heading','发明内容'),('body','与现有技术相比，本发明的有益效果在于：'),
               ('body','1.通过保留记录支持回查。'),('heading','附图说明'),
               ('heading','具体实施方式'),('body','保存对应记录。'),('body',token)]
        spec['sections'][3]['blocks']=[dict(id='section'+str(i),type='paragraph',role=role,text=text)
                                      for i,(role,text) in enumerate(lines)]
        with tempfile.TemporaryDirectory(dir=path.parent) as temp:
            root=Path(temp)
            (root/'spec.json').write_text(json.dumps(spec,ensure_ascii=False),encoding='utf-8')
            toolkit.build(root,'spec.json','generated')
            shutil.copyfile(root/'generated/draft.docx',path)

    def record(self, reserved, verdict=None):
        verdict = verdict or ('pass' if reserved['attempt']['stage'] == 'review' else 'complete')
        value = self.receipt(reserved, verdict)
        self.submit(reserved, value)
        return value

    def submit(self, reserved, value):
        return queue.record(self.manifest, reserved['case_id'], reserved['attempt']['id'], value)

    def revised_receipt(self, reserved):
        attempt = reserved['attempt']
        artifact = Path(attempt['directory']) / 'revised.docx'
        self.write_docx(artifact, 'corrected_' + attempt['id'])
        return dict(NEW_MODELS['review'], context=attempt['context'], stage='review',
                    agent_id='/root/' + attempt['task_name'], verdict='revised',
                    reviewed_sha256=attempt['input_artifact_sha256'], artifact=str(artifact),
                    artifact_sha256=b.sha(artifact),
                    checks={key: 'not_run' if key == 'rendered_pages' else 'checked_and_corrected'
                            for key in b.CHECKS},
                    findings=[{'status': 'resolved', 'location': 'word/document.xml P0001',
                               'correction_evidence': 'Synthetic source-bound correction fixture'}])


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_policy_explicitly_selects_sol_xhigh_for_review_and_revise(self):
        policy = b.read(SKILL / 'references/batch-policy.json')
        self.assertEqual(policy['model_policy'], MARKER)
        self.assertEqual(policy['models'], NEW_MODELS)
        queue.validate_policy(policy)

    def test_marker_selects_exact_pairs_without_reinterpreting_old_policy(self):
        legacy = copy.deepcopy(SERIAL_POLICY)
        new = current_policy(legacy)
        b.validate_policy(legacy)
        b.validate_policy(new)
        self.assertEqual(b.model_label(legacy, 'review'), 'GPT-6-astra/high')
        self.assertEqual(b.model_label(new, 'draft'), 'GPT-6.1-sol/high')
        self.assertEqual(b.model_label(new, 'review'), 'GPT-6.1-sol/xhigh')
        self.assertEqual(b.model_label(new, 'revise'), 'GPT-6.1-sol/xhigh')
        for stage in NEW_MODELS:
            for replacement in ({'model': 'gpt-6-astra', 'reasoning_effort': 'high'},
                                {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'},
                                {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'},
                                {'model': 'gpt-6.1-sol', 'reasoning_effort': None}):
                if replacement == NEW_MODELS[stage]:
                    continue
                policy = copy.deepcopy(new)
                policy['models'][stage] = replacement
                with self.subTest(stage=stage, replacement=replacement), self.assertRaises(b.BatchError):
                    b.validate_policy(policy)
        without_marker = copy.deepcopy(new)
        del without_marker['model_policy']
        with self.assertRaises(b.BatchError):
            b.validate_policy(without_marker)
        for marker in (None, '', 'future_unknown_policy', True):
            policy = dict(new, model_policy=marker)
            with self.subTest(marker=marker), self.assertRaises(b.BatchError):
                b.validate_policy(policy)
        old = copy.deepcopy(legacy)
        old['models']['draft']['reasoning_effort'] = None
        b.validate_policy(old, legacy_snapshot=True)
        with self.assertRaises(b.BatchError):
            b.validate_policy(old)

    def test_real_default_init_uses_separate_revision_and_new_sol_xhigh_review(self):
        w = QueueFixture(self.root / 'default')
        m = b.read(w.manifest)
        self.assertEqual(m['policy']['model_policy'], MARKER)
        self.assertEqual(m['policy'], b.read(w.batch / 'policy.snapshot.json'))
        draft = w.reserve(1)
        self.assertEqual({k: draft['spawn_arguments'][k] for k in NEW_MODELS['draft']}, NEW_MODELS['draft'])
        self.assertEqual(draft['spawn_arguments']['fork_turns'], 'none')
        w.record(draft)
        review = w.reserve(1)
        self.assertEqual({k: review['spawn_arguments'][k] for k in NEW_MODELS['review']}, NEW_MODELS['review'])
        self.assertEqual(m['policy']['stage_pools'], {'draft': 6, 'review': 3, 'revise': 3})
        self.assertEqual(m['policy']['review_mode'], 'review_only')
        with self.assertRaises(b.BatchError):
            w.submit(review, w.revised_receipt(review))
        w.record(review, 'fix_required')
        revision = w.reserve(1)
        self.assertEqual(revision['attempt']['stage'], 'revise')
        self.assertEqual({k: revision['spawn_arguments'][k] for k in NEW_MODELS['revise']}, NEW_MODELS['revise'])
        with self.assertRaises(b.BatchError):
            w.submit(revision, w.receipt(revision, 'pass'))
        w.record(revision)
        state = b.read(w.manifest)['projects'][0]
        self.assertEqual(state['status'], 'revised')
        self.assertNotIn('passed_review_sha256', state)
        with self.assertRaises(b.BatchError):
            queue.deliver(w.manifest)
        recheck = w.reserve(1)
        self.assertEqual(recheck['attempt']['stage'], 'review')
        self.assertEqual({k: recheck['spawn_arguments'][k] for k in NEW_MODELS['review']}, NEW_MODELS['review'])
        self.assertEqual(recheck['spawn_arguments']['fork_turns'], 'none')
        self.assertNotEqual(recheck['attempt']['id'], review['attempt']['id'])
        self.assertNotEqual(recheck['attempt']['task_name'], review['attempt']['task_name'])
        self.assertNotEqual(recheck['attempt']['directory'], review['attempt']['directory'])
        self.assertNotEqual(recheck['attempt']['task_name'], revision['attempt']['task_name'])
        stale_worker = w.receipt(recheck, 'pass')
        stale_worker['agent_id'] = '/root/' + review['attempt']['task_name']
        with self.assertRaises(b.BatchError):
            w.submit(recheck, stale_worker)
        w.record(recheck)
        dest = Path(queue.deliver(w.manifest))
        self.assertEqual([path.name for path in dest.glob('*.docx')], ['一种处理系统.docx'])
        case = b.read(w.manifest)['projects'][0]
        self.assertEqual(case['status'], 'delivered')
        self.assertEqual([a['stage'] for a in case['attempts']], ['draft', 'review', 'revise', 'review'])

    def test_independent_revise_and_following_review_both_use_sol_xhigh(self):
        policy = current_policy(queue_fixtures.POLICY)
        policy['review_mode'] = 'review_fix_then_recheck'
        w = QueueFixture(self.root / 'revise', policy)
        draft = w.reserve(1)
        w.record(draft)
        review = w.reserve(1)
        w.record(review, 'fix_required')
        revision = w.reserve(1)
        self.assertEqual(revision['attempt']['stage'], 'revise')
        self.assertEqual({k: revision['spawn_arguments'][k] for k in NEW_MODELS['revise']}, NEW_MODELS['revise'])
        w.record(revision)
        recheck = w.reserve(1)
        self.assertEqual({k: recheck['spawn_arguments'][k] for k in NEW_MODELS['review']}, NEW_MODELS['review'])
        self.assertNotEqual(recheck['attempt']['task_name'], revision['attempt']['task_name'])
        self.assertEqual(recheck['spawn_arguments']['fork_turns'], 'none')
        w.record(recheck)
        self.assertEqual(b.read(w.manifest)['projects'][0]['status'], 'accepted')

    def test_review_dispatch_and_receipt_reject_wrong_model_or_lower_effort(self):
        w = QueueFixture(self.root / 'reject', current_policy(queue_fixtures.POLICY))
        w.record(w.reserve(1))
        review = queue.reserve(w.manifest, 1)
        attempt = review['attempt']
        path = Path(attempt['directory']) / 'dispatch.json'
        good = {'tool': 'collaboration.spawn_agent', 'context': attempt['context'],
                'arguments': review['spawn_arguments'], 'result': {'task_name': '/root/' + attempt['task_name']}}
        replacements = ({'model': 'gpt-6-astra'}, {'reasoning_effort': 'high'},
                        {'reasoning_effort': 'low'}, {'reasoning_effort': None}, {'fork_turns': 'all'})
        for replacement in replacements:
            bad = copy.deepcopy(good)
            bad['arguments'].update(replacement)
            b.atomic(path, bad)
            with self.subTest(replacement=replacement), self.assertRaises(b.BatchError):
                queue.bind(w.manifest, review['case_id'], attempt['id'], path)
        b.atomic(path, good)
        queue.bind(w.manifest, review['case_id'], attempt['id'], path)
        receipt = w.receipt(review, 'pass')
        for replacement in replacements[:-1]:
            with self.subTest(replacement=replacement), self.assertRaises(b.BatchError):
                w.submit(review, dict(receipt, **replacement))
        w.submit(review, receipt)

    def test_old_frozen_policy_and_stage_packet_keep_astra_high(self):
        legacy = copy.deepcopy(queue_fixtures.POLICY)
        legacy['worker_preparation'] = stage_bundle.MODE
        w = QueueFixture(self.root / 'legacy', legacy)
        before_policy = (w.batch / 'policy.snapshot.json').read_bytes()
        w.record(w.reserve(1))
        review = w.reserve(1)
        self.assertEqual(review['spawn_arguments']['model'], 'gpt-6-astra')
        self.assertEqual(review['spawn_arguments']['reasoning_effort'], 'high')
        frozen = Path(review['worker_context']['prepared']['pipeline']['root'])
        packet, _ = stage_bundle.build_packet(frozen, {'version': 4, 'rules': []}, 'review')
        self.assertIn('gpt-6-astra/high', packet.splitlines()[2].lower())
        self.assertNotIn('gpt-6.1-sol/xhigh', packet.splitlines()[2].lower())
        w.record(review)
        self.assertEqual((w.batch / 'policy.snapshot.json').read_bytes(), before_policy)
        self.assertEqual(b.read(w.manifest)['projects'][0]['receipts'][-1]['model'], 'gpt-6-astra')

    def test_stage_packets_and_both_prompt_modes_name_frozen_stage_model(self):
        w = QueueFixture(self.root / 'prompt')
        w.record(w.reserve(1))
        review = w.reserve(1)
        frozen = Path(review['worker_context']['prepared']['pipeline']['root'])
        for stage in ('draft', 'review', 'revise'):
            packet, _ = stage_bundle.build_packet(frozen, {'version': 20, 'rules': []}, stage)
            self.assertIn(b.model_label(b.read(w.manifest)['policy'], stage).lower(), packet.splitlines()[2].lower())
        for pipeline in (True, False):
            reserved = copy.deepcopy(review)
            if not pipeline:
                reserved['worker_context']['prepared'].pop('pipeline')
            prompt = cli_pool.worker_prompt(reserved)
            with self.subTest(pipeline=pipeline):
                self.assertIn('gpt-6.1-sol/xhigh', prompt.lower())
                self.assertNotIn('实际模型/high', prompt)
                self.assertNotIn('模型和high强度', prompt)

    def test_cli_job_uses_reserved_review_xhigh_in_both_command_and_prompt(self):
        w = QueueFixture(self.root / 'cli-job')
        w.record(w.reserve(1))
        review = queue.reserve(w.manifest, 1, transport='codex_cli')
        job = b.read(cli_pool.prepare_job(review, str(self.root / 'codex.exe')))
        self.assertEqual(job['model'], NEW_MODELS['review'])
        self.assertEqual(job['argv'][job['argv'].index('--model') + 1], 'gpt-6.1-sol')
        self.assertEqual(job['argv'][job['argv'].index('-c') + 1], 'model_reasoning_effort="xhigh"')
        folder = Path(review['attempt']['directory'])
        self.assertEqual(job['prompt_sha256'], b.sha(folder / 'prompt.txt'))
        self.assertIn('gpt-6.1-sol/xhigh', (folder / 'prompt.txt').read_text(encoding='utf-8').lower())
        self.assertTrue((folder / 'cli-launch-intent.json').is_file())

    def test_recovery_preserves_old_and_new_frozen_policy_and_next_model(self):
        for new_policy in (False, True):
            with self.subTest(new_policy=new_policy):
                fixture = recovery_fixtures.RecoveryTests(methodName='runTest')
                fixture.setUp()
                try:
                    if new_policy:
                        manifest = b.read(fixture.manifest)
                        manifest['policy'] = current_policy(manifest['policy'])
                        b.atomic(fixture.batch / 'policy.snapshot.json', manifest['policy'])
                        b.atomic(fixture.manifest, manifest)
                    reserved, _ = fixture.failed_draft()
                    folder = Path(reserved['attempt']['directory'])
                    old_manifest = fixture.manifest.read_bytes()
                    old_policy = (fixture.batch / 'policy.snapshot.json').read_bytes()
                    evidence = {name: (folder / name).read_bytes()
                                for name in ('dispatch.json', 'events.jsonl', 'session.jsonl', 'worker-final.json')}
                    with patch.object(ce, 'process_identity', return_value=None):
                        destination = recovery.recover(fixture.manifest, fixture.root / 'recoveries', 'review-controller')
                    self.assertEqual(fixture.manifest.read_bytes(), old_manifest)
                    self.assertEqual((fixture.batch / 'policy.snapshot.json').read_bytes(), old_policy)
                    self.assertEqual({name: (folder / name).read_bytes() for name in evidence}, evidence)
                    recovered = queue.load_manifest(destination)
                    case = recovered['projects'][0]
                    provenance = b.read(case['recovery_evidence'])
                    expected = NEW_MODELS['review'] if new_policy else SERIAL_POLICY['models']['review']
                    self.assertEqual(provenance['next_model'], expected)
                    self.assertEqual(provenance['next_stage'], 'independent_sol_review' if new_policy else 'independent_astra_review')
                    self.assertFalse(provenance['acceptance_inherited'])
                    self.assertEqual(case['status'], 'drafted')
                    self.assertNotIn('passed_review_sha256', case)
                    next_review = queue.reserve(destination, 1, controller='review-controller')
                    self.assertEqual({key: next_review['spawn_arguments'][key] for key in expected}, expected)
                    self.assertEqual(next_review['spawn_arguments']['fork_turns'], 'none')
                finally:
                    fixture.doCleanups()

    def serial_delivery(self, root, policy, pending=False):
        root.mkdir()
        source = root / 'input'
        source.mkdir()
        names = ['a.docx', 'b.docx'] if pending else ['a.docx']
        for name in names:
            docx(source / name, name)
        policy_path = root / 'policy.json'
        rules_path = root / 'rules.json'
        b.atomic(policy_path, policy)
        b.atomic(rules_path, {'version': 20, 'rules': []})
        selection = {'projects': [{'title': name, 'source_files': [name], 'primary_source': name} for name in names]}
        with patch.object(b, 'POLICY', policy_path), patch.object(b, 'RULES', rules_path):
            manifest = b.init(source, root / 'work', selection, root / 'desktop')
        before = (manifest.parent / 'policy.snapshot.json').read_bytes()
        for stage in ('draft', 'review'):
            m = b.read(manifest)
            model = m['policy']['models'][stage]
            agent = 'fixture_' + stage
            dispatch = manifest.parent / (stage + '.json')
            b.atomic(dispatch, {'tool': 'collaboration.spawn_agent',
                'context': {'batch_id': m['batch_id'], 'case_id': '001', 'stage': stage},
                'arguments': dict(model, fork_turns='none', task_name=agent), 'result': {'task_name': agent}})
            value = dict(model, stage=stage, agent_id=agent, dispatch_evidence=str(dispatch))
            if stage == 'draft':
                artifact = manifest.parent / 'draft.docx'
                docx(artifact, agent)
                value['artifact'] = str(artifact)
            else:
                value.update(reviewed_sha256=b.sha(b.read(manifest)['projects'][0]['artifact']),
                             verdict='pass', findings=[], checks={key: 'passed' for key in b.CHECKS})
            b.record(manifest, '001', value)
        dest = Path(b.deliver(manifest))
        self.assertEqual((manifest.parent / 'policy.snapshot.json').read_bytes(), before)
        return (dest / '交付清单.txt').read_text(encoding='utf-8')

    def test_serial_legacy_and_incremental_delivery_labels_follow_frozen_policy(self):
        for new in (False, True):
            for incremental in (False, True):
                policy = current_policy(SERIAL_POLICY) if new else copy.deepcopy(SERIAL_POLICY)
                with self.subTest(new=new, incremental=incremental):
                    text = self.serial_delivery(self.root / f'serial-{new}-{incremental}', policy, pending=incremental)
                    label = 'GPT-6.1-sol/xhigh' if new else 'GPT-6-astra/high'
                    self.assertIn('GPT-6.1-sol/high', text)
                    self.assertIn(label, text)
                    self.assertNotIn('GPT-6-astra/high' if new else 'GPT-6.1-sol/xhigh', text)

    def test_parallel_consolidated_delivery_label_follows_frozen_policy(self):
        for new in (False, True):
            policy = current_policy(queue_fixtures.POLICY) if new else copy.deepcopy(queue_fixtures.POLICY)
            w = QueueFixture(self.root / f'parallel-{new}', policy)
            w.record(w.reserve(1))
            w.record(w.reserve(1))
            dest = Path(queue.deliver(w.manifest))
            text = (dest / '交付清单.txt').read_text(encoding='utf-8')
            self.assertIn('GPT-6.1-sol/xhigh' if new else 'GPT-6-astra/high', text)
            self.assertNotIn('GPT-6-astra/high' if new else 'GPT-6.1-sol/xhigh', text)


class CliModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cli_fixtures.CliEvidenceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configure(self, model, effort):
        f = self.fixture
        f.model = {'model': model, 'reasoning_effort': effort}
        f.dispatch['arguments'].update(f.model)
        f.dispatch['arguments']['argv'] = ce.build_command(f.exe, f.root, model, effort)
        f.session[1]['payload'].update(model=model, effort=effort)
        return f

    def test_cli_allows_only_explicit_supported_model_effort_pairs(self):
        f = self.fixture
        for model, effort in (('gpt-6.1-sol', 'high'), ('gpt-6.1-sol', 'xhigh'), ('gpt-6-astra', 'high')):
            with self.subTest(model=model, effort=effort):
                argv = ce.build_command(f.exe, f.root, model, effort)
                self.assertEqual(argv[argv.index('--model') + 1], model)
                self.assertEqual(argv[argv.index('-c') + 1], f'model_reasoning_effort="{effort}"')
        for model, effort in (('gpt-6-astra', 'xhigh'), ('gpt-6.1-sol', 'low'),
                              ('gpt-6.1-sol', None), ('gpt-6.1-sol', 'max'), ('other', 'high')):
            with self.subTest(model=model, effort=effort), self.assertRaises(ValueError):
                ce.build_command(f.exe, f.root, model, effort)

    def test_xhigh_dispatch_completion_and_recovery_match_actual_session(self):
        f = self.configure('gpt-6.1-sol', 'xhigh')
        with patch.object(ce, 'process_identity', return_value=f.identity):
            self.assertEqual(ce.verify_dispatch(f.dispatch, f.attempt, f.model), 'cli:attempt1')
        f.seal()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertEqual(ce.verify_completion(f.attempt, f.dispatch)['session_id'], f.session_id)
            self.assertEqual(ce.verify_dispatch(f.dispatch, f.attempt, f.model), 'cli:attempt1')

    def test_xhigh_dispatch_rejects_lowered_argv_and_self_declared_wrong_effort(self):
        f = self.configure('gpt-6.1-sol', 'xhigh')
        for field in ('arguments', 'argv', 'frozen_policy'):
            dispatch = copy.deepcopy(f.dispatch)
            model = dict(f.model)
            if field == 'arguments':
                dispatch['arguments']['reasoning_effort'] = 'high'
            elif field == 'argv':
                dispatch['arguments']['argv'] = ce.build_command(f.exe, f.root, 'gpt-6.1-sol', 'high')
            else:
                model['reasoning_effort'] = 'high'
            with self.subTest(field=field), patch.object(ce, 'process_identity', return_value=f.identity):
                with self.assertRaises(ValueError):
                    ce.verify_dispatch(dispatch, f.attempt, model)

    def test_every_real_turn_context_must_match_xhigh_and_sol(self):
        f = self.configure('gpt-6.1-sol', 'xhigh')
        original = copy.deepcopy(f.session)
        for key, value in (('effort', 'high'), ('effort', None), ('model', 'gpt-6-astra')):
            # Keep the first context correct; validating only one context must
            # not allow a later silently downgraded or switched context.
            f.session = copy.deepcopy(original)
            extra = copy.deepcopy(original[1])
            extra['payload'][key] = value
            f.session.append(extra)
            f.seal()
            with self.subTest(key=key, value=value), patch.object(ce, 'process_identity', return_value=None):
                with self.assertRaises(ValueError):
                    ce.verify_completion(f.attempt, f.dispatch)

    def test_old_astra_high_execution_evidence_still_validates(self):
        f = self.configure('gpt-6-astra', 'high')
        f.seal()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertEqual(ce.verify_completion(f.attempt, f.dispatch)['receipt'], f.receipt)


if __name__ == '__main__':
    unittest.main()
