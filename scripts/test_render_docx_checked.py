import copy
from contextlib import nullcontext
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from pypdf import PdfWriter
import render_docx_checked as r
import wps_render_backend as wps
import cli_evidence

class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.docx'
        self.source.write_bytes(b'test bytes used only for hash binding')
        self.out = self.root/'render'
        self.out.mkdir()
        pdf = PdfWriter()
        pdf.add_blank_page(100, 100)
        pdf.write(self.out/'source.pdf')
        Image.new('RGB', (100, 100), 'white').save(self.out/'page-1.png')
        self.evidence = {'status': 'rendered_pending_visual_review', 'input_sha256': r.sha(self.source),
                         'pdf': {'file': 'source.pdf', 'sha256': r.sha(self.out/'source.pdf')},
                         'pages': [{'file': 'page-1.png', 'sha256': r.sha(self.out/'page-1.png')}]}
        self.write()

    def write(self):
        r.dump(self.out/'render.json', self.evidence)

    def test_hash_bound_render_and_not_visual_acceptance(self):
        result = r.verify_render(self.out, self.source)
        self.assertEqual(result['status'], 'rendered_pending_visual_review')
        self.source.write_bytes(b'changed document')
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)

    def test_missing_or_tampered_page_rejected(self):
        (self.out/'page-1.png').write_bytes(b'changed image')
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)
        (self.out/'page-1.png').unlink()
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)

    def test_page_number_gap_and_pdf_count_rejected(self):
        Image.new('RGB', (100, 100), 'white').save(self.out/'page-3.png')
        self.evidence['pages'][0] = {'file': 'page-3.png', 'sha256': r.sha(self.out/'page-3.png')}
        self.write()
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)
        self.evidence['pages'].append({'file': 'page-1.png', 'sha256': r.sha(self.out/'page-1.png')})
        self.write()
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)

    def test_output_paths_cannot_escape_render_directory(self):
        self.evidence['pdf'] = {'file': '../source.docx', 'sha256': r.sha(self.source)}
        self.write()
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)

    def test_failed_conversion_cannot_be_verified(self):
        self.evidence['status'] = 'failed'
        self.write()
        with self.assertRaises(r.RenderError): r.verify_render(self.out, self.source)

    def test_existing_output_directory_is_not_overwritten(self):
        before = (self.out/'page-1.png').read_bytes()
        with patch.object(r, 'preflight', return_value={}):
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {})
        self.assertEqual((self.out/'page-1.png').read_bytes(), before)

    def reusable(self):
        tools = self.root/'tools'; tools.mkdir()
        self.paths = {'python': tools/'python.exe', 'renderer': tools/'render.py',
                      'libreoffice': tools/'soffice.exe', 'poppler_dir': tools}
        for name in ('python.exe', 'render.py', 'soffice.exe', 'pdfinfo.exe', 'pdftoppm.exe'):
            (tools/name).write_bytes(name.encode())
        self.evidence.update(input=str(self.source.resolve()), runtime_fingerprint=r.runtime_fingerprint(self.paths, {}, 144))
        self.write()

    def test_reuse_skips_conversion_and_preserves_evidence(self):
        self.reusable(); before = (self.out/'render.json').read_bytes()
        with patch.object(r, 'preflight', return_value=self.paths), patch.object(r.subprocess, 'Popen') as process:
            result = r.render(self.source, self.out, {}, reuse=True)
        process.assert_not_called()
        self.assertTrue(result['cache_hit'])
        self.assertEqual((self.out/'render.json').read_bytes(), before)
        self.assertEqual(r.compact_result(result, self.out)['visual_review'], 'not_run')

    def test_reuse_rejects_changed_runtime_options_or_artifact_path(self):
        self.reusable(); before = (self.out/'render.json').read_bytes()
        other = self.root/'same-bytes.docx'; other.write_bytes(self.source.read_bytes())
        with patch.object(r, 'preflight', return_value=self.paths):
            with self.assertRaises(r.RenderError): r.render(other, self.out, {}, reuse=True)
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {}, dpi=72, reuse=True)
            self.paths['renderer'].write_bytes(b'new tool version')
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {}, reuse=True)
        self.assertEqual((self.out/'render.json').read_bytes(), before)

    def test_reuse_rejects_changed_docx_or_tampered_page(self):
        self.reusable(); before = self.source.read_bytes()
        with patch.object(r, 'preflight', return_value=self.paths):
            self.source.write_bytes(b'new DOCX')
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {}, reuse=True)
            self.source.write_bytes(before)
            (self.out/'page-1.png').write_bytes(b'changed page')
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {}, reuse=True)

    def test_reuse_rechecks_source_after_page_verification(self):
        self.reusable(); original = r.verify_render
        def changed(*args):
            result = original(*args); self.source.write_bytes(b'concurrent edit'); return result
        with patch.object(r, 'preflight', return_value=self.paths), patch.object(r, 'verify_render', side_effect=changed):
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, {}, reuse=True)

    def test_font_content_change_invalidates_same_document_pages(self):
        self.reusable()
        font = self.root/'example.ttf'; font.write_bytes(b'font-version-one')
        config = {'font_files': {'math': str(font)}}
        self.evidence['runtime_fingerprint'] = r.runtime_fingerprint(self.paths, config, 144)
        self.write()
        with patch.object(r, 'preflight', return_value=self.paths), patch.object(r.subprocess, 'Popen') as process:
            self.assertTrue(r.render(self.source, self.out, config, reuse=True)['cache_hit'])
            font.write_bytes(b'font-version-two')
            with self.assertRaises(r.RenderError): r.render(self.source, self.out, config, reuse=True)
        process.assert_not_called()

    def test_missing_configured_font_is_not_silently_ignored(self):
        with self.assertRaises(r.RenderError):
            r.font_identity({'font_files': {'math': str(self.root/'missing.ttf')}})
        with self.assertRaises(r.RenderError):
            r.font_identity({'font_files': {'math': 'relative.ttf'}})

    def test_missing_runtime_and_relative_path_rejected(self):
        config = {'schema_version': 1, 'python': str(self.root/'missing.exe'),
                  'renderer': str(self.root/'render.py'), 'libreoffice': str(self.root/'soffice.exe'),
                  'poppler_dir': str(self.root/'poppler')}
        with self.assertRaises(r.RenderError): r.preflight(config)
        config['python'] = 'python.exe'
        with self.assertRaises(r.RenderError): r.preflight(config)

