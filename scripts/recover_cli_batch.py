"""Import verified drafts rejected only by the legacy retry-event check into a new batch.

The old batch and its sealed evidence remain unchanged. No acceptance is imported.
"""
import argparse
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import uuid

import batch_tool as b
import cli_evidence as ce
import parallel_batch as q

LEGACY_REASON = 'CLI result was not accepted: Invalid CLI completion: CLI events contain an error or failed turn'
RECOVERED_NOTICE_REASON = 'CLI result was not accepted: Invalid CLI completion: CLI events contain an unknown, terminal or unrecovered error'


def recover_case(manifest, case_id, controller):
    """Import one sealed, completed draft into the same idle batch; retain rejection.

    Only the exact retry-notice rejection is eligible. This does not record the
    cancelled attempt as successful and never imports a review or acceptance.
    """
    manifest = Path(manifest).resolve(strict=True)
    with q.locked(manifest.parent/'cli-pool'/'owner.json', timeout=0), q.locked(manifest, timeout=0):
        original_bytes = manifest.read_bytes()
        original_sha = b.sha(manifest)
        m = q.load_manifest(manifest)
        if m['controller'] != controller:
            raise q.BatchError('Recovery controller mismatch')
        if any(c['active_attempt'] for c in m['projects']):
            raise q.BatchError('Wait for every old worker to exit before recovery')
        c = q.case_by_id(m, case_id)
        q.verify_case(m, c, original=True)
        prior = c.get('recovery_evidence')
        if prior:
            record = b.read(q.file_path(prior, q.case_root(m, c)))
            if record.get('event') != 'controller_cli_draft_recovery' or record.get('source_manifest') != str(manifest):
                raise q.BatchError('Existing recovery provenance differs')
            a = record['source_attempt']
        else:
            if c['status'] != 'failed' or not c['attempts']:
                raise q.BatchError('Recovery requires a rejected draft')
            a = c['attempts'][-1]
        if (a['state'] != 'cancelled' or a['stage'] != 'draft'
                or a.get('transport') != 'codex_cli' or a.get('reason') != RECOVERED_NOTICE_REASON):
            raise q.BatchError('Only exact retry-notice draft rejection can be recovered')
        if not any(old == a for old in c['attempts']):
            raise q.BatchError('Original rejected attempt changed')
        root = Path(a['directory'])
        if b.read(q.file_path(root/'cli-rejection.json', root)).get('reason') != RECOVERED_NOTICE_REASON:
            raise q.BatchError('Original rejection evidence differs')
        supervisor = root/'supervisor.json'
        if supervisor.exists():
            ce._require_stopped(b.read(supervisor))
        dispatch = a['dispatch_record']
        if (b.read(q.file_path(a['dispatch_evidence'], root)) != dispatch
                or any(dispatch.get('arguments', {}).get(k) != v for k, v in m['policy']['models']['draft'].items())):
            raise q.BatchError('Recovered dispatch differs from sealed evidence or frozen draft model')
        execution = ce.verify_completion(a, dispatch)
        receipt = execution['receipt']
        if (receipt.get('context') != q.context(m, c, a) or receipt.get('stage') != 'draft'
                or receipt.get('agent_id') != a['agent_id'] or receipt.get('verdict') != 'complete'
                or any(receipt.get(k) != v for k, v in m['policy']['models']['draft'].items())):
            raise q.BatchError('Recovered draft receipt does not match its original dispatch')
        if any(execution['session_id'] == old.get('cli_session_id') or
               execution['session_id'] in row.get('recovered_cli_session_ids', [])
               for row in m['projects'] if not prior or row['id'] != case_id
               for old in (row['attempts'] or [{}])):
            raise q.BatchError('CLI session already used')
        artifact = q.file_path(receipt['artifact'], root)
        if artifact.suffix.lower() != '.docx' or b.sha(artifact) != receipt.get('artifact_sha256'):
            raise q.BatchError('Recovered DOCX hash mismatch')
        b.verify_docx(artifact)
        if m['policy'].get('execution_optimization') == 'lean_execution_v24':
            from worker_receipt import precheck_receipt
            precheck_receipt(m, c, a, receipt)
        if prior:
            if record['original_receipt'] != receipt or record['cli_session_id'] != execution['session_id']:
                raise q.BatchError('Recovered receipt changed')
            if b.sha(q.file_path(record['source_manifest_snapshot'], Path(prior).parent)) != record['source_manifest_sha256']:
                raise q.BatchError('Original manifest snapshot changed')
            if b.sha(record['artifact']) != receipt['artifact_sha256']:
                raise q.BatchError('Recovered DOCX changed')
            return Path(record['artifact'])
        folder = q.case_root(m, c)/'stages'/'recovered-draft'
        folder.mkdir(exist_ok=True)

        def preserve(path, data):
            if path.exists():
                if q.file_path(path, folder).read_bytes() != data:
                    raise q.BatchError('Recovery output collision: '+path.name)
            else:
                with path.open('xb') as stream:
                    stream.write(data)

        output = folder/'output.docx'
        preserve(folder/'source-manifest.json', original_bytes)
        preserve(output, artifact.read_bytes())
        for name in ('evidence.json', 'source_map.json', 'terms.json', 'formula_registry.json', 'figure_registry.json'):
            candidate = root/name
            if candidate.is_file():
                preserve(folder/name, q.file_path(candidate, root).read_bytes())
        provenance = {'event': 'controller_cli_draft_recovery', 'source_manifest': str(manifest),
                      'source_manifest_sha256': original_sha, 'source_manifest_snapshot': str(folder/'source-manifest.json'),
                      'source_attempt': copy.deepcopy(a), 'original_receipt': receipt,
                      'cli_session_id': execution['session_id'],
                      'completion_seal_sha256': b.sha(root/'cli-completion.json'),
                      'recovery_tool': str(Path(__file__).resolve()), 'recovery_tool_sha256': b.sha(Path(__file__)),
                      'artifact': str(output), 'artifact_sha256': b.sha(output),
                      'acceptance_inherited': False, 'next_stage': ('independent_sol_review' if m['policy'].get('model_policy') == 'sol_review_xhigh_v20' else 'independent_astra_review'),
                      'next_model': copy.deepcopy(m['policy']['models']['review'])}
        record_path = folder/'recovery.json'
        if record_path.exists():
            if b.read(record_path) != provenance:
                raise q.BatchError('Recovery evidence collision')
        else:
            b.atomic(record_path, provenance)
        c.update(status='drafted', artifact=str(output), artifact_sha256=b.sha(output),
                 recovered_cli_session_ids=[execution['session_id']], recovery_evidence=str(record_path),
                 recovery_evidence_sha256=b.sha(record_path))
        c['receipts'].append(provenance)
        q.verify_case(m, c, original=True)
        if manifest.read_bytes() != original_bytes:
            raise q.BatchError('Batch changed during recovery')
        b.atomic(manifest, m)
        return output


