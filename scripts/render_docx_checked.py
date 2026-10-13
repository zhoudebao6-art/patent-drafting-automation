#!/usr/bin/env python3
"""Run the canonical DOCX renderer with verified local paths and hash evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

CONFIG = Path(__file__).resolve().parent.parent/'references/runtime.json'

class RenderError(ValueError):
    pass

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def preflight(config):
    if config.get('schema_version') != 1:
        raise RenderError('Unsupported runtime configuration')
    paths = {name: Path(config[name]) for name in ('python', 'renderer', 'libreoffice', 'poppler_dir')}
    if any(not p.is_absolute() for p in paths.values()):
        raise RenderError('Runtime paths must be absolute')
    for key in ('python', 'renderer', 'libreoffice'):
        if not paths[key].is_file():
            raise RenderError('Missing configured runtime: ' + key)
    for name in ('pdfinfo.exe', 'pdftoppm.exe'):
        if not (paths['poppler_dir']/name).is_file():
            raise RenderError('Missing bundled Poppler: ' + name)
    if paths['libreoffice'].name.lower() != 'soffice.exe':
        raise RenderError('Expected the verified native soffice.exe')
    if sha(paths['libreoffice']) != config.get('libreoffice_sha256'):
        raise RenderError('LibreOffice executable changed; re-detect and verify the configured installation')
    # This wrapper must run in the document runtime, not a random system Python.
    if not os.path.samefile(sys.executable, paths['python']):
        raise RenderError('Run this helper with the configured document Python runtime')
    from PIL import Image  # noqa: F401
    from pypdf import PdfReader  # noqa: F401
    import pdf2image  # noqa: F401
    return paths

def verify_render(output_dir, input_path):
    """Validate immutable render evidence; does not claim visual acceptance."""
    root = Path(output_dir).resolve()
    evidence = read(root/'render.json')
    if evidence.get('status') != 'rendered_pending_visual_review':
        raise RenderError('Render did not complete')
    if evidence.get('input_sha256') != sha(input_path):
        raise RenderError('Render belongs to different DOCX bytes')
    from PIL import Image
    from pypdf import PdfReader
    entries = [evidence['pdf'], *evidence['pages']]
    if not evidence['pages']:
        raise RenderError('No rendered pages')
    for row in entries:
        p = root/row['file']
        if p.resolve().parent != root or not p.is_file() or sha(p) != row['sha256']:
            raise RenderError('Missing or changed rendered artifact')
    if len(PdfReader(root/evidence['pdf']['file']).pages) != len(evidence['pages']):
        raise RenderError('PDF and page image counts differ')
    for i, row in enumerate(evidence['pages'], 1):
        if row['file'] != f'page-{i}.png':
            raise RenderError('Rendered page sequence is incomplete')
        with Image.open(root/row['file']) as im:
            im.verify()
    return evidence

def runtime_fingerprint(paths, config, dpi):
    """Bind reuse to configured conversion tools and options, not timestamps."""
    tools = {name: paths[name] for name in ('python', 'renderer', 'libreoffice')}
    tools.update({name: paths['poppler_dir']/name for name in ('pdfinfo.exe', 'pdftoppm.exe')})
    payload = {'config': config, 'dpi': dpi, 'wrapper_sha256': sha(__file__),
               'tools': {name: {'path': str(path), 'sha256': sha(path)} for name, path in tools.items()}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def compact_result(evidence, output_dir):
    return {'status': evidence['status'], 'visual_review': 'not_run',
            'input_sha256': evidence['input_sha256'], 'pages': len(evidence['pages']),
            'evidence': str(Path(output_dir).absolute()/'render.json'),
            'cache_hit': evidence.get('cache_hit', False)}

def render(input_path, output_dir, config, timeout=180, dpi=144, reuse=False):
    paths = preflight(config)
    source = Path(input_path).resolve(strict=True)
    if source.suffix.lower() != '.docx':
        raise RenderError('Only DOCX is supported by this workflow')
    out = Path(output_dir).absolute()
    if out.exists():
        if not reuse:
            raise RenderError('Use a new output directory for every render')
        evidence = verify_render(out, source)
        if evidence.get('input') != str(source):
            raise RenderError('Reuse must refer to the same case artifact path')
        if evidence.get('runtime_fingerprint') != runtime_fingerprint(paths, config, dpi):
            raise RenderError('Render tools/options changed or reuse evidence is missing; use a new output directory')
        if sha(source) != evidence['input_sha256']:
            raise RenderError('DOCX changed while verifying reusable pages')
        return dict(evidence, cache_hit=True)
    fingerprint = runtime_fingerprint(paths, config, dpi)
    out.mkdir(parents=True, exist_ok=False)
    initial = sha(source)
    evidence = {'schema_version': 1, 'input': str(source), 'input_sha256': initial,
                'renderer': str(paths['renderer']), 'renderer_sha256': sha(paths['renderer']),
                'libreoffice': str(paths['libreoffice']), 'libreoffice_sha256': sha(paths['libreoffice']),
                'python': str(paths['python']), 'dpi': dpi,
                'runtime_fingerprint': fingerprint,
                'status': 'running', 'visual_review': 'not_run'}
    dump(out/'render.json', evidence)
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join([str(paths['libreoffice'].parent), str(paths['poppler_dir']),
                                  str(paths['python'].parent), str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32')])
    if Path(shutil.which('soffice.exe', path=env['PATH']) or '').resolve() != paths['libreoffice'].resolve():
        raise RenderError('Configured executable would not be selected')
    command = [str(paths['python']), '-X', 'utf8', str(paths['renderer']), str(source),
               '--output_dir', str(out), '--emit_pdf', '--dpi', str(dpi), '--verbose']
    evidence['command'] = command
    start = time.monotonic()
    try:
        with (out/'renderer.log').open('w', encoding='utf-8') as log:
            proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            evidence['renderer_pid'] = proc.pid
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Target only the still-live process tree created by this helper.
                # Never kill all Office/LibreOffice instances by executable name.
                if proc.poll() is None:
                    killer = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/taskkill.exe'
                    cleanup = subprocess.run([str(killer), '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=20,
                                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    evidence['timeout_cleanup_exit'] = cleanup.returncode
                    proc.wait(timeout=20)
                raise RenderError('Renderer timed out; see isolated invocation log')
        evidence['exit_code'] = code
        if code != 0:
            raise RenderError('Canonical renderer failed; see renderer.log')
        if sha(source) != initial:
            raise RenderError('DOCX changed while rendering')
        pdfs = list(out.glob('*.pdf'))
        if len(pdfs) != 1:
            raise RenderError('Expected exactly one output PDF')
        pages = sorted(out.glob('page-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
        evidence['pdf'] = {'file': pdfs[0].name, 'sha256': sha(pdfs[0])}
        evidence['pages'] = [{'file': p.name, 'sha256': sha(p)} for p in pages]
        evidence['status'] = 'rendered_pending_visual_review'
        evidence['elapsed_seconds'] = round(time.monotonic() - start, 3)
        dump(out/'render.json', evidence)
        verify_render(out, source)
        return evidence
    except Exception as exc:
        evidence['status'] = 'failed'
        evidence['error'] = str(exc)
        evidence['elapsed_seconds'] = round(time.monotonic() - start, 3)
        dump(out/'render.json', evidence)
        raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', nargs='?')
    p.add_argument('--output-dir')
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--preflight', action='store_true')
    p.add_argument('--verify', action='store_true')
    p.add_argument('--reuse', action='store_true', help='Reuse valid pages in this same artifact output directory; never overwrite mismatches')
    p.add_argument('--compact', action='store_true', help='Print a short result; full evidence remains in render.json')
    p.add_argument('--timeout', type=int, default=180)
    args = p.parse_args()
    try:
        if args.preflight:
            result = {'status': 'dependencies_available', 'paths': {k: str(v) for k, v in preflight(read(args.config)).items()},
                      'scope': 'Dependency check only; use a real DOCX render to prove conversion'}
        else:
            if not args.input or not args.output_dir:
                raise RenderError('Input and --output-dir are required')
            result = verify_render(args.output_dir, args.input) if args.verify else render(args.input, args.output_dir, read(args.config), args.timeout, reuse=args.reuse)
            if args.compact:
                result = compact_result(result, args.output_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        print('DOCX render failed: ' + str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
