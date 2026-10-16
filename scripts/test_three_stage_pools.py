"""Synthetic three-pool scheduling and evidence; never paid capacity probes."""
import copy
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import batch_tool as b
import cli_pool as pool
import parallel_batch as queue
import stage_bundle
import worker_receipt
import test_parallel_batch as fixtures


MARKER = 'separate_stage_pools_v35'
LIMITS = {'draft': 6, 'review': 3, 'revise': 3}


def three_pool_policy():
    policy = copy.deepcopy(fixtures.POLICY)
    policy.update(stage_scheduling=MARKER, stage_pools=copy.deepcopy(LIMITS),
                  review_mode='review_only', model_policy='sol_review_xhigh_v20',
                  dispatch_transports=['collaboration', 'codex_cli'])
    policy['concurrency'] = {'target_workers': 12, 'max_in_flight_cases': 24,
                             'min_draft_slots': 1}
    policy['models'] = {stage: {'model': 'gpt-6.1-sol',
                       'reasoning_effort': 'high' if stage == 'draft' else 'xhigh'}
                       for stage in LIMITS}
    policy['delivery'] = {'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files',
                          'timing': 'after_each_acceptance'}
    return policy


def synthetic_manifest(statuses=(), active=(), sequence=100):
    """Only plan mechanics are tested here; verify_case is explicitly mocked."""
    rows = []
    for status in statuses:
        rows.append({'id': f'{len(rows)+1:03}', 'status': status,
                     'active_attempt': None, 'attempts': [], 'ready_sequence': 90})
    for index, stage in enumerate(active):
        ident = f'active-{index}'
        # A reserved worker, a bound worker and either transport consume the
        # same case/stage slot, irrespective of whether execution has started.
        attempt = {'id': ident, 'stage': stage,
                   'state': 'reserved' if index % 2 else 'bound'}
        if index % 3:
            attempt['transport'] = 'codex_cli'
        rows.append({'id': f'{len(rows)+1:03}',
                     'status': {'draft': 'pending', 'review': 'drafted',
                                'revise': 'fix_required'}[stage],
                     'active_attempt': ident, 'attempts': [attempt],
                     'ready_sequence': 90})
    return {'policy': three_pool_policy(), 'projects': rows,
            'sequence': sequence, 'last_reserved_stage': None}


