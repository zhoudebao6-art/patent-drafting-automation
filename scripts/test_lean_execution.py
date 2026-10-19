"""General throughput contracts using isolated fixtures; no model/Word run claimed."""
import copy
import json
import re
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as q
import flat_delivery as flat
import prepare_handoff as h
import stage_bundle as s
import inspect_docx as inspector
import cli_pool as pool
import test_parallel_batch as fixtures


class LeanTests(unittest.TestCase):
    reserve = fixtures.ParallelTests.reserve
    receipt = fixtures.ParallelTests.receipt
    record = fixtures.ParallelTests.record

    def setUp(self):
        fixtures.ParallelTests.setUp(self)
        self.actual = Path(__file__).resolve().parent.parent
        policy = copy.deepcopy(fixtures.POLICY)
        policy.update(worker_preparation=s.MODE, execution_optimization=s.LEAN,
                      dispatch_transports=['collaboration', 'codex_cli'])
        policy['delivery'] = {'folder_prefix':'专利撰写完成','layout':'flat_final_files','timing':'after_each_acceptance'}
        b.atomic(q.POLICY, policy)
        shutil.copyfile(self.actual/'references/rules.json', q.RULES)
        selection = {'projects':[{'title':f'项目{i}', 'source_files':[f'{i}.docx'], 'primary_source':f'{i}.docx'} for i in range(1,4)]}
        self.manifest = q.init(self.src, self.root/'lean-work', selection, self.root/'desktop')
        self.batch = self.manifest.parent

    def test_packet_preserves_rule_routes_exceptions_and_exact_read_coverage(self):
        m=q.load_manifest(self.manifest); root=s.verify(m)
        rules=b.read(root/'references/rules.json')['rules']; coverage=b.read(root/'packet-coverage.json')
        for stage in s.STAGES:
            packet=(root/f'worker-{stage}.md').read_text(encoding='utf-8')
            controller=(root/'worker-controller.md').read_text(encoding='utf-8')
            record=coverage[stage]; included=record['included_rule_ids']; routed=record['controller_rule_ids']
            self.assertEqual(set(included)|set(routed),{r['id'] for r in rules if r['status']=='active'})
            self.assertFalse(set(included)&set(routed))
            for r in rules:
                if r['status']=='active':
                    target=packet if r['id'] in included else controller
                    self.assertIn(r['instruction'],target);self.assertIn(str(r['check']),target)
                    if r['exceptions']:self.assertIn(s.canonical(r['exceptions']),target)
            pieces=[(root/p['file']).read_text(encoding='utf-8') for p in record['read_parts']]
            self.assertEqual(''.join(pieces),packet)
            self.assertTrue(all(len(x)<=7000 for x in pieces))
        # The conditional index must lead to complete frozen sources, not drop
        # math/figure requirements or force both document families to be read.
        math_index = (root/'worker-math.md').read_text(encoding='utf-8')
        destinations = {url.split('#', 1)[0] for url in re.findall(r'\]\(([^)]+)\)', math_index)}
        self.assertEqual(destinations, {'references/'+name for name in s.MATH})
        for name in s.MATH:
            self.assertEqual((root/'references'/name).read_bytes(),
                             (self.actual/'references'/name).read_bytes())
        self.assertLess(len(math_index), sum(len((root/'references'/name).read_text(encoding='utf-8')) for name in s.MATH))
        policy=b.read(root/'references/batch-policy.json');policy.pop('execution_optimization')
        b.atomic(root/'references/batch-policy.json',policy)
        full,_=s.build_packet(root, {'version':m['rule_version'],'rules':rules}, 'draft')
        self.assertLess(len(packet.encode()),len(full.encode()))

    def test_unknown_project_rule_is_not_dropped_and_reference_drift_fails(self):
        m=q.load_manifest(self.manifest);root=s.verify(m);rules=b.read(root/'references/rules.json')
        extra=copy.deepcopy(rules['rules'][0]);extra.update(id='FUTURE-99',scope='project:特定',instruction='完整未知约束',exceptions=['未知例外'])
        rules['rules'].append(extra)
        # Even a familiar controller ID must remain visible when project scoped.
        next(r for r in rules['rules'] if r['id']=='BATCH-01')['scope']='project:特定'
        text,c=s.build_packet(root,rules,'draft')
        self.assertIn('FUTURE-99',c['included_rule_ids']);self.assertIn('BATCH-01',c['included_rule_ids'])
        self.assertIn(extra['instruction'],text)
        path=root/'references/manual-format-and-wording.md';path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'coverage|reference changed'):s.build_packet(root,rules,'draft')

    def test_source_navigation_and_active_rules_reused_without_recompiling_or_zip_parse(self):
        draft=self.reserve(1);prep=draft['worker_context']['prepared'];receipt=self.receipt(draft)
        inspector.run_inspection(receipt['artifact'],prep['draft_inspection_cache'],'draft',True,True)
        q.record(self.manifest,draft['case_id'],draft['attempt']['id'],receipt)
        with patch.object(h,'source_index',side_effect=AssertionError('Duplicate navigation compilation')), patch.object(inspector,'inspect',side_effect=AssertionError('DOCX reparse')):
            review=self.reserve(1)
        after=review['worker_context']['prepared']
        self.assertEqual(prep['rules_active'],after['rules_active'])
        self.assertEqual(prep['source_inspections'][0]['index'],after['source_inspections'][0]['index'])
        self.assertFalse((Path(review['attempt']['directory'])/'prepared/rules.active.json').exists())
        m=q.load_manifest(self.manifest);c=m['projects'][0];h.verify_prepared(m,c,c['attempts'][-1])

    def test_changed_navigation_or_packet_blocks_binding(self):
        r=q.reserve(self.manifest,1);prepared=r['worker_context']['prepared']
        m=q.load_manifest(self.manifest);c=m['projects'][0];a=c['attempts'][0]
        for f in [Path(prepared['source_inspections'][0]['index']),Path(prepared['pipeline']['stage_read_parts'][0]['path'])]:
            old=f.read_bytes();f.write_bytes(old+b'bad')
            with self.assertRaises(ValueError):h.verify_prepared(m,c,a)
            f.write_bytes(old)

    def test_incremental_publish_verifies_new_case_once_and_old_history_at_close(self):
        counts=[];original=q.verify_case
        def verify(m,c,*args,**kwargs):
            counts.append(c['id']);return original(m,c,*args,**kwargs)
        for expected in ['001','002','003']:
            self.record(self.reserve(1));self.record(self.reserve(1));counts.clear()
            with patch.object(q,'verify_case',side_effect=verify):q.deliver(self.manifest)
            self.assertEqual(counts.count(expected),1)
            self.assertEqual(set(counts),{'001','002','003'} if expected=='003' else {expected})
        counts.clear()
        with patch.object(q,'verify_case',side_effect=verify):q.deliver(self.manifest)
        self.assertEqual(counts,[])

    def test_published_file_change_still_blocks_next_append(self):
        self.record(self.reserve(1));self.record(self.reserve(1));dest=Path(q.deliver(self.manifest))
        final=next(dest.glob('*.docx'));final.write_bytes(final.read_bytes()+b'changed')
        self.record(self.reserve(1));self.record(self.reserve(1))
        with self.assertRaisesRegex(b.BatchError,'Delivered artifact changed'):q.deliver(self.manifest)
        self.assertEqual(len(list(dest.glob('*.docx'))),1)

    def test_source_or_render_changed_during_new_publication_cannot_pass(self):
        self.record(self.reserve(1));self.record(self.reserve(1))
        m=q.load_manifest(self.manifest);c=m['projects'][0]
        page=Path(c['receipts'][-1]['render']['evidence']).parent/'page-1.png'
        original=flat.shutil.copyfileobj
        def change(src,out):
            result=original(src,out);page.write_bytes(b'changed');return result
        with patch.object(flat.shutil,'copyfileobj',side_effect=change):
            with self.assertRaises(b.BatchError):q.deliver(self.manifest)
        self.assertFalse(list((self.root/'desktop').rglob('*.docx')))

    def test_last_case_failure_or_needs_input_still_closes_and_audits_history(self):
        self.record(self.reserve(1));self.record(self.reserve(1));q.deliver(self.manifest)
        q.defer(self.manifest,'002','failed','Isolated receipt failure fixture')
        q.defer(self.manifest,'003','needs_input','Missing source fixture')
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), patch.object(pool,'emit'), \
             patch.object(q,'verify_case',wraps=q.verify_case) as checked:
            result=pool.run(self.manifest,'controller',4)
        self.assertFalse(result['halted'])
        self.assertTrue(q.load_manifest(self.manifest)['delivery_closed'])
        self.assertTrue(any(call.args[1]['id']=='001' for call in checked.call_args_list))

    def test_terminal_failure_cannot_skip_tampered_historical_page(self):
        self.record(self.reserve(1));self.record(self.reserve(1));q.deliver(self.manifest)
        m=q.load_manifest(self.manifest)
        page=Path(m['projects'][0]['receipts'][-1]['render']['evidence']).parent/'page-1.png'
        page.write_bytes(b'tampered final page')
        q.defer(self.manifest,'002','failed','Known local failure')
        q.defer(self.manifest,'003','failed','Known local failure')
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), patch.object(pool,'emit'):
            result=pool.run(self.manifest,'controller',4)
        self.assertTrue(result['halted'])
        self.assertIn('Published history audit failed',result['blocking_reasons'][0]['reason'])
        self.assertFalse(q.load_manifest(self.manifest)['delivery_closed'])

    def test_interrupted_publication_recovery_rechecks_evidence_without_copying_again(self):
        self.record(self.reserve(1));self.record(self.reserve(1))
        original=b.atomic
        def fail(path,value):
            if Path(path)==self.manifest and value.get('delivery_path'):raise OSError('commit interrupted')
            return original(path,value)
        with patch.object(b,'atomic',side_effect=fail):
            with self.assertRaisesRegex(OSError,'commit interrupted'):q.deliver(self.manifest)
        final=next((self.root/'desktop').rglob('*.docx'));before=(final.read_bytes(),final.stat().st_mtime_ns)
        with patch.object(flat.shutil,'copyfileobj',side_effect=AssertionError('Duplicate copy')), \
             patch.object(q,'verify_case',wraps=q.verify_case) as checked:q.deliver(self.manifest)
        self.assertTrue(any(call.args[1]['id']=='001' for call in checked.call_args_list))
        self.assertEqual(before,(final.read_bytes(),final.stat().st_mtime_ns))
        self.assertEqual(q.load_manifest(self.manifest)['projects'][0]['status'],'delivered')

    def test_restart_checks_published_history_before_launching_pending_case(self):
        self.record(self.reserve(1));self.record(self.reserve(1));q.deliver(self.manifest)
        m=q.load_manifest(self.manifest)
        page=Path(m['projects'][0]['receipts'][-1]['render']['evidence']).parent/'page-1.png'
        page.write_bytes(b'changed between pool runs')
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), patch.object(pool,'emit'), \
             patch.object(pool,'start_supervisor') as launch:
            result=pool.run(self.manifest,'controller',4)
        self.assertTrue(result['halted'])
        launch.assert_not_called()
        self.assertEqual(q.load_manifest(self.manifest)['projects'][1]['status'],'pending')

    def test_failed_restart_audit_still_collects_existing_completed_work(self):
        self.record(self.reserve(1));self.record(self.reserve(1));q.deliver(self.manifest)
        completed=self.reserve(1);receipt=self.receipt(completed)
        m=q.load_manifest(self.manifest)
        page=Path(m['projects'][0]['receipts'][-1]['render']['evidence']).parent/'page-1.png'
        page.write_bytes(b'changed old evidence')
        def collect(manifest,controller,*,state=None,suppress_delivery=False):
            self.assertTrue(suppress_delivery)
            q.record(manifest,completed['case_id'],completed['attempt']['id'],receipt,controller)
            return True,False
        with patch.object(pool,'collect',side_effect=collect) as received, patch.object(pool,'emit'), \
             patch.object(pool,'preflight') as probe, patch.object(pool,'start_supervisor') as launch:
            result=pool.run(self.manifest,'controller',4)
        self.assertTrue(result['halted']);received.assert_called_once();probe.assert_not_called();launch.assert_not_called()
        self.assertEqual(q.load_manifest(self.manifest)['projects'][1]['status'],'drafted')


if __name__=='__main__':unittest.main()