def verified_drafts(manifest):
    m = q.load_manifest(manifest)
    if any(c['active_attempt'] for c in m['projects']):
        raise q.BatchError('Wait for every old worker to exit before recovery')
    results = []
    sessions = set()
    for c in m['projects']:
        q.verify_case(m, c, original=True)
        if c['status'] != 'failed' or not c['attempts']:
            raise q.BatchError('Recovery requires a failed draft for every selected case')
        a = c['attempts'][-1]
        if (a['state'] != 'cancelled' or a['stage'] != 'draft'
                or a.get('transport') != 'codex_cli' or a.get('reason') != LEGACY_REASON):
            raise q.BatchError('Only legacy retry-check draft rejection can be recovered')
        dispatch = a['dispatch_record']
        if (b.read(q.file_path(a['dispatch_evidence'], a['directory'])) != dispatch
                or any(dispatch.get('arguments', {}).get(k) != v
                       for k, v in m['policy']['models']['draft'].items())):
            raise q.BatchError('Recovered dispatch differs from sealed evidence or frozen draft model')
        execution = ce.verify_completion(a, a['dispatch_record'])
        r = execution['receipt']
        if (r.get('context') != q.context(m, c, a) or r.get('stage') != 'draft'
                or r.get('agent_id') != a['agent_id'] or r.get('verdict') != 'complete'
                or any(r.get(k) != v for k, v in m['policy']['models']['draft'].items())):
            raise q.BatchError('Recovered draft receipt does not match its original dispatch')
        if execution['session_id'] in sessions:
            raise q.BatchError('A CLI session cannot supply multiple recovered drafts')
        sessions.add(execution['session_id'])
        artifact = q.file_path(r['artifact'], a['directory'])
        if artifact.suffix.lower() != '.docx' or b.sha(artifact) != r.get('artifact_sha256'):
            raise q.BatchError('Recovered DOCX hash mismatch')
        b.verify_docx(artifact)
        results.append((c, a, execution, artifact))
    return m, results


