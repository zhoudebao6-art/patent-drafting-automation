"""Natural prose flow and uncaptioned abstract image readback regressions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from lxml import etree as ET
import authoring_toolkit as t
from test_authoring_toolkit import minimal_spec, MATH
from docx import Document
from PIL import Image


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pagination-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = minimal_spec()

    def create(self, figures=False, math=False):
        if figures:
            p = self.root/'image.png'
            Image.new('RGB', (100,80), 'white').save(p)
            self.spec['sources'].append({'id':'image', 'file':p.name, 'sha256':t.sha(p)})
            self.spec['figures'].append({'id':'f1', 'source_id':'image', 'sha256':t.sha(p),
                                         'number':1, 'title':'排版测试图', 'width_cm':5})
            self.spec['sections'][1]['blocks'].append({'id':'abs', 'type':'figure', 'figure_id':'f1'})
            self.spec['sections'][3]['blocks'].append({'id':'desc', 'type':'figure_description', 'figure_id':'f1'})
            self.spec['sections'][4]['blocks'].append({'id':'image', 'type':'figure', 'figure_id':'f1'})
        if math:
            doc = Document()
            doc.add_paragraph()._p.append(ET.fromstring(MATH))
            doc.save(self.root/'math.docx')
            inv = t.inventory(self.root,'math.docx')
            self.spec['sources'].append(inv['source'])
            row = inv['omml_objects'][0]
            row.pop('linear_text_for_navigation_only')
            row['category'] = 'main_formula'
            self.spec['formulas'].append(row)
            self.spec['formula_policy']['source_main_count'] = 1
            self.spec['sections'][3]['blocks'].append({'id':'math', 'type':'formula', 'formula_id':row['id']})
        self.context = t.validate(self.root, self.spec)
        self.path = self.root/'draft.docx'
        t.render_document(self.context, self.path)
        self.parts = t.package(self.path.read_bytes(),str(self.path))
        self.doc = t.xml(self.parts['word/document.xml'],'doc')
        return t.verify_document(self.context, self.path)

    def block(self, name):
        block = next(b for b in self.context['blocks'] if b['id']==name)
        return self.doc.xpath(f'.//w:p[w:bookmarkStart/@w:name="{block["bookmark"]}"]',namespaces=t.NS)[0]

    def changed(self):
        self.parts['word/document.xml'] = ET.tostring(self.doc)
        with zipfile.ZipFile(self.path,'w',zipfile.ZIP_DEFLATED) as z:
            for k,v in self.parts.items():z.writestr(k,v)

    def test_ordinary_prose_has_no_forced_keep_or_widow_and_keeps_body_geometry(self):
        self.create()
        for name in ('summary','claim1','claim1b','body'):
            p = self.block(name)
            for prop in ('keepLines','keepNext','widowControl','pageBreakBefore'):
                self.assertEqual(p.find('w:pPr/w:'+prop,t.NS).get(t.qn('w:val')),'0')
            self.assertEqual(p.find('w:pPr/w:ind',t.NS).get(t.qn('w:firstLineChars')),'200')

    def test_all_populated_section_boundaries_attach_to_real_content(self):
        self.create(figures=True,math=True)
        carriers = self.doc.xpath('./w:body/w:p[w:pPr/w:sectPr]',namespaces=t.NS)
        self.assertEqual(len(carriers),4)
        self.assertTrue(all(p.find('w:bookmarkStart',t.NS) is not None for p in carriers))
        self.assertIsNotNone(self.block('math').find('w:pPr/w:sectPr',t.NS))

    def test_empty_logical_section_uses_minimal_structural_carrier(self):
        self.create()
        empty = self.doc.xpath('./w:body/w:p[w:pPr/w:sectPr][not(w:bookmarkStart)]',namespaces=t.NS)
        self.assertEqual(len(empty),1)
        self.assertEqual(empty[0].find('w:pPr/w:spacing',t.NS).get(t.qn('w:line')),'20')

    def test_abstract_media_and_math_preserved_but_only_final_figure_has_caption(self):
        result=self.create(figures=True,math=True)
        self.assertEqual(self.block('abs').find('w:pPr/w:keepNext',t.NS).get(t.qn('w:val')),'0')
        self.assertEqual(self.block('image').find('w:pPr/w:keepNext',t.NS).get(t.qn('w:val')),'1')
        captions=self.doc.xpath('./w:body/w:p[w:r/w:t="图1"]',namespaces=t.NS)
        self.assertEqual(len(captions),1)
        self.assertEqual(result['counts']['omml_occurrences'],1)
        self.assertEqual(result['counts']['image_occurrences_including_abstract_reuse'],2)

    def test_readback_rejects_each_reintroduced_body_keep_property(self):
        self.create()
        original=deepcopy(self.doc)
        for prop in ('keepLines','keepNext','widowControl','pageBreakBefore'):
            with self.subTest(prop=prop):
                self.doc=deepcopy(original)
                self.block('body').find('w:pPr/w:'+prop,t.NS).set(t.qn('w:val'),'1')
                self.changed()
                with self.assertRaises(t.AuthoringError):t.verify_document(self.context,self.path)

    def test_readback_rejects_extra_caption_inside_abstract_image(self):
        self.create(figures=True)
        p=self.block('abs');r=t.element('w:r');node=t.element('w:t');node.text='图1';r.append(node);p.append(r)
        self.changed()
        with self.assertRaises(t.AuthoringError):t.verify_document(self.context,self.path)

    def test_readback_rejects_full_height_empty_section_carrier(self):
        self.create()
        p=self.doc.xpath('./w:body/w:p[w:pPr/w:sectPr][not(w:bookmarkStart)]',namespaces=t.NS)[0]
        p.find('w:pPr/w:spacing',t.NS).set(t.qn('w:line'),'312')
        self.changed()
        with self.assertRaises(t.AuthoringError):t.verify_document(self.context,self.path)


if __name__=='__main__':unittest.main()
