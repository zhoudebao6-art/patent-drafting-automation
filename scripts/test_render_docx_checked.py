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

    def test_missing_runtime_and_relative_path_rejected(self):
        config = {'schema_version': 1, 'python': str(self.root/'missing.exe'),
                  'renderer': str(self.root/'render.py'), 'libreoffice': str(self.root/'soffice.exe'),
                  'poppler_dir': str(self.root/'poppler')}
        with self.assertRaises(r.RenderError): r.preflight(config)
        config['python'] = 'python.exe'
        with self.assertRaises(r.RenderError): r.preflight(config)

if __name__ == '__main__':
    unittest.main()
