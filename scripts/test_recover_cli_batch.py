"""Recovery tests use simulated process identity, not patent acceptance evidence."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import batch_tool as b
import cli_evidence as ce
import cli_pool
import parallel_batch as q
import recover_cli_batch as recovery
import prepare_handoff
import test_cli_pool as fixtures


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.CliPoolTests.setUp
    receipt = fixtures.CliPoolTests.receipt
    fixture_cli = fixtures.CliPoolTests.fixture_cli
    enable_cli = fixtures.CliPoolTests.enable_cli

    def failed_draft(self):
        self.enable_cli()
        m = b.read(self.manifest); m['projects'] = m['projects'][:1]; b.atomic(self.manifest, m)
        r = q.reserve(self.manifest, 4, transport='codex_cli')
        receipt, dispatch, _ = self.fixture_cli(r)
        folder = Path(r['attempt']['directory'])
        events = [json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
        events.insert(2, {'type': 'error', 'message': 'Reconnecting... 2/5 (stream disconnected before completion: tls handshake eof)'})
        (folder/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
        seal = b.read(folder/'cli-completion.json'); seal['files']['events.jsonl'] = b.sha(folder/'events.jsonl')
        b.atomic(folder/'cli-completion.json', seal)
        m = q.load_manifest(self.manifest); c = m['projects'][0]; a = c['attempts'][0]
        with patch.object(ce, 'process_identity', return_value=None):
            cli_pool.release_failed(self.manifest, c, a, recovery.LEGACY_REASON, 'controller')
        return r, receipt

    def test_recover_retains_old_batch_and_requires_new_review(self):
        r, receipt = self.failed_draft()
        old_bytes = self.manifest.read_bytes()
        with patch.object(ce, 'process_identity', return_value=None):
            destination = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        self.assertEqual(self.manifest.read_bytes(), old_bytes)
        new = q.load_manifest(destination); c = new['projects'][0]
        self.assertEqual(c['status'], 'drafted')
        self.assertEqual(c['attempts'], [])
        self.assertNotIn('passed_review_sha256', c)
        self.assertEqual(b.sha(c['artifact']), receipt['artifact_sha256'])
        self.assertNotEqual(Path(c['artifact']), Path(receipt['artifact']))
        next_attempt = q.reserve(destination, 4, controller='recovery-controller')
        self.assertEqual(next_attempt['attempt']['stage'], 'review')
        self.assertEqual(next_attempt['spawn_arguments']['model'], 'gpt-6-astra')
        self.assertNotEqual(next_attempt['attempt']['context']['batch_id'], r['attempt']['context']['batch_id'])
        self.assertEqual(recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller'), destination)

    def test_recovery_rejects_changed_draft_source_and_unknown_error(self):
        for corruption in ('draft', 'source', 'error'):
            # Each subcase uses a fresh isolated fixture via a separate test instance.
            with self.subTest(corruption=corruption):
                fixture = RecoveryTests(); fixture.setUp()
                try:
                    r, receipt = fixture.failed_draft(); folder = Path(r['attempt']['directory'])
                    if corruption == 'draft':
                        Path(receipt['artifact']).write_bytes(b'changed')
                    elif corruption == 'source':
                        (fixture.src/'1.docx').write_bytes(b'changed')
                    else:
                        p = folder/'events.jsonl'; p.write_text(p.read_text()+'\n'+json.dumps({'type':'error','message':'unknown'}))
                        seal = b.read(folder/'cli-completion.json'); seal['files']['events.jsonl'] = b.sha(p)
                        b.atomic(folder/'cli-completion.json', seal)
                    with patch.object(ce, 'process_identity', return_value=None), self.assertRaises((ValueError, q.BatchError)):
                        recovery.recover(fixture.manifest, fixture.root/'recoveries', 'recovery-controller')
                finally:
                    fixture.doCleanups()

    def test_recovery_refuses_active_worker_and_changed_provenance(self):
        self.failed_draft()
        m = b.read(self.manifest); m['projects'][0]['active_attempt'] = m['projects'][0]['attempts'][0]['id']
        b.atomic(self.manifest,m)
        with self.assertRaisesRegex(q.BatchError, 'worker'):
            recovery.recover(self.manifest,self.root/'recoveries','recovery-controller')
        m['projects'][0]['active_attempt'] = None; b.atomic(self.manifest,m)
        with patch.object(ce,'process_identity',return_value=None):
            destination = recovery.recover(self.manifest,self.root/'recoveries','recovery-controller')
        new=q.load_manifest(destination); c=new['projects'][0]
        Path(c['recovery_evidence']).write_text('{}')
        with self.assertRaisesRegex(q.BatchError,'recovery evidence'):
            q.verify_case(new,c)
        with self.assertRaisesRegex(q.BatchError,'recovery evidence'):
            recovery.recover(self.manifest,self.root/'recoveries','recovery-controller')

    def test_recovery_rejects_dispatch_record_differing_from_sealed_file(self):
        self.failed_draft()
        m = b.read(self.manifest)
        m['projects'][0]['attempts'][0]['dispatch_record']['extra'] = 'changed'
        b.atomic(self.manifest, m)
        with patch.object(ce, 'process_identity', return_value=None):
            with self.assertRaisesRegex(q.BatchError, 'dispatch differs'):
                recovery.verified_drafts(self.manifest)

    def test_recovery_rejects_actual_wrong_model_despite_sol_receipt(self):
        r, _ = self.failed_draft()
        folder = Path(r['attempt']['directory'])
        m = b.read(self.manifest)
        a = m['projects'][0]['attempts'][0]
        dispatch = a['dispatch_record']
        dispatch['arguments']['model'] = 'gpt-6-astra'
        dispatch['arguments']['argv'] = ce.build_command(
            dispatch['result']['executable'], folder, 'gpt-6-astra', 'high')
        b.atomic(folder/'dispatch.json', dispatch)
        a['dispatch_sha256'] = b.sha(folder/'dispatch.json')
        session = [json.loads(line) for line in (folder/'session.jsonl').read_text().splitlines()]
        for row in session:
            if row['type'] == 'turn_context':
                row['payload']['model'] = 'gpt-6-astra'
        (folder/'session.jsonl').write_text('\n'.join(json.dumps(row) for row in session), encoding='utf8')
        seal = b.read(folder/'cli-completion.json')
        seal['files']['session.jsonl'] = b.sha(folder/'session.jsonl')
        b.atomic(folder/'cli-completion.json', seal)
        b.atomic(self.manifest, m)
        with patch.object(ce, 'process_identity', return_value=None):
            # The transport evidence itself is consistent, but not draft policy.
            ce.verify_completion(a, dispatch)
            with self.assertRaisesRegex(q.BatchError, 'frozen draft model'):
                recovery.verified_drafts(self.manifest)

    def test_existing_recovery_pointer_must_reference_this_source(self):
        self.failed_draft()
        with patch.object(ce, 'process_identity', return_value=None):
            destination = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        new = b.read(destination)
        new['recovered_from']['manifest'] = str(self.root/'another-manifest.json')
        b.atomic(destination, new)
        with self.assertRaisesRegex(q.BatchError, 'pointer provenance'):
            recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')

    def test_recovered_sol_session_cannot_supply_new_review(self):
        self.failed_draft()
        with patch.object(ce, 'process_identity', return_value=None):
            destination = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        new = q.load_manifest(destination)
        session_id = new['projects'][0]['recovered_cli_session_ids'][0]
        self.manifest = destination
        r = q.reserve(destination, 4, controller='recovery-controller', transport='codex_cli')
        receipt, dispatch, _ = self.fixture_cli(r, bind=False)
        with patch.object(ce, 'process_identity', return_value=dispatch['result']):
            q.bind(destination, r['case_id'], r['attempt']['id'],
                   Path(r['attempt']['directory'])/'dispatch.json', controller='recovery-controller')
        with patch.object(ce, 'verify_completion', return_value={'session_id': session_id, 'receipt': receipt}):
            with self.assertRaisesRegex(q.BatchError, 'Recovered draft session'):
                q.record(destination, r['case_id'], r['attempt']['id'], receipt, controller='recovery-controller')

    def test_recovered_draft_supports_real_prepared_review_reservation(self):
        self.failed_draft()
        with patch.object(ce, 'process_identity', return_value=None):
            destination = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        before = q.load_manifest(destination)
        self.assertEqual(before['projects'][0]['attempts'], [])
        reserved = q.reserve(destination, 4, controller='recovery-controller', prepare=True, transport='codex_cli')
        self.assertEqual(reserved['attempt']['stage'], 'review')
        self.assertEqual(reserved['spawn_arguments']['model'], 'gpt-6-astra')
        self.assertIsNotNone(reserved['worker_context']['prepared']['input_inspection'])
        after = q.load_manifest(destination)
        prepare_handoff.verify_prepared(after, after['projects'][0], after['projects'][0]['attempts'][0])

    def test_new_generation_preserves_failed_manifest_and_pointer_history(self):
        self.failed_draft()
        original = self.manifest.read_bytes()
        with patch.object(ce, 'process_identity', return_value=None):
            first = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        # Reproduce the pool's pre-dispatch failure and defer path. The new
        # generation below must then pass the real, unmocked prepare function.
        with patch.object(prepare_handoff, 'prepare', side_effect=ValueError('Foreign or changed current artifact')):
            with self.assertRaises(q.PreparationError) as failure:
                q.reserve(first, 4, controller='recovery-controller', prepare=True, transport='codex_cli')
        q.defer(first, '001', 'failed', str(failure.exception), 'recovery-controller')
        failed_bytes = first.read_bytes()
        pointer_bytes = (self.manifest.parent/'cli-recovery.json').read_bytes()
        with patch.object(ce, 'process_identity', return_value=None):
            second = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller', new_generation=True)
        self.assertNotEqual(first, second)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(first.read_bytes(), failed_bytes)
        histories = list(self.manifest.parent.glob('cli-recovery.history.*.json'))
        self.assertEqual(len(histories), 1)
        self.assertEqual(histories[0].read_bytes(), pointer_bytes)
        self.assertEqual(b.read(self.manifest.parent/'cli-recovery.json')['manifest'], str(second))
        reserved = q.reserve(second, 4, controller='recovery-controller', prepare=True, transport='codex_cli')
        self.assertEqual(reserved['attempt']['stage'], 'review')

    def test_new_generation_rejects_other_failure_or_any_dispatch(self):
        self.failed_draft()
        with patch.object(ce, 'process_identity', return_value=None):
            first = recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller')
        pointer_bytes = (self.manifest.parent/'cli-recovery.json').read_bytes()
        with self.assertRaisesRegex(q.BatchError, 'undispatched layout'):
            recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller', new_generation=True)
        q.defer(first, '001', 'failed', 'Case 001 preparation failed: Foreign or changed current artifact', 'recovery-controller')
        c = q.load_manifest(first)['projects'][0]
        intent = Path(c['artifact']).parent/'cli-launch-intent.json'
        b.atomic(intent, {})
        with self.assertRaisesRegex(q.BatchError, 'undispatched layout'):
            recovery.recover(self.manifest, self.root/'recoveries', 'recovery-controller', new_generation=True)
        self.assertEqual((self.manifest.parent/'cli-recovery.json').read_bytes(), pointer_bytes)


if __name__ == '__main__':
    unittest.main()
