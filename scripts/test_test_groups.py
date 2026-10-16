"""Test selection must retain coverage, isolation and failure propagation."""
import unittest
from unittest.mock import patch
import test_groups as groups


class GroupSelectionTests(unittest.TestCase):
    def test_catalog_is_complete_and_overlapping_groups_run_each_id_once(self):
        groups.catalog_check()
        first = groups.select(['antecedents'])
        second = groups.select(['claims'])
        combined = groups.select(['antecedents', 'claims', 'antecedents'])
        self.assertEqual(set(combined['test_ids']), set(first['test_ids']) | set(second['test_ids']))
        self.assertEqual(len(combined['test_ids']), len(set(combined['test_ids'])))
        self.assertLess(combined['test_cases'], first['test_cases']+second['test_cases'])

    def test_changed_text_and_tests_select_only_their_required_checks(self):
        references = groups.select(changed=['references/rules.json', 'references/terminology-and-formula.md'])
        self.assertEqual(set(references['test_ids']), set(groups.PACKET_CHECKS))
        changed_test = groups.select(changed=['scripts/test_verified_json_reads.py'])
        self.assertTrue(changed_test['test_ids'])
        self.assertTrue(all(name.startswith('test_verified_json_reads.') for name in changed_test['test_ids']))
        docs = groups.run_plan(groups.select(changed=['README.md']))
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
