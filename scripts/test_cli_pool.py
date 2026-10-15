"""Synthetic scheduling/evidence regressions, not independent patent acceptance."""
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import cli_pool as pool
import cli_evidence as ce
import test_parallel_batch as fixtures


class CliPoolTests(unittest.TestCase):
    setUp = fixtures.ParallelTests.setUp
    receipt = fixtures.ParallelTests.receipt

    def enable_cli(self):
        m = b.read(self.manifest)
        m['policy']['dispatch_transports'] = ['collaboration', 'codex_cli']
        b.atomic(self.batch/'policy.snapshot.json', m['policy']); b.atomic(self.manifest, m)
        (self.batch/'cli-pool').mkdir(exist_ok=True)

    def fixture_cli(self, r, bind=True, verdict=None):
        a = r['attempt']; root = Path(a['directory'])
        model = {k: r['spawn_arguments'][k] for k in ('model', 'reasoning_effort')}
        (root/'prompt.txt').write_bytes(b'fixture input')
        identity = {'pid': 123, 'created': 987654, 'executable': str(self.root/'codex.exe')}
        ev = {'tool': 'codex.exec', 'context': a['context'], 'agent_id': 'cli:'+a['id'], 'result': identity,
              'prompt_sha256': b.sha(root/'prompt.txt'), 'arguments': {'cwd': str(root), **model,
              'argv': ce.build_command(identity['executable'], root, **model)}}
        b.atomic(root/'dispatch.json', ev); b.atomic(root/'cli-launch-intent.json', {})
        if bind:
            with patch.object(ce, 'process_identity', return_value=identity):
                p.bind(self.manifest, r['case_id'], a['id'], root/'dispatch.json')
        receipt = self.receipt(r) if verdict is None else self.receipt(r, verdict)
        receipt['agent_id'] = ev['agent_id']
        ident = str(uuid.uuid4())
        events = [{'type': 'thread.started', 'thread_id': ident},
                  {'type': 'turn.started'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(receipt)}},
                  {'type': 'turn.completed'}]
        session = [{'type': 'session_meta', 'payload': {'id': ident, 'source': 'exec', 'cwd': str(root)}},
                   {'type': 'turn_context', 'payload': {'model': model['model'], 'effort': model['reasoning_effort'], 'cwd': str(root),
                                                      'sandbox_policy': {'type': 'workspace-write'}}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
                       {'type': 'input_text', 'text': 'fixture input'}]}}]
        session.append({'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer',
                                                        'content':[{'type':'output_text','text':json.dumps(receipt)}]}})
        for name, rows in [('events.jsonl', events), ('session.jsonl', session)]:
            (root/name).write_text('\n'.join(json.dumps(x) for x in rows), encoding='utf8')
        b.atomic(root/'worker-final.json', receipt)
        b.atomic(root/'cli-exit.json', {'pid': identity['pid'], 'created': identity['created'], 'returncode': 0, 'started': 1, 'finished': 2})
        meta = {'exit_code': 0, 'pid': identity['pid'], 'created': identity['created'],
                'files': {name: b.sha(root/name) for name in ce.COMPLETION_FILES}}
        b.atomic(root/'cli-completion.json', meta)
        return receipt, ev, meta

    def test_reserve_verifies_only_first_eligible_case_once(self):
        with patch.object(p, 'verify_case', wraps=p.verify_case) as verify:
            r = p.reserve(self.manifest, 4)
        self.assertEqual(r['case_id'], '001')
        self.assertEqual(verify.call_count, 1)

    def test_reserve_skips_bad_source_then_checks_one_good_case(self):
        fixtures.docx(self.src/'1.docx', 'changed')
        with patch.object(p, 'verify_case', wraps=p.verify_case) as verify:
            r = p.reserve(self.manifest, 4)
        self.assertEqual(r['case_id'], '002')
        self.assertEqual(verify.call_count, 2)

    def test_explicit_plan_still_reports_all_blocked_sources(self):
        for i in (1, 9): fixtures.docx(self.src/f'{i}.docx', 'changed')
        plan = p.plan(self.manifest, 4)
        self.assertEqual({r['case_id'] for r in plan['blocked']}, {'001', '009'})
        self.assertEqual(len(plan['ready']), 8)

    def test_old_frozen_policy_cannot_silently_use_cli(self):
        with self.assertRaisesRegex(b.BatchError, 'Transport not authorized'):
            p.reserve(self.manifest, 4, transport='codex_cli')

    def test_cli_completion_passes_original_draft_gate_and_new_review_session(self):
        self.enable_cli()
        r = p.reserve(self.manifest, 4, transport='codex_cli')
        receipt, ev, _ = self.fixture_cli(r)
        with patch.object(ce, 'process_identity', return_value=None):
            recorded = p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)
        self.assertEqual(recorded['status'], 'drafted')
        self.assertIsNotNone(recorded['attempts'][0]['cli_session_id'])
        review = p.reserve(self.manifest, 4, transport='codex_cli')
        self.assertEqual(review['attempt']['stage'], 'review')
        self.assertEqual(review['spawn_arguments']['model'], 'gpt-6-astra')
        self.assertNotEqual(review['attempt']['id'], r['attempt']['id'])

    def test_live_cli_cannot_release_slot_by_early_receipt(self):
        self.enable_cli(); r = p.reserve(self.manifest, 4, transport='codex_cli')
        receipt, ev, _ = self.fixture_cli(r)
        with patch.object(ce, 'process_identity', return_value=ev['result']):
            with self.assertRaisesRegex(b.BatchError, 'still live'):
                p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)
        self.assertEqual(p.load_manifest(self.manifest)['projects'][0]['active_attempt'], r['attempt']['id'])

    def test_changed_submitted_receipt_rejected(self):
        self.enable_cli(); r = p.reserve(self.manifest, 4, transport='codex_cli')
        receipt, _, _ = self.fixture_cli(r); receipt['verdict'] = 'needs_input'
        with patch.object(ce, 'process_identity', return_value=None):
            with self.assertRaisesRegex(b.BatchError, 'differs from submitted'):
                p.record(self.manifest, r['case_id'], r['attempt']['id'], receipt)

    def test_launch_intent_requires_exit_proof_before_reserved_cancel(self):
        self.enable_cli(); r = p.reserve(self.manifest, 4, transport='codex_cli')
        _, ev, _ = self.fixture_cli(r, bind=False)
        with self.assertRaisesRegex(b.BatchError, 'confirmed stop'):
            p.cancel(self.manifest, r['case_id'], r['attempt']['id'], 'fixture')
        with patch.object(ce, 'process_identity', return_value=None):
            pool.release_failed(self.manifest, {'id':r['case_id']}, r['attempt'], 'fixture failure', 'controller')
        self.assertEqual(p.load_manifest(self.manifest)['projects'][0]['status'], 'failed')

    def test_completed_unbound_worker_is_collected_without_relaunch(self):
        self.enable_cli(); r = p.reserve(self.manifest, 4, transport='codex_cli')
        self.fixture_cli(r, bind=False)
        with patch.object(ce, 'process_identity', return_value=None), patch.object(pool, 'start_supervisor', side_effect=AssertionError('No replay')):
            changed, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        self.assertEqual(p.load_manifest(self.manifest)['projects'][0]['status'], 'drafted')

    def test_bound_worker_has_one_full_completion_gate_when_collected(self):
        self.enable_cli(); r=p.reserve(self.manifest,4,transport='codex_cli')
        self.fixture_cli(r)
        with patch.object(ce,'process_identity',return_value=None), \
             patch.object(ce,'verify_completion',wraps=ce.verify_completion) as verify, patch.object(pool,'emit'):
            changed,halted=pool.collect(self.manifest,'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        self.assertEqual(verify.call_count,1)

    def closeout_fixture(self):
        from test_lean_execution import LeanTests
        fixture = LeanTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        policy = b.read(p.POLICY)
        policy['post_review_optimization'] = 'single_closeout_v25'
        b.atomic(p.POLICY, policy)
        selection = {'projects': [{'title': '项目1', 'source_files': ['1.docx'], 'primary_source': '1.docx'}]}
        fixture.manifest = p.init(fixture.src, fixture.root/'closeout-work', selection, fixture.root/'desktop')
        fixture.batch = fixture.manifest.parent
        (fixture.batch/'cli-pool').mkdir(exist_ok=True)
        return fixture

    def test_unbound_completion_parses_twice_without_new_frozen_policy(self):
        self.enable_cli(); r = p.reserve(self.manifest, 4, transport='codex_cli')
        self.fixture_cli(r, bind=False)
        with patch.object(ce, 'process_identity', return_value=None), \
             patch.object(ce, '_verify_completion_full', wraps=ce._verify_completion_full) as full, \
             patch.object(pool, 'emit'):
            changed, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        self.assertEqual(full.call_count, 2)

    def test_new_policy_unbound_completion_parses_once_and_records_real_result(self):
        fixture = self.closeout_fixture()
        r = p.reserve(fixture.manifest, 4, transport='codex_cli')
        self.fixture_cli.__func__(fixture, r, bind=False)
        with patch.object(ce, 'process_identity', return_value=None), \
             patch.object(ce, '_verify_completion_full', wraps=ce._verify_completion_full) as full, \
             patch.object(ce, '_recheck_completion', wraps=ce._recheck_completion) as recheck, \
             patch.object(pool, 'emit'):
            changed, halted = pool.collect(fixture.manifest, 'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        self.assertEqual((full.call_count, recheck.call_count), (1, 1))
        self.assertEqual(p.load_manifest(fixture.manifest)['projects'][0]['status'], 'drafted')

    def test_new_policy_change_after_bind_halts_and_does_not_record(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                fixture = self.closeout_fixture()
                r = p.reserve(fixture.manifest, 4, transport='codex_cli')
                self.fixture_cli.__func__(fixture, r, bind=False)
                original = p.bind
                def change(*args, **kwargs):
                    result = original(*args, **kwargs)
                    path = Path(r['attempt']['directory'])/'events.jsonl'
                    data = path.read_bytes()
                    if replace:
                        import os
                        temporary = path.with_suffix('.replacement')
                        temporary.write_bytes(data); os.replace(temporary, path)
                    else:
                        path.write_bytes(data + b' ')
                    return result
                with patch.object(ce, 'process_identity', return_value=None), \
                     patch.object(p, 'bind', side_effect=change), patch.object(pool, 'emit'):
                    changed, halted = pool.collect(fixture.manifest, 'controller')
                self.assertTrue(changed); self.assertTrue(halted)
                case = p.load_manifest(fixture.manifest)['projects'][0]
                self.assertEqual(case['status'], 'failed')
                self.assertFalse(any(row.get('stage') == 'draft' for row in case['receipts']))
                self.assertEqual(case['attempts'][0]['state'], 'cancelled')
                rejection = b.read(Path(r['attempt']['directory'])/'cli-rejection.json')
                self.assertEqual(rejection['scope'], 'pool')

    def test_delivery_event_matches_actual_outcome_after_source_change(self):
        import flat_delivery as flat
        for changed_source in (False,True):
            with self.subTest(changed_source=changed_source):
                fixture=self.closeout_fixture();fixture.record(fixture.reserve(1))
                review=p.reserve(fixture.manifest,1,transport='codex_cli')
                receipt=fixture.receipt
                with patch.object(fixture,'receipt',side_effect=lambda job:receipt(job,'pass')):
                    self.fixture_cli.__func__(fixture,review,bind=False)
                original=flat.shutil.copyfileobj
                def copy(src,dest):
                    original(src,dest)
                    if changed_source:(fixture.src/'1.docx').write_bytes(b'changed during publication')
                with patch.object(ce,'process_identity',return_value=None), \
                     patch.object(flat.shutil,'copyfileobj',side_effect=copy), \
                     patch.object(pool,'event') as event,patch.object(pool,'emit'):
                    changed,halted=pool.collect(fixture.manifest,'controller')
                self.assertTrue(changed);self.assertFalse(halted)
                events=[call.args[1] for call in event.call_args_list]
                case=p.load_manifest(fixture.manifest)['projects'][0]
                files=list((fixture.root/'desktop').rglob('*.docx'))
                if changed_source:
                    self.assertEqual(case['status'],'needs_input');self.assertFalse(files)
                    self.assertIn('delivery_input_changed',events);self.assertNotIn('delivered',events)
                else:
                    self.assertEqual(case['status'],'delivered');self.assertEqual(len(files),1)
                    self.assertIn('delivered',events);self.assertNotIn('delivery_input_changed',events)

    def test_new_policy_does_not_share_completion_across_collect_calls(self):
        fixture = self.closeout_fixture()
        r = p.reserve(fixture.manifest, 4, transport='codex_cli')
        self.fixture_cli.__func__(fixture, r, bind=False)
        original = pool.event
        def interrupt(manifest, kind, **kwargs):
            if kind == 'bound': raise RuntimeError('simulated controller interruption')
            return original(manifest, kind, **kwargs)
        with patch.object(ce, 'process_identity', return_value=None), \
             patch.object(ce, '_verify_completion_full', wraps=ce._verify_completion_full) as full, \
             patch.object(pool, 'emit'):
            with patch.object(pool, 'event', side_effect=interrupt):
                with self.assertRaisesRegex(RuntimeError, 'controller interruption'):
                    pool.collect(fixture.manifest, 'controller')
            changed, halted = pool.collect(fixture.manifest, 'controller')
        self.assertTrue(changed); self.assertFalse(halted)
        self.assertEqual(full.call_count, 2)

    def test_bad_unbound_completion_does_not_prevent_collecting_other_case(self):
        self.enable_cli()
        bad = p.reserve(self.manifest, 4, transport='codex_cli')
        good = p.reserve(self.manifest, 4, transport='codex_cli')
        self.fixture_cli(bad, bind=False); self.fixture_cli(good, bind=False)
        Path(bad['attempt']['directory'], 'worker-final.json').write_text('{}', encoding='utf8')
        with patch.object(ce, 'process_identity', return_value=None):
            changed, halted = pool.collect(self.manifest, 'controller')
        self.assertTrue(changed); self.assertTrue(halted)
        m=p.load_manifest(self.manifest)
        self.assertEqual(m['projects'][0]['status'], 'failed')
        self.assertEqual(m['projects'][1]['status'], 'drafted')

    def test_prompt_contains_only_current_case_and_explicit_stage(self):
        self.enable_cli(); r=p.reserve(self.manifest,4,prepare=True,transport='codex_cli')
        prompt=pool.worker_prompt(r)
        self.assertIn('cli:'+r['attempt']['id'],prompt)
        self.assertIn(json.dumps(r['worker_context']['primary_snapshot'],ensure_ascii=False),prompt)
        other=p.load_manifest(self.manifest)['projects'][1]['sources'][0]['snapshot_path']
        self.assertNotIn(json.dumps(other,ensure_ascii=False),prompt)

    def test_duplicate_worker_cannot_acquire_attempt_lock_or_start_model(self):
        self.enable_cli(); r=p.reserve(self.manifest,4,prepare=True,transport='codex_cli')
        job=pool.prepare_job(r,str(self.root/'codex.exe'))
        with p.locked(job), patch.object(pool.subprocess,'Popen',side_effect=AssertionError('Duplicate model launch')):
            with self.assertRaisesRegex(b.BatchError,'lock busy'):
                pool.worker(job)

    def test_four_slots_fill_then_completed_case_refills_while_three_still_work(self):
        self.enable_cli(); launched=[]; completed=False; iterations=0
        class StopTest(Exception): pass
        real_collect=pool.collect
        def collect(manifest,controller,*,state=None):
            nonlocal completed, iterations
            iterations += 1
            self.assertLess(iterations, 9, 'Pool did not refill promptly')
            if len(launched)==4 and not completed:
                completed=True
                self.fixture_cli(launched[0],bind=False)
                return real_collect(manifest,controller,state=state)
            return False,False
        def prepare(reserved,exe):
            launched.append(reserved)
            if len(launched)==5:
                self.assertEqual(reserved['attempt']['stage'],'review')
                self.assertEqual(reserved['case_id'],launched[0]['case_id'])
                m=p.load_manifest(self.manifest)
                self.assertEqual(sum(bool(c['active_attempt']) for c in m['projects']),4)
                for r in launched[1:4]:
                    self.assertEqual(p.case_by_id(m,r['case_id'])['active_attempt'],r['attempt']['id'])
                raise StopTest()
            return 'synthetic-job'
        def status(manifest,*,state=None):
            return {'cases':[{'case_id':c['id'],'next_action':'wait_cli' if c['active_attempt'] else 'draft'}
                            for c in (state if state is not None else p.load_manifest(manifest))['projects']]}
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), \
             patch.object(pool,'collect',side_effect=collect), patch.object(pool,'status',side_effect=status), \
             patch.object(pool,'prepare_job',side_effect=prepare), patch.object(pool,'start_supervisor'), \
             patch.object(ce,'process_identity',return_value=None), patch.object(pool,'emit'):
            with self.assertRaises(StopTest): pool.run(self.manifest,'controller',4)
        self.assertEqual(len({r['case_id'] for r in launched[:4]}),4)

    def test_malformed_dispatch_retains_slot_but_other_case_is_collected(self):
        self.enable_cli()
        bad=p.reserve(self.manifest,4,transport='codex_cli'); good=p.reserve(self.manifest,4,transport='codex_cli')
        self.fixture_cli(bad,bind=False); self.fixture_cli(good,bind=False)
        Path(bad['attempt']['directory'],'dispatch.json').write_text('{broken',encoding='utf8')
        with patch.object(ce,'process_identity',return_value=None), patch.object(pool,'emit'):
            changed,halted=pool.collect(self.manifest,'controller')
            status=pool.status(self.manifest)
        self.assertTrue(changed and halted)
        m=p.load_manifest(self.manifest)
        self.assertEqual(m['projects'][0]['active_attempt'],bad['attempt']['id'])
        self.assertEqual(m['projects'][1]['status'],'drafted')
        self.assertNotEqual(status['cases'][0]['next_action'],'done')

    def test_idle_iteration_reads_manifest_once_and_reuses_only_current_view(self):
        self.enable_cli()
        for _ in range(4): p.reserve(self.manifest,4,transport='codex_cli')
        class StopTest(Exception): pass
        states=[]
        def collect(manifest,controller,*,state=None):
            states.append(state)
            return False,False
        def status(manifest,*,state=None):
            self.assertIs(state,states[-1])
            return {'cases':[{'case_id':c['id'],'next_action':'wait_cli'} for c in state['projects'] if c['active_attempt']]}
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), \
             patch.object(pool,'collect',side_effect=collect), patch.object(pool,'status',side_effect=status), \
             patch.object(pool,'emit'), patch.object(pool.time,'sleep',side_effect=StopTest), \
             patch.object(p,'load_manifest',wraps=p.load_manifest) as loads:
            with self.assertRaises(StopTest): pool.run(self.manifest,'controller',4)
        self.assertEqual(loads.call_count,2)  # Startup plus one current iteration.

    def test_next_iteration_reloads_frozen_metadata_and_rejects_changes(self):
        self.enable_cli()
        for _ in range(4): p.reserve(self.manifest,4,transport='codex_cli')
        def status(manifest,*,state=None):
            return {'cases':[{'case_id':c['id'],'next_action':'wait_cli'} for c in state['projects'] if c['active_attempt']]}
        def change_snapshot(_):
            b.atomic(self.batch/'policy.snapshot.json', {'changed': True})
        with patch.object(pool,'preflight',return_value=str(self.root/'codex.exe')), \
             patch.object(pool,'collect',return_value=(False,False)), patch.object(pool,'status',side_effect=status), \
             patch.object(pool,'emit'), patch.object(pool.time,'sleep',side_effect=change_snapshot):
            with self.assertRaisesRegex(b.BatchError,'Policy snapshot changed'):
                pool.run(self.manifest,'controller',4)


if __name__ == '__main__': unittest.main()
