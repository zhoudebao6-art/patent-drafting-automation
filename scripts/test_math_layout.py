"""Regression tests for real formula layout failure modes, not math approval."""
import tempfile
import unittest
from xml.etree import ElementTree as ET
from inspect_docx import NS, math_evidence, inspect, precheck
import test_inspect_docx as fixtures


def run(text):
    return f'<m:r><m:t>{text}</m:t></m:r>'


def math(body):
    return ET.fromstring('<m:oMath ' + ' '.join(f'xmlns:{k}="{v}"' for k, v in NS.items()) + '>' + body + '</m:oMath>')


class MathLayoutTests(unittest.TestCase):
    def data(self, body):
        with tempfile.TemporaryDirectory() as root:
            path = fixtures.InspectionTests().make_docx(root, body)
            before = path.read_bytes()
            data = inspect(path)
            precheck(data, 'proposal')
            self.assertEqual(path.read_bytes(), before)
            return data

    def codes(self, body):
        return {issue['code'] for issue in self.data(body)['issues']}

    def test_screenshot_operator_only_first_row_without_break_tag(self):
        body = '<m:eqArr><m:e>' + run('C(θ)=') + '</m:e><m:e>' + run('a∫L(λ)dλ') + '</m:e></m:eqArr>'
        data = self.data('<w:p>' + ET.tostring(math(body), encoding='unicode') + '</w:p>')
        self.assertIn('MATH_ARRAY_TRAILING_OPERATOR', {i['code'] for i in data['issues']})
        layout = data['formulas'][0]['math_evidence']['layout']
        self.assertEqual(layout['explicit_breaks_or_tabs'], [])
        self.assertEqual(layout['equation_arrays'][0]['row_count'], 2)

    def test_nested_conditions_are_preserved_not_display_breaks(self):
        conditions = '<m:eqArr><m:e>' + run('q=1') + '</m:e><m:e>' + run('q≠p') + '</m:e></m:eqArr>'
        node = math('<m:nary><m:sub>' + conditions + '</m:sub><m:sup/><m:e>' + run('x') + '</m:e></m:nary>')
        before = ET.tostring(node)
        evidence = math_evidence(node)
        self.assertFalse(evidence['layout']['equation_arrays'][0]['top_level'])
        self.assertEqual(evidence['layout']['empty_required_slots'], [])
        self.assertEqual(ET.tostring(node), before)

    def test_multi_equation_array_has_no_orphan_relation_warning(self):
        node = math('<m:eqArr><m:e>' + run('x=1') + '</m:e><m:e>' + run('y=2') + '</m:e></m:eqArr>')
        self.assertNotIn('MATH_ARRAY_TRAILING_OPERATOR', self.codes('<w:p>' + ET.tostring(node, encoding='unicode') + '</w:p>'))

    def test_blank_subscript_base_and_displaced_limit_not_hidden_by_spaces(self):
        node = math('<m:limLow><m:e>' + run('argmin') + '<m:sSub><m:e>' + run(' ') + '</m:e><m:sub>' + run('x∈S') + '</m:sub></m:sSub></m:e><m:lim>' + run(' ') + '</m:lim></m:limLow>')
        gaps = math_evidence(node)['layout']['empty_required_slots']
        self.assertEqual({(g['element'], g['slot']) for g in gaps}, {('limLow', 'lim'), ('sSub', 'e')})
        correct = math('<m:limLow><m:e>' + run('argmin') + '</m:e><m:lim>' + run('x∈S') + '</m:lim></m:limLow>')
        self.assertEqual(math_evidence(correct)['layout']['empty_required_slots'], [])

    def test_legitimate_hidden_nary_upper_limit_is_not_missing_operand(self):
        node = math('<m:nary><m:naryPr><m:supHide m:val="1"/></m:naryPr><m:sub>' + run('i∈I') + '</m:sub><m:sup/><m:e>' + run('x') + '</m:e></m:nary>')
        self.assertEqual(math_evidence(node)['layout']['empty_required_slots'], [])

    def test_direct_conflict_is_flagged_but_semantic_bold_is_preserved(self):
        body = '<m:r><m:rPr><m:sty m:val="p"/></m:rPr><w:rPr><w:b/></w:rPr><m:t>x</m:t></m:r>'
        self.assertIn('MATH_STYLE_CONFLICT', self.codes('<w:p>' + ET.tostring(math(body), encoding='unicode') + '</w:p>'))
        vector = math(body.replace('m:val="p"', 'm:val="b"'))
        evidence = math_evidence(vector)
        self.assertEqual(evidence['layout']['conflicting_run_properties'], [])
        self.assertEqual(evidence['runs'][0]['math_run_properties_explicit']['sty'], 'b')

    def test_paragraph_clip_risk_and_character_indent_are_reported(self):
        body = '<w:p><w:pPr><w:ind w:firstLine="0" w:firstLineChars="200"/><w:spacing w:line="240" w:lineRule="exact"/></w:pPr><m:oMath>' + run('x=y') + '</m:oMath></w:p>'
        self.assertTrue({'MATH_FIXED_LINE_HEIGHT', 'MATH_DISPLAY_INDENT'} <= self.codes(body))
        mixed = body.replace('<m:oMath>', '<w:r><w:t>其中，</w:t></w:r><m:oMath>')
        self.assertNotIn('MATH_DISPLAY_INDENT', self.codes(mixed))

    def test_missing_direct_properties_stay_unknown(self):
        data = self.data('<w:p><m:oMath>' + run('x=y') + '</m:oMath></w:p>')
        self.assertIsNone(data['parts'][0]['paragraphs'][0]['layout_evidence']['spacing_explicit'])
        self.assertIsNone(data['formulas'][0]['math_evidence']['runs'][0]['word_run_properties_explicit'])

    def test_nontext_tabs_and_breaks_are_not_silently_ignored(self):
        node = math('<m:r><m:rPr><m:brk/></m:rPr><m:t>x</m:t><w:tab/><w:br/></m:r>')
        self.assertEqual(len(math_evidence(node)['layout']['explicit_breaks_or_tabs']), 3)


if __name__ == '__main__':
    unittest.main()
