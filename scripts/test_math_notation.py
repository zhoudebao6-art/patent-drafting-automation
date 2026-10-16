"""Notation candidates are source-located hints, never inferred meanings."""
import unittest
from lxml import etree as ET
import authoring_toolkit as t
import inspect_docx as scan
import batch_tool as b
import test_authoring_formula_prose as author
import test_formula_prose_contract as gate

def mr(text):return '<m:r><m:t>'+text+'</m:t></m:r>'
def sup(base,exponent):return '<m:sSup><m:e>'+mr(base)+'</m:e><m:sup>'+mr(exponent)+'</m:sup></m:sSup>'
def math(content):return '<m:oMath xmlns:m="'+scan.NS['m']+'">'+content+'</m:oMath>'
def wr(text):return '<w:r><w:t>'+text+'</w:t></w:r>'

class NotationScan(unittest.TestCase):
    def test_chinese_in_fraction_and_exponent_is_located_without_mutation(self):
        node=ET.fromstring(math('<m:f><m:num>'+mr('鲜质量观测−5')+'</m:num><m:den>'+mr('2')+'</m:den></m:f>'+sup('增量+1','2')))
        before=ET.tostring(node)
        hits=scan.math_notation_candidates(node)
        self.assertEqual([h['reason'] for h in hits],['math_cjk_requires_english_symbol']*2)
        self.assertEqual([h['text'] for h in hits],['鲜质量观测','增量'])
        for h in hits:
            target=node.find(h['math_path'],scan.NS)
            self.assertEqual(target.text[h['node_offset']:h['node_end_offset']],h['text'])
        self.assertEqual(ET.tostring(node),before)

    def test_transpose_candidates_only_in_script_slot_with_exact_paths(self):
        node=ET.fromstring(math(mr('T=')+sup('J','T')+'<m:d><m:e>'+sup('A','T')+'</m:e></m:d>'+sup('B','H')+sup('C','⊺')+sup('x','2')+sup('z','T+1')))
        hits=scan.math_notation_candidates(node)
        self.assertEqual(len(hits),2)
        self.assertTrue(all(h['reason']=='superscript_T_requires_role_review' for h in hits))
        self.assertEqual(len({h['math_path'] for h in hits}),2)
        for h in hits:self.assertEqual(''.join(node.find(h['math_path'],scan.NS).itertext()),'T')

class NotationGate(unittest.TestCase):
    setUp=gate.FormulaProseTests.setUp
    report=gate.FormulaProseTests.report
    with_paragraph=gate.FormulaProseTests.with_paragraph
    accept=gate.FormulaProseTests.accept
    finding=gate.FormulaProseTests.finding

    def test_formula_chinese_cannot_be_waived_as_original_source(self):
        r=self.with_paragraph(wr('按')+math(sup('增量+1','2')+mr('≤1'))+wr('计算。'))
        hits=r['formula_prose_evidence']['review_candidates']
        self.assertTrue(any(h['reason']=='math_cjk_requires_english_symbol' for h in hits))
        findings=[self.finding(r,h) for h in hits]
        for f in findings:f['format_disposition']='not_formula'
        with self.assertRaisesRegex(b.BatchError,'Chinese mathematical text'):self.accept(r,findings,[gate.SOURCE_HASH])

    def test_T_requires_exact_nontranspose_role_finding_for_each_script(self):
        r=self.with_paragraph(wr('按')+math(sup('x','T')+mr('+')+sup('y','T'))+wr('计算。'))
        hits=[h for h in r['formula_prose_evidence']['review_candidates'] if h['reason']=='superscript_T_requires_role_review']
        self.assertEqual(len(hits),2)
        generic=[self.finding(r,h) for h in hits]
        with self.assertRaises(b.BatchError):self.accept(r,generic,[gate.SOURCE_HASH])
        exact=[dict(f,format_disposition='not_matrix_transpose') for f in generic]
        self.accept(r,exact,[gate.SOURCE_HASH])
        with self.assertRaises(b.BatchError):self.accept(r,exact[:1],[gate.SOURCE_HASH])
        bad=[dict(f,math_path='./m:sup[999]') for f in exact]
        with self.assertRaises(b.BatchError):self.accept(r,bad,[gate.SOURCE_HASH])

    def test_normalized_transpose_and_Chinese_prose_stay_valid(self):
        r=self.with_paragraph(wr('其中，')+math(sup('J','⊺')+mr('=A'))+wr('表示矩阵转置，中文解释继续保留。'))
        self.assertFalse(r['formula_prose_evidence']['review_candidates'])
        self.accept(r)

class NotationImport(unittest.TestCase):
    setUp=author.FormulaProseTests.setUp
    source=author.FormulaProseTests.source
    create=author.FormulaProseTests.create

    def test_unnormalized_chinese_math_import_requires_reviewed_mapping(self):
        fid=self.source([math(mr('增量+1=2'))],['main_formula'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        before=t.sha(self.root/'source.docx')
        with self.assertRaisesRegex(t.AuthoringError,'Chinese mathematical text'):t.validate(self.root,self.spec)
        self.assertEqual(before,t.sha(self.root/'source.docx'))

if __name__=='__main__':unittest.main()