def recover(manifest, work_root, controller, new_generation=False):
    manifest = Path(manifest).resolve(strict=True)
    # Prevent two recovery controllers and do not race a running pool.
    with ExitStack() as locks, q.locked(manifest.parent/'cli-pool'/'owner.json', timeout=0):
        pointer = manifest.parent/'cli-recovery.json'
        prior_pointer = None
        if pointer.exists():
            prior = b.read(pointer)
            if prior.get('controller') != controller:
                raise q.BatchError('Recovery already belongs to another controller')
            destination = Path(prior['manifest'])
            locks.enter_context(q.locked(destination, timeout=0))
            recovered = q.load_manifest(destination)
            source_sha = b.sha(manifest)
            if (prior.get('source_manifest_sha256') != source_sha
                    or recovered.get('controller') != controller
                    or recovered.get('recovered_from', {}).get('manifest') != str(manifest)
                    or recovered.get('recovered_from', {}).get('sha256') != source_sha):
                raise q.BatchError('Existing recovery pointer provenance changed')
            for case in recovered['projects']:
                q.verify_case(recovered, case, original=True)
            if not new_generation:
                return destination
            for case in recovered['projects']:
                expected = {'event': 'controller_defer', 'previous_status': 'drafted',
                            'reason': 'Case '+case['id']+' preparation failed: Foreign or changed current artifact',
                            'verdict': 'failed'}
                stages = q.case_root(recovered, case)/'stages'
                if (case['status'] != 'failed' or case['active_attempt'] or case['attempts']
                        or not case['receipts'] or case['receipts'][-1] != expected
                        or any(stages.glob('*/cli-launch-intent.json'))
                        or any(stages.glob('*/dispatch.json'))):
                    raise q.BatchError('New recovery generation requires only undispatched layout preparation failures')
            prior_pointer = pointer.read_bytes()
        elif new_generation:
            raise q.BatchError('New recovery generation requires an existing recovery pointer')
        original_sha = b.sha(manifest)
        old, drafts = verified_drafts(manifest)
        selection = {'projects': [{'title': c['title'],
                      'source_files': [s['relative_path'] for s in c['sources']],
                      'primary_source': str(Path(c['primary_source']).relative_to(old['input']))}
                     for c in old['projects']]}
        new_path = q.init(old['input'], work_root, selection, old['desktop'], controller)
        new = q.load_manifest(new_path)
        # Deliberately preserve the original content/model policies for this recovery.
        shutil.copyfile(manifest.parent/'rules.snapshot.json', new_path.parent/'rules.snapshot.json')
        shutil.copyfile(manifest.parent/'policy.snapshot.json', new_path.parent/'policy.snapshot.json')
        new.update(policy=copy.deepcopy(old['policy']), rule_version=old['rule_version'],
                   rules_sha256=old['rules_sha256'])
        if new.get('skill_snapshot') or old.get('skill_snapshot'):
            from stage_bundle import restore_snapshot
            restore_snapshot(old, new)
        for target, (source, a, execution, artifact) in zip(new['projects'], drafts):
            if (target['id'] != source['id'] or
                [(s['relative_path'], s['sha256']) for s in target['sources']] !=
                [(s['relative_path'], s['sha256']) for s in source['sources']]):
                raise q.BatchError('Original sources changed while preparing recovery')
            folder = q.case_root(new, target)/'stages'/'recovered-draft'
            folder.mkdir(parents=True)
            output = folder/'output.docx'
            shutil.copyfile(artifact, output)
            if b.sha(output) != execution['receipt']['artifact_sha256']:
                raise q.BatchError('Recovery copy differs from verified draft')
            # These are navigation/evidence records, never inherited review conclusions.
            for name in ('evidence.json', 'source_map.json', 'terms.json', 'formula_registry.json', 'figure_registry.json'):
                candidate = Path(a['directory'])/name
                if candidate.is_file():
                    shutil.copyfile(candidate, folder/name)
            provenance = {'event': 'controller_cli_draft_recovery',
                          'source_manifest': str(manifest), 'source_manifest_sha256': original_sha,
                          'source_attempt': copy.deepcopy(a), 'original_receipt': execution['receipt'],
                          'cli_session_id': execution['session_id'],
                          'completion_seal_sha256': b.sha(Path(a['directory'])/'cli-completion.json'),
                          'artifact': str(output), 'artifact_sha256': b.sha(output),
                          'acceptance_inherited': False, 'next_stage': ('independent_sol_review' if new['policy'].get('model_policy') == 'sol_review_xhigh_v20' else 'independent_astra_review'),
                          'next_model': copy.deepcopy(new['policy']['models']['review'])}
            b.atomic(folder/'recovery.json', provenance)
            target.update(status='drafted', artifact=str(output), artifact_sha256=b.sha(output),
                          recovered_cli_session_ids=[execution['session_id']],
                          recovery_evidence=str(folder/'recovery.json'),
                          recovery_evidence_sha256=b.sha(folder/'recovery.json'))
            target['receipts'].append(provenance)
        if b.sha(manifest) != original_sha:
            raise q.BatchError('Old batch changed during recovery')
        # Recheck all sealed old evidence before publishing the new manifest.
        verified_drafts(manifest)
        new['recovered_from'] = {'manifest': str(manifest), 'sha256': original_sha,
                                 'scope': 'verified drafts only; no review acceptance'}
        b.atomic(new_path, new)
        for case in new['projects']:
            q.verify_case(new, case, original=True)
        if prior_pointer is not None:
            history = pointer.with_name('cli-recovery.history.'+uuid.uuid4().hex+'.json')
            with history.open('xb') as stream:
                stream.write(prior_pointer)
        b.atomic(pointer, {'controller': controller, 'manifest': str(new_path),
                           'source_manifest_sha256': original_sha})
        return new_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--work-root')
    parser.add_argument('--controller', required=True)
    parser.add_argument('--case', help='Recover one exact retry-notice rejection in the same idle batch')
    parser.add_argument('--new-generation', action='store_true',
                        help='Replace only an undispatched recovery that failed the old artifact layout check')
    args = parser.parse_args()
    if args.case:
        if args.new_generation or args.work_root:
            parser.error('--case cannot be combined with --work-root or --new-generation')
        result = recover_case(args.manifest, args.case, args.controller)
    else:
        if not args.work_root:
            parser.error('--work-root is required without --case')
        result = recover(args.manifest, args.work_root, args.controller, args.new_generation)
    print(json.dumps(str(result), ensure_ascii=False))
