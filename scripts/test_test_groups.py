"""Test selection must retain coverage, isolation and failure propagation."""
import contextlib
import ast
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_groups as groups


class GroupSelectionTests(unittest.TestCase):
    def write_report(self, root, **changes):
        ids = groups.select(['antecedents'])['test_ids'][:2]
        report = dict(status='completed', passed=False, test_ids=ids,
                      test_cases=2, tests_run=2, retry_test_ids=[ids[0]])
        report.update(changes)
        path = root/'previous.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path, ids

    def test_failed_report_selects_fresh_failures_without_inheriting_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path, ids = self.write_report(Path(directory))
            plan = groups.select_failed(path)
            self.assertEqual(plan['test_ids'], [ids[0]])
            self.assertNotIn('passed', plan)
            report = json.loads(path.read_text())
            report.pop('retry_test_ids')
            path.write_text(json.dumps(report))
            self.assertEqual(groups.select_failed(path)['test_ids'], ids)
            path, _ = self.write_report(Path(directory), passed=True, retry_test_ids=[])
            empty = groups.run_plan(groups.select_failed(path))
            self.assertEqual(empty['status'], 'no_runtime_tests_required')
            self.assertIsNone(empty['passed'])

    def test_invalid_or_contradictory_retry_reports_are_rejected(self):
        changes = [dict(status='running'), dict(passed=None), dict(retry_test_ids=[]),
                   dict(test_cases=3), dict(test_ids=['os.system']),
                   dict(retry_test_ids=['unknown.C.test_one']), dict(passed=True),
                   dict(passed=True, retry_test_ids=[], tests_run=0)]
        with tempfile.TemporaryDirectory() as directory:
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    path, _ = self.write_report(Path(directory), **change)
                    groups.select_failed(path)

    def test_failures_errors_subtests_and_unexpected_success_retain_parent_ids(self):
        class Synthetic(unittest.TestCase):
            def test_pass(self): pass
            def test_failure(self): self.fail('synthetic')
            def test_error(self): raise ValueError('synthetic')
            def test_subtest(self):
                for index in range(2):
                    with self.subTest(index=index): self.assertEqual(index, 0)
            @unittest.expectedFailure
            def test_unexpected_success(self): pass
        names = ['test_pass', 'test_failure', 'test_error', 'test_subtest', 'test_unexpected_success']
        with patch.object(groups.unittest, 'TestLoader') as factory:
            factory.return_value.errors = []
            factory.return_value.loadTestsFromName.side_effect = lambda name: Synthetic(name)
            result = groups.run_ids(names)
        self.assertFalse(result['passed'])
        self.assertEqual(result['tests_run'], len(names))
        self.assertEqual(result['failed_test_ids'], sorted(Synthetic(name).id() for name in names[1:]))

    def test_retry_scope_is_precise_unless_the_worker_report_is_incomplete(self):
        ids = ['a.C.test_one', 'a.C.test_two']
        cases = [({'passed':False, 'tests_run':2, 'failed_test_ids':[ids[1]]}, [ids[1]]),
                 ({'passed':False, 'tests_run':1, 'failed_test_ids':[ids[1]]}, ids),
                 ({'passed':False, 'tests_run':2}, ids),
                 ({'passed':True, 'tests_run':0}, ids)]
        for outcome, expected in cases:
            with self.subTest(outcome=outcome), patch.object(groups, 'run_ids', return_value=outcome):
                result = groups.run_plan({'test_ids':ids}, 1)
                self.assertFalse(result['passed'])
                self.assertEqual(result['retry_test_ids'], expected)

    def test_cli_unions_failure_scope_with_changed_tests_and_preserves_original_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, ids = self.write_report(root)
            original = previous.read_bytes()
            output = root/'rerun.json'
            with contextlib.redirect_stdout(io.StringIO()):
                code = groups.main(['--failed-from', str(previous), '--changed',
                                    'scripts/test_verified_json_reads.py', '--list', '--output', str(output)])
            self.assertEqual(code, 0)
            selected = json.loads(output.read_text(encoding='utf-8'))
            expected = set(groups.select(changed=['scripts/test_verified_json_reads.py'])['test_ids']) | {ids[0]}
            self.assertEqual(set(selected['test_ids']), expected)
            self.assertEqual(previous.read_bytes(), original)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                groups.main(['--failed-from', str(previous), '--output', str(previous)])
            self.assertEqual(previous.read_bytes(), original)

    def test_catalog_is_complete_and_overlapping_groups_run_each_id_once(self):
        groups.catalog_check()
        first = groups.select(['antecedents'])
        second = groups.select(['claims'])
        combined = groups.select(['antecedents', 'claims', 'antecedents'])
        self.assertEqual(set(combined['test_ids']), set(first['test_ids']) | set(second['test_ids']))
        self.assertEqual(len(combined['test_ids']), len(set(combined['test_ids'])))
        self.assertLess(combined['test_cases'], first['test_cases']+second['test_cases'])

    def test_repeated_changed_options_union_impacts_without_losing_or_duplicating_tests(self):
        paths = ['scripts/stage_bundle.py', 'references/claim-families-and-benefits.md']
        expected = groups.select(changed=paths)
        cases = [paths, [paths[0], '--changed', paths[1]],
                 [paths[0], '--changed', paths[1], paths[0]]]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'selection.json'
            for arguments in cases:
                with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()):
                    code = groups.main(['--changed', *arguments, '--list', '--output', str(output)])
                self.assertEqual(code, 0)
                actual = json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(actual['test_ids'], expected['test_ids'])
                self.assertEqual(actual['groups'], expected['groups'])
                self.assertEqual(actual['test_cases'], len(set(actual['test_ids'])))

    def test_changed_text_and_tests_select_only_their_required_checks(self):
        references = groups.select(changed=['references/rules.json', 'references/terminology-and-formula.md'])
        self.assertEqual(set(references['test_ids']), set(groups.PACKET_CHECKS))
        changed_test = groups.select(changed=['scripts/test_verified_json_reads.py'])
        self.assertTrue(changed_test['test_ids'])
        self.assertTrue(all(name.startswith('test_verified_json_reads.') for name in changed_test['test_ids']))
        for changed in ('README.md', 'CHANGELOG.md', 'SKILL.md', 'agents/openai.yaml'):
            with self.subTest(documentation_change=changed):
                docs = groups.run_plan(groups.select(changed=[changed]))
                self.assertEqual(docs['status'], 'no_runtime_tests_required')
                self.assertIsNone(docs['passed'])
        expected_render = groups.select(['pages', 'workflow'])['test_ids']
        for changed in ('scripts/wps_render_backend.py', 'scripts/wps_readonly_pdf.ps1',
                        'references/runtime.json'):
            with self.subTest(render_change=changed):
                self.assertEqual(groups.select(changed=[changed])['test_ids'], expected_render)
        for script, consumers in {
            'parallel_batch.py': ['test_punctuation_contract', 'test_figure_caption_contract',
                                  'test_figure_description_contract', 'test_formula_prose_contract'],
            'prepare_handoff.py': ['test_content_spacing'],
            'flat_delivery.py': ['test_lean_execution'],
            'authoring_toolkit.py': ['test_model_policy'],
        }.items():
            plan = groups.select(changed=['scripts/'+script])
            for module in consumers:
                with self.subTest(script=script, consumer=module):
                    self.assertTrue(any(name.startswith(module+'.') for name in plan['test_ids']))

    def test_unknown_changes_expand_and_invalid_selection_never_silently_passes(self):
        all_ids = groups.select(all_tests=True)['test_ids']
        for path in ('scripts/future_shared_gate.py',
                     'references/source-manifest.json', 'references/future_config.json',
                     'references/batch-policy.json'):
            with self.subTest(changed=path):
                self.assertEqual(groups.select(changed=[path])['test_ids'], all_ids)
        for kwargs in ({'groups':['unknown']}, {'changed':['../foreign.py']},
                       {'changed':['scripts/test_missing.py']}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                groups.select(**kwargs)

    def test_cli_impact_selection_covers_direct_consumers_and_pipeline_drivers(self):
        for script in ('cli_pool', 'cli_evidence', 'stage_pool_runner'):
            plan = groups.select(changed=['scripts/'+script+'.py'])
            selected = {ident.split('.')[0] for ident in plan['test_ids']}
            consumers = set()
            for path in groups.ROOT.glob('test_*.py'):
                if path.stem == 'test_groups': continue
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                imports = {alias.name.split('.')[0] for node in ast.walk(tree)
                           if isinstance(node,ast.Import) for alias in node.names}
                imports.update(node.module.split('.')[0] for node in ast.walk(tree)
                               if isinstance(node,ast.ImportFrom) and node.module)
                if script in imports: consumers.add(path.stem)
            if script == 'stage_pool_runner':
                consumers.update(('test_nonblocking_pools','test_cli_pool','test_overlapped_pipeline',
                                  'test_three_stage_pools','test_model_policy'))
            with self.subTest(script=script):
                self.assertTrue(consumers <= selected, f'Uncovered CLI consumers: {consumers-selected}')
                self.assertNotIn('test_batch_throughput',selected)
                self.assertNotIn('test_rules_tool',selected)
                self.assertNotIn('test_claim_wording',selected)
        plan = groups.select(changed=['scripts/cli_evidence.py'])
        self.assertTrue(any(ident.startswith('test_render_docx_checked.') for ident in plan['test_ids']))

    def test_impact_selection_unions_core_changes_and_rejects_bad_routes(self):
        local = groups.select(changed=['scripts/cli_pool.py'])
        explicit = groups.select(groups=['workflow'],changed=['scripts/cli_pool.py'])
        expected = set(local['test_ids']) | set(groups.select(groups=['workflow'])['test_ids'])
        self.assertEqual(set(explicit['test_ids']),expected)
        self.assertEqual(len(explicit['test_ids']),len(expected))
        mixed = groups.select(changed=['scripts/cli_pool.py','scripts/parallel_batch.py'])
        self.assertEqual(mixed['test_ids'],groups.select(all_tests=True)['test_ids'])
        with patch.dict(groups.SCRIPT_CHECKS,{'cli_pool.py':['test_missing_consumer']}):
            with self.assertRaisesRegex(ValueError,'Unknown script impact'):
                groups.select(changed=['scripts/cli_pool.py'])

    def test_partition_keeps_modules_together_and_preserves_every_id(self):
        ids = ['a.C.test_one', 'a.C.test_two', 'b.C.test_one', 'c.C.test_one']
        chunks = groups.partitions(ids, 2)
        self.assertEqual(sorted(item for chunk in chunks for item in chunk), sorted(ids))
        self.assertTrue(any(set(ids[:2]).issubset(chunk) for chunk in chunks))
        with patch.object(groups, 'run_ids', return_value={'passed':False, 'tests_run':1, 'log':'fixture failure'}):
            self.assertFalse(groups.run_plan({'test_ids':[ids[0]]}, 1)['passed'])
        with patch.object(groups, 'run_ids', return_value={'passed':True, 'tests_run':0}):
            self.assertFalse(groups.run_plan({'test_ids':[ids[0]]}, 1)['passed'])

    def test_two_real_processes_report_the_selected_cases(self):
        # Two existing tiny parser tests: no Word conversion or paid CLI.
        selected = groups.select(['antecedents'])['test_ids']
        ids = [next(name for name in selected if name.startswith(module+'.'))
               for module in ('test_antecedent_wording', 'test_claim_wording')]
        result = groups.run_plan({'test_ids':ids}, 2)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['tests_run'], 2)
        self.assertEqual(result['workers'], 2)


if __name__ == '__main__':
    unittest.main()
