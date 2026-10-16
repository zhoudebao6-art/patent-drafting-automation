"""Deterministic local-I/O concurrency tests; no model calls or capacity claims."""
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import batch_tool as b
import cli_evidence as evidence
import cli_pool as cli
import flat_delivery as flat
import parallel_batch as queue
import prepare_handoff as handoff
import stage_bundle
import test_cli_pool as cli_fixtures
import test_parallel_batch as fixtures
import test_three_stage_pools as three


ANALYSIS = Path(os.environ.get('PATENT_POOL_TEST_ARTIFACT_DIR',
                              str(Path(tempfile.gettempdir())/'patent-pool-tests')))
MODE = 'nonblocking_handoff_v36'
TIMEOUT = 15


def policy():
    result = three.three_pool_policy()
    result.update(pool_execution=MODE, worker_preparation=stage_bundle.MODE)
    return result


class SharedManifestReaderTests(unittest.TestCase):
    def test_manifest_validation_keeps_lexical_name_when_resolve_observes_old_backup(self):
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='stable-manifest-test-', dir=ANALYSIS) as directory:
            root = Path(directory)
            target, obsolete = root/'manifest.json', root/'.batch-previous-obsolete.tmp'
            b.atomic(target, {'version': 2})
            real_resolve = Path.resolve

            def resolving(path, *args, **kwargs):
                if path == target:
                    return obsolete
                return real_resolve(path, *args, **kwargs)

            with patch.object(Path, 'resolve', new=resolving):
                self.assertEqual(queue.file_path(target, root), target)
                self.assertEqual(b.read(queue.file_path(target, root)), {'version': 2})

    def test_open_reader_keeps_old_complete_json_without_blocking_atomic_replace(self):
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='shared-reader-test-', dir=ANALYSIS) as directory:
            target = Path(directory)/'manifest.json'
            previous = {'version': 1, '内容': ['旧清单']*1024}
            current = {'version': 2, '内容': ['新清单']*1024}
            target.write_text(json.dumps(previous, ensure_ascii=False), encoding='utf-8-sig')
            with b.shared_reader(target) as old_stream:
                b.atomic(target, current)
                self.assertEqual(json.load(old_stream), previous)
                self.assertEqual(b.read(target), current)
            self.assertEqual(b.read(target), current)

    @unittest.skipUnless(b.os.name == 'nt', 'Windows ReplaceFile failure contract')
    def test_partial_windows_replacement_failure_preserves_both_versions(self):
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='replace-failure-test-', dir=ANALYSIS) as directory:
            target = Path(directory)/'manifest.json'
            previous, current = {'version': 1}, {'version': 2}
            b.atomic(target, previous)
            retained = {}

            def partial_replace(old, replacement, backup, *unused):
                retained.update(replacement=Path(replacement), backup=Path(backup))
                Path(old).rename(backup)
                b.ctypes.set_last_error(1176)
                return 0

            replace = Mock(side_effect=partial_replace)
            kernel = Mock(ReplaceFileW=replace)
            with patch.object(b.ctypes, 'WinDLL', return_value=kernel):
                with self.assertRaises(OSError) as rejected:
                    b.atomic(target, current)
            self.assertEqual(rejected.exception.winerror, 1176)
            replace.assert_called_once()
            self.assertEqual(b.read(retained['replacement']), current)
            self.assertEqual(b.read(retained['backup']), previous)


