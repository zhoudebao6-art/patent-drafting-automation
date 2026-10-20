import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import inspect_docx as scan
import tempfile
import unittest
import xml.etree.ElementTree as ET
import test_inspect_docx as fixtures
from inspect_docx import inspect, precheck, isolated_symbol_candidate, NS


def paragraph(text):
    return '<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'


class ClaimWordingTests(unittest.TestCase):
    def read(self, body, kind='draft', extra=None, rule_version=0):
        with tempfile.TemporaryDirectory() as root:
            path = fixtures.InspectionTests().make_docx(root, body, extra)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = inspect(path)
            precheck(result, kind, rule_version)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            return result

    def test_rule75_claim_inventory_counts_claims_not_continuation_paragraphs(self):
        cases={'ten':list(range(1,11)), 'nine':list(range(1,10)), 'eleven':list(range(1,12)),
               'duplicate_with_maximum_ten':list(range(1,9))+[8,10], 'missing_with_maximum_ten':list(range(1,9))+[10]}
        for name,numbers in cases.items():
            body=['权利要求书']
            for number in numbers:
                body.extend([f'{number}.一种记录方法，其特征在于，保存记录数据。','继续核对已有记录数据。'])
            body.append('技术领域')
            with self.subTest(case=name):
                report=self.read(''.join(map(paragraph,body)),rule_version=75)
                self.assertEqual([claim['number'] for claim in report['claims_detected']],numbers)
                self.assertTrue(all(len(claim['paragraphs'])==2 for claim in report['claims_detected']))
                self.assertEqual(any(issue['code']=='CLAIM_COUNT' for issue in report['issues']),name!='ten')
        old=self.read(paragraph('1.一种记录方法，其特征在于，保存记录数据。')+paragraph('技术领域'),rule_version=74)
        self.assertFalse(any(issue['code']=='CLAIM_COUNT' for issue in old['issues']))
        source=self.read(paragraph('1.来源第一项。'),kind='proposal',rule_version=75)
        self.assertNotIn('claims_detected',source)
        self.assertFalse(any(issue['code']=='CLAIM_COUNT' for issue in source['issues']))

    def test_calling_skill_rules_and_rule75_cache_activation_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);skill=root/'skill';(skill/'scripts').mkdir(parents=True);(skill/'references').mkdir()
            (skill/'references'/'rules.json').write_text(json.dumps({'version':75}),encoding='utf-8')
            with patch.object(scan,'__file__',str(skill/'scripts'/'inspect_docx.py')):
                self.assertEqual(scan.tool_rule_version(),75)
            source=fixtures.InspectionTests().make_docx(root,paragraph('1.一种方法。')+paragraph('技术领域'))
            first=scan.run_inspection(source,root/'cache',reuse=True,rule_version=74)
            second=scan.run_inspection(source,root/'cache',reuse=True,rule_version=75)
            self.assertEqual(second['cache_status'],'miss')
            self.assertNotEqual(first['inspection'],second['inspection'])
            report=json.loads(Path(second['inspection']).read_text(encoding='utf-8'))
            self.assertTrue(any(issue['code']=='CLAIM_COUNT' for issue in report['issues']))
            with patch.object(scan,'inspect',side_effect=AssertionError('duplicate parse')):
                third=scan.run_inspection(source,root/'cache',reuse=True,rule_version=75)
            self.assertEqual(third['cache_status'],'hit')
            self.assertEqual(scan.cache_identity(source,'proposal',False,74),scan.cache_identity(source,'proposal',False,75))

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

    def test_standard_storage_medium_uses_two_occurrences_without_semantic_pass(self):
        prefix='一种计算机可读存储介质，其上存储有计算机程序，其特征在于，所述计算机程序被处理器执行时实现权利要求'
        for number, reference in ((10,'1至7任一项'),(6,'2至4任一项'),(3,'1')):
            with self.subTest(number=number,reference=reference):
                # The helper checks existing numbers, not the referenced category.
                earlier=[f'{n}.一种记录系统，其特征在于，保存数据。' for n in range(1,number)]
                medium=f'{number}.'+prefix+reference+'所述的方法。'
                result=self.read(''.join(map(paragraph,['权利要求书',*earlier,medium,'技术领域'])))
                claim=result['claims_detected'][-1]
                evidence=claim['antecedent_count_evidence']
                self.assertEqual(claim['text'],medium)
                self.assertEqual(evidence['literal_count'],2)
                self.assertEqual(evidence['required_valid_minimum'],2)
                self.assertIn('not_automatic_pass',evidence['status'])
                self.assertIn('method-category scope',evidence['applicability'])
                self.assertIn('Rule version 74',evidence['applicability'])
                self.assertEqual(len([row for row in result['antecedent_occurrences'] if row['paragraph']==claim['p']]),2)
                self.assertFalse(any(row['code']=='CLAIM_SHUSU_MINIMUM' and row['locator']==f'P{claim["p"]:04}' for row in result['issues']))

    def test_storage_medium_quota_exception_requires_exact_wording_and_earlier_complete_range(self):
        standard='10.一种计算机可读存储介质，其上存储有计算机程序，其特征在于，所述计算机程序被处理器执行时实现权利要求1至7任一项所述的方法。'
        variants={
            'missing_storage_clause':standard.replace('其上存储有计算机程序，',''),
            'changed_category_name':standard.replace('计算机可读存储介质','存储介质'),
            'changed_intro_punctuation':standard.replace('其特征在于，','其特征在于：'),
            'changed_range_punctuation':standard.replace('1至7','1-7'),
            'descending':standard.replace('1至7','7至1'),
            'singleton_range':standard.replace('1至7','1至1'),
            'zero':standard.replace('1至7','0至7'),
            'leading_zero':standard.replace('1至7','01至7'),
            'self':standard.replace('1至7','1至10'),
            'future':standard.replace('1至7','1至11'),
            'too_large':standard.replace('1至7','1至999999999999999999999'),
            'extra_content':standard+'附加步骤。',
            'ordinary_claim':'10.一种记录方法，其特征在于，获取记录数据，核对所述记录数据，保存所述记录数据。',
        }
        for label, medium in variants.items():
            with self.subTest(case=label):
                earlier=[f'{n}.一种记录方法，其特征在于，保存数据。' for n in range(1,10)]
                result=self.read(''.join(map(paragraph,['权利要求书',*earlier,medium,'技术领域'])))
                claim=result['claims_detected'][-1]
                self.assertEqual(claim['text'],medium)
                self.assertEqual(claim['antecedent_count_evidence']['required_valid_minimum'],3)
                self.assertTrue(any(row['code']=='CLAIM_SHUSU_MINIMUM' and row['locator']==f'P{claim["p"]:04}' for row in result['issues']))
        earlier=[f'{n}.一种记录方法，其特征在于，保存数据。' for n in (1,2,3,5,6,7,8,9)]
        result=self.read(''.join(map(paragraph,['权利要求书',*earlier,standard,'技术领域'])))
        self.assertEqual(result['claims_detected'][-1]['antecedent_count_evidence']['required_valid_minimum'],3)

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
        for xml in ['<m:r><m:t>α</m:t></m:r>','<m:r><m:t>ℓ</m:t></m:r>',
                    '<m:r><m:t>é</m:t></m:r>','<m:r><m:t>g=1</m:t></m:r>','<m:r><m:t>字</m:t></m:r>',
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
