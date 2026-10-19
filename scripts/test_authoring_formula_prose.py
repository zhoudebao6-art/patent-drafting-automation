"""Source-bound semantic categories must determine Word representation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from docx import Document
from lxml import etree as ET
import authoring_toolkit as t
from test_authoring_toolkit import minimal_spec, MATH


def flat(text, props=''):
    return f'<m:oMath xmlns:m="{t.NS["m"]}" xmlns:w="{t.NS["w"]}"><m:r>{props}<m:t>{text}</m:t></m:r></m:oMath>'


class FormulaProseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = minimal_spec()

    def source(self, objects, categories):
        doc = Document()
        for value in objects:
            doc.add_paragraph()._p.append(ET.fromstring(value))
        path = self.root/'source.docx'
        doc.save(path)
        inventory = t.inventory(self.root, path.name)
        self.spec['sources'] = [inventory['source']]
        rows = []
        for row, category in zip(inventory['omml_objects'], categories):
            row.pop('linear_text_for_navigation_only')
            row['category'] = category
            rows.append(row)
        self.spec['formulas'] = rows
        self.spec['formula_policy']['source_main_count'] = categories.count('main_formula')
        return [r['id'] for r in rows]

    def inline(self, ids, prefix='其中，'):
        runs = [{'type':'text', 'text':prefix}]
        for fid in ids:
            runs += [{'type':'formula','formula_id':fid}, {'type':'text','text':'表示已核对的内容；'}]
        self.spec['sections'][3]['blocks'].append({'id':'symbols','type':'paragraph','role':'body','runs':runs})

    def create(self):
        context = t.validate(self.root, self.spec)
        target = self.root/'draft.docx'
        t.render_document(context, target)
        result = t.verify_document(context, target)
        with zipfile.ZipFile(target) as z:
            doc = ET.fromstring(z.read('word/document.xml'))
        return context, target, result, doc

    def mutate(self, path, callback):
        with zipfile.ZipFile(path) as z:
            parts = {n:z.read(n) for n in z.namelist()}
        root = ET.fromstring(parts['word/document.xml'])
        callback(root)
        parts['word/document.xml'] = ET.tostring(root)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in parts.items():z.writestr(name,data)

    def test_intro_split_runs_and_normal_words(self):
        blocks = self.spec['sections'][3]['blocks']
        blocks += [dict(id='intro',type='paragraph',role='body',runs=[{'type':'text','text':'式'}, {'type':'text','text':'中，x表示内容。'}]),
                   dict(id='normal',type='paragraph',role='body',text='具体实施方式中，公式中的条件保持。')]
        original = deepcopy(self.spec)
        context, _, _, doc = self.create()
        self.assertEqual(self.spec, original)
        self.assertEqual(context['block_texts']['intro'], '其中，x表示内容。')
        self.assertIn('具体实施方式中，公式中的条件保持。', ''.join(doc.xpath('//w:t/text()', namespaces=t.NS)))

    def test_bound_latin_symbol_and_numeric_prose_stay_source_bound_text(self):
        ids=self.source([flat('x'),flat('3−0+1=4')],['symbol_reference','prose_example'])
        self.inline(ids)
        source_sha=t.sha(self.root/'source.docx')
        _, _, result, doc=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],0)
        self.assertEqual(result['counts']['source_text_occurrences'],2)
        self.assertEqual(result['counts']['main_formula_occurrences'],0)
        self.assertEqual([r['text'] for r in result['source_objects'] if r['kind']=='source_text'],['x','3−0+1=4'])
        self.assertEqual(result['source_objects'][0]['source_sha256'],source_sha)
        self.assertIn('3−0+1=4', ''.join(doc.xpath('//w:t/text()',namespaces=t.NS)))
        self.assertEqual(source_sha,t.sha(self.root/'source.docx'))

    def test_ordinary_greek_is_text_but_structured_special_and_bold_stay_native(self):
        symbol = ET.fromstring(MATH)
        for child in list(symbol)[1:]: symbol.remove(child)
        ids=self.source([ET.tostring(symbol,encoding='unicode'),flat('Ω'),flat('ℕ'),flat('x','<m:rPr><m:sty m:val="b"/></m:rPr>')],['symbol_reference']*4)
        self.inline(ids)
        _, _, result, doc=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],3)
        self.assertEqual(result['counts']['source_text_occurrences'],1)
        self.assertEqual([r['text'] for r in result['source_objects'] if r['kind']=='source_text'],['Ω'])
        self.assertIsNotNone(doc.find('.//m:sSub',t.NS))

    def test_unrepresentable_prose_rejected_not_flattened(self):
        ids=self.source([MATH],['prose_example']);self.inline(ids)
        with self.assertRaisesRegex(t.AuthoringError,'not safely representable'):t.validate(self.root,self.spec)

    def test_underline_semantics_prevent_scalar_conversion(self):
        ids=self.source([flat('x','<w:rPr><w:u w:val="single"/></w:rPr>')],['symbol_reference']);self.inline(ids)
        _, _, result, _=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],1)

    def test_ordinary_numeric_text_never_creates_formula(self):
        self.spec['sections'][3]['blocks'].append(dict(id='example',type='paragraph',role='body',text='地区更新后的计数为3−0+1=4。'))
        _, _, result, _=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],0)

    def test_prose_label_is_source_bound_and_function_structure_not_flattened(self):
        ids=self.source([flat('seen','<m:rPr><m:nor/></m:rPr>'),flat('threshold2')],['prose_label']*2)
        self.inline(ids)
        _,_,result,_=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],0)
        self.assertEqual([r['text'] for r in result['source_objects']],['seen','threshold2'])
        self.assertIsNone(t.plain_source_text(ET.fromstring(MATH),'prose_label'))

    def test_display_equation_gets_external_semicolon_and_upright_display_only(self):
        fid=self.source([MATH],['main_formula'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        _, _, result, doc=self.create()
        node=doc.find('.//m:oMath',t.NS)
        self.assertEqual(t.tree_signature(node),t.tree_signature(t.upright_math_display(ET.fromstring(MATH))))
        self.assertEqual(result['source_objects'][0]['tree_sha256'],self.spec['formulas'][0]['tree_sha256'])
        self.assertEqual(t.word_text(node.getparent()),'；')
        self.assertEqual(result['counts']['main_formula_occurrences'],1)

    def test_existing_math_semicolon_not_duplicated(self):
        fid=self.source([flat('a=b；')],['main_formula'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        _, _, _, doc=self.create()
        node=doc.find('.//m:oMath',t.NS)
        self.assertEqual(t.word_text(node.getparent()),'')
        self.assertEqual(''.join(node.xpath('.//m:t/text()',namespaces=t.NS)),'a=b；')

    def test_internal_delimiter_semicolon_does_not_count_as_final(self):
        node=ET.fromstring(f'<m:oMath xmlns:m="{t.NS["m"]}"><m:d><m:e><m:r><m:t>y；</m:t></m:r></m:e></m:d></m:oMath>')
        self.assertEqual(t.formula_suffix({'node':node}),'；')
        node=ET.fromstring(f'<m:oMath xmlns:m="{t.NS["m"]}"><m:sSub><m:e><m:r><m:t>y</m:t></m:r></m:e><m:sub><m:r><m:t>i；</m:t></m:r></m:sub></m:sSub></m:oMath>')
        self.assertEqual(t.formula_suffix({'node':node}),'；')

    def test_other_formula_punctuation_needs_review(self):
        with self.assertRaisesRegex(t.AuthoringError,'other punctuation'):
            t.formula_suffix({'node':ET.fromstring(flat('a=b。'))})

    def test_empty_trailing_math_run_does_not_duplicate_semicolon(self):
        node=ET.fromstring(flat('a=b；'))
        node.append(ET.fromstring(f'<m:r xmlns:m="{t.NS["m"]}"><m:rPr/></m:r>'))
        self.assertEqual(t.formula_suffix({'node':node}),'')

    def test_inline_equation_does_not_gain_display_punctuation(self):
        ids=self.source([flat('a=b')],['main_formula']);self.inline(ids)
        context, _, _, _=self.create()
        self.assertEqual(context['block_texts']['symbols'],'其中，表示已核对的内容；')

    def test_prose_example_cannot_be_display_block_or_inflate_main_count(self):
        fid=self.source([flat('3−0+1=4')],['prose_example'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        with self.assertRaisesRegex(t.AuthoringError,'paragraph runs'):t.validate(self.root,self.spec)
        self.spec['formula_policy']['source_main_count']=1
        with self.assertRaisesRegex(t.AuthoringError,'source_main_count'):t.validate(self.root,self.spec)

    def test_structured_symbol_reference_cannot_be_promoted_to_display_formula(self):
        symbol = ET.fromstring(MATH)
        for child in list(symbol)[1:]: symbol.remove(child)
        fid=self.source([ET.tostring(symbol,encoding='unicode')],['symbol_reference'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        with self.assertRaisesRegex(t.AuthoringError,'Symbol references belong'):t.validate(self.root,self.spec)

    def test_reference_letter_role_matches_manual_and_rejects_font_tamper(self):
        self.spec['sections'][3]['blocks'].append(dict(id='letter',type='paragraph',role='body',runs=[{'type':'symbol_reference','text':'J','style':'reference_letter'},{'type':'text','text':'表示已核对的计数。'}]))
        context, target, _, doc=self.create()
        run=next(r for r in doc.findall('.//w:r',t.NS) if t.word_text(r)=='J')
        self.assertEqual(run.find('w:rPr/w:rFonts',t.NS).get(t.qn('w:ascii')),'宋体')
        self.assertEqual(run.find('w:rPr/w:sz',t.NS).get(t.qn('w:val')),'28')
        def change(root):
            r=next(r for r in root.findall('.//w:r',t.NS) if t.word_text(r)=='J')
            r.find('w:rPr/w:rFonts',t.NS).set(t.qn('w:ascii'),'Arial')
        self.mutate(target,change)
        with self.assertRaisesRegex(t.AuthoringError,'font changed'):t.verify_document(context,target)

    def test_missing_semicolon_rejected_by_readback(self):
        fid=self.source([MATH],['main_formula'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        context,target,_,_=self.create()
        def change(root):
            p=root.find('.//m:oMath',t.NS).getparent()
            p.remove(p.find('w:r',t.NS))
        self.mutate(target,change)
        with self.assertRaises(t.AuthoringError):t.verify_document(context,target)

    def test_shifted_baseline_is_rejected_even_with_baseline_vertalign(self):
        context,target,_,_=self.create()
        def change(root):
            run=next(r for r in root.findall('.//w:r',t.NS) if t.word_text(r)=='g')
            run.find('w:rPr',t.NS).append(t.element('w:position',val=12))
        self.mutate(target,change)
        with self.assertRaisesRegex(t.AuthoringError,'baseline position'):t.verify_document(context,target)


if __name__=='__main__':unittest.main()
