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

    def test_seal_uses_lf_records_and_preserves_raw_unicode_event_bytes(self):
        root = self.root / 'unicode-seal'
        root.mkdir()
        ident = str(uuid.uuid4())
        rows = [{'type': 'thread.started', 'thread_id': ident},
                {'type': 'item.completed', 'item': {'type': 'command_execution',
                                                  'aggregated_output': 'alpha\u2029beta'}}]
        raw = ('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n').encode('utf-8')
        (root / 'events.jsonl').write_bytes(raw)
        for name in ('worker-final.json', 'cli-exit.json'):
            (root / name).write_text('{}', encoding='utf-8')
        (root / 'prompt.txt').write_text('input', encoding='utf-8')
        native = self.root / 'native-session.jsonl'
        native.write_bytes(b'{"type":"session_meta"}\n')
        with patch.object(pool, 'locate_session', return_value=native):
            pool.seal_completion(root, {'result': {'pid': 123, 'created': 456}}, 0)
        self.assertEqual((root / 'events.jsonl').read_bytes(), raw)
        self.assertEqual((root / 'session.jsonl').read_bytes(), native.read_bytes())
        self.assertEqual(b.read(root / 'cli-completion.json')['files']['events.jsonl'], b.sha(root / 'events.jsonl'))

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

    def capacity_fixture(self, reserved, *, bind=True):
        _, dispatch, _ = self.fixture_cli(reserved, bind=bind)
        root = Path(reserved['attempt']['directory'])
        rows = [json.loads(line) for line in (root/'events.jsonl').read_text().split('\n')]
        rows = rows[:2] + [{'type': 'error', 'message': ce.MODEL_CAPACITY},
                          {'type': 'turn.failed', 'error': {'message': ce.MODEL_CAPACITY}}]
        (root/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        exited = b.read(root/'cli-exit.json'); exited['returncode'] = 1
        b.atomic(root/'cli-exit.json', exited)
        (root/'worker-final.json').unlink(); (root/'cli-completion.json').unlink()
        return root, dispatch

    def test_capacity_exit_preserves_previous_stage_and_drains_valid_peers(self):
        self.enable_cli()
        for bound in (False, True):
            with self.subTest(bound=bound):
                bad = p.reserve(self.manifest, 4, transport='codex_cli')
                good = p.reserve(self.manifest, 4, transport='codex_cli')
                before = p.case_by_id(b.read(self.manifest), bad['case_id'])
                root, _ = self.capacity_fixture(bad, bind=bound)
                self.fixture_cli(good)
                raw = (root/'events.jsonl').read_bytes()
                with patch.object(ce, 'process_identity', return_value=None), patch.object(pool, 'emit'):
                    result = pool.collect(self.manifest, 'controller', drain=True)
                self.assertEqual(result, (True, True)); self.assertTrue(result.capacity_only)
                current = p.case_by_id(p.load_manifest(self.manifest), bad['case_id'])
                self.assertEqual(current['status'], before['status'])
                self.assertEqual(current.get('artifact_sha256'), before.get('artifact_sha256'))
                self.assertEqual(current['receipts'], before['receipts'])
                self.assertIsNone(current['active_attempt'])
                self.assertEqual(current['attempts'][-1]['state'], 'cancelled')
                self.assertEqual((root/'events.jsonl').read_bytes(), raw)
                self.assertEqual(b.read(root/'cli-rejection.json')['code'], 'model_capacity')
                peer = p.case_by_id(b.read(self.manifest), good['case_id'])
                self.assertIn(peer['status'], {'drafted', 'accepted'})

    def test_capacity_classifier_rejects_unknown_or_contradictory_evidence(self):
        self.enable_cli()
        modes = ('valid', 'nested_only', 'mixed_error', 'completed', 'missing_turn',
                 'final_file', 'bad_exit', 'wrong_model', 'changed_prompt', 'changed_dispatch')
        for mode in modes:
            with self.subTest(mode=mode):
                reserved = p.reserve(self.manifest, 4, transport='codex_cli')
                root, dispatch = self.capacity_fixture(reserved)
                current = p.case_by_id(b.read(self.manifest), reserved['case_id'])['attempts'][-1]
                rows = [json.loads(line) for line in (root/'events.jsonl').read_text().split('\n')]
                if mode == 'nested_only':
                    rows = rows[:2]+[{'type':'item.completed','item':{'type':'agent_message','text':ce.MODEL_CAPACITY}}]
                elif mode == 'mixed_error': rows.insert(2, {'type':'error','message':'unknown failure'})
                elif mode == 'completed': rows.insert(2, {'type':'turn.completed'})
                elif mode == 'missing_turn': rows.pop(1)
                elif mode == 'final_file': b.atomic(root/'worker-final.json', {})
                elif mode == 'bad_exit':
                    exited = b.read(root/'cli-exit.json'); exited['pid'] += 1; b.atomic(root/'cli-exit.json',exited)
                elif mode == 'wrong_model': dispatch['arguments']['model'] = 'other-model'
                elif mode == 'changed_prompt': (root/'prompt.txt').write_text('changed')
                elif mode == 'changed_dispatch': (root/'dispatch.json').write_text('{}')
                (root/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
                model = b.read(self.manifest)['policy']['models'][current['stage']]
                with patch.object(ce, 'process_identity', return_value=None):
                    if mode in {'bad_exit','wrong_model','changed_prompt','changed_dispatch'}:
                        with self.assertRaises(ValueError): ce.capacity_failure(current, dispatch, model)
                    else:
                        result = ce.capacity_failure(current, dispatch, model)
                        self.assertEqual(result is not None, mode == 'valid')
                # No production execution: isolate each synthetic case for the next scenario.
                m = b.read(self.manifest); c = p.case_by_id(m, reserved['case_id'])
                c['active_attempt'] = None; c['status'] = 'failed'; b.atomic(self.manifest,m)

    def test_capacity_with_live_supervisor_or_changed_source_never_releases_slot(self):
        self.enable_cli()
        for mode in ('supervisor', 'source', 'original'):
            with self.subTest(mode=mode):
                reserved = p.reserve(self.manifest, 4, transport='codex_cli')
                root, dispatch = self.capacity_fixture(reserved)
                if mode == 'supervisor': b.atomic(root/'supervisor.json',dict(dispatch['result'],pid=456))
                else:
                    case = p.case_by_id(b.read(self.manifest),reserved['case_id'])
                    source=Path(case['sources'][0]['original_path' if mode=='original' else 'snapshot_path'])
                    source.chmod(0o600)
                    source.write_bytes(b'changed source')
                def identity(pid): return dict(dispatch['result'],pid=456) if mode == 'supervisor' and pid == 456 else None
                with patch.object(ce,'process_identity',side_effect=identity),patch.object(pool,'emit'):
                    result=pool.collect(self.manifest,'controller',case_ids={reserved['case_id']})
                self.assertFalse(result.capacity_only)
                self.assertIsNotNone(p.case_by_id(b.read(self.manifest),reserved['case_id'])['active_attempt'])

    def test_capacity_backoff_is_bounded_serial_and_does_not_retry_unknown_faults(self):
        for mode in ('recover','exhaust','unknown'):
            with self.subTest(mode=mode):
                slots=[]
                def run_once(workers):
                    slots.append(workers)
                    limited = mode == 'exhaust' or mode == 'recover' and len(slots)==1
                    return {'halted':limited or mode=='unknown','capacity_limited':limited}
                with patch.object(pool.time,'sleep') as sleep,patch.object(pool,'event'):
                    result=pool.capacity_recovery(self.manifest,run_once,16)
                self.assertEqual(slots, {'recover':[16,1],'exhaust':[16,1,1],'unknown':[16]}[mode])
                self.assertEqual(sum(call.args[0] for call in sleep.call_args_list), {'recover':60,'exhaust':180,'unknown':0}[mode])
                self.assertEqual(result.get('automatic_retry_exhausted',False),mode=='exhaust')

    def test_capacity_pipeline_retries_only_after_drain_and_delivers_existing_work(self):
        from test_nonblocking_pools import NonblockingBehaviorTests
        fixture=NonblockingBehaviorTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        failed_once=[]; launches=[]
        def prepare(reserved, executable):
            launches.append((reserved['case_id'],reserved['attempt']['stage']))
            if not failed_once:
                failed_once.append(reserved['attempt']['id'])
                self.capacity_fixture.__func__(fixture,reserved,bind=False)
            else:
                fixture.fixture_cli(reserved,bind=False,verdict='pass' if reserved['attempt']['stage']=='review' else 'complete')
            return Path(reserved['attempt']['directory'])/'synthetic-job.json'
        normal_sleep=pool.time.sleep
        def wait(seconds):
            if seconds != 30:
                return normal_sleep(seconds)  # Keep unrelated filesystem retry timing real.
            self.assertFalse(any(c['active_attempt'] for c in b.read(fixture.manifest)['projects']))
            self.assertEqual(seconds,30)
        with patch.object(pool.time,'sleep',side_effect=wait):
            result,count=fixture.run_fake_pipeline(workers=1,prepare=prepare)
        self.assertFalse(result['halted'],result)
        self.assertEqual(count,17)  # Eight draft/review pairs plus one refused attempt.
        self.assertTrue(all(c['status']=='delivered' for c in b.read(fixture.manifest)['projects']))

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
        # One contract covers legacy mutation and independent case-lock merging.
        from test_nonblocking_pools import NonblockingBehaviorTests
        self.enable_cli()
        for nonblocking in (False, True):
            with self.subTest(nonblocking=nonblocking):
                fixture = self
                if nonblocking:
                    fixture = NonblockingBehaviorTests(); fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                r = p.reserve(fixture.manifest, 4, transport='codex_cli')
                fixture.fixture_cli(r, bind=False)
                phase = ['collect']; record_loads = []
                real_load, real_bind, real_record = p.load_manifest, p.bind, p.record
                def load(*args, **kwargs):
                    self.assertNotEqual(phase[0], 'bound', 'Do not reload after bind returns its committed attempt')
                    if phase[0] == 'record': record_loads.append(args[0])
                    return real_load(*args, **kwargs)
                def bind(*args, **kwargs):
                    result = real_bind(*args, **kwargs)
                    committed = p.attempt_by_id(p.case_by_id(b.read(fixture.manifest), r['case_id']), r['attempt']['id'])
                    self.assertEqual(result, committed)
                    self.assertEqual(result['state'], 'bound')
                    self.assertEqual(result['stage'], 'draft')
                    self.assertEqual(result['agent_id'], 'cli:' + r['attempt']['id'])
                    self.assertEqual('prepared_handoff' in result, nonblocking)
                    phase[0] = 'bound'
                    return result
                def record(*args, **kwargs):
                    self.assertEqual(phase[0], 'bound')
                    phase[0] = 'record'
                    return real_record(*args, **kwargs)
                with patch.object(ce, 'process_identity', return_value=None), \
                     patch.object(pool, 'start_supervisor', side_effect=AssertionError('No replay')), \
                     patch.object(p, 'load_manifest', side_effect=load), \
                     patch.object(p, 'bind', side_effect=bind), patch.object(p, 'record', side_effect=record) as recorded, \
                     patch.object(ce, 'verify_completion', wraps=ce.verify_completion) as verified, patch.object(pool, 'emit'):
                    changed, halted = pool.collect(fixture.manifest, 'controller')
                self.assertTrue(changed); self.assertFalse(halted)
                self.assertEqual(recorded.call_count, 1)
                self.assertTrue(record_loads, 'record must independently reload and validate the current manifest')
                self.assertEqual(verified.call_count, 2)  # Separate bind and record evidence gates.
                self.assertEqual(p.case_by_id(p.load_manifest(fixture.manifest), r['case_id'])['status'], 'drafted')

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

    def test_status_filters_before_process_observation_and_defaults_to_all_cases(self):
        self.enable_cli()
        reserved = [p.reserve(self.manifest, 4, transport='codex_cli') for _ in range(2)]
        for pid, row in enumerate(reserved, 101):
            b.atomic(Path(row['attempt']['directory'])/'supervisor.json',
                     {'pid': pid, 'created': pid})
        state = p.load_manifest(self.manifest)
        selected = reserved[1]['case_id']
        with patch.object(ce, 'process_identity', side_effect=lambda pid: {'created': pid}) as observe:
            filtered = pool.status(self.manifest, state=state, case_ids={selected})
            self.assertEqual([row['case_id'] for row in filtered['cases']], [selected])
            self.assertEqual(filtered['cases'][0]['next_action'], 'wait_cli')
            self.assertEqual([call.args[0] for call in observe.call_args_list], [102])
            observe.reset_mock()
            self.assertEqual(pool.status(self.manifest, state=state, case_ids=set())['cases'], [])
            observe.assert_not_called()
            complete = pool.status(self.manifest, state=state)
            self.assertEqual(len(complete['cases']), len(state['projects']))
            self.assertEqual([call.args[0] for call in observe.call_args_list], [101, 102])
        b.atomic(self.batch/'policy.snapshot.json', {'changed': True})
        with self.assertRaisesRegex(b.BatchError, 'Policy snapshot changed'):
            pool.status(self.manifest, case_ids=set())

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
