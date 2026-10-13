"""Compact output must not truncate durable case evidence or weaken acceptance."""
import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch
import batch_tool as b
import parallel_batch as p
import test_parallel_batch as fixtures


class CompactTests(unittest.TestCase):
    setUp = fixtures.ParallelTests.setUp
    reserve = fixtures.ParallelTests.reserve
    receipt = fixtures.ParallelTests.receipt

    def test_record_compact_preserves_full_dispatch_receipt_and_case(self):
        reserved = self.reserve(1); receipt = self.receipt(reserved)
        receipt['evidence_detail'] = 'fixture detail ' * 1000
        path = reserved['worker_context']['receipt_path']; b.atomic(path, receipt)
        args = ['parallel_batch.py', 'record', '--manifest', str(self.manifest), '--controller', 'controller',
                '--case', reserved['case_id'], '--attempt', reserved['attempt']['id'], '--receipt', str(path), '--compact']
        stream = io.StringIO()
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(stream): self.assertEqual(p.main(), 0)
        result = json.loads(stream.getvalue()); case = b.read(self.manifest)['projects'][0]
        self.assertEqual(result['status'], 'drafted')
        self.assertEqual(case['receipts'][-1], receipt)
        self.assertEqual(b.read(path), receipt)
        self.assertEqual(case['attempts'][0]['state'], 'completed')
        self.assertLess(len(stream.getvalue()), len(json.dumps(case)) / 5)

    def test_compact_does_not_hide_wrong_model_rejection(self):
        reserved = self.reserve(1); receipt = self.receipt(reserved); receipt['reasoning_effort'] = 'low'
        path = reserved['worker_context']['receipt_path']; b.atomic(path, receipt)
        args = ['parallel_batch.py', 'record', '--manifest', str(self.manifest), '--controller', 'controller',
                '--case', reserved['case_id'], '--attempt', reserved['attempt']['id'], '--receipt', str(path), '--compact']
        stream = io.StringIO()
        with patch.object(sys, 'argv', args), contextlib.redirect_stderr(stream): self.assertEqual(p.main(), 2)
        self.assertIn('mismatch', stream.getvalue())
        self.assertEqual(b.read(self.manifest)['projects'][0]['status'], 'pending')


if __name__ == '__main__': unittest.main()
