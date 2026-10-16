"""v30 formula presentation boundaries; generated fixtures are not acceptance."""
import copy
import unittest
import batch_tool as b
import test_formula_prose_contract as fixtures

mr=fixtures.mr
omml=fixtures.omml


def wr(text):
    return '<w:r><w:t>'+text+'</w:t></w:r>'


def sub(base,index):
    return '<m:sSub><m:e>'+mr(base)+'</m:e><m:sub>'+mr(index)+'</m:sub></m:sSub>'


def array(*rows):
    return '<m:oMath><m:eqArr>'+''.join('<m:e>'+r+'</m:e>' for r in rows)+'</m:eqArr></m:oMath>'


class InlineFormulaStyleTests(unittest.TestCase):
    setUp=fixtures.FormulaProseTests.setUp
    report=fixtures.FormulaProseTests.report
    with_paragraph=fixtures.FormulaProseTests.with_paragraph
    accept=fixtures.FormulaProseTests.accept

    def finding(self,report,hit):
        finding=fixtures.FormulaProseTests.finding(self,report,hit)
        if 'offset' in hit:
            finding.update(format_disposition='not_formula',
                           reason='测试仅核验回执字段及范围绑定，不代表真实语义验收。')
        return finding

    def candidates(self,content):
        report=self.with_paragraph(content)
        return report,report['formula_prose_evidence']['review_candidates']

    def test_plain_and_cross_run_relations_are_exact_located_spans(self):
        for content in [wr('令Δζ=ζα−ζβ，随后计算。'),
                        wr('令Δ')+wr('ζ=')+wr('ζ')+wr('α−ζβ')+wr('，随后计算。')]:
            with self.subTest(content=content):
                report,hits=self.candidates(content)
                self.assertEqual([h['text'] for h in hits],['Δζ=ζα−ζβ'])
                hit=hits[0]
                self.assertEqual((hit['offset'],hit['end_offset']),(1,9))
                self.assertEqual(hit['reason'],'inline_symbolic_relation_requires_complete_source_math')
                with self.assertRaisesRegex(b.BatchError,'formula/prose review'):self.accept(report)

    def test_mixed_loss_formula_keeps_constant_one_in_complete_symbolic_relation(self):
        report,hits=self.candidates(wr('按原损失')+'<m:oMath>'+sub('L','α,β')+'</m:oMath>'+
                                   wr('=log(1+exp(−Δζ))，计算结果。'))
        self.assertEqual([h['text'] for h in hits],['Lα,β=log(1+exp(−Δζ))'])
        self.assertEqual(hits[0]['representation'],'mixed')
        self.assertEqual(hits[0]['reason'],'inline_symbolic_relation_requires_complete_source_math')
        complete=wr('按原损失')+'<m:oMath>'+sub('L','α,β')+mr('=log(1+exp(−Δζ))')+'</m:oMath>'+wr('，计算结果。')
        corrected,corrected_hits=self.candidates(complete)
        self.assertFalse(corrected_hits);self.accept(corrected)

    def test_numeric_assignment_is_one_whole_math_object_v40(self):
        for content in [wr('当Δζ=−4时继续。'),wr('当')+omml('Δζ')+wr('=−4时继续。'),
                        wr('当')+omml('Δζ=')+wr('−4时继续。')]:
            with self.subTest(content=content):
                report,hits=self.candidates(content)
                self.assertEqual(len(hits),1)
                self.assertEqual(hits[0]['reason'],'inline_symbolic_relation_requires_complete_source_math')
                with self.assertRaisesRegex(b.BatchError,'formula/prose review'):self.accept(report)
        for number in ['−4','-4','+4.5','0','1e-3']:
            report,hits=self.candidates(wr('当')+omml('Δζ='+number)+wr('时继续。'))
            self.assertFalse(hits);self.accept(report)

    def test_symbolic_expression_with_numeric_coefficients_is_not_assignment(self):
        report,hits=self.candidates(wr('令y=2*x+1，计算。'))
        self.assertEqual(hits[0]['reason'],'inline_symbolic_relation_requires_complete_source_math')
        corrected,hits=self.candidates(wr('令')+omml('y=2*x+1')+wr('，计算。'))
        self.assertFalse(hits);self.accept(corrected)

    def test_labels_numbers_and_chinese_equations_do_not_become_formulas(self):
        for text in ['字段status=ready时继续。','标签base=new，标签不变。','当前总数=4，继续。',
                     '地区52由3−0+1=4得到。','结果为4.0181。','说明等号=用于表达。']:
            with self.subTest(text=text):
                report,hits=self.candidates(wr(text));self.assertFalse(hits);self.accept(report)
        report,hits=self.candidates(wr('其中，')+omml('sg(·)')+wr('表示停止梯度操作。'))
        self.assertFalse(hits);self.accept(report)

    def test_same_paragraph_findings_require_exact_both_offsets(self):
        report,hits=self.candidates(wr('标记Δζ=ζα−ζβ，标记Δζ=ζα−ζβ。'))
        self.assertEqual(len(hits),2)
        findings=[self.finding(report,h) for h in hits]
        self.accept(report,findings,[fixtures.SOURCE_HASH])
        for disposition in [None,'source_formula','fixed','ignore']:
            edited=copy.deepcopy(findings);edited[0]['format_disposition']=disposition
            with self.subTest(disposition=disposition),self.assertRaisesRegex(b.BatchError,'formula/prose review'):
                self.accept(report,edited,[fixtures.SOURCE_HASH])
        edited=copy.deepcopy(findings);edited[0]['offset_projection']='plain_text_without_OMML'
        with self.assertRaisesRegex(b.BatchError,'formula/prose review'):
            self.accept(report,edited,[fixtures.SOURCE_HASH])
        with self.assertRaisesRegex(b.BatchError,'formula/prose review'):
            self.accept(report,[findings[0]],[fixtures.SOURCE_HASH])
        for field in ['offset','end_offset']:
            for change in ['omit','wrong','string']:
                edited=copy.deepcopy(findings)
                if change=='omit':edited[0].pop(field)
                elif change=='wrong':edited[0][field]+=1
                else:edited[0][field]=str(edited[0][field])
                with self.subTest(field=field,change=change),self.assertRaisesRegex(b.BatchError,'formula/prose review'):
                    self.accept(report,edited,[fixtures.SOURCE_HASH])

    def test_complete_group_nonfinal_comma_is_mandatory_and_not_waivable(self):
        centered='<w:pPr><w:jc w:val="center"/></w:pPr>'
        for punctuation in ['', ';','；']:
            report=self.with_paragraph(array(mr('x=y'+punctuation),mr('a=b；')),centered)
            hits=report['formula_prose_evidence']['group_separator_hits']
            self.assertEqual(len(hits),1)
            self.assertTrue(hits[0]['path'].endswith('/eqArr[1]/e[1]'))
            with self.assertRaisesRegex(b.BatchError,'equation group needs exactly one comma'):
                self.accept(report,[self.finding(report,hits[0])],[fixtures.SOURCE_HASH])
        for punctuation in [',','，']:
            report=self.with_paragraph(array(mr('x=y'+punctuation),mr('a=b；')),centered)
            self.assertFalse(report['formula_prose_evidence']['group_separator_hits']);self.accept(report)

    def test_three_equation_rows_each_need_separator_but_nested_comma_is_not_one(self):
        centered='<w:pPr><w:jc w:val="center"/></w:pPr>'
        first=mr('x=')+'<m:d><m:e>'+mr('f(a,b),')+'</m:e></m:d>'
        report=self.with_paragraph(array(first,mr('y=z'),mr('a=b；')),centered)
        self.assertEqual([h['row'] for h in report['formula_prose_evidence']['group_separator_hits']],[1,2])

    def test_duplicate_group_punctuation_in_one_or_adjacent_runs_is_rejected(self):
        centered='<w:pPr><w:jc w:val="center"/></w:pPr>'
        cases=[(mr('x=y,，'),mr('a=b；'),1,',，'),
               (mr('x=y,')+mr('，'),mr('a=b；'),1,',，'),
               (mr('x=y,'),mr('a=b；；'),2,'；；'),
               (mr('x=y,'),mr('a=b；')+mr(';'),2,'；;'),
               (mr('x=y;')+mr('，'),mr('a=b；'),1,';，')]
        for first,last,expected_row,punctuation in cases:
            with self.subTest(punctuation=punctuation,row=expected_row):
                report=self.with_paragraph(array(first,last),centered)
                hits=report['formula_prose_evidence']['group_separator_hits']
                self.assertEqual([(h['row'],h['terminal']) for h in hits],[(expected_row,punctuation)])
                with self.assertRaisesRegex(b.BatchError,'equation group needs exactly one comma'):
                    self.accept(report,[self.finding(report,hits[0])],[fixtures.SOURCE_HASH])
        nested=mr('x=')+'<m:d><m:e>'+mr('f(a,b),')+'</m:e></m:d>'+mr('，')
        report=self.with_paragraph(array(nested,mr('a=b；')),centered)
        self.assertFalse(report['formula_prose_evidence']['group_separator_hits']);self.accept(report)

    def test_continuation_condition_and_matrix_rows_are_not_guessed_groups(self):
        centered='<w:pPr><w:jc w:val="center"/></w:pPr>'
        for rows in [(mr('x=y+'),mr('z；')),(mr('x=1'),mr('x>0；')),
                     (mr('x=1ifx>0'),mr('x=0otherwise；')),
                     (mr('x=')+'<m:m><m:mr><m:e>'+mr('1')+'</m:e></m:mr></m:m>',mr('y=z；'))]:
            with self.subTest(rows=rows):
                report=self.with_paragraph(array(*rows),centered)
                self.assertFalse(report['formula_prose_evidence']['group_separator_hits'])

    def test_missing_group_evidence_cannot_pass_strict_contract(self):
        report=self.with_paragraph(wr('说明内容。'))
        report['formula_prose_evidence'].pop('group_separator_hits')
        with self.assertRaisesRegex(b.BatchError,'inspection'):self.accept(report)

    def test_operator_definition_candidates_come_from_actual_calls_not_names(self):
        for name in ['sg','mask','smooth']:
            with self.subTest(name=name):
                call='<m:oMath>'+mr(name)+'<m:d><m:e>'+mr('x')+'</m:e></m:d></m:oMath>'
                report,hits=self.candidates(call+wr('。其中，'+name+'表示相应操作。'))
                hits=[h for h in hits if h['reason']=='source_operator_explanation_requires_complete_argument_notation']
                self.assertEqual([h['text'] for h in hits],[name])
                self.assertEqual(hits[0]['operator_calls'][0]['object_id'],'F0001')
                with self.assertRaisesRegex(b.BatchError,'formula/prose review'):self.accept(report)
                corrected,hits=self.candidates(call+wr('。其中，')+omml(name+'(·)')+wr('表示相应操作。'))
                self.assertFalse(hits);self.accept(corrected)
                ordinary,hits=self.candidates(wr('其中，'+name+'表示普通字段标签。'))
                self.assertFalse(hits);self.accept(ordinary)

    def test_function_name_node_and_cross_run_definition_are_located(self):
        call='<m:oMath><m:func><m:fName>'+mr('clip')+'</m:fName><m:e>'+mr('x')+'</m:e></m:func></m:oMath>'
        report,hits=self.candidates(call+wr('。其中，cl')+wr('ip代表截断操作。'))
        hits=[h for h in hits if h['reason']=='source_operator_explanation_requires_complete_argument_notation']
        self.assertEqual([h['text'] for h in hits],['clip'])
        self.assertEqual(hits[0]['end_offset']-hits[0]['offset'],4)
        self.assertEqual(hits[0]['operator_calls'][0]['part'],'word/document.xml')


if __name__=='__main__':unittest.main()