class ThreePoolPlanTests(unittest.TestCase):
    def plan(self, manifest, capacity=12, **kwargs):
        with patch.object(queue, 'verify_case'):
            return queue.plan_manifest(manifest, capacity, **kwargs)

    def test_marker_and_immediate_delivery_helper_include_both_generations(self):
        self.assertEqual(queue.THREE_POOLS, MARKER)
        for marker in (MARKER, queue.IMMEDIATE_STAGES):
            self.assertTrue(queue.immediate_stages({'stage_scheduling': marker}))
        self.assertFalse(queue.immediate_stages({}))

    def test_each_pool_has_a_hard_limit_and_does_not_borrow(self):
        for stage, limit in LIMITS.items():
            status = {'draft': 'pending', 'review': 'drafted', 'revise': 'fix_required'}[stage]
            with self.subTest(stage=stage):
                manifest = synthetic_manifest([status] * 8, [stage] * limit)
                result = self.plan(manifest)
                self.assertEqual(result['action'], 'wait')
                self.assertEqual(result['ready'], [])
                self.assertEqual(result['pool_status'][stage], {'active': limit, 'limit': limit})

    def test_total_capacity_is_respected_for_one_through_twelve_slots(self):
        for capacity in (1, 2, 3, 6, 11, 12):
            with self.subTest(capacity=capacity):
                manifest = synthetic_manifest(['pending'] * 20 + ['drafted'] * 6 +
                                              ['fix_required'] * 6)
                selected = []
                for index in range(capacity):
                    result = self.plan(manifest, capacity, first_only=True)
                    self.assertEqual(result['action'], 'reserve')
                    row = result['ready'][0]
                    selected.append(row['stage'])
                    case = queue.case_by_id(manifest, row['case_id'])
                    attempt = {'id': f'pick-{index}', 'stage': row['stage'],
                               'state': 'reserved' if index % 2 else 'bound'}
                    case['active_attempt'] = attempt['id']
                    case['attempts'].append(attempt)
                    self.assertTrue(all(Counter(selected)[stage] <= LIMITS[stage]
                                        for stage in LIMITS))
                result = self.plan(manifest, capacity)
                self.assertEqual(result['action'], 'wait')
                self.assertEqual(result['active_workers'], capacity)
                self.assertEqual(sum(row['active'] for row in result['pool_status'].values()), capacity)
                if capacity == 12:
                    self.assertEqual(dict(Counter(selected)), LIMITS)

    def test_ratio_prefers_the_least_occupied_eligible_pool(self):
        manifest = synthetic_manifest(['pending', 'drafted', 'fix_required'],
                                      ['draft', 'review'])
        self.assertEqual(self.plan(manifest, first_only=True)['ready'][0]['stage'], 'revise')
        manifest = synthetic_manifest(['pending', 'drafted', 'fix_required'],
                                      ['draft', 'review', 'revise'])
        self.assertEqual(self.plan(manifest, first_only=True)['ready'][0]['stage'], 'draft')

    def test_ties_followup_first_and_single_slot_finishes_repair_chain(self):
        manifest = synthetic_manifest(['pending', 'drafted', 'fix_required'])
        self.assertEqual(self.plan(manifest, 1, first_only=True)['ready'][0]['stage'], 'revise')
        manifest = synthetic_manifest(['pending', 'drafted'])
        self.assertEqual(self.plan(manifest, 1, first_only=True)['ready'][0]['stage'], 'review')

    def test_review_pool_retains_urgent_recheck_and_overdue_first_review_priority(self):
        manifest = synthetic_manifest(['drafted', 'revised'])
        self.assertEqual(self.plan(manifest, first_only=True)['ready'][0]['case_id'], '002')
        manifest['projects'][0]['ready_sequence'] = 1
        self.assertEqual(self.plan(manifest, first_only=True)['ready'][0]['case_id'], '001')

    def test_max_in_flight_only_blocks_new_drafts(self):
        manifest = synthetic_manifest(['drafted'] * 12 + ['fix_required'] * 6 +
                                      ['revised'] * 6 + ['pending'] * 5)
        result = self.plan(manifest)
        self.assertEqual(result['in_flight_cases'], 24)
        self.assertEqual({row['stage'] for row in result['ready']}, {'review', 'revise'})
        self.assertTrue(all(row['case_id'] not in {'025', '026', '027', '028', '029'}
                            for row in result['ready']))

    def test_bad_source_in_one_pool_does_not_block_other_pools(self):
        manifest = synthetic_manifest(['drafted', 'fix_required', 'pending'])
        manifest['projects'][0]['ready_sequence'] = 1
        def verify(state, case, original=False):
            if case['id'] == '001':
                raise b.BatchError('synthetic changed source')
        with patch.object(queue, 'verify_case', side_effect=verify):
            result = queue.plan_manifest(manifest, 12, first_only=True)
        self.assertEqual(result['ready'][0]['stage'], 'revise')
        self.assertEqual(result['blocked'][0]['case_id'], '001')

    def test_accepted_case_is_delivered_even_with_total_capacity_occupied(self):
        manifest = synthetic_manifest(['accepted'], ['draft'] * 6 + ['review'] * 3 + ['revise'] * 3)
        result = self.plan(manifest)
        self.assertEqual(result['action'], 'deliver')
        self.assertEqual(result['accepted_case_ids'], ['001'])
        self.assertEqual(result['active_workers'], 12)
        self.assertEqual({stage: row['active'] for stage, row in result['pool_status'].items()}, LIMITS)

    def test_legacy_double_lane_still_borrows_and_has_no_new_pool_limits(self):
        manifest = synthetic_manifest(['pending'] * 10)
        manifest['policy'] = copy.deepcopy(fixtures.POLICY)
        manifest['policy']['stage_scheduling'] = queue.IMMEDIATE_STAGES
        for index in range(4):
            result = self.plan(manifest, 4, first_only=True)
            row = result['ready'][0]
            self.assertEqual(row['stage'], 'draft')
            case = queue.case_by_id(manifest, row['case_id'])
            case['active_attempt'] = str(index)
            case['attempts'].append({'id': str(index), 'stage': 'draft', 'state': 'reserved'})
        self.assertEqual(self.plan(manifest, 4)['action'], 'wait')

    def test_invalid_actual_capacity_never_uses_config_as_evidence(self):
        for capacity in (None, 0, -1, 13, True, 12.0):
            with self.subTest(capacity=capacity), self.assertRaises(b.BatchError):
                self.plan(synthetic_manifest(['pending']), capacity)

    def test_corrupt_active_pool_overflow_cannot_authorize_more_work(self):
        for stage, limit in LIMITS.items():
            with self.subTest(stage=stage), self.assertRaises(b.BatchError):
                self.plan(synthetic_manifest(['pending'], [stage] * (limit + 1)))


