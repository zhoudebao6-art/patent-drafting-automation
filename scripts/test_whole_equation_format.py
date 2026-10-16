"""v40 whole-equation formatting, using synthetic source-neutral fixtures."""
import unittest
import batch_tool as b
import inspect_docx as scan
import test_formula_prose_contract as fixtures
from test_inline_formula_style import wr, sub

omml=fixtures.omml

class WholeEquationTests(unittest.TestCase):
    setUp=fixtures.FormulaProseTests.setUp
    report=fixtures.FormulaProseTests.report
    with_paragraph=fixtures.FormulaProseTests.with_paragraph
    accept=fixtures.FormulaProseTests.accept

    def check_span(self,content,expected,props=''):
        report=self.with_paragraph(content,props)
        hits=[h for h in report['formula_prose_evidence']['review_candidates']
              if h['reason']=='inline_symbolic_relation_requires_complete_source_math']
        self.assertEqual([h['text'] for h in hits],expected)
        if expected:
            with self.assertRaises(b.BatchError):self.accept(report)
        else:self.accept(report)
        return hits

    def test_screenshot_function_with_only_numeric_arguments(self):
        expression='max(0,min(10-0-6,6))=4'
        for content in [wr('计算得到'+expression+'，继续。'),
                        wr('计算得到max(0,')+wr('min(10-0-6,6))=4，继续。'),
                        wr('计算得到')+omml('max(0,min(10-0-6,6))')+wr('=4，继续。')]:
            with self.subTest(content=content):self.check_span(content,[expression])
        self.check_span(wr('计算得到')+omml(expression)+wr('，继续。'),[])

    def test_numeric_assignment_and_reversed_or_compound_left_side(self):
        for expression in ['x=1','Δx=−4','x=1e-3','x=1E+3','a+b=4','2*x=4',
                           '4=x+1','log(4)=2','f(x)=4','x=y=4','√4=2','x＝4']:
            with self.subTest(expression=expression):
                self.check_span(wr('当'+expression+'时继续。'),[expression])
                self.check_span(wr('当')+omml(expression)+wr('时继续。'),[])

    def test_math_object_ownership_prevents_fragment_equation_passing(self):
        for content in [omml('Δx')+wr('=−4'),omml('Δx=')+wr('−4'),
                        omml('Δx')+omml('=−4'),omml('Δx=')+omml('−4')]:
            hits=self.check_span(wr('取')+content+wr('，继续。'),['Δx=−4'])
            self.assertGreaterEqual(hits[0]['math_object_count'],1)

    def test_top_level_equations_separate_but_function_commas_are_preserved(self):
        self.check_span(wr('令x=1,y=max(0,min(1,2))，继续。'),['x=1','y=max(0,min(1,2))'])
        self.check_span(wr('令x=1,y=2,z=3。'),['x=1','y=2','z=3'])
        self.check_span(wr('令')+omml('x=1')+wr(',')+omml('y=2')+wr('。'),[])

    def test_structured_quantity_and_multiple_runs_in_one_object(self):
        structured='<m:oMath>'+sub('x','i,j')+'</m:oMath>'
        self.check_span(wr('当')+structured+wr('=4时。'),['xi,j=4'])
        complete='<m:oMath>'+sub('x','i,j')+fixtures.mr('=')+fixtures.mr('4')+'</m:oMath>'
        self.check_span(wr('当')+complete+wr('时。'),[])

    def test_centered_and_display_carriers_do_not_bypass_scan(self):
        center='<w:pPr><w:jc w:val="center"/></w:pPr>'
        expression='max(0,min(10-0-6,6))=4'
        self.check_span(wr(expression+'；'),[expression],center)
        self.check_span('<m:oMathPara>'+omml('Δx')+'</m:oMathPara>'+wr('=−4；'),['Δx=−4'])
        self.check_span('<m:oMathPara>'+omml(expression+'；')+'</m:oMathPara>',[],center)

    def test_numeric_examples_scientific_literals_and_labels_stay_plain(self):
        for text in ['3−0+1=4','1e-3+2e-3=3e-3','1E+2=100','(3+1)/2=2','2²=4','½+½=1',
                     '字段status=ready','总数量=4','地区52','其中，i表示索引']:
            with self.subTest(text=text):self.check_span(wr(text+'。'),[])

    def test_unicode_letterlike_scripts_and_accents_keep_the_full_equation(self):
        for expression in ['ℒ=log(1+exp(−Δx))','ℓ=4','ℏ=4','𝓛=4','x₁=4',
                           'x̂=4','x̅=4','x²=4','xᵢ=4','x=±4','ℤ=4','x⃗=4','xⁱ=4','x′=4','x=½']:
            with self.subTest(expression=expression):
                hits=self.check_span(wr('取'+expression+'，继续。'),[expression])
                self.assertEqual((hits[0]['offset'],hits[0]['end_offset']),(1,len(expression)+1))
                self.check_span(wr('取')+omml(expression)+wr('，继续。'),[])

    def test_known_source_function_name_extends_candidates_without_guessing_labels(self):
        text='smooth(0,1)=4'
        self.assertFalse(scan.inline_formula_candidates([('text',text)],'word/document.xml',1))
        hits=scan.inline_formula_candidates([('text',text)],'word/document.xml',1,{'smooth':['source-location']})
        self.assertEqual([h['text'] for h in hits],[text])

    def test_unmodified_span_offsets_include_internal_spaces_for_existing_spacing_review(self):
        text='当x = -4时继续。'
        hits=self.check_span(wr(text),['x = -4'])
        self.assertEqual(text[hits[0]['offset']:hits[0]['end_offset']],'x = -4')

if __name__=='__main__':unittest.main()
