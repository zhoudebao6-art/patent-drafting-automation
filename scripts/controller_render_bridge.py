"""Batch-bound WPS I/O in the controller's existing host environment.

Workers keep workspace-write. This module changes no execution policy, sandbox,
Office settings, or review decision. Only a live, hash-bound case may request a
render in its own attempt; the controller uses the unchanged render function.
"""
import hashlib
import json
from pathlib import Path
import time
import uuid
import batch_tool as b

MODE = 'bound_requests_v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    # The atomic basename is independent of the long destination filename.
    # Appending UUID to a 223-character response path reached MAX_PATH=260.
    b.atomic(path, value)


def response_path(request):
    # Never make the response filename longer than the already valid request.
    request = Path(request)
    return request.with_name(request.name.replace('.request.json', '.rsp.json'))


def enabled(cwd=None):
    try:
        attempt = attempt_root(Path.cwd() if cwd is None else cwd)
    except ValueError:
        return False
    job = attempt/'cli-job.json'
    return job.is_file() and read(job).get('controller_render') == MODE


def pending(m):
    if m['policy'].get('controller_render') != MODE:
        return []
    requests = []
    for case in m['projects']:
        active = case.get('active_attempt')
        if not active:
            continue
        attempt = next(a for a in case['attempts'] if a['id'] == active)
        for request in (Path(attempt['directory'])/'.controller-render-requests').glob('*.request.json'):
            response = response_path(request)
            if not response.exists():
                requests.append(request)
    return sorted(requests)


def clean(path, root, *, regular=False):
    path, root = Path(path).absolute(), Path(root).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('Render bridge path escapes its case/attempt')
    for node in (path, *path.parents):
        if node.is_symlink() or (node.exists() and getattr(node, 'is_junction', lambda: False)()):
            raise ValueError('Linked render bridge paths prohibited')
        if node == root:
            break
    resolved = path.resolve(strict=regular)
    if not resolved.is_relative_to(root):
        raise ValueError('Resolved render bridge path escapes its root')
    if regular and (not resolved.is_file() or resolved.stat().st_nlink != 1):
        raise ValueError('Independent regular render bridge file required')
    return resolved


def attempt_root(cwd):
    for node in (Path(cwd).resolve(), *Path(cwd).resolve().parents):
        if node.parent.name == 'stages' and node.parent.parent.parent.name == 'cases':
            return node
    raise ValueError('Render bridge requires a case attempt working directory')


def request_render(args, script):
    attempt = attempt_root(Path.cwd())
    case = attempt.parent.parent
    source = clean(args.input, case, regular=True)
    output = clean(args.output_dir, attempt)
    config = Path(args.config).resolve(strict=True)
    expected_config = script.parent.parent/'references/runtime.json'
    if config != expected_config.resolve(strict=True):
        raise ValueError('Only the frozen render configuration is supported')
    requests = attempt/'.controller-render-requests'
    requests.mkdir(exist_ok=True)
    nonce = uuid.uuid4().hex
    request = requests/(nonce+'.request.json')
    response = response_path(request)
    body = {'schema_version': 1, 'case_id': case.name, 'attempt': attempt.name,
            'script': str(script), 'script_sha256': sha(script),
            'config': str(config), 'config_sha256': sha(config),
            'input': str(source), 'input_sha256': sha(source), 'output_dir': str(output),
            'timeout': args.timeout, 'reuse': bool(args.reuse)}
    write(request, body)
    request_sha = sha(request)
    until = time.monotonic()+args.timeout+600
    while time.monotonic() < until:
        if response.is_file():
            result = read(response)
            if result.get('request_sha256') != request_sha or sha(request) != request_sha:
                raise ValueError('Render bridge response/request identity mismatch')
            if not result.get('ok'):
                raise ValueError('Controller render failed: '+result.get('error', 'unknown failure'))
            import render_docx_checked as renderer
            evidence = renderer.verify_render(output, source,
                expected_evidence_sha256=result['evidence_sha256'])
            return evidence
        time.sleep(.25)
    raise ValueError('Controller render response timed out; request and original evidence retained')


def validate_request(m, request, script, config):
    batch = Path(m['batch_dir']).resolve(strict=True)
    request = clean(request, batch, regular=True)
    body = read(request)
    expected = {'schema_version','case_id','attempt','script','script_sha256',
                'config','config_sha256','input','input_sha256','output_dir','timeout','reuse'}
    if set(body) != expected or body['schema_version'] != 1:
        raise ValueError('Unknown render request schema')
    case = next((c for c in m['projects'] if c['id'] == body['case_id']), None)
    if case is None or case.get('active_attempt') != body['attempt']:
        raise ValueError('Render request does not belong to a live case attempt')
    attempt = next((a for a in case['attempts'] if a['id'] == body['attempt']), None)
    if attempt is None or attempt['state'] not in {'reserved','bound'}:
        raise ValueError('Render request attempt is no longer active')
    attempt_dir = clean(attempt['directory'], batch)
    if request.parent != attempt_dir/'.controller-render-requests':
        raise ValueError('Render request is outside its own attempt')
    source = clean(body['input'], batch/'cases'/case['id'], regular=True)
    output_root = batch/'cases'/case['id'] if attempt['stage'] == 'review' and body['reuse'] is True else attempt_dir
    output = clean(body['output_dir'], output_root)
    if output == attempt_dir:
        raise ValueError('Render output must be a separate directory')
    if attempt['stage'] == 'review' and (str(source) != attempt['input_artifact']
            or body['input_sha256'] != attempt['input_artifact_sha256']):
        raise ValueError('Review render is not the currently reviewed DOCX')
    if attempt['stage'] in {'draft','revise'} and not source.is_relative_to(attempt_dir):
        raise ValueError('Writing stage can render only its own new DOCX')
    if source.suffix.lower() != '.docx' or sha(source) != body['input_sha256']:
        raise ValueError('Render input DOCX hash mismatch')
    if (Path(body['script']).resolve() != script.resolve() or sha(script) != body['script_sha256']
            or Path(body['config']).resolve() != config.resolve() or sha(config) != body['config_sha256']):
        raise ValueError('Render tool/configuration identity mismatch')
    if type(body['timeout']) is not int or not 1 <= body['timeout'] <= 600 or type(body['reuse']) is not bool:
        raise ValueError('Invalid render options')
    return body, source, output


def fulfill(m, request, renderer):
    request = Path(request)
    response = response_path(request)
    consumed_sha = sha(request)
    try:
        body, source, output = validate_request(m, request, Path(renderer.__file__), renderer.CONFIG)
        # Canonical render/reuse already performs full byte/format validation.
        # A completed, interrupted handoff reuses its exact valid render rather
        # than converting the manuscript again. Mismatches remain rejected.
        result = renderer.render(source, output, renderer.read(renderer.CONFIG),
                                 body['timeout'], reuse=body['reuse'] or output.exists())
        if sha(request) != consumed_sha or sha(source) != body['input_sha256']:
            raise ValueError('Render request/input changed during conversion')
        value = {'ok': True, 'request_sha256': consumed_sha,
                 'evidence': str(output/'render.json'), 'evidence_sha256': sha(output/'render.json'),
                 'input_sha256': body['input_sha256'], 'pages':len(result['pages']),
                 'visual_review': 'not_run'}
    except Exception as exc:
        value = {'ok': False, 'request_sha256': consumed_sha, 'error':str(exc)}
    write(response, value)
    return value
