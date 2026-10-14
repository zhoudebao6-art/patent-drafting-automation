import hashlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
import test_inspect_docx as fixtures
from inspect_docx import inspect, precheck, isolated_symbol_candidate, NS


def paragraph(text):
    return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'


class ClaimWordingTests(unittest.TestCase):
    def read(self, body, kind='draft', extra=None):
        with tempfile.TemporaryDirectory() as root:
            path = fixtures.InspectionTests().make_docx(root, body, extra)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = inspect(path)
            precheck(result, kind)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            return result

    def test_whole_claim_count_spans_continuation_paragraphs(self):
        text = ['权利要求书','1.一种方法，获取记录并核对所述记录；','根据所述记录生成清单；','保存所述清单。','2.根据权利要求1所述的方法，其特征在于，核对所述清单。','技术领域']
        result = self.read(''.join(map(paragraph,text)))
        a,b=result['claims_detected']
        self.assertEqual(a['paragraphs'],[2,3,4])
        self.assertEqual(a['antecedent_count_evidence']['literal_count'],3)
        self.assertEqual(b['antecedent_count_evidence']['literal_count'],2)
        self.assertEqual(b['antecedent_count_evidence']['reference_head_count'],1)
        warnings=[r for r in result['issues'] if r['code']=='CLAIM_SHUSU_MINIMUM']
        self.assertEqual([r['locator'] for r in warnings],['P0005'])

    def test_three_occurrences_are_never_a_semantic_pass(self):
        result=self.read(paragraph('1.所述新对象、所述另一新对象、所述第三新对象。')+paragraph('技术领域'))
        evidence=result['claims_detected'][0]['antecedent_count_evidence']
        self.assertEqual(evidence['literal_count'],3)
        self.assertIn('not_automatic_pass',evidence['status'])
        self.assertEqual(len(result['antecedent_occurrences']),3)

    def test_claim_only_words_do_not_leak_into_description(self):
        text=['1.邻近的节点具有较大数值，包括甲、乙等。','技术领域','邻近的节点具有较大数值，包括甲、乙等。']
        result=self.read(''.join(map(paragraph,text)))
        rows=result['wording_evidence']['occurrences']
        self.assertEqual([r['text'] for r in rows],['邻近的','较','等'])
        self.assertTrue(all(r['paragraph']==1 for r in rows))

    def test_names_are_candidates_not_blindly_modified(self):
        text=['一种检测装置','1.根据一种检测装置，使用组件和引擎。','技术领域','本技术项目、本方案、本方法、本申请均有装置。']
        result=self.read(''.join(map(paragraph,text)))
        self.assertEqual(result['parts'][0]['paragraphs'][0]['text'],text[0])
        rows=result['wording_evidence']['occurrences']
        self.assertEqual(sum(r['category']=='self_reference' for r in rows),4)
        self.assertTrue(all('protected_name' in r['status'] for r in rows))

    def test_comparison_and_equality_are_not_character_deletions(self):
        result=self.read(paragraph('1.比较值等于阈值，等级相等。')+paragraph('技术领域'))
        rows=result['wording_evidence']['occurrences']
        self.assertTrue(rows)
        self.assertTrue(all(r['replacement'] is None for r in rows))
        self.assertTrue(all('semantic_classification' in r['status'] for r in rows))
        self.assertEqual(result['parts'][0]['paragraphs'][0]['text'],'1.比较值等于阈值，等级相等。')

    def test_source_proposals_do_not_receive_target_wording_rules(self):
        result=self.read(paragraph('1.邻近较大等，本方案装置。'),'proposal')
        self.assertNotIn('wording_evidence',result)
        self.assertNotIn('claims_detected',result)

    def test_atom_only_not_script_accent_or_relation(self):
        def atom(xml):
            return isolated_symbol_candidate(ET.fromstring(f'<m:oMath xmlns:m="{NS["m"]}">{xml}</m:oMath>'))
        self.assertTrue(atom('<m:r><m:t>g</m:t></m:r>'))
        self.assertTrue(atom('<m:r><m:t>α</m:t></m:r>'))
        for xml in ['<m:r><m:t>g=1</m:t></m:r>','<m:r><m:t>字</m:t></m:r>',
                    '<m:r><m:t> g</m:t></m:r>',
                    '<m:sSub><m:e><m:r><m:t>g</m:t></m:r></m:e><m:sub><m:r><m:t>i</m:t></m:r></m:sub></m:sSub>',
                    '<m:acc><m:e><m:r><m:t>g</m:t></m:r></m:e></m:acc>']:
            self.assertFalse(atom(xml))

    def test_paragraph_and_header_formatting_retained_without_math(self):
        body='<w:p><w:pPr><w:ind w:firstLineChars="200"/><w:spacing w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:rPr><w:rFonts w:eastAsia="宋体"/><w:sz w:val="28"/></w:rPr><w:t>g</w:t></w:r></w:p>'
        hdr=f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:rPr><w:b/><w:spacing w:val="90"/></w:rPr><w:t>权利要求书</w:t></w:r></w:p></w:hdr>'
        result=self.read(body,extra={'word/header1.xml':hdr})
        p=result['parts'][0]['paragraphs'][0]
        self.assertEqual(p['layout_evidence']['indent_explicit']['firstLineChars'],'200')
        self.assertEqual(p['word_run_formats'][0]['samples'],['g'])
        self.assertFalse(any(i['code']=='MATH_FIXED_LINE_HEIGHT' for i in result['issues']))
        header=next(p for p in result['parts'] if p['part']=='word/header1.xml')
        self.assertIn({'name':'b','attributes':{}},header['paragraphs'][0]['word_run_formats'][0]['properties_explicit'])


if __name__=='__main__': unittest.main()