class WpsRenderTests(unittest.TestCase):
    """No Office launch: exercise the real adapter around a synthetic COM result."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.docx'; self.source.write_bytes(b'fixture docx identity')
        tools = self.root/'tools'; tools.mkdir()
        self.paths = {'python': tools/'python.exe', 'poppler_dir': tools,
                      'wps_program': tools/'wps.exe', 'wps_powershell': tools/'powershell.exe',
                      'wps_helper': tools/'helper.ps1', 'wps_backend': tools/'backend.py'}
        for path in [p for k, p in self.paths.items() if k != 'poppler_dir'] + [tools/'pdfinfo.exe', tools/'pdftoppm.exe']:
            path.write_bytes(path.name.encode())
        self.config = {'conversion_backend': 'wps',
                       'wps_program': str(self.paths['wps_program']), 'wps_program_sha256': r.sha(self.paths['wps_program']),
                       'wps_powershell': str(self.paths['wps_powershell']), 'wps_powershell_sha256': r.sha(self.paths['wps_powershell'])}
        self.identity = {'pid': 123, 'created': 456, 'executable': str(self.paths['wps_program'])}

    def helper(self, command, out, paths, timeout):
        copy_path = out/'readonly-synthetic.docx'; copy_path.write_bytes(self.source.read_bytes())
        pdf = PdfWriter(); pdf.add_blank_page(100, 100); pdf.add_blank_page(100, 100)
        pdf.write(out/'document.pdf')
        record = {'backend': 'WPS', 'version': 'synthetic-test', 'process': self.identity,
                  'input': str(self.source), 'input_sha256': r.sha(self.source),
                  'copy': str(copy_path), 'copy_sha256': r.sha(copy_path),
                  'pdf': str(out/'document.pdf'), 'pdf_sha256': r.sha(out/'document.pdf'),
                  'read_only': True, 'document_closed': True,
                  'program_sha256': self.config['wps_program_sha256']}
        r.dump(out/'converter-result.json', record)
        return {'pid': 789, 'created': 987, 'executable': str(self.paths['wps_powershell'])}

    def raster(self, command, **unused):
        prefix = Path(command[-1])
        for number in (1, 2):
            Image.new('RGB', (100, 100), 'white').save(prefix.parent/f'page-{number:02}.png')

    def patches(self, helper=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(r, 'preflight', return_value=self.paths))
        stack.enter_context(patch.object(wps, 'converter_lock', side_effect=lambda _: nullcontext()))
        stack.enter_context(patch.object(wps, '_run_helper', side_effect=helper or self.helper))
        stack.enter_context(patch.object(cli_evidence, 'process_identity', return_value=self.identity))
        stack.enter_context(patch.object(wps.subprocess, 'run', side_effect=self.raster))
        return stack

    def test_wps_canonical_pages_identity_and_same_artifact_reuse(self):
        out = self.root/'render'
        with self.patches():
            result = r.render(self.source, out, self.config)
            before = (out/'render.json').read_bytes()
            with patch.object(wps, 'convert') as conversion:
                cached = r.render(self.source, out, self.config, reuse=True)
            conversion.assert_not_called()
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(before, (out/'render.json').read_bytes())
        self.assertEqual(result['conversion_backend'], 'wps')
        self.assertEqual(result['backend_process'], self.identity)
        self.assertEqual(result['visual_review'], 'not_run')
        self.assertEqual([p['file'] for p in result['pages']], ['page-1.png', 'page-2.png'])
        self.assertEqual(r.verify_render(out, self.source), result)

    def test_wps_rejects_wrong_host_changed_copy_or_unclosed_document(self):
        for defect in ('host', 'copy', 'unclosed', 'writable', 'foreign_copy', 'source'):
            with self.subTest(defect=defect):
                self.source.write_bytes(b'fixture docx identity')
                out = self.root/defect
                def corrupted(command, folder, paths, timeout):
                    identity = self.helper(command, folder, paths, timeout)
                    record = r.read(folder/'converter-result.json')
                    if defect == 'host': record['process']['created'] += 1
                    if defect == 'copy': Path(record['copy']).write_bytes(b'changed')
                    if defect == 'unclosed': record['document_closed'] = False
                    if defect == 'writable': record['read_only'] = False
                    if defect == 'foreign_copy': record['copy'] = str(self.source)
                    if defect == 'source': self.source.write_bytes(b'changed source')
                    r.dump(folder/'converter-result.json', record)
                    return identity
                with self.patches(corrupted), self.assertRaises(ValueError):
                    r.render(self.source, out, self.config)
                self.assertEqual(r.read(out/'render.json')['status'], 'failed')

    def test_wps_reuse_binds_helper_backend_program_dpi_and_full_pages(self):
        with self.patches():
            out = self.root/'render'; r.render(self.source, out, self.config)
            for member in ('wps_helper', 'wps_backend', 'wps_program', 'wps_powershell'):
                path = self.paths[member]; before = path.read_bytes()
                path.write_bytes(before+b'changed')
                with self.subTest(member=member), self.assertRaises(r.RenderError):
                    r.render(self.source, out, self.config, reuse=True)
                path.write_bytes(before)
            with self.assertRaises(r.RenderError): r.render(self.source, out, self.config, dpi=72, reuse=True)
            (out/'page-2.png').unlink()
            with self.assertRaises(r.RenderError): r.render(self.source, out, self.config, reuse=True)

    def test_wps_preflight_binds_executables_and_fixed_com_progid(self):
        with patch.object(wps, 'HELPER', self.paths['wps_helper']):
            self.assertEqual(wps.preflight(self.config)['wps_program'], self.paths['wps_program'])
            for key, value in [('wps_program_sha256', 'bad'), ('wps_powershell_sha256', 'bad'),
                               ('wps_progid', 'Unrelated.Application')]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    wps.preflight(dict(self.config, **{key: value}))

    def test_wps_timeout_kills_only_the_owned_helper_not_shared_com_tree(self):
        from unittest.mock import Mock
        out = self.root/'timeout'; out.mkdir()
        process = Mock(pid=789)
        process.wait.side_effect = [subprocess.TimeoutExpired(['powershell.exe'], 1), 0]
        process.poll.return_value = None
        identity = {'pid': 789, 'created': 987, 'executable': str(self.paths['wps_powershell'])}
        with patch.object(wps.subprocess, 'Popen', return_value=process), \
             patch.object(cli_evidence, 'process_identity', return_value=identity), \
             patch.object(wps.subprocess, 'run') as unrelated_process:
            with self.assertRaises(subprocess.TimeoutExpired):
                wps._run_helper(['powershell.exe'], out, self.paths, 1)
        process.kill.assert_called_once_with()
        unrelated_process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
