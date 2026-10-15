import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cli_evidence as ce


class CliEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.exe = str(self.root / 'codex.exe')
        self.model = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'}
        self.attempt = {'id': 'attempt1', 'directory': str(self.root),
                        'context': {'case_id': '001', 'attempt': 'attempt1'}}
        self.identity = {'pid': 123, 'created': 23456789, 'executable': self.exe}
        self.prompt = '只处理本案。\n请返回真实回执。'
        (self.root / 'prompt.txt').write_bytes(self.prompt.encode('utf-8'))
        self.dispatch = {
            'tool': 'codex.exec', 'context': self.attempt['context'],
            'arguments': {'argv': ce.build_command(self.exe, self.root, **self.model),
                          'cwd': str(self.root), **self.model},
            'result': self.identity, 'prompt_sha256': self.sha('prompt.txt'),
            'agent_id': 'cli:attempt1'}
        self.session_id = '01a138c4-5fcd-7053-b553-f763330b7180'
        self.receipt = {'context': self.attempt['context'], 'verdict': 'complete'}
        self.events = [
            {'type': 'thread.started', 'thread_id': self.session_id},
            {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'agent_message',
                                             'text': json.dumps(self.receipt)}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 1}}]
        self.session = [
            {'type': 'session_meta', 'payload': {'id': self.session_id, 'source': 'exec', 'cwd': str(self.root)}},
            {'type': 'turn_context', 'payload': {'model': self.model['model'], 'effort': 'high',
                                              'cwd': str(self.root), 'sandbox_policy': {'type': 'workspace-write'}}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                               'content': [{'type': 'input_text', 'text': self.prompt}]}}]

    def sha(self, name):
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def seal(self):
        session = self.session + [{'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                   'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': json.dumps(self.receipt)}]}}]
        for name, rows in (('events.jsonl', self.events), ('session.jsonl', session)):
            (self.root / name).write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
        (self.root / 'worker-final.json').write_text(json.dumps(self.receipt), encoding='utf-8')
        (self.root / 'cli-exit.json').write_text(json.dumps({'pid': self.identity['pid'], 'created': self.identity['created'], 'returncode': 0}), encoding='utf8')
        meta = {'exit_code': 0, 'pid': self.identity['pid'], 'created': self.identity['created'],
                'files': {n: self.sha(n) for n in ce.COMPLETION_FILES}}
        (self.root / 'cli-completion.json').write_text(json.dumps(meta), encoding='utf-8')
        return meta

    def test_command_and_dispatch_use_explicit_safe_model_and_prompt(self):
        self.assertEqual(self.dispatch['arguments']['argv'], [
            self.exe, 'exec', '--model', 'gpt-6.1-sol', '-c', 'model_reasoning_effort="high"',
            '--sandbox', 'workspace-write', '--skip-git-repo-check', '--json', '--color', 'never',
            '-C', str(self.root), '-o', str(self.root / 'worker-final.json'), '-'])
        with patch.object(ce, 'process_identity', return_value=self.identity):
            self.assertEqual(ce.verify_dispatch(self.dispatch, self.attempt, self.model), 'cli:attempt1')

    def test_dispatch_rejects_changed_model_effort_context_prompt_and_unsafe_argv(self):
        for key, value in [('model', 'gpt-6-astra'), ('reasoning_effort', 'low'),
                           ('cwd', str(self.root.parent)), ('argv', ['codex', 'exec', '--yolo'])]:
            ev = copy.deepcopy(self.dispatch)
            ev['arguments'][key] = value
            with self.subTest(key=key), patch.object(ce, 'process_identity', return_value=self.identity), self.assertRaises(ValueError):
                ce.verify_dispatch(ev, self.attempt, self.model)
        for key, value in [('context', {'case_id': 'other'}), ('prompt_sha256', '0' * 64), ('agent_id', 'cli:other')]:
            ev = copy.deepcopy(self.dispatch); ev[key] = value
            with self.subTest(key=key), patch.object(ce, 'process_identity', return_value=self.identity), self.assertRaises(ValueError):
                ce.verify_dispatch(ev, self.attempt, self.model)

    def test_dispatch_rejects_missing_reused_or_different_process(self):
        for identity in (None, dict(self.identity, created=1), dict(self.identity, executable=str(self.root / 'other.exe'))):
            with self.subTest(identity=identity), patch.object(ce, 'process_identity', return_value=identity), self.assertRaises(ValueError):
                ce.verify_dispatch(self.dispatch, self.attempt, self.model)

    def test_completion_returns_actual_session_and_receipt(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertEqual(ce.verify_completion(self.attempt, self.dispatch),
                             {'session_id': self.session_id, 'receipt': self.receipt})

    def test_collection_scope_shares_parsing_but_not_mutable_results(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None), \
             patch.object(ce, '_verify_completion_full', wraps=ce._verify_completion_full) as full:
            with ce.completion_scope(self.attempt, self.dispatch):
                first = ce.verify_completion(self.attempt, self.dispatch)
                first['receipt']['verdict'] = 'changed by caller'
                self.assertEqual(ce.verify_completion(self.attempt, self.dispatch)['receipt'], self.receipt)
            self.assertEqual(full.call_count, 1)
            with ce.completion_scope(self.attempt, self.dispatch):
                ce.verify_completion(self.attempt, self.dispatch)
            self.assertEqual(full.call_count, 2)
            ce.verify_completion(self.attempt, self.dispatch)
            self.assertEqual(full.call_count, 3)

    def test_collection_scope_rejects_changed_sealed_bytes_and_file_identity(self):
        for name in (*ce.COMPLETION_FILES, 'cli-completion.json'):
            for replace in (False, True):
                self.seal()
                path = self.root / name
                with self.subTest(name=name, replace=replace), \
                     patch.object(ce, 'process_identity', return_value=None), \
                     ce.completion_scope(self.attempt, self.dispatch):
                    ce.verify_completion(self.attempt, self.dispatch)
                    data = path.read_bytes()
                    if replace:
                        temporary = self.root / 'replacement.tmp'
                        temporary.write_bytes(data)
                        os.replace(temporary, path)
                    else:
                        path.write_bytes(data + b' ')
                    with self.assertRaises(ValueError):
                        ce.verify_completion(self.attempt, self.dispatch)
                    path.write_bytes(data)

    def test_collection_scope_rejects_resealed_changes_and_wrong_provenance(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None), \
             ce.completion_scope(self.attempt, self.dispatch):
            ce.verify_completion(self.attempt, self.dispatch)
            for key, value in [('id', 'other'), ('context', {'case_id': 'other'}),
                               ('input_artifact_sha256', 'changed')]:
                attempt = dict(self.attempt, **{key: value})
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'scope provenance'):
                    ce.verify_completion(attempt, self.dispatch)
            dispatch = copy.deepcopy(self.dispatch)
            dispatch['arguments']['reasoning_effort'] = 'xhigh'
            with self.assertRaisesRegex(ValueError, 'scope provenance'):
                ce.verify_completion(self.attempt, dispatch)
            self.events[-1]['usage'] = {'input_tokens': 2}
            self.seal()
            with self.assertRaisesRegex(ValueError, 'seal changed'):
                ce.verify_completion(self.attempt, self.dispatch)

    def test_collection_scope_rechecks_live_process_and_clears_on_exception(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None), \
             patch.object(ce, '_verify_completion_full', wraps=ce._verify_completion_full) as full:
            with self.assertRaisesRegex(RuntimeError, 'interrupt'):
                with ce.completion_scope(self.attempt, self.dispatch):
                    ce.verify_completion(self.attempt, self.dispatch)
                    with patch.object(ce, 'process_identity', return_value=self.identity):
                        with self.assertRaisesRegex(ValueError, 'still live'):
                            ce.verify_completion(self.attempt, self.dispatch)
                    raise RuntimeError('interrupt')
            ce.verify_completion(self.attempt, self.dispatch)
            self.assertEqual(full.call_count, 2)

    def retry_event(self, message=None):
        return {'type': 'error', 'message': message if message is not None else
                'Reconnecting... 2/5 (stream disconnected before completion: transport interrupted)'}

    def test_completion_accepts_only_recovered_connection_retries(self):
        self.events[2:2] = [self.retry_event(), self.retry_event(
            'Reconnecting... 5/5 (stream disconnected before completion: transport interrupted)')]
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertEqual(ce.verify_completion(self.attempt, self.dispatch),
                             {'session_id': self.session_id, 'receipt': self.receipt})
            self.assertEqual(ce.verify_dispatch(self.dispatch, self.attempt, self.model), 'cli:attempt1')

    def test_completion_rejects_unknown_or_malformed_retry_errors(self):
        base = copy.deepcopy(self.events)
        errors = [self.retry_event(message) for message in (
            'failed', 'Reconnecting... 2/5 (some other failure)',
            'Reconnecting... 0/5 (stream disconnected before completion: interrupted)',
            'Reconnecting... 6/5 (stream disconnected before completion: interrupted)',
            'Reconnecting... 2/5 (stream disconnected before completion: )',
            'Reconnecting... 2/5 (stream disconnected before completion: interrupted)\nturn failed',
            'Reconnecting... 2/5 (stream disconnected before completion: interrupted) trailing',
            None)]
        errors[-1]['message'] = None
        errors.append(dict(self.retry_event(), fatal=True))
        for error in errors:
            self.events = base[:2] + [error] + base[2:]
            self.seal()
            with self.subTest(error=error), patch.object(ce, 'process_identity', return_value=None):
                with self.assertRaises(ValueError):
                    ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_rejects_retry_outside_turn_or_without_new_final(self):
        base = copy.deepcopy(self.events)
        # Even an identical pre-error receipt and a genuine session final do not
        # prove recovery unless a new completed CLI final follows the retry.
        for index in (0, 1, 3, 4):
            self.events = base[:index] + [self.retry_event()] + base[index:]
            self.seal()
            with self.subTest(index=index), patch.object(ce, 'process_identity', return_value=None):
                with self.assertRaises(ValueError):
                    ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_requires_one_ordered_turn_even_with_recovered_retry(self):
        base = copy.deepcopy(self.events)
        valid = base[:2] + [self.retry_event()] + base[2:]
        mutations = [valid + [{'type': 'turn.failed'}], valid + [base[-1]],
                     valid[:2] + [base[1]] + valid[2:], valid[0:1] + valid[2:],
                     valid[:-1], [base[0], base[-1], base[1], self.retry_event(), base[2]],
                     base[:2] + [self.retry_event(), self.retry_event('unknown error')] + base[2:]]
        for index, rows in enumerate(mutations):
            self.events = rows
            self.seal()
            with self.subTest(index=index), patch.object(ce, 'process_identity', return_value=None):
                with self.assertRaises(ValueError):
                    ce.verify_completion(self.attempt, self.dispatch)

    def test_dispatch_recovery_requires_complete_successful_evidence(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertEqual(ce.verify_dispatch(self.dispatch, self.attempt, self.model), 'cli:attempt1')
        (self.root / 'worker-final.json').write_text('{}', encoding='utf-8')
        with patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
            ce.verify_dispatch(self.dispatch, self.attempt, self.model)

    def test_completion_rejects_live_original_but_accepts_pid_reuse(self):
        self.seal()
        with patch.object(ce, 'process_identity', return_value=self.identity), self.assertRaises(ValueError):
            ce.verify_completion(self.attempt, self.dispatch)
        with patch.object(ce, 'process_identity', return_value=dict(self.identity, created=999)):
            ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_rejects_each_tampered_file(self):
        for name in ('events.jsonl', 'session.jsonl', 'worker-final.json', 'prompt.txt'):
            self.seal()
            original = (self.root / name).read_bytes()
            (self.root / name).write_bytes(original + b' ')
            with self.subTest(name=name), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_completion(self.attempt, self.dispatch)
            (self.root / name).write_bytes(original)

    def test_completion_rejects_wrong_turn_model_effort_cwd_or_sandbox(self):
        base = copy.deepcopy(self.session)
        for key, value in [('model', 'gpt-6-astra'), ('effort', 'low'), ('cwd', str(self.root.parent)),
                           ('sandbox_policy', {'type': 'danger-full-access'})]:
            self.session = copy.deepcopy(base); self.session[1]['payload'][key] = value
            self.seal()
            with self.subTest(key=key), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_rejects_wrong_prompt_session_and_non_exec_source(self):
        base = copy.deepcopy(self.session)
        for key, value in [('id', '01a138c4-5fcd-7053-b553-f763330b7181'), ('source', 'cli'), ('cwd', str(self.root.parent))]:
            self.session = copy.deepcopy(base); self.session[0]['payload'][key] = value
            self.seal()
            with self.subTest(key=key), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_completion(self.attempt, self.dispatch)
        self.session = copy.deepcopy(base)
        self.session[2]['payload']['content'][0]['text'] += '\nextra'
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
            ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_rejects_error_failed_missing_completed_duplicate_or_invalid_thread(self):
        base = copy.deepcopy(self.events)
        mutations = [base + [{'type': 'error', 'message': 'failed'}],
                     base + [{'type': 'turn.failed'}], base[:-1], base + [base[0]],
                     [dict(base[0], thread_id='not-a-uuid'), *base[1:]]]
        for rows in mutations:
            self.events = rows; self.seal()
            with self.subTest(rows=rows), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_completion(self.attempt, self.dispatch)

    def test_completion_rejects_final_receipt_disagreement_and_bad_exit_identity(self):
        self.events[2]['item']['text'] = '{"verdict":"other"}'
        self.seal()
        with patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
            ce.verify_completion(self.attempt, self.dispatch)
        self.events[2]['item']['text'] = json.dumps(self.receipt)
        for key, value in [('exit_code', 1), ('exit_code', False), ('pid', 456), ('created', 1)]:
            meta = self.seal(); meta[key] = value
            (self.root / 'cli-completion.json').write_text(json.dumps(meta), encoding='utf-8')
            with self.subTest(key=key, value=value), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_completion(self.attempt, self.dispatch)

    def test_stop_requires_exit_identity_and_confirmed_original_stopped(self):
        ev = {'tool': 'codex.exec.exit', 'context': self.attempt['context'], 'pid': 123,
              'created': self.identity['created'], 'confirmed_stopped': True, 'returncode': 0}
        with patch.object(ce, 'process_identity', return_value=None):
            ce.verify_stop(ev, self.attempt, self.dispatch)
        with patch.object(ce, 'process_identity', return_value=self.identity), self.assertRaises(ValueError):
            ce.verify_stop(ev, self.attempt, self.dispatch)
        for key, value in [('tool', 'unknown'), ('context', {}), ('pid', 456), ('created', 1),
                           ('confirmed_stopped', False), ('returncode', None), ('returncode', False)]:
            bad = dict(ev); bad[key] = value
            with self.subTest(key=key), patch.object(ce, 'process_identity', return_value=None), self.assertRaises(ValueError):
                ce.verify_stop(bad, self.attempt, self.dispatch)

    def test_stop_can_release_exited_failed_worker_even_if_prompt_was_damaged(self):
        ev = {'tool': 'codex.exec.exit', 'context': self.attempt['context'], 'pid': 123,
              'created': self.identity['created'], 'confirmed_stopped': True, 'returncode': 1}
        (self.root / 'prompt.txt').unlink()
        with patch.object(ce, 'process_identity', return_value=None):
            self.assertTrue(ce.verify_stop(ev, self.attempt, self.dispatch))

    @unittest.skipUnless(os.name == 'nt', 'Windows native identity')
    def test_hardlinked_evidence_cannot_be_accepted_even_with_matching_hash(self):
        self.seal()
        os.link(self.root/'events.jsonl',self.root/'linked-events.jsonl')
        with patch.object(ce,'process_identity',return_value=None):
            with self.assertRaisesRegex(ValueError,'Hard-linked'):
                ce.verify_completion(self.attempt,self.dispatch)

    def test_actual_exit_and_actual_final_cannot_be_overruled_by_resealing(self):
        for corrupt in ('exit','final','aborted'):
            with self.subTest(corrupt=corrupt):
                meta=self.seal()
                if corrupt=='exit':
                    path=self.root/'cli-exit.json'; data=json.loads(path.read_text()); data['returncode']=17
                    path.write_text(json.dumps(data),encoding='utf8')
                else:
                    path=self.root/'session.jsonl'; rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
                    if corrupt=='final': rows[-1]['payload']['content'][0]['text']='{"wrong":true}'
                    else: rows.append({'type':'event_msg','payload':{'type':'turn_aborted'}})
                    path.write_text('\n'.join(json.dumps(x) for x in rows),encoding='utf8')
                meta['files'][path.name]=self.sha(path.name)
                (self.root/'cli-completion.json').write_text(json.dumps(meta),encoding='utf8')
                with patch.object(ce,'process_identity',return_value=None):
                    with self.assertRaises(ValueError): ce.verify_completion(self.attempt,self.dispatch)

    def test_native_process_identity_current_and_absent(self):
        import sys
        identity = ce.process_identity(os.getpid())
        self.assertEqual(identity['pid'], os.getpid())
        self.assertIs(type(identity['created']), int)
        # Windows venv python.exe may redirect to the base runtime process.
        self.assertTrue(os.path.samefile(identity['executable'], sys._base_executable))
        self.assertIsNone(ce.process_identity(0xFFFFFFFE))


if __name__ == '__main__':
    unittest.main()
