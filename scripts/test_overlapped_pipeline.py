"""Synthetic queue/CLI evidence; these fixtures are not patent acceptance."""
import copy
import tempfile
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import cli_pool as pool
import cli_evidence as ce
import test_parallel_batch as fixtures
from test_parallel_batch import POLICY, docx
import test_cli_pool as cli_fixtures


class PipelineTests(unittest.TestCase):
    reserve = fixtures.ParallelTests.reserve
    record = fixtures.ParallelTests.record
    fixture_cli = cli_fixtures.CliPoolTests.fixture_cli

    def setUp(self):
        fixtures.ParallelTests.setUp(self)
        # Change the synthetic fixture policy before any work starts, not a
        # production batch. Content contracts are tested by their own suites.
        m = b.read(self.manifest)
        m['policy'].update(stage_scheduling=p.IMMEDIATE_STAGES,
                           model_policy='sol_review_xhigh_v20',
                           review_mode='review_fix_then_recheck',
                           dispatch_transports=['collaboration', 'codex_cli'])
        m['policy']['models'] = {
            s: {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high' if s == 'draft' else 'xhigh'}
            for s in ('draft', 'review', 'revise')}
        m['policy']['delivery'] = dict(folder_prefix='专利撰写完成', layout='flat_final_files', timing='after_each_acceptance')
        for c in m['projects']:
            c['title'] = '测试项目' + c['id']
        b.atomic(self.batch/'policy.snapshot.json', m['policy'])
        b.atomic(self.manifest, m)
        (self.batch/'cli-pool').mkdir(exist_ok=True)

    def receipt(self, r, verdict='complete'):
        result = fixtures.ParallelTests.receipt(self, r, verdict)
        result.update({k:r['spawn_arguments'][k] for k in ('model','reasoning_effort')})
        return result

    def test_four_slots_keep_two_drafts_and_two_reviews_when_both_queues_ready(self):
        drafts = [self.reserve() for _ in range(4)]
        for r in drafts:
            self.record(r)
        selected = [self.reserve() for _ in range(4)]
        self.assertEqual([r['attempt']['stage'] for r in selected], ['review','review','draft','draft'])
        self.assertEqual([r['case_id'] for r in selected[:2]], ['001','002'])
        self.assertEqual(p.plan(self.manifest,4)['action'],'wait')

    def test_two_slots_review_finished_case_while_other_draft_continues(self):
        first, other = self.reserve(2), self.reserve(2)
        self.record(first)
        next_stage = self.reserve(2)
        self.assertEqual((next_stage['case_id'],next_stage['attempt']['stage']),('001','review'))
        self.assertEqual(p.case_by_id(b.read(self.manifest),other['case_id'])['active_attempt'],other['attempt']['id'])

    def test_single_slot_repair_chain_and_delivery_before_another_draft(self):
        draft = self.reserve(1); self.record(draft)
        review = self.reserve(1); self.record(review,'fix_required')
        revise = self.reserve(1)
        self.assertEqual((revise['case_id'],revise['attempt']['stage']),('001','revise'))
        self.record(revise)
        check = self.reserve(1)
        self.assertEqual((check['case_id'],check['attempt']['stage']),('001','review'))
        self.assertNotEqual(check['attempt']['id'],review['attempt']['id'])
        self.assertEqual(check['spawn_arguments']['reasoning_effort'],'xhigh')
        self.record(check)
        self.assertEqual(p.plan(self.manifest,1)['action'],'deliver')
        with self.assertRaises(p.NoReadyWork):
            self.reserve(1)
        target = Path(p.deliver(self.manifest))
        self.assertTrue((target/'测试项目001.docx').is_file())
        self.assertEqual(self.reserve(1)['case_id'],'002')

    def test_inline_revision_is_rechecked_before_next_draft(self):
        draft=self.reserve(1);self.record(draft)
        review=self.reserve(1);receipt=self.receipt(review,'revised')
        output=Path(review['attempt']['directory'])/'revised.docx';docx(output,'changed content')
        receipt.update(artifact=str(output),artifact_sha256=b.sha(output),
                       findings=[{'status':'closed','location':'P1','correction_evidence':'synthetic correction'}])
        p.record(self.manifest,review['case_id'],review['attempt']['id'],receipt)
        m=b.read(self.manifest);self.assertEqual(m['projects'][0]['status'],'revised')
        self.assertFalse(m.get('delivery_path'))
        check=self.reserve(1)
        self.assertEqual((check['case_id'],check['attempt']['stage']),('001','review'))
        self.assertEqual(check['attempt']['input_artifact_sha256'],b.sha(output))

    def test_borrow_unused_lane_and_skip_ineligible_draft(self):
        drafts=[self.reserve() for _ in range(4)]
        for r in drafts:self.record(r)
        one,two=self.reserve(),self.reserve()
        self.assertEqual([one['attempt']['stage'],two['attempt']['stage']],['review','review'])
        for n in range(5,11):docx(self.src/f'{n}.docx','source changed')
        borrowed=self.reserve()
        self.assertEqual(borrowed['attempt']['stage'],'review')

    def test_delivery_barrier_even_when_remaining_capacity_is_full(self):
        drafts=[self.reserve() for _ in range(4)]
        self.record(drafts[0]);review=self.reserve();self.record(review)
        plan=p.plan(self.manifest,3)
        self.assertEqual(plan['action'],'deliver')
        self.assertEqual(plan['accepted_case_ids'],['001'])
        p.deliver(self.manifest)
        self.assertEqual(p.plan(self.manifest,3)['action'],'wait')

    def test_unknown_marker_and_non_incremental_delivery_rejected(self):
        policy=copy.deepcopy(b.read(self.manifest)['policy'])
        p.validate_policy(policy)
        with self.assertRaisesRegex(b.BatchError,'stage scheduling'):
            p.validate_policy(dict(policy,stage_scheduling='unknown'))
        policy['delivery']=copy.deepcopy(POLICY['delivery'])
        with self.assertRaisesRegex(b.BatchError,'per-case delivery'):
            p.validate_policy(policy)

    def test_collect_yields_after_one_completed_case_then_can_drain(self):
        rows=[p.reserve(self.manifest,4,transport='codex_cli') for _ in range(3)]
        for r in rows:self.fixture_cli(r,bind=False)
        with patch.object(ce,'process_identity',return_value=None),patch.object(pool,'emit'):
            self.assertEqual(pool.collect(self.manifest,'controller'),(True,False))
            self.assertEqual([c['status'] for c in b.read(self.manifest)['projects'][:3]],['drafted','pending','pending'])
            self.assertEqual(pool.collect(self.manifest,'controller',drain=True),(True,False))
        self.assertEqual([c['status'] for c in b.read(self.manifest)['projects'][:3]],['drafted']*3)

    def test_known_pool_failure_drains(self):
        rows=[p.reserve(self.manifest,4,transport='codex_cli') for _ in range(3)]
        for r in rows:self.fixture_cli(r,bind=False)
        Path(rows[0]['attempt']['directory'],'dispatch.json').write_text('{broken',encoding='utf-8')
        with patch.object(ce,'process_identity',return_value=None),patch.object(pool,'emit'):
            changed,halted=pool.collect(self.manifest,'controller')
        self.assertTrue(changed and halted)
        m=b.read(self.manifest)
        self.assertIsNotNone(m['projects'][0]['active_attempt'])
        self.assertEqual([c['status'] for c in m['projects'][1:3]],['drafted','drafted'])

    def test_cli_event_order_review_and_delivery_before_other_drafts_finish(self):
        launched=[];events=[];sealed=set()
        class Stop(Exception):pass
        real_collect=pool.collect
        def collect(manifest,controller,*,state=None,**kwargs):
            if len(launched)>=4 and 'draft' not in sealed:
                sealed.add('draft');self.fixture_cli(launched[0],bind=False)
            elif len(launched)>=5 and 'review' not in sealed:
                sealed.add('review');self.fixture_cli(launched[4],bind=False,verdict='pass')
            return real_collect(manifest,controller,state=state,**kwargs)
        def prepare(reserved,exe):
            launched.append(reserved)
            if len(launched)==5:
                self.assertEqual((reserved['case_id'],reserved['attempt']['stage']),('001','review'))
            if len(launched)==6:
                m=p.load_manifest(self.manifest)
                self.assertTrue((Path(m['delivery_path'])/'测试项目001.docx').is_file())
                self.assertEqual(m['projects'][0]['status'],'delivered')
                for r in launched[1:4]:
                    self.assertEqual(p.case_by_id(m,r['case_id'])['active_attempt'],r['attempt']['id'])
                self.assertIn('delivered',events)
                raise Stop()
            return 'synthetic-job'
        def status(manifest,*,state=None):
            return {'cases':[{'case_id':c['id'],'next_action':'wait_cli' if c['active_attempt'] else 'draft'}
                            for c in (state or p.load_manifest(manifest))['projects']]}
        def event(manifest,name,**kwargs):events.append(name)
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')),\
             patch.object(pool,'collect',side_effect=collect),patch.object(pool,'status',side_effect=status),\
             patch.object(pool,'prepare_job',side_effect=prepare),patch.object(pool,'start_supervisor'),\
             patch.object(ce,'process_identity',return_value=None),patch.object(pool,'event',side_effect=event),\
             patch.object(pool.time,'sleep',side_effect=AssertionError('Unexpected wait before handoff')):
            with self.assertRaises(Stop):pool.run(self.manifest,'controller',4)
        self.assertEqual(len(launched),6)


class StopDrainTests(unittest.TestCase):
    def test_exit_after_single_case_yield_drains_once_without_spinning(self):
        for peer in ('inspect_interrupted_launch','wait_external_worker'):
            for unresolved in (False,True):
                with self.subTest(peer=peer,unresolved=unresolved),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);manifest=root/'manifest.json';manifest.write_text('{}')
                    m={'controller':'controller','policy':{'stage_scheduling':p.IMMEDIATE_STAGES,
                       'dispatch_transports':['codex_cli'],'concurrency':{'target_workers':4}},
                       'projects':[{'id':str(n),'status':'pending','active_attempt':str(n)} for n in range(3)]}
                    calls=[]
                    def collect(manifest,controller,*,state,**kwargs):
                        calls.append(kwargs)
                        self.assertLessEqual(len(calls),2,'Unresolved completion caused repeated drain')
                        if len(calls)==1:state['projects'][0].update(status='drafted',active_attempt=None)
                        elif not unresolved:state['projects'][2].update(status='drafted',active_attempt=None)
                        return True,False
                    def status(manifest,*,state=None):
                        return {'cases':[{'case_id':c['id'],'next_action':(peer if c['id']=='1' else 'verify_and_collect')
                                         if c['active_attempt'] else 'draft'} for c in m['projects']]}
                    with patch.object(p,'load_manifest',return_value=m),patch.object(p,'locked',return_value=nullcontext()),\
                         patch.object(pool,'preflight',return_value='synthetic-cli'),patch.object(pool,'collect',side_effect=collect),\
                         patch.object(pool,'status',side_effect=status),patch.object(pool,'event'),\
                         patch.object(p,'reserve',side_effect=p.NoReadyWork({'action':'wait','ready':[],'blocked':[]})):
                        result=pool.run(manifest,'controller',4)
                    self.assertEqual(calls,[{}, {'drain':True}])
                    self.assertIsNotNone(m['projects'][1]['active_attempt'])
                    self.assertEqual(result['halted'],peer=='inspect_interrupted_launch')


if __name__=='__main__':unittest.main()