class NonblockingPolicyTests(unittest.TestCase):
    def test_only_explicit_new_three_pool_policy_enables_independent_io(self):
        current = policy()
        queue.validate_policy(current)
        self.assertTrue(queue.nonblocking_pools(current))
        old = three.three_pool_policy()
        self.assertFalse(queue.nonblocking_pools(old))
        for change in ({'pool_execution': 'unknown'},
                       {'worker_preparation': None},
                       {'stage_scheduling': queue.IMMEDIATE_STAGES}):
            with self.subTest(change=change), self.assertRaises(b.BatchError):
                queue.validate_policy(dict(current, **change))

    def test_accepted_case_does_not_block_other_ready_pools_in_new_mode(self):
        manifest = three.synthetic_manifest(['accepted', 'pending', 'drafted', 'fix_required'])
        manifest['policy'] = policy()
        with patch.object(queue, 'verify_case'):
            planned = queue.plan_manifest(manifest, 12)
        self.assertEqual(planned['action'], 'reserve')
        self.assertEqual({row['stage'] for row in planned['ready']}, {'draft', 'review', 'revise'})
        manifest['policy'].pop('pool_execution')
        with patch.object(queue, 'verify_case'):
            old = queue.plan_manifest(manifest, 12)
        self.assertEqual(old['action'], 'deliver')

    def test_reservations_in_all_states_obey_actual_capacity_and_pool_limits(self):
        for actual in (1, 3, 12):
            with self.subTest(simulated_actual_capacity=actual):
                manifest = three.synthetic_manifest(['pending'] * 12 + ['drafted'] * 8 + ['fix_required'] * 8)
                manifest['policy'] = policy()
                with patch.object(queue, 'verify_case'):
                    for index in range(actual):
                        row = queue.plan_manifest(manifest, actual, first_only=True)['ready'][0]
                        case = queue.case_by_id(manifest, row['case_id'])
                        case['active_attempt'] = str(index)
                        case['attempts'].append({'id': str(index), 'stage': row['stage'],
                                                 'state': 'reserved' if index % 2 else 'bound',
                                                 'preparation_state': 'pending'})
                        counts = {stage: sum(queue.attempt_by_id(c, c['active_attempt'])['stage'] == stage
                                  for c in manifest['projects'] if c['active_attempt'])
                                  for stage in three.LIMITS}
                        self.assertTrue(all(counts[stage] <= limit for stage, limit in three.LIMITS.items()))
                    result = queue.plan_manifest(manifest, actual)
                self.assertEqual(result['action'], 'wait')
                self.assertEqual(result['active_workers'], actual)
                if actual == 12:
                    self.assertEqual(counts, three.LIMITS)


