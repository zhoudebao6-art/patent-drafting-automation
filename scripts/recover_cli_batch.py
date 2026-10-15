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
    parser.add_argument('--work-root', required=True)
    parser.add_argument('--controller', required=True)
    parser.add_argument('--new-generation', action='store_true',
                        help='Replace only an undispatched recovery that failed the old artifact layout check')
    args = parser.parse_args()
    print(json.dumps(str(recover(args.manifest, args.work_root, args.controller, args.new_generation)), ensure_ascii=False))
