"""Local synthetic regressions for latency faults; no patent data or model calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
from PIL import Image, ImageDraw
from pypdf import PdfWriter

import batch_tool as b
from capacity_budget import CapacityBudget, MODE
import capacity_budget
import cli_pool as cli
import controller_render_bridge as bridge
import page_probe
import parallel_batch as queue
import stage_bundle
import test_nonblocking_pools as nonblocking


class DelayRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='delay-repair-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root/'manifest.json'

    def history(self, rows):
        folder = self.root/'cli-pool'; folder.mkdir(exist_ok=True)
        (folder/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n', encoding='utf-8')

    def test_recovery_uses_real_completions_caps_and_cumulative_restart_budget(self):
        self.history([{'event': 'capacity_backoff'}, {'event': 'capacity_restored', 'next_workers': 2}])
        budget = CapacityBudget(self.manifest, 8)
        self.assertEqual((budget.workers, budget.retries), (2, 1))
        emit, sleep = Mock(), Mock()
        budget.completed(emit, case_id='001', attempt='verified')
        self.assertEqual(budget.workers, 4)
        budget.completed(emit, case_id='002', attempt='verified-peer')
        budget.completed(emit, case_id='003', attempt='at-ceiling')
        self.assertEqual(budget.workers, 8)
        self.assertEqual(emit.call_count, 2)
        self.assertTrue(budget.cooldown(emit, sleep))
        self.assertEqual((budget.workers, budget.retries), (1, 2))
        self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), 120)
        self.assertFalse(budget.cooldown(emit, sleep))
        self.history([{'event': 'capacity_backoff'}, {'event': 'capacity_backoff'}])
        restarted = CapacityBudget(self.manifest, 32)
        self.assertFalse(restarted.cooldown(emit, sleep))
        self.assertEqual((restarted.workers, restarted.retries), (1, 2))

    def test_explicit_low_budget_and_corrupt_journal_do_not_silently_expand(self):
        self.history([{'event': 'capacity_backoff'}, {'event': 'capacity_restored', 'next_workers': 16}])
        budget = CapacityBudget(self.manifest, 4)
        emit = Mock(); budget.completed(emit, case_id='001', attempt='done')
        self.assertEqual(budget.workers, 4); emit.assert_not_called()
        self.history([{'event': 'capacity_restored', 'next_workers': True}])
        with self.assertRaises(ValueError): CapacityBudget(self.manifest, 32)
        (self.root/'cli-pool/events.jsonl').write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError): CapacityBudget(self.manifest, 32)

    def test_unknown_failure_does_not_backoff_or_replay(self):
        budget = CapacityBudget(self.manifest, 8)
        run = Mock(return_value={'halted': True, 'capacity_limited': False})
        with patch.object(cli, 'event'), patch.object(cli.time, 'sleep') as sleep:
            result = cli.capacity_recovery(self.manifest, run, 8, budget=budget)
        run.assert_called_once_with(8); sleep.assert_not_called()
        self.assertTrue(result['halted'])

    def test_restart_waits_only_remaining_cooldown_without_consuming_another_retry(self):
        self.history([{'event': 'capacity_backoff', 'time': 90, 'delay_seconds': 60}])
        with patch.object(capacity_budget.time, 'time', return_value=100):
            budget = CapacityBudget(self.manifest, 8)
        run = Mock(return_value={'halted': False, 'capacity_limited': False})
        with patch.object(cli, 'event'), patch.object(cli.time, 'sleep') as sleep:
            cli.capacity_recovery(self.manifest, run, 8, budget=budget)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 20])
        self.assertEqual(budget.retries, 1); run.assert_called_once_with(1)

    def test_old_snapshot_does_not_advertise_an_unavailable_page_tool(self):
        (self.root/'worker-review.md').write_text('synthetic frozen guide', encoding='utf-8')
        manifest = {'policy': {}, 'skill_snapshot': {'sha256': 'synthetic'}}
        with patch.object(stage_bundle, 'verify', return_value=self.root):
            self.assertNotIn('page_probe', stage_bundle.worker_paths(manifest, 'review'))
            scripts = self.root/'scripts'; scripts.mkdir()
            (scripts/'page_probe.py').write_text('synthetic available tool', encoding='utf-8')
            self.assertEqual(stage_bundle.worker_paths(manifest, 'review')['page_probe'], str(scripts/'page_probe.py'))

    def test_reservation_cannot_enter_cancel_defer_gap(self):
        root = self.root/'attempt'; root.mkdir()
        b.atomic(root/'cli-exit.json', {'pid': 1, 'created': 2, 'returncode': 1})
        attempt = {'directory': str(root), 'id': 'a', 'context': {}}
        cancelled, release, reserved = threading.Event(), threading.Event(), threading.Event()
        deferred = []
        def cancel(*args):
            cancelled.set()
            self.assertTrue(release.wait(5))
        def reserve(*args, **kwargs):
            self.assertEqual(deferred, [True]); reserved.set()
        with patch.object(queue, 'cancel', side_effect=cancel), \
             patch.object(queue, 'defer', side_effect=lambda *args: deferred.append(True)), \
             patch.object(queue, 'reserve', side_effect=reserve), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(cli.release_failed, self.manifest, {'id': '001'}, attempt, 'fault', 'controller')
            self.assertTrue(cancelled.wait(5))
            second = executor.submit(cli.reserve, self.manifest, 4)
            try:
                self.assertFalse(reserved.wait(.05))
            finally:
                release.set()
            first.result(timeout=5); second.result(timeout=5)

    def test_failed_quarantine_stops_new_reservation(self):
        key = str(self.manifest.absolute())
        self.addCleanup(cli.RESERVATION_FAILURES.discard, key)
        with patch.object(cli, '_release_failed', side_effect=RuntimeError('cannot defer')):
            with self.assertRaises(RuntimeError):
                cli.release_failed(self.manifest, {}, {}, 'fault', 'controller')
        with patch.object(queue, 'reserve') as reserve:
            with self.assertRaisesRegex(b.BatchError, 'quarantine'):
                cli.reserve(self.manifest, 4)
        reserve.assert_not_called()

    def fixture(self, **policy_values):
        fixture = nonblocking.NonblockingBehaviorTests()
        self.addCleanup(fixture.doCleanups)
        policy = nonblocking.policy(); policy.update(policy_values)
        with patch.object(nonblocking, 'policy', return_value=policy):
            fixture.setUp()
        return fixture

    def test_pipeline_restores_parallelism_without_new_controller_or_probe_calls(self):
        fixture = self.fixture(capacity_recovery=MODE)
        (fixture.batch/'cli-pool/events.jsonl').write_text('{"event":"capacity_backoff"}\n', encoding='utf-8')
        budgets, events = [], []
        original = queue.reserve
        def reserve(*args, **kwargs):
            budgets.append(args[1]); return original(*args, **kwargs)
        with patch.object(queue, 'reserve', side_effect=reserve):
            result, launches = fixture.run_fake_pipeline(workers=8,
                event=lambda manifest, kind, **values: events.append((kind, values)))
        self.assertFalse(result['halted'], result)
        self.assertEqual(launches, 16)  # Eight ordinary draft/review pairs; no probes.
        self.assertTrue({1, 2, 4, 8}.issubset(set(budgets)))
        self.assertLessEqual(max(budgets), 8)
        self.assertEqual([v['next_workers'] for k, v in events if k=='capacity_restored'], [2, 4, 8])
        self.assertTrue(all(c['status']=='delivered' for c in fixture.state()['projects']))

    def test_slow_render_io_does_not_block_peer_stages_or_publication(self):
        fixture = self.fixture(controller_render=bridge.MODE)
        # Isolate publication readiness from unrelated draft/source preparation.
        fixture.accepted_first_case()
        started, release, peer_delivered = threading.Event(), threading.Event(), threading.Event()
        offered = []
        def pending(manifest):
            if not offered:
                offered.append(True)
                return [fixture.root/'synthetic.request.json']
            return []
        def fulfill(*args):
            started.set(); self.assertTrue(release.wait(60))
            return {'ok': True, 'visual_review': 'not_run'}
        original_deliver = queue.deliver
        def publish(*args, **kwargs):
            if not kwargs.get('audit_history'):
                self.assertTrue(started.wait(30))
            return original_deliver(*args, **kwargs)
        def event(manifest, kind, **values):
            if (kind=='publication_complete' and '001' in values.get('delivered_case_ids', [])
                    and started.is_set() and not release.is_set()):
                peer_delivered.set()
        with patch.object(bridge, 'pending', side_effect=pending), \
             patch.object(bridge, 'fulfill', side_effect=fulfill), \
             patch.object(queue, 'deliver', side_effect=publish), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fixture.run_fake_pipeline, workers=4, event=event)
            try:
                self.assertTrue(started.wait(30))
                self.assertTrue(peer_delivered.wait(30), 'ready publication waited for slow render I/O')
                self.assertFalse(future.done(), 'controller exited before its render lane')
            finally:
                release.set()
            result, launches = future.result(timeout=60)
        self.assertFalse(result['halted'], result); self.assertEqual(launches, 14)


class RenderHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='render-handoff-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.attempt = self.root/'cases/001/stages'/('a'*32)
        self.requests = self.attempt/'.controller-render-requests'
        self.requests.mkdir(parents=True)
        self.source = self.attempt/'source.docx'; self.source.write_bytes(b'synthetic source')
        script = self.root/'scripts/render_docx_checked.py'; script.parent.mkdir()
        script.write_bytes(b'canonical synthetic renderer identity')
        config = self.root/'runtime.json'; b.atomic(config, {})
        self.output = self.attempt/'render'
        self.renderer = SimpleNamespace(__file__=str(script), CONFIG=config, render=Mock(), read=b.read)
        self.body = {'schema_version': 1, 'case_id': '001', 'attempt': self.attempt.name,
            'script': str(script), 'script_sha256': b.sha(script), 'config': str(config),
            'config_sha256': b.sha(config), 'input': str(self.source), 'input_sha256': b.sha(self.source),
            'output_dir': str(self.output), 'timeout': 180, 'reuse': False}
        self.request = self.requests/('b'*32+'.request.json'); bridge.write(self.request, self.body)
        attempt = {'id': self.attempt.name, 'directory': str(self.attempt), 'state': 'bound', 'stage': 'revise'}
        self.manifest = {'batch_dir': str(self.root), 'policy': {'controller_render': bridge.MODE},
            'projects': [{'id': '001', 'active_attempt': self.attempt.name, 'attempts': [attempt]}]}

    def test_response_reuses_completed_render_and_carries_no_visual_pass(self):
        self.output.mkdir(); b.atomic(self.output/'render.json', {'identity': 'completed'})
        self.renderer.render.return_value = {'pages': [1]}
        result = bridge.fulfill(self.manifest, self.request, self.renderer)
        self.assertTrue(result['ok']); self.assertEqual(result['visual_review'], 'not_run')
        self.assertTrue(self.renderer.render.call_args.kwargs['reuse'])
        self.assertEqual(b.read(bridge.response_path(self.request)), result)
        self.assertEqual(bridge.pending(self.manifest), [])

    def test_binding_options_and_foreign_paths_are_rejected(self):
        for mode in ('input_hash', 'script_hash', 'config_hash', 'attempt', 'foreign_output', 'hardlink'):
            with self.subTest(mode=mode):
                body = copy.deepcopy(self.body); manifest = copy.deepcopy(self.manifest)
                if mode=='input_hash': body['input_sha256']='0'*64
                if mode=='script_hash': body['script_sha256']='0'*64
                if mode=='config_hash': body['config_sha256']='0'*64
                if mode=='attempt': manifest['projects'][0]['active_attempt']='stale'
                if mode=='foreign_output': body['output_dir']=str(self.root/'foreign')
                link = self.attempt/'link.docx'
                if mode=='hardlink': os.link(self.source, link)
                try:
                    bridge.write(self.request, body)
                    result = bridge.fulfill(manifest, self.request, self.renderer)
                    self.assertFalse(result['ok'])
                finally:
                    if link.exists(): link.unlink()
        self.renderer.render.assert_not_called()

    def test_current_review_can_readonly_reuse_its_prior_attempt_render(self):
        self.output.mkdir()
        current = self.attempt.parent/('c'*32); current.mkdir()
        requests = current/'.controller-render-requests'; requests.mkdir()
        request = requests/self.request.name
        body = dict(self.body, attempt=current.name, reuse=True)
        bridge.write(request, body)
        attempt = {'id': current.name, 'directory': str(current), 'state': 'bound', 'stage': 'review',
                   'input_artifact': str(self.source), 'input_artifact_sha256': b.sha(self.source)}
        manifest = copy.deepcopy(self.manifest)
        manifest['projects'][0].update(active_attempt=current.name, attempts=[attempt])
        _, source, output = bridge.validate_request(manifest, request, Path(self.renderer.__file__), self.renderer.CONFIG)
        self.assertEqual(source, self.source); self.assertEqual(output, self.output)

    def test_conversion_failure_returns_explicit_bound_error(self):
        self.renderer.render.side_effect = RuntimeError('synthetic conversion failure')
        result = bridge.fulfill(self.manifest, self.request, self.renderer)
        self.assertFalse(result['ok']); self.assertIn('synthetic conversion failure', result['error'])
        self.assertEqual(result['request_sha256'], b.sha(self.request))
        self.assertEqual(b.read(bridge.response_path(self.request)), result)

    @unittest.skipUnless(os.name=='nt', 'Native Windows MAX_PATH regression')
    def test_max_path_atomic_response_uses_short_names(self):
        old_name = 'd'*32+'.response.json'
        padding = 223-len(str(self.root/'long'/old_name))-1
        folder = self.root/'long'/('文'*padding); folder.mkdir(parents=True)
        legacy = folder/old_name
        self.assertEqual(len(str(legacy)), 223)
        old_temp = legacy.with_name(legacy.name+'.tmp-'+uuid.uuid4().hex)
        self.assertEqual(len(str(old_temp)), 260)
        with self.assertRaises(FileNotFoundError): old_temp.write_text('synthetic path probe')
        request = folder/('d'*32+'.request.json')
        response = bridge.response_path(request)
        bridge.write(response, {'ok': False, 'visual_review': 'not_run'})
        self.assertLess(len(str(response)), len(str(request)))
        self.assertEqual(b.read(response)['visual_review'], 'not_run')


class PageProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='page-probe-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/'source.docx'; self.source.write_bytes(b'synthetic page source')
        self.render = self.root/'render'; self.render.mkdir()
        image = Image.new('RGB', (100, 200), 'white')
        ImageDraw.Draw(image).rectangle((20, 10, 80, 20), fill='black')
        image.save(self.render/'page-1.png'); image.save(self.render/'page-2.png')
        writer = PdfWriter(); writer.add_blank_page(100, 200); writer.add_blank_page(100, 200)
        writer.write(self.render/'source.pdf')
        self.evidence = {'status': 'rendered_pending_visual_review', 'input': str(self.source),
            'input_sha256': b.sha(self.source), 'pdf': {'file': 'source.pdf', 'sha256': b.sha(self.render/'source.pdf')},
            'pages': [{'file': f'page-{n}.png', 'sha256': b.sha(self.render/f'page-{n}.png')} for n in (1, 2)]}
        self.path = self.render/'render.json'; b.atomic(self.path, self.evidence)

    def test_probe_crops_original_pixels_without_rechecking_other_pages_or_self_passing(self):
        before = b.sha(self.render/'page-1.png')
        original = b.sha; reads=[]
        def sha(path): reads.append(Path(path)); return original(path)
        with patch.object(b, 'sha', side_effect=sha):
            result = page_probe.probe(self.path, self.source, 1, region='header', crop_dir=self.root/'crops',
                                      evidence_sha256=original(self.path), pdf_text=True)
        self.assertEqual(result['visual_review'], 'not_run'); self.assertFalse(result['rescaled'])
        with Image.open(result['crop']) as image: self.assertEqual(image.size, (100, 36))
        self.assertNotIn(self.render/'page-2.png', reads)
        self.assertEqual(original(self.render/'page-1.png'), before)
        self.assertEqual(result['original_png_sha256'], before)

    def test_wrong_source_evidence_page_and_modified_bytes_are_rejected(self):
        with self.assertRaises(ValueError): page_probe.probe(self.path, self.source, 3)
        with self.assertRaises(ValueError): page_probe.probe(self.path, self.source, 1, evidence_sha256='0'*64)
        with self.assertRaises(ValueError):
            page_probe.probe(self.path, self.source, 1, region='header', crop_dir=self.render/'crops')
        raw = self.source.read_bytes(); self.source.write_bytes(b'changed')
        with self.assertRaises(ValueError): page_probe.probe(self.path, self.source, 1)
        self.source.write_bytes(raw)
        (self.render/'page-1.png').write_bytes(b'changed')
        with self.assertRaises(ValueError): page_probe.probe(self.path, self.source, 1)

    def test_pdf_change_during_text_lookup_does_not_get_a_bound_result(self):
        from pypdf import PdfReader
        def changed(path):
            result = PdfReader(path)
            Path(path).write_bytes(b'changed while reading selected page')
            return result
        with patch('pypdf.PdfReader', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                page_probe.probe(self.path, self.source, 1, pdf_text=True)


if __name__ == '__main__': unittest.main()