class ThreePoolPolicyTests(unittest.TestCase):
    def test_valid_policy_preserves_model_choices_and_exact_pool_limits(self):
        policy = three_pool_policy()
        queue.validate_policy(policy)
        self.assertEqual(policy['models']['draft'], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'})
        for stage in ('review', 'revise'):
            self.assertEqual(policy['models'][stage], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'})

    def test_invalid_stage_pool_shapes_and_limits_are_rejected(self):
        for pools in (None, [], {}, {'draft': 6, 'review': 3},
                      dict(LIMITS, recheck=1), dict(LIMITS, review=0),
                      dict(LIMITS, revise=-1), dict(LIMITS, review=True),
                      dict(LIMITS, draft=6.0), dict(LIMITS, draft='6')):
            policy = three_pool_policy()
            policy['stage_pools'] = pools
            with self.subTest(pools=pools), self.assertRaises(b.BatchError):
                queue.validate_policy(policy)

    def test_pool_sum_review_mode_and_delivery_must_match_marker(self):
        changes = ({'review_mode': 'review_fix_then_recheck'},
                   {'stage_scheduling': 'unknown'},
                   {'concurrency': {'target_workers': 11, 'max_in_flight_cases': 24, 'min_draft_slots': 1}},
                   {'concurrency': {'target_workers': 12, 'max_in_flight_cases': 8, 'min_draft_slots': 1}},
                   {'delivery': copy.deepcopy(fixtures.POLICY['delivery'])})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(b.BatchError):
                queue.validate_policy(dict(three_pool_policy(), **change))

    def test_stage_packet_matches_the_frozen_review_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = root/'references'
            references.mkdir()
            actual = Path(__file__).resolve().parent.parent/'references'
            for name in (*stage_bundle.CORE, 'data-contracts.md'):
                (references/name).write_bytes((actual/name).read_bytes())
            policy = three_pool_policy()
            b.atomic(references/'batch-policy.json', policy)
            packet, coverage = stage_bundle.build_packet(root, {'version': 35, 'rules': []}, 'review')
            instruction = packet.split('\n\n')[1]
            self.assertIn('fix_required', instruction)
            self.assertIn('revise', instruction)
            self.assertNotIn('可集中修正并返回revised', instruction)
            self.assertFalse(coverage['semantic_approval'])
            policy['review_mode'] = 'review_fix_then_recheck'
            policy['stage_scheduling'] = queue.IMMEDIATE_STAGES
            policy.pop('stage_pools')
            b.atomic(references/'batch-policy.json', policy)
            legacy, _ = stage_bundle.build_packet(root, {'version': 35, 'rules': []}, 'review')
            self.assertIn('可集中修正并返回revised', legacy.split('\n\n')[1])


class ThreePoolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root/'input'
        self.src.mkdir()
        for index in range(1, 31):
            fixtures.docx(self.src/f'{index}.docx', f'case source {index}')
        policy_path = self.root/'policy.json'
        b.atomic(policy_path, three_pool_policy())
        rules_path = self.root/'rules.json'
        b.atomic(rules_path, {'version': 35, 'rules': []})
        for name, value in (('POLICY', policy_path), ('RULES', rules_path)):
            patcher = patch.object(queue, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        selection = {'projects': [{'title': f'测试项目{index:03}',
                     'source_files': [f'{index}.docx'], 'primary_source': f'{index}.docx'}
                     for index in range(1, 31)]}
        self.manifest = queue.init(self.src, self.root/'work', selection, self.root/'desktop')
        self.batch = self.manifest.parent

    reserve = fixtures.ParallelTests.reserve
    record = fixtures.ParallelTests.record

    def receipt(self, reserved, verdict='complete'):
        result = fixtures.ParallelTests.receipt(self, reserved, verdict)
        result.update({key: reserved['spawn_arguments'][key] for key in ('model', 'reasoning_effort')})
        return result

    def test_real_reservations_count_unbound_and_bound_slots_together(self):
        rows = [queue.reserve(self.manifest, 12) for _ in range(3)]
        rows += [self.reserve(12) for _ in range(3)]
        result = queue.plan(self.manifest, 12)
        self.assertEqual(result['action'], 'wait')
        self.assertEqual(result['pool_status']['draft'], {'active': 6, 'limit': 6})
        self.assertEqual(result['active_workers'], 6)
        self.assertEqual({row['case_id'] for row in rows}, {f'{index:03}' for index in range(1, 7)})

    def test_real_bad_source_skips_to_the_next_draft_without_occupying_a_slot(self):
        fixtures.docx(self.src/'1.docx', 'source changed after freeze')
        row = queue.reserve(self.manifest, 12)
        self.assertEqual(row['case_id'], '002')
        result = queue.plan(self.manifest, 12)
        self.assertEqual(result['pool_status']['draft']['active'], 1)
        self.assertIsNone(queue.case_by_id(b.read(self.manifest), '001')['active_attempt'])

    def test_separate_revision_and_fresh_review_precede_delivery(self):
        draft = self.reserve(1)
        self.record(draft)
        review = self.reserve(1)
        self.record(review, 'fix_required')
        revision = self.reserve(1)
        self.assertEqual(revision['attempt']['stage'], 'revise')
        self.assertEqual(revision['worker_context']['latest_review']['verdict'], 'fix_required')
        self.record(revision)
        state = b.read(self.manifest)['projects'][0]
        self.assertEqual(state['status'], 'revised')
        self.assertNotIn('passed_review_sha256', state)
        with self.assertRaises(b.BatchError):
            queue.deliver(self.manifest)
        recheck = self.reserve(1)
        self.assertEqual(recheck['attempt']['stage'], 'review')
        for old in (draft, review, revision):
            self.assertNotEqual(recheck['attempt']['id'], old['attempt']['id'])
            self.assertNotEqual(recheck['attempt']['task_name'], old['attempt']['task_name'])
        self.assertEqual(recheck['attempt']['input_artifact_sha256'], state['artifact_sha256'])
        self.record(recheck)
        self.assertEqual(queue.plan(self.manifest, 1)['action'], 'deliver')
        destination = Path(queue.deliver(self.manifest))
        self.assertEqual([path.name for path in destination.glob('*.docx')], ['测试项目001.docx'])
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'delivered')
        self.assertEqual(self.reserve(1)['case_id'], '002')

    def test_review_only_cannot_return_a_modified_artifact_or_sign_revision_pass(self):
        self.record(self.reserve(1))
        review = self.reserve(1)
        receipt = self.receipt(review, 'revised')
        artifact = Path(review['attempt']['directory'])/'changed.docx'
        fixtures.docx(artifact, 'changed review output')
        receipt.update(artifact=str(artifact), artifact_sha256=b.sha(artifact),
                       findings=[{'status': 'closed', 'location': 'body',
                                  'correction_evidence': 'synthetic correction'}])
        for verdict in ('revised', 'pass'):
            bad = dict(receipt, verdict=verdict)
            with self.subTest(verdict=verdict), self.assertRaises(b.BatchError):
                queue.record(self.manifest, review['case_id'], review['attempt']['id'], bad)
        self.record(review, 'fix_required')
        revision = self.reserve(1)
        bad = self.receipt(revision, 'pass')
        with self.assertRaises(b.BatchError):
            queue.record(self.manifest, revision['case_id'], revision['attempt']['id'], bad)

    def test_cli_requires_explicit_actual_capacity_before_any_probe_or_launch(self):
        with patch.object(pool, 'preflight') as preflight, patch.object(pool, 'start_supervisor') as launch:
            with self.assertRaises(b.BatchError):
                pool.run(self.manifest, 'controller')
        preflight.assert_not_called()
        launch.assert_not_called()

    def test_cli_legacy_omitted_capacity_keeps_four_slots(self):
        state = b.read(self.manifest)
        state['policy'] = copy.deepcopy(fixtures.POLICY)
        state['policy']['dispatch_transports'] = ['codex_cli']
        state['policy']['stage_scheduling'] = queue.IMMEDIATE_STAGES
        state['policy']['delivery'] = three_pool_policy()['delivery']
        for case in state['projects']:
            case['status'] = 'failed'
        b.atomic(self.batch/'policy.snapshot.json', state['policy'])
        b.atomic(self.manifest, state)
        with patch.object(pool, 'preflight', return_value='synthetic-cli'), \
             patch.object(queue, 'capacity_value', wraps=queue.capacity_value) as capacity, \
             patch.object(pool, 'emit'):
            result = pool.run(self.manifest, 'controller')
        self.assertFalse(result['halted'])
        self.assertTrue(capacity.called)
        self.assertTrue(all(call.args[1] == 4 for call in capacity.call_args_list))

    def test_cli_explicit_actual_capacity_is_used_without_a_paid_probe(self):
        state = b.read(self.manifest)
        for case in state['projects']:
            case['status'] = 'failed'
        b.atomic(self.manifest, state)
        for actual in (3, 12):
            with self.subTest(actual=actual), \
                 patch.object(pool, 'preflight', return_value='synthetic-cli'), \
                 patch.object(queue, 'capacity_value', wraps=queue.capacity_value) as capacity, \
                 patch.object(pool, 'start_supervisor') as launch, patch.object(pool, 'emit'):
                result = pool.run(self.manifest, 'controller', actual)
            self.assertFalse(result['halted'])
            self.assertTrue(capacity.called)
            self.assertTrue(all(call.args[1] == actual for call in capacity.call_args_list))
            launch.assert_not_called()

    def test_prompt_modes_route_fixes_to_the_independent_revision_stage(self):
        self.record(self.reserve(1))
        reserved = self.reserve(1)
        for mode in ('fallback', 'frozen', 'lean'):
            row = copy.deepcopy(reserved)
            if mode != 'fallback':
                row['worker_context']['prepared'] = {'pipeline': {
                    'root': str(self.root), 'stage_guide': 'review-guide',
                    'math_guide': 'math-guide', 'toolkit_guide': 'toolkit-guide',
                    'toolkit': 'toolkit'}}
                if mode == 'lean':
                    row['worker_context']['prepared']['pipeline']['execution_optimization'] = worker_receipt.MODE
            prompt = pool.worker_prompt(row)
            with self.subTest(mode=mode):
                self.assertIn('fix_required', prompt)
                self.assertIn('revise', prompt)
                self.assertNotIn('可本轮修正并返回revised', prompt)
                self.assertNotIn('小修可在当前复查轮完成', prompt)
                self.assertNotIn('本轮若修稿则返回revised', prompt)


if __name__ == '__main__':
    unittest.main()
