"""Controller-owned case preparation; cached data never grants review approval."""
from pathlib import Path
import json
import inspect_docx as inspector
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
import hashlib
import uuid

# Source container/XML errors should be queue refusals, not uncaught tracebacks.
SOURCE_ERRORS = (BadZipFile, ParseError)


def save_new(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def source_index(report):
    """Navigation only. Full OMML and source bytes remain the authority."""
    return {'input': report['input'], 'sha256': report['sha256'],
            'content_boundary': 'linear_text is a search index, not a complete mathematical expression; inspect each relevant omml_xml and source display',
            'math_objects': [{'id': f['id'], 'part': f['part'], 'paragraph': f['paragraph'],
                              'linear_text': f['linear_text'],
                              'structure_elements': f.get('math_evidence', {}).get('structure_elements', [])}
                             for f in report['formulas']],
            'issues': report['issues'], 'media': report['media'],
            'embeddings': report['embeddings'],
            'formula_count_status': report['formula_count_status']}


def shared_source_index(case, case_root, summary):
    """Controller-sealed navigation; never recompile it in every stage."""
    identity = {'inspection_sha256': inspector.file_sha(summary['inspection']),
                'mapper_sha256': inspector.file_sha(__file__)}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    records = case.setdefault('source_navigation', {})
    saved = records.get(key)
    directory = inspector.local_path(case_root/'source-navigation')
    if saved:
        target = inspector.local_path(saved['path'])
        if target.parent != directory or saved['identity'] != identity or inspector.file_sha(target) != saved['sha256']:
            raise ValueError('Shared source navigation changed')
    else:
        directory.mkdir(exist_ok=True)
        target = directory/(key[:20]+'-'+uuid.uuid4().hex[:8]+'.json')
        report = json.loads(Path(summary['inspection']).read_text(encoding='utf-8'))
        save_new(target, source_index(report))
        saved = {'path': str(target), 'sha256': inspector.file_sha(target), 'identity': identity}
        records[key] = saved
    return {'index': saved['path'], 'index_sha256': saved['sha256'], 'index_identity': identity}


def read_existing_inspection(source, cache_root, kind):
    """Read a prior writer's verified cache without modifying its directory."""
    identity = inspector.cache_identity(source, kind, True)
    key = inspector.cache_key(identity)
    root = inspector.local_path(cache_root)
    for candidate in sorted((root/key).glob('generation-*')):
        try:
            result = inspector.verified_cache(candidate, identity)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if (inspector.file_sha(source) != identity['source_sha256']
                or not inspector.extractor_unchanged(identity)):
            raise ValueError('Artifact or extractor changed during handoff preparation')
        return inspector.inspection_summary(result, candidate, 'hit', 'prior_writer_verified_cache')
    return None


def seal_inspection(summary):
    """Bind verified cache metadata so re-signing changed outputs cannot hide edits."""
    metadata = inspector.local_path(Path(summary['output_directory'])/'cache.json')
    return dict(summary, cache_evidence_sha256=inspector.file_sha(metadata))


def render_candidates(artifact, expected_sha):
    """Only advertise the current artifact writer's evidence, never stale reviews."""
    if not artifact:
        return []
    source = inspector.local_path(artifact)
    root = source.parent
    candidates = []
    # Bounded, conventional paths avoid searching other attempts or whole batches.
    for folder in sorted(root.glob('render*')):
        if not folder.is_dir():
            continue
        try:
            path = inspector.local_path(folder/'render.json')
            if not path.is_relative_to(root):
                continue
            evidence = json.loads(path.read_text(encoding='utf-8-sig'))
            if (not isinstance(evidence, dict) or evidence.get('status') != 'rendered_pending_visual_review'
                    or evidence.get('input_sha256') != expected_sha
                    or str(inspector.local_path(evidence['input'])) != str(source)):
                continue
            candidates.append({'evidence': str(path), 'evidence_sha256': inspector.file_sha(path),
                               'backend': evidence.get('conversion_backend', 'configured_renderer'),
                               'status': 'candidate_requires_integrity_and_visual_review'})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return candidates


def prepare(m, case, attempt):
    """Called under the controller lock, before dispatch and reservation commit."""
    case_root = inspector.local_path(Path(m['batch_dir'])/'cases'/case['id'])
    attempt_root = inspector.local_path(attempt['directory'])
    if not attempt_root.is_relative_to(case_root/'stages'):
        raise ValueError('Handoff must belong to the reserved case attempt')
    out = attempt_root/'prepared'
    out.mkdir(exist_ok=False)
    pipeline = None
    if m.get('skill_snapshot') or m['policy'].get('worker_preparation'):
        from stage_bundle import worker_paths
        pipeline = worker_paths(m, attempt['stage'])
    lean = m['policy'].get('execution_optimization') == 'lean_execution_v24'
    rules_path = inspector.local_path(Path(m['batch_dir'])/'rules.snapshot.json')
    if inspector.file_sha(rules_path) != m['rules_sha256']:
        raise ValueError('Frozen rules changed during preparation')
    if lean:
        active_path = Path(pipeline['rules_active'])
    else:
        rules = json.loads(rules_path.read_text(encoding='utf-8-sig'))
        active = [r for r in rules['rules'] if r['status'] == 'active']
        active_path = out/'rules.active.json'
        save_new(active_path, {'rule_version': m['rule_version'], 'snapshot': str(rules_path),
                              'snapshot_sha256': m['rules_sha256'], 'rules': active,
                              'scope_note': 'All active rules retained including exceptions and evidence; apply their recorded scopes. History is not needed for ordinary drafting.'})
    reports = []
    for i, src in enumerate(case['sources'], 1):
        source = inspector.local_path(src['snapshot_path'])
        if not source.is_relative_to(case_root/'sources') or inspector.file_sha(source) != src['sha256']:
            raise ValueError('Foreign or changed source snapshot')
        if source.suffix.lower() != '.docx':
            reports.append({'input': str(source), 'sha256': src['sha256'], 'status': 'read_original_non_docx'})
            continue
        summary = inspector.run_inspection(source, case_root/'source-cache', 'proposal', True, True)
        if lean:
            navigation = shared_source_index(case, case_root, summary)
        else:
            report = json.loads(Path(summary['inspection']).read_text(encoding='utf-8'))
            index = out/f'source-{i:03}-index.json'
            save_new(index, source_index(report))
            navigation = {'index': str(index)}
        reports.append(dict(seal_inspection(summary), **navigation))
    artifact_inspection = None
    if attempt.get('input_artifact'):
        artifact = inspector.local_path(attempt['input_artifact'])
        if not artifact.is_relative_to(case_root/'stages') or inspector.file_sha(artifact) != attempt['input_artifact_sha256']:
            raise ValueError('Foreign or changed current artifact')
        artifact_inspection = read_existing_inspection(artifact, artifact.parent/'inspection-cache', 'draft')
        if artifact_inspection is None:
            artifact_inspection = inspector.run_inspection(artifact, attempt_root/'inspection-cache', 'draft', True, True)
        artifact_inspection = seal_inspection(artifact_inspection)
    result = {'status': 'prepared_data_only', 'context': attempt['context'],
              'rules_active': str(active_path), 'source_inspections': reports,
              'input_inspection': artifact_inspection,
              'render_candidates': render_candidates(attempt.get('input_artifact'), attempt.get('input_artifact_sha256')),
              'draft_inspection_cache': str(attempt_root/'inspection-cache'),
              'boundary': 'Read content and index first; load full OMML for relevant IDs. Reuse data, never semantic approval. Verify render candidates before viewing; invalid or distorted output cannot pass.'}
    if pipeline:
        result['pipeline'] = pipeline
    result['prepared_files'] = [{'file': p.name, 'sha256': inspector.file_sha(p)} for p in sorted(out.glob('*.json'))]
    path = out/'handoff.json'
    save_new(path, result)
    return result, {'path': str(path), 'sha256': inspector.file_sha(path)}


def verify_inspection(summary, source, expected_sha, kind, allowed_roots):
    """Recheck evidence bytes at dispatch binding without parsing the DOCX again."""
    source = inspector.local_path(source)
    identity = inspector.cache_identity(source, kind, True)
    if identity['source_sha256'] != expected_sha:
        raise ValueError('Prepared inspection source changed')
    directory = inspector.local_path(summary['output_directory'])
    if (not any(directory.parent == inspector.local_path(root)/inspector.cache_key(identity) for root in allowed_roots)
            or not directory.name.startswith('generation-')):
        raise ValueError('Prepared inspection belongs to a foreign cache')
    if (inspector.local_path(summary['input']) != source
            or inspector.local_path(summary['inspection']) != directory/'inspection.json'
            or inspector.local_path(summary['content']) != directory/'content.txt'):
        raise ValueError('Prepared inspection paths changed')
    if inspector.file_sha(inspector.local_path(directory/'cache.json')) != summary['cache_evidence_sha256']:
        raise ValueError('Prepared cache metadata changed')
    report = inspector.verified_cache(directory, identity)
    if (inspector.file_sha(source) != expected_sha
            or not inspector.extractor_unchanged(identity)):
        raise ValueError('Prepared source or extractor changed during binding')
    return report


def verify_prepared(m, case, attempt):
    record = attempt.get('prepared_handoff')
    if not record:
        return
    root = inspector.local_path(Path(attempt['directory'])/'prepared')
    path = inspector.local_path(record['path'])
    if path != root/'handoff.json' or inspector.file_sha(path) != record['sha256']:
        raise ValueError('Prepared handoff changed')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['context'] != attempt['context']:
        raise ValueError('Prepared handoff context mismatch')
    if m.get('skill_snapshot') or m['policy'].get('worker_preparation'):
        from stage_bundle import worker_paths
        if data.get('pipeline') != worker_paths(m, attempt['stage']):
            raise ValueError('Prepared frozen pipeline changed')
        if m['policy'].get('execution_optimization') == 'lean_execution_v24' and data['rules_active'] != data['pipeline']['rules_active']:
            raise ValueError('Prepared shared rules path changed')
    for entry in data['prepared_files']:
        target = inspector.local_path(root/entry['file'])
        if target.parent != root or inspector.file_sha(target) != entry['sha256']:
            raise ValueError('Prepared rule/index file changed')
    case_root = inspector.local_path(Path(m['batch_dir'])/'cases'/case['id'])
    if len(data['source_inspections']) != len(case['sources']):
        raise ValueError('Prepared source inventory mismatch')
    for summary, source in zip(data['source_inspections'], case['sources']):
        path = inspector.local_path(source['snapshot_path'])
        if not path.is_relative_to(case_root/'sources'):
            raise ValueError('Prepared source belongs to a foreign case')
        if path.suffix.lower() == '.docx':
            verify_inspection(summary, path, source['sha256'], 'proposal', [case_root/'source-cache'])
            if m['policy'].get('execution_optimization') == 'lean_execution_v24':
                index = inspector.local_path(summary['index'])
                identity = {'inspection_sha256': inspector.file_sha(summary['inspection']),
                            'mapper_sha256': inspector.file_sha(__file__)}
                key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                expected = {'path': str(index), 'sha256': summary['index_sha256'], 'identity': identity}
                if (index.parent != case_root/'source-navigation' or summary['index_identity'] != identity
                        or case.get('source_navigation', {}).get(key) != expected
                        or inspector.file_sha(index) != summary['index_sha256']):
                    raise ValueError('Prepared shared source navigation changed')
        elif (inspector.local_path(summary['input']) != path or inspector.file_sha(path) != source['sha256']
              or summary['sha256'] != source['sha256'] or summary['status'] != 'read_original_non_docx'):
            raise ValueError('Prepared non-DOCX source changed')
    input_report = None
    if attempt.get('input_artifact'):
        artifact = inspector.local_path(attempt['input_artifact'])
        if not artifact.is_relative_to(case_root/'stages'):
            raise ValueError('Prepared artifact belongs to a foreign case')
        input_report = verify_inspection(data['input_inspection'], artifact, attempt['input_artifact_sha256'], 'draft',
                                         [artifact.parent/'inspection-cache', Path(attempt['directory'])/'inspection-cache'])
    elif data['input_inspection'] is not None:
        raise ValueError('Unexpected prepared input inspection')
    return {'handoff': data, 'input_report': input_report}
