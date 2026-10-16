"""Read-only WPS PDF conversion; never owns or terminates the shared Office app."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

MUTEX_NAME = 'Global\\CodexPatentWpsReadOnlyRenderV1'
HELPER = Path(__file__).with_name('wps_readonly_pdf.ps1')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def preflight(config):
    if os.name != 'nt':
        raise ValueError('The WPS backend requires Windows')
    if config.get('wps_progid', 'KWPS.Application') != 'KWPS.Application':
        raise ValueError('Only the verified KWPS.Application backend is supported')
    result = {}
    for name in ('wps_program', 'wps_powershell'):
        path = Path(config.get(name, ''))
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Missing absolute WPS runtime: '+name)
        if sha(path) != config.get(name+'_sha256'):
            raise ValueError('WPS runtime changed; verify configuration: '+name)
        result[name] = path
    if result['wps_program'].name.lower() != 'wps.exe' or result['wps_powershell'].name.lower() != 'powershell.exe':
        raise ValueError('Expected verified wps.exe and Windows powershell.exe')
    if not HELPER.is_file():
        raise ValueError('Frozen WPS helper is missing')
    result.update(wps_helper=HELPER, wps_backend=Path(__file__))
    return result


@contextmanager
def converter_lock(timeout):
    """One machine-wide converter across batches; a dead helper leaves no file lock."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('WPS converter busy; no conversion was started')
            status = kernel.WaitForSingleObject(handle, min(250, max(1, int(remaining*1000))))
            if status in (0, 0x80):
                acquired = True
                break
            if status != 0x102:
                raise ctypes.WinError(ctypes.get_last_error())
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _verify_identity(record, expected_program):
    from cli_evidence import process_identity
    if not isinstance(record, dict):
        raise ValueError('Actual WPS process identity is required')
    current = process_identity(record.get('pid'))
    if (current is None or current['created'] != record.get('created')
            or not same_path(current['executable'], expected_program)
            or not same_path(record.get('executable', ''), expected_program)):
        raise ValueError('Actual WPS process differs from the configured program')
    return current


def _run_helper(command, out, paths, timeout):
    """A timeout stops only our PowerShell process, never its shared COM server."""
    from cli_evidence import process_identity
    with (out/'converter.stdout.txt').open('wb') as stdout, (out/'converter.stderr.txt').open('wb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            identity = process_identity(process.pid)
            if identity is None or not same_path(identity['executable'], paths['wps_powershell']):
                raise ValueError('Cannot prove the launched PowerShell helper identity')
            code = process.wait(timeout=timeout)
        except BaseException:
            if process.poll() is None:
                process.kill()  # Exact Popen handle only: never taskkill /T or WPS.
                process.wait(timeout=20)
            raise
    if code != 0:
        raise ValueError('WPS read-only conversion failed; inspect converter logs')
    return identity


def convert(source, out, paths, config, timeout, dpi):
    """Produce PDF and canonical page-N.png files in this new render directory."""
    from pypdf import PdfReader
    source, out = Path(source), Path(out)
    before = sha(source)
    pdf, result_path = out/'document.pdf', out/'converter-result.json'
    if pdf.exists() or result_path.exists():
        raise ValueError('Partial WPS conversion retained; use a new output directory')
    command = [str(paths['wps_powershell']), '-NoProfile', '-NonInteractive', '-File', str(paths['wps_helper']),
               '-InputDocx', str(source), '-OutputPdf', str(pdf), '-ResultJson', str(result_path),
               '-ExpectedProgram', str(paths['wps_program']),
               '-ExpectedProgramSha256', config['wps_program_sha256']]
    with converter_lock(timeout):
        helper_identity = _run_helper(command, out, paths, timeout)
        raw = result_path.read_bytes()
        record = json.loads(raw.decode('utf-8-sig'))
        identity = _verify_identity(record.get('process'), paths['wps_program'])
        copy_path = Path(record['copy'])
        if (not same_path(record.get('input', ''), source) or not same_path(record.get('pdf', ''), pdf)
                or copy_path.resolve().parent != out.resolve() or not copy_path.name.startswith('readonly-')
                or copy_path.suffix.lower() != '.docx' or not copy_path.is_file()
                or record.get('read_only') is not True or record.get('document_closed') is not True
                or record.get('backend') != 'WPS' or not record.get('version')
                or record.get('input_sha256') != before or record.get('copy_sha256') != before
                or sha(copy_path) != before or sha(source) != before
                or record.get('pdf_sha256') != sha(pdf)
                or record.get('program_sha256') != config['wps_program_sha256']
                or sha(paths['wps_program']) != config['wps_program_sha256']):
            raise ValueError('WPS source, copy, PDF or actual backend identity changed')
    # Rasterization has no shared Office state and need not hold the COM lock.
    count = len(PdfReader(pdf).pages)
    if count < 1:
        raise ValueError('WPS produced an empty PDF')
    raster = [str(paths['poppler_dir']/'pdftoppm.exe'), '-r', str(dpi), '-png', str(pdf), str(out/'page')]
    with (out/'rasterizer.log').open('wb') as log:
        subprocess.run(raster, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                       timeout=timeout, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    pages = list(out.glob('page-*.png'))
    indexed = {}
    for path in pages:
        number = int(path.stem.split('-')[-1])
        if number in indexed:
            raise ValueError('Ambiguous WPS raster page')
        indexed[number] = path
    if set(indexed) != set(range(1, count+1)):
        raise ValueError('Missing or extra WPS raster pages')
    for number, path in indexed.items():
        canonical = out/f'page-{number}.png'
        if canonical != path:
            if canonical.exists():
                raise ValueError('Canonical page output already exists')
            path.rename(canonical)
    if sha(source) != before or sha(copy_path) != before or sha(pdf) != record['pdf_sha256']:
        raise ValueError('WPS artifacts changed during rasterization')
    return {'conversion_backend': 'wps', 'backend_version': record['version'],
            'backend_process': identity, 'helper_process': helper_identity,
            'backend_program': str(paths['wps_program']), 'backend_program_sha256': config['wps_program_sha256'],
            'readonly_copy': {'file': copy_path.name, 'sha256': before},
            'converter_result': {'file': result_path.name, 'sha256': hashlib.sha256(raw).hexdigest()},
            'command': command, 'raster_command': raster, 'exit_code': 0}