class NonblockingBehaviorTests(unittest.TestCase):
    reserve = fixtures.ParallelTests.reserve
    record = fixtures.ParallelTests.record
    fixture_cli = cli_fixtures.CliPoolTests.fixture_cli

    def setUp(self):
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='nonblocking-test-', dir=ANALYSIS)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root/'input'
        self.src.mkdir()
        for index in range(1, 9):
            fixtures.docx(self.src/f'{index}.docx', f'source {index}')
        policy_path, rules_path = self.root/'policy.json', self.root/'rules.json'
        b.atomic(policy_path, policy())
        b.atomic(rules_path, {'version': 36, 'rules': []})
        for name, value in (('POLICY', policy_path), ('RULES', rules_path)):
            patcher = patch.object(queue, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        selection = {'projects': [{'title': f'并行测试项目{index:03}', 'source_files': [f'{index}.docx'],
                                  'primary_source': f'{index}.docx'} for index in range(1, 9)]}
        self.manifest = queue.init(self.src, self.root/'work', selection, self.root/'desktop')
        self.batch = self.manifest.parent
        (self.batch/'cli-pool').mkdir(exist_ok=True)

    def receipt(self, reserved, verdict='complete'):
        result = fixtures.ParallelTests.receipt(self, reserved, verdict)
        result.update({key: reserved['spawn_arguments'][key] for key in ('model', 'reasoning_effort')})
        return result

    def state(self):
        return queue.load_manifest(self.manifest)

    def case(self, ident):
        return queue.case_by_id(self.state(), ident)

    def accepted_first_case(self):
        self.record(self.reserve(1))
        self.record(self.reserve(1))
        self.assertEqual(self.case('001')['status'], 'accepted')

    def run_fake_pipeline(self, *, workers=4, prepare=None, event=None):
        """Real reserve/prepare/bind/record/publish; synthetic stopped CLI evidence."""
        def fake_job(reserved, executable):
            verdict = 'pass' if reserved['attempt']['stage'] == 'review' else 'complete'
            self.fixture_cli(reserved, bind=False, verdict=verdict)
            return Path(reserved['attempt']['directory'])/'synthetic-job.json'
        with patch.object(cli, 'preflight', return_value=str(self.root/'synthetic-cli.exe')), \
             patch.object(cli, 'prepare_job', side_effect=prepare or fake_job), \
             patch.object(cli, 'start_supervisor') as launch, \
             patch.object(cli, 'event', side_effect=event), \
             patch.object(evidence, 'process_identity', return_value=None):
            result = cli.run(self.manifest, 'controller', workers)
        if result.get('halted'):
            retained = ANALYSIS/'failure-evidence'
            retained.mkdir(exist_ok=True)
            b.atomic(retained/(self.state()['batch_id']+'.json'), {'result': result, 'manifest': self.state()})
        return result, launch.call_count

    def test_slow_draft_preparation_allows_review_revision_and_publication(self):
        self.accepted_first_case()
        second, third = self.reserve(3), self.reserve(3)
        self.record(second)
        self.record(self.reserve(3), 'fix_required')
        self.record(third)
        self.assertEqual(self.case('002')['status'], 'fix_required')
        self.assertEqual(self.case('003')['status'], 'drafted')
        started, release = threading.Event(), threading.Event()
        seen = {name: threading.Event() for name in ('review', 'revise', 'publish')}
        real_prepare = handoff.prepare

        def slow_prepare(m, case, attempt):
            if case['id'] == '004' and attempt['stage'] == 'draft':
                started.set()
                if not release.wait(TIMEOUT):
                    raise AssertionError('test did not release slow draft preparation')
            return real_prepare(m, case, attempt)

        def event(manifest, kind, **values):
            if kind == 'launched' and values.get('stage') in seen:
                seen[values['stage']].set()
            if kind == 'publication_complete' and '001' in values.get('delivered_case_ids', []):
                seen['publish'].set()

        with patch.object(handoff, 'prepare', side_effect=slow_prepare), \
             ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.run_fake_pipeline, workers=4, event=event)
            try:
                self.assertTrue(started.wait(TIMEOUT), 'slow draft was never reserved')
                for name, ready in seen.items():
                    self.assertTrue(ready.wait(TIMEOUT), f'{name} waited for unrelated draft preparation')
                self.assertFalse(release.is_set())
                self.assertEqual(self.case('001')['status'], 'delivered')
            finally:
                release.set()
            result, launches = future.result(timeout=TIMEOUT*2)
        self.assertFalse(result['halted'], result)
        self.assertGreater(launches, 0)
        self.assertTrue(all(c['status'] == 'delivered' for c in self.state()['projects']))
        chain = [a['stage'] for a in self.case('002')['attempts']]
        self.assertEqual(chain, ['draft', 'review', 'revise', 'review'])
        sessions = [a.get('cli_session_id') or a['task_name'] for a in self.case('002')['attempts']]
        self.assertEqual(len(sessions), len(set(sessions)))

    def test_preparation_holds_no_manifest_lock_and_cannot_commit_changed_source(self):
        reserved = queue.reserve(self.manifest, 3, defer_preparation=True, transport='codex_cli')
        entered, release = threading.Event(), threading.Event()
        real_prepare = handoff.prepare

        def slow(*args):
            entered.set()
            if not release.wait(TIMEOUT):
                raise AssertionError('preparation gate timeout')
            return real_prepare(*args)

        with patch.object(handoff, 'prepare', side_effect=slow), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(queue.prepare_reserved, self.manifest, reserved['case_id'], reserved['attempt']['id'])
            try:
                self.assertTrue(entered.wait(TIMEOUT))
                with queue.locked(self.manifest, timeout=0):
                    self.assertEqual(self.case('001')['active_attempt'], reserved['attempt']['id'])
                other = queue.reserve(self.manifest, 3, defer_preparation=True, transport='codex_cli')
                self.assertEqual(other['case_id'], '002')
            finally:
                release.set()
            prepared = future.result(timeout=TIMEOUT)
        fixtures.docx(self.src/'1.docx', 'changed original after preparation')
        with self.assertRaises(b.BatchError):
            queue.finish_preparation(self.manifest, prepared)
        self.assertFalse((Path(reserved['attempt']['directory'])/'cli-launch-intent.json').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows manifest replacement observation')
    def test_manifest_name_gap_revalidates_with_bounded_exact_path_retries(self):
        real_path = queue.file_path
        calls = []

        def transient(path, root):
            if Path(path) == self.manifest:
                calls.append(path)
                if len(calls) == 1:
                    error = b.ctypes.WinError(2)
                    error.filename = str(self.manifest)
                    raise error
            return real_path(path, root)

        with patch.object(queue, 'file_path', side_effect=transient):
            self.assertEqual(queue.load_manifest(self.manifest)['batch_id'], self.state()['batch_id'])
        self.assertGreater(len(calls), 1)

        for failed_path, code, expected_attempts in ((self.manifest, 2, 8),
                                                    (self.manifest, 5, 1),
                                                    (self.batch/'rules.snapshot.json', 2, 1)):
            with self.subTest(path=failed_path.name, winerror=code):
                attempts = []

                def missing(path, root):
                    if Path(path) == failed_path:
                        attempts.append(path)
                        error = b.ctypes.WinError(code)
                        error.filename = str(failed_path)
                        raise error
                    return real_path(path, root)

                with patch.object(queue, 'file_path', side_effect=missing):
                    with self.assertRaises(OSError) as rejected:
                        queue.load_manifest(self.manifest)
                self.assertEqual(rejected.exception.winerror, code)
                self.assertEqual(len(attempts), expected_attempts)

    def test_slow_publication_preserves_peer_reservation_and_collection(self):
        self.accepted_first_case()
        peer = queue.reserve(self.manifest, 4, transport='codex_cli')
        self.fixture_cli(peer, bind=False)
        entered, release = threading.Event(), threading.Event()
        real_copy = flat.shutil.copyfileobj

        def slow_copy(*args, **kwargs):
            entered.set()
            if not release.wait(TIMEOUT):
                raise AssertionError('publication copy gate timeout')
            return real_copy(*args, **kwargs)

        with patch.object(flat.shutil, 'copyfileobj', side_effect=slow_copy), \
             patch.object(evidence, 'process_identity', return_value=None), patch.object(cli, 'emit'), \
             ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(queue.deliver, self.manifest)
            try:
                self.assertTrue(entered.wait(TIMEOUT))
                with queue.locked(self.manifest, timeout=0):
                    pass
                new = queue.reserve(self.manifest, 4, defer_preparation=True, transport='codex_cli')
                self.assertEqual(new['case_id'], '003')
                self.assertEqual(cli.collect(self.manifest, 'controller', suppress_delivery=True,
                                             case_ids={peer['case_id']}), (True, False))
                self.assertEqual(self.case('002')['status'], 'drafted')
            finally:
                release.set()
            destination = Path(future.result(timeout=TIMEOUT))
        self.assertTrue((destination/'并行测试项目001.docx').is_file())
        self.assertEqual(self.case('002')['status'], 'drafted')
        self.assertEqual(self.case('003')['active_attempt'], new['attempt']['id'])
        metadata = b.read(flat.work_directory(self.state())/'批次清单.json')
        self.assertEqual(queue.case_by_id(metadata, '002')['status'], 'drafted')
        self.assertEqual(queue.case_by_id(metadata, '003')['active_attempt'], new['attempt']['id'])

    def test_concurrent_case_records_merge_without_losing_peer_progress(self):
        rows = [self.reserve(3) for _ in range(2)]
        receipts = [self.receipt(row) for row in rows]
        barrier = threading.Barrier(2, timeout=TIMEOUT)
        real_verify = queue.verify_case

        def synchronized(m, case, *args, **kwargs):
            result = real_verify(m, case, *args, **kwargs)
            barrier.wait()
            return result

        before = self.state()['sequence']
        with patch.object(queue, 'verify_case', side_effect=synchronized), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(queue.record, self.manifest, row['case_id'], row['attempt']['id'], receipt)
                       for row, receipt in zip(rows, receipts)]
            results = [future.result(timeout=TIMEOUT*2) for future in futures]
        self.assertEqual([row['status'] for row in results], ['drafted', 'drafted'])
        current = self.state()
        self.assertEqual(current['sequence'], before+2)
        for case in current['projects'][:2]:
            self.assertEqual(case['status'], 'drafted')
            self.assertEqual(len(case['receipts']), 1)
            self.assertIsNone(case['active_attempt'])

    def test_racing_same_cli_session_is_rejected_at_merge(self):
        rows = [queue.reserve(self.manifest, 3, transport='codex_cli') for _ in range(2)]
        receipts = [self.fixture_cli(row)[0] for row in rows]
        barrier = threading.Barrier(2, timeout=TIMEOUT)
        real_completion = evidence.verify_completion

        def same_session(*args, **kwargs):
            result = real_completion(*args, **kwargs)
            barrier.wait()
            return dict(result, session_id='synthetic-identical-session')

        with patch.object(evidence, 'process_identity', return_value=None), \
             patch.object(evidence, 'verify_completion', side_effect=same_session), \
             ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(queue.record, self.manifest, row['case_id'], row['attempt']['id'], receipt)
                       for row, receipt in zip(rows, receipts)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=TIMEOUT*2)['status'])
                except b.BatchError:
                    outcomes.append('rejected')
        self.assertCountEqual(outcomes, ['drafted', 'rejected'])
        self.assertEqual(sum(c['status'] == 'drafted' for c in self.state()['projects']), 1)

    def test_preparation_failure_isolated_and_other_case_completes(self):
        real_prepare = handoff.prepare

        def damaged(m, case, attempt):
            if case['id'] == '001':
                raise ValueError('synthetic malformed DOCX')
            return real_prepare(m, case, attempt)

        with patch.object(handoff, 'prepare', side_effect=damaged):
            result, launches = self.run_fake_pipeline(workers=3)
        self.assertFalse(result['halted'], result)
        self.assertEqual(self.case('001')['status'], 'failed')
        self.assertIsNone(self.case('001')['active_attempt'])
        self.assertEqual(self.case('002')['status'], 'delivered')
        self.assertGreater(launches, 0)
        self.assertTrue(all(not c.get('active_attempt') for c in self.state()['projects']))

    def test_publication_io_failure_allows_stages_and_remains_reported(self):
        self.accepted_first_case()
        with patch.object(queue, 'deliver', side_effect=OSError('synthetic destination unavailable')):
            result, launches = self.run_fake_pipeline(workers=3)
        self.assertFalse(result['halted'], result)
        self.assertGreater(launches, 0)
        self.assertTrue(all(c['status'] == 'accepted' for c in self.state()['projects']))
        self.assertTrue(any('Publication I/O unavailable' in row.get('reason', '')
                            for row in result['blocking_reasons']), result)

    def test_restart_keeps_unlaunched_reservation_without_duplicate_paid_launch(self):
        reserved = queue.reserve(self.manifest, 1, defer_preparation=True, transport='codex_cli')
        with self.assertRaises(b.BatchError):
            cli.prepare_job(reserved, 'synthetic-cli')
        self.assertFalse((Path(reserved['attempt']['directory'])/'cli-launch-intent.json').exists())
        result = queue.prepare_reserved(self.manifest, reserved['case_id'], reserved['attempt']['id'])
        queue.finish_preparation(self.manifest, result)
        with patch.object(cli, 'preflight', return_value='synthetic-cli'), \
             patch.object(cli, 'prepare_job', side_effect=AssertionError('duplicate paid launch')), \
             patch.object(cli, 'start_supervisor', side_effect=AssertionError('duplicate paid launch')), \
             patch.object(cli, 'event'):
            recovered = cli.run(self.manifest, 'controller', 1)
        self.assertTrue(recovered['halted'])
        self.assertEqual(self.case('001')['active_attempt'], reserved['attempt']['id'])
        self.assertEqual(len(self.case('001')['attempts']), 1)

    def test_publication_restart_recovers_existing_file_identity(self):
        self.accepted_first_case()
        real_snapshot = queue.deliver_snapshot

        def publish_then_crash(*args, **kwargs):
            real_snapshot(*args, **kwargs)
            raise RuntimeError('synthetic crash after atomic publication before merge')

        with patch.object(queue, 'deliver_snapshot', side_effect=publish_then_crash):
            with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
                queue.deliver(self.manifest)
        before = self.state()
        self.assertEqual(self.case('001')['status'], 'accepted')
        destination = flat.destination(before)
        artifact = destination/'并行测试项目001.docx'
        identity, digest = flat.file_identity(artifact), b.sha(artifact)
        with patch.object(flat.shutil, 'copyfileobj', side_effect=AssertionError('recovery recopied published file')):
            recovered = Path(queue.deliver(self.manifest))
        self.assertEqual(recovered, destination)
        self.assertEqual(flat.file_identity(artifact), identity)
        self.assertEqual(b.sha(artifact), digest)
        self.assertEqual(self.case('001')['status'], 'delivered')
        self.assertEqual(self.state()['delivery_transaction_id'], before['delivery_transaction_id'])

    def test_publication_closure_between_observation_and_reserve_is_not_a_failure(self):
        self.accepted_first_case()
        for ident in range(2, 9):
            queue.defer(self.manifest, f'{ident:03}', 'failed', 'not selected for this terminal fixture')
        queue.deliver(self.manifest)
        self.assertFalse(self.state()['delivery_closed'])
        closed = threading.Event()
        real_deliver, real_reserve = queue.deliver, queue.reserve

        def close(*args, **kwargs):
            destination = real_deliver(*args, **kwargs)
            closed.set()
            return destination

        def reserve_after_close(*args, **kwargs):
            self.assertTrue(closed.wait(TIMEOUT), 'terminal publication did not finish')
            return real_reserve(*args, **kwargs)

        with patch.object(queue, 'deliver', side_effect=close), \
             patch.object(queue, 'reserve', side_effect=reserve_after_close), \
             patch.object(cli, 'preflight', return_value='synthetic-cli'), \
             patch.object(cli, 'start_supervisor', side_effect=AssertionError('terminal batch launched model')), \
             patch.object(cli, 'event'):
            result = cli.run(self.manifest, 'controller', 1)
        self.assertTrue(closed.is_set())
        self.assertFalse(result['halted'], result)
        self.assertEqual(result['blocking_reasons'], [])
        self.assertTrue(self.state()['delivery_closed'])


if __name__ == '__main__':
    unittest.main()
