import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from pypdf import PdfWriter
import render_docx_checked as r

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

if __name__ == '__main__':
    unittest.main()
