"""One fixture, scenario-based accounting checks; no real batch or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import batch_tool as b
import batch_throughput as t


class ThroughputTests(unittest.TestCase):
    def test_unique_accepted_outputs_complete_windows_and_unreliable_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cli-pool').mkdir()
            manifest = root / 'manifest.json'
            baseline = {'batch_id': 'synthetic', 'rules_sha256': 'rules', 'rule_version': 57,
                        'projects': [], 'delivery_items': {}}
            events = [{'event': 'pool_started', 'time': 1000}]
            for i in range(10):
                ident = str(i)
                output = root / (ident + '.docx'); output.write_bytes(('test artifact ' + ident).encode())
                digest = b.sha(output)
                folder = root / ('draft-' + ident); folder.mkdir()
                b.atomic(folder / 'cli-exit.json', {'started': 1001 + i * 5, 'finished': 1099 + i * 300, 'returncode': 0})
                attempt = {'stage': 'draft', 'state': 'completed', 'directory': str(folder), 'context': {
                    'batch_id': 'synthetic', 'case_id': ident, 'rules_sha256': 'rules'}}
                baseline['projects'].append({'id': ident, 'status': 'delivered', 'delivered_artifact': str(output),
                    'artifact_sha256': digest, 'passed_review_sha256': digest, 'attempts': [attempt], 'receipts': [{
                        'stage': 'review', 'verdict': 'pass', 'reviewed_sha256': digest,
                        'checks': {k: 'passed' for k in b.CHECKS}, 'context': {'rules_sha256': 'rules'}}]})
                baseline['delivery_items'][ident] = {'kind': 'accepted', 'sha256': digest, 'relative_path': output.name}
                # Cumulative publication events deliberately repeat previous cases.
                events.append({'event': 'publication_complete', 'time': 1100 + i * 300,
                               'delivered_case_ids': [str(j) for j in range(i+1)]})
            events.append({'event': 'pool_finished', 'time': 4600})
            warm = root / 'earlier-draft'; warm.mkdir()
            b.atomic(warm / 'cli-exit.json', {'started': 900, 'finished': 1000, 'returncode': 0})
            for scenario in ('valid', 'early_burst_long_quiet_tail', 'short', 'recovery', 'missing_draft_time', 'earlier_draft', 'wrong_sha', 'failed_check', 'wrong_rules',
                             'missing_time', 'malformed_event', 'clock_rollback', 'duplicate_case', 'missing_file'):
                with self.subTest(scenario=scenario):
                    m, rows = copy.deepcopy(baseline), copy.deepcopy(events)
                    if scenario == 'short': rows.pop()
                    elif scenario == 'early_burst_long_quiet_tail': rows[-1]['time'] = 10000
                    elif scenario == 'recovery': m['recovered_from'] = 'old-batch'
                    elif scenario == 'missing_draft_time': m['projects'][0]['attempts'] = []
                    elif scenario == 'earlier_draft': m['projects'][0]['attempts'][0]['directory'] = str(warm)
                    elif scenario == 'wrong_sha': m['delivery_items']['0']['sha256'] = 'wrong'
                    elif scenario == 'failed_check': m['projects'][0]['receipts'][0]['checks']['formulas'] = 'failed'
                    elif scenario == 'wrong_rules': m['projects'][0]['receipts'][0]['context']['rules_sha256'] = 'old'
                    elif scenario == 'missing_time': rows = [rows[0], rows[-1]]
                    elif scenario == 'clock_rollback': rows[-1]['time'] = 900
                    elif scenario == 'duplicate_case': m['projects'].append(copy.deepcopy(m['projects'][0]))
                    elif scenario == 'missing_file': m['projects'][0]['delivered_artifact'] = str(root / 'missing.docx')
                    b.atomic(manifest, m)
                    (root / 'cli-pool/events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) +
                        ('\n{' if scenario == 'malformed_event' else ''), encoding='utf-8')
                    result = t.measure(manifest)
                    self.assertEqual(result['rule_version'], 57)
                    if scenario in ('valid', 'early_burst_long_quiet_tail'):
                        self.assertEqual(result['status'], 'met_in_observed_window')
                        self.assertEqual(result['cold_first_hour_deliveries'], 10)
                        self.assertEqual(result['best_full_rolling_hour_deliveries'], 10)
                        self.assertEqual(result['first_target_delivery_elapsed_seconds'], 2800)
                    elif scenario == 'short':
                        self.assertEqual(result['status'], 'insufficient_observation')
                        self.assertIsNone(result['best_full_rolling_hour_deliveries'])
                    elif scenario == 'recovery':
                        self.assertEqual(result['status'], 'recovery_not_fresh_benchmark')
                        self.assertIsNone(result['cold_first_hour_deliveries'])
                    elif scenario in ('missing_draft_time', 'earlier_draft'):
                        self.assertEqual(result['status'], 'warm_start_unknown')
                        self.assertIsNone(result['cold_first_hour_deliveries'])
                        self.assertEqual(result['best_full_rolling_hour_deliveries'], 10)
                    elif scenario in ('malformed_event', 'clock_rollback'):
                        self.assertEqual(result['status'], 'unreliable_timeline')
                    elif scenario == 'duplicate_case':
                        self.assertEqual(result['valid_timed_deliveries'], 10)
                        self.assertEqual(len(result['excluded_deliveries']), 1)
                    else:
                        self.assertLess(result['valid_timed_deliveries'], 10)
                        self.assertNotEqual(result['status'], 'met_in_observed_window')


if __name__ == '__main__':
    unittest.main()
