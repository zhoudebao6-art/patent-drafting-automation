"""One source equation may change display rows and size, never mathematical identity."""
from copy import deepcopy
import math
import unittest

from lxml import etree as ET

import authoring_toolkit as t
import batch_tool as b
import inspect_docx as scan
import output_contract as contract
import single_line_math as layout
import test_authoring_formula_prose as fixtures


OPTIONS = {'classification': 'single_equation', 'font_size_pt': 12, 'source_base_pt': 14}


def mr(text, properties=''):
    return '<m:r>' + properties + '<m:t>' + text + '</m:t></m:r>'


def omml(body):
    return '<m:oMath xmlns:m="' + t.NS['m'] + '" xmlns:w="' + t.NS['w'] + '">' + body + '</m:oMath>'


def array(*rows, props=''):
    return omml('<m:eqArr>' + props + ''.join('<m:e>' + row + '</m:e>' for row in rows) + '</m:eqArr>')


def sub(letter, index):
    return '<m:sSub><m:e>' + mr(letter) + '</m:e><m:sub>' + mr(index) + '</m:sub></m:sSub>'


def sum_term():
    # The nested array is a genuine operator limit, not a display continuation.
    limits = '<m:eqArr><m:e>' + mr('i=1') + '</m:e><m:e>' + mr('i≠q') + '</m:e></m:eqArr>'
    return ('<m:nary><m:naryPr><m:chr m:val="∑"/><m:limLoc m:val="undOvr"/>'
            '<m:ctrlPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></m:ctrlPr></m:naryPr>'
            '<m:sub>' + limits + '</m:sub><m:sup>' + mr('H') + '</m:sup><m:e>'
            '<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr><m:sub>' + mr('j=0')
            + '</m:sub><m:sup>' + mr('N') + '</m:sup><m:e><m:f><m:num>' + sub('a', 'q,i,j')
            + '</m:num><m:den>' + mr('2') + '</m:den></m:f></m:e></m:nary></m:e></m:nary>')


def three_rows(terminal='；'):
    hat = '<m:acc><m:accPr><m:chr m:val="̂"/></m:accPr><m:e>' + mr('y') + '</m:e></m:acc>'
    lhs = '<m:sSub><m:e>' + hat + '</m:e><m:sub>' + mr('q') + '</m:sub></m:sSub>'
    return array(lhs + mr('(k+h|k)=') + sub('y', 'q') + mr('(k)'),
                 mr('+') + sum_term(), mr('+') + sum_term() + (mr(terminal) if terminal else ''))


def without_sizes(node):
    """Ignore only allowed font-size edits, retaining all mathematical and style nodes."""
    result = deepcopy(node)
    for prop in result.xpath('.//w:sz | .//w:szCs', namespaces=t.NS):
        prop.getparent().remove(prop)
    for props in result.xpath('.//w:rPr', namespaces=t.NS):
        if not len(props) and not props.attrib and not (props.text or '').strip():
            props.getparent().remove(props)
    return t.tree_signature(result)


class SingleLineMathTests(unittest.TestCase):
    setUp = fixtures.FormulaProseTests.setUp
    source = fixtures.FormulaProseTests.source
    create = fixtures.FormulaProseTests.create
    mutate = fixtures.FormulaProseTests.mutate

    def formula(self, content=None, options=OPTIONS):
        fid = self.source([content or three_rows()], ['main_formula'])[0]
        self.spec['formulas'][0]['equation_count'] = 1
        block = dict(id='single', type='formula', formula_id=fid)
        if options is not None:
            block['single_line'] = deepcopy(options)
        self.spec['sections'][3]['blocks'].append(block)
        return fid, block

    def inspect_target(self, target):
        before = t.sha(target)
        report = scan.inspect(target)
        scan.precheck(report, 'draft')
        self.assertEqual(t.sha(target), before)
        return report

    def test_three_rows_are_one_source_equation_with_same_order_and_native_operands(self):
        self.formula()
        before_spec = deepcopy(self.spec)
        source_sha = t.sha(self.root / 'source.docx')
        source = t.upright_math_display(ET.fromstring(three_rows()))
        _, _, result, document = self.create()
        display = document.find('.//m:oMath', t.NS)
        source_children = [child for row in source.findall('m:eqArr/m:e', t.NS) for child in row]
        self.assertEqual([without_sizes(c) for c in display], [without_sizes(c) for c in source_children])
        self.assertIsNone(display.find('./m:eqArr', t.NS))
        self.assertEqual(len(display.findall('.//m:nary', t.NS)), 4)
        self.assertEqual(len(display.findall('.//m:nary/m:sub/m:eqArr', t.NS)), 2)
        self.assertEqual(result['counts']['main_formula_occurrences'], 1)
        self.assertEqual(result['counts']['omml_occurrences'], 1)
        self.assertEqual(result['source_objects'][0]['equation_count'], 1)
        self.assertNotEqual(result['source_objects'][0]['source_tree_sha256'], result['source_objects'][0]['display_tree_sha256'])
        self.assertEqual(t.sha(self.root / 'source.docx'), source_sha)
        self.assertEqual(self.spec, before_spec)

    def test_first_row_ending_at_equality_can_join_its_complete_rhs(self):
        node = ET.fromstring(array(mr('x='), sub('y', 'i'), mr('+') + sum_term() + mr('；')))
        before = t.tree_signature(node)
        result = layout.render(node, OPTIONS, 1)
        self.assertEqual(''.join(result.xpath('./m:r/m:t/text()', namespaces=t.NS)), 'x=+；')
        self.assertIsNone(result.find('m:eqArr', t.NS))
        self.assertEqual(t.tree_signature(node), before)

    def test_existing_single_line_scales_locally_and_preserves_symbol_styles_and_root_metadata(self):
        style = ('<m:rPr><m:sty m:val="b"/><m:scr m:val="script"/></m:rPr>'
                 '<w:rPr><w:rFonts w:ascii="Cambria Math"/><w:b/><w:sz w:val="28"/></w:rPr>')
        node = ET.fromstring(omml(mr('A', style) + mr('=') + '<m:sSup><m:e>' + mr('x')
                                 + '</m:e><m:sup>' + mr('2') + '</m:sup></m:sSup>'))
        node.set('{urn:test}source-note', 'retain')
        original = t.tree_signature(node)
        display = layout.render(node, OPTIONS, 1)
        self.assertEqual(display.attrib, node.attrib)
        self.assertEqual(without_sizes(display), without_sizes(node))
        self.assertEqual(display.find('m:r/m:rPr/m:sty', t.NS).get(t.qn('m:val')), 'b')
        self.assertEqual(display.find('m:r/m:rPr/m:scr', t.NS).get(t.qn('m:val')), 'script')
        self.assertEqual(t.tree_signature(node), original)

    def test_array_reflow_preserves_root_metadata_and_math_properties(self):
        node = ET.fromstring(three_rows())
        node.set('{urn:test}source-note', 'retain')
        prop = ET.Element(t.qn('m:oMathPr'))
        node.insert(0, prop)
        display = layout.render(node, OPTIONS, 1)
        self.assertEqual(display.attrib, node.attrib)
        self.assertIsNotNone(display.find('m:oMathPr', t.NS))
        self.assertEqual(t.tree_signature(display[0]), t.tree_signature(prop))

    def test_explicit_sizes_keep_ratios_and_missing_size_slot_uses_existing_partner(self):
        content = omml(mr('a', '<w:rPr><w:sz w:val="28"/><w:szCs w:val="14"/></w:rPr>')
                       + mr('=', '<w:rPr><w:sz w:val="21"/></w:rPr>')
                       + mr('b', '<w:rPr><w:szCs w:val="7"/></w:rPr>') + mr('+c'))
        source = ET.fromstring(content)
        display = layout.render(source, OPTIONS, 1)
        values = [(r.find('w:rPr/w:sz', t.NS).get(t.qn('w:val')),
                   r.find('w:rPr/w:szCs', t.NS).get(t.qn('w:val'))) for r in display.findall('m:r', t.NS)]
        self.assertEqual(values, [('24', '12'), ('18', '18'), ('6', '6'), ('24', '24')])
        self.assertEqual(without_sizes(source), without_sizes(display))

    def test_operator_control_size_scales_without_changing_limit_location(self):
        result = layout.render(ET.fromstring(three_rows()), OPTIONS, 1)
        controls = result.findall('.//m:ctrlPr/w:rPr', t.NS)
        self.assertEqual(len(controls), 2)
        for props in controls:
            self.assertEqual(props.find('w:sz', t.NS).get(t.qn('w:val')), '24')
            self.assertEqual(props.find('w:szCs', t.NS).get(t.qn('w:val')), '24')
            self.assertIsNotNone(props.find('w:b', t.NS))
        self.assertTrue(all(n.get(t.qn('m:val')) == 'undOvr' for n in result.findall('.//m:limLoc', t.NS)))

    def test_complete_equation_groups_and_conditions_are_rejected_without_source_edits(self):
        for content, count in [(array(mr('a=b'), mr('c=d；')), 1),
                               (array(mr('a=b'), mr('c=d；')), 2),
                               (omml(mr('a=b，c=d；')), 1),
                               (array(mr('x=1'), mr('0，otherwise')), 1),
                               (array(mr('x=1'), mr('+2，条件成立')), 1),
                               (array(mr('x=1，'), mr('+2；')), 1)]:
            with self.subTest(content=content, count=count):
                node = ET.fromstring(content)
                before = t.tree_signature(node)
                with self.assertRaises(ValueError):
                    layout.render(node, OPTIONS, count)
                self.assertEqual(t.tree_signature(node), before)

    def test_standalone_matrix_piecewise_and_nested_limit_arrays_are_never_flattened(self):
        expressions = [
            '<m:m><m:mr><m:e>' + mr('a=b') + '</m:e><m:e>' + mr('c') + '</m:e></m:mr></m:m>',
            '<m:d><m:dPr><m:begChr m:val="{"/></m:dPr><m:e><m:eqArr><m:e>' + mr('x=1')
            + '</m:e><m:e>' + mr('y=2') + '</m:e></m:eqArr></m:e></m:d>',
            sum_term(),
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                node = ET.fromstring(omml(expression))
                before = t.tree_signature(node)
                self.assertIsNone(layout.continuation_rows(node))
                with self.assertRaises(ValueError):
                    layout.render(node, OPTIONS, 1)
                self.assertEqual(t.tree_signature(node), before)

    def test_unknown_discarded_shell_properties_and_attributes_are_rejected(self):
        for props in [
            '<m:eqArrPr><m:ctrlPr><w:rPr><w:b/></w:rPr></m:ctrlPr></m:eqArrPr>',
            '<m:eqArrPr><m:unknown m:val="1"/></m:eqArrPr>',
            '<m:eqArrPr><m:baseJc m:val="center" w:unknown="1"/></m:eqArrPr>',
            '<m:eqArrPr><m:baseJc m:val="center">hidden</m:baseJc></m:eqArrPr>',
        ]:
            with self.subTest(props=props), self.assertRaises(ValueError):
                layout.render(ET.fromstring(array(mr('x=y'), mr('+z；'), props=props)), OPTIONS, 1)
        for path in ('m:eqArr', 'm:eqArr/m:e'):
            node = ET.fromstring(array(mr('x=y'), mr('+z；')))
            node.find(path, t.NS).set('{urn:test}unknown', '1')
            with self.subTest(path=path), self.assertRaises(ValueError):
                layout.render(node, OPTIONS, 1)

    def test_duplicate_properties_or_nonwhitespace_shell_content_is_not_silently_lost(self):
        duplicate = ('<m:eqArrPr><m:baseJc m:val="center"/></m:eqArrPr>'
                     '<m:eqArrPr><m:ctrlPr><w:rPr><w:b/></w:rPr></m:ctrlPr></m:eqArrPr>')
        node = ET.fromstring(array(mr('x=y'), mr('+z；'), props=duplicate))
        with self.assertRaises(ValueError):
            layout.render(node, OPTIONS, 1)
        for path, slot in [('m:eqArr', 'text'), ('m:eqArr', 'tail'),
                           ('m:eqArr/m:e', 'text'), ('m:eqArr/m:e', 'tail')]:
            node = ET.fromstring(array(mr('x=y'), mr('+z；')))
            setattr(node.find(path, t.NS), slot, 'unclassified-content')
            before = t.tree_signature(node)
            with self.subTest(path=path, slot=slot), self.assertRaises(ValueError):
                layout.render(node, OPTIONS, 1)
            self.assertEqual(t.tree_signature(node), before)

    def test_invalid_sizes_or_classification_cannot_bypass_single_equation_contract(self):
        variants = [None, {}, dict(OPTIONS, classification='group'), dict(OPTIONS, extra=True)]
        variants += [dict(OPTIONS, font_size_pt=value) for value in [True, math.nan, math.inf, 8.5, 14.5, 12.25, '12']]
        variants += [dict(OPTIONS, source_base_pt=value) for value in [False, 11, math.nan, '14', 73]]
        for options in variants:
            with self.subTest(options=options), self.assertRaises(ValueError):
                layout.render(ET.fromstring(three_rows()), options, 1)

    def test_supported_local_sizes_keep_the_same_one_equation_and_source_tree(self):
        node = ET.fromstring(three_rows())
        before = t.tree_signature(node)
        for size in (9, 9.5, 10.5, 14):
            with self.subTest(size=size):
                display = layout.render(node, dict(OPTIONS, font_size_pt=size), 1)
                self.assertIsNone(display.find('./m:eqArr', t.NS))
                self.assertEqual(len(display.findall('.//m:nary', t.NS)), 4)
                first_size = display.find('.//m:r/w:rPr/w:sz', t.NS).get(t.qn('w:val'))
                self.assertEqual(first_size, str(int(size * 2)))
                self.assertEqual(t.tree_signature(node), before)

    def test_display_breaks_and_single_line_are_mutually_exclusive(self):
        _, block = self.formula()
        block['display_breaks'] = [2]
        with self.assertRaisesRegex(t.AuthoringError, 'mutually exclusive'):
            t.validate(self.root, self.spec)

    def test_only_direct_display_breaks_are_removed_and_nested_breaks_are_rejected(self):
        node = ET.fromstring(omml(mr('x=', '<m:rPr><m:brk/><m:aln/></m:rPr>') + mr('y')))
        display = layout.render(node, OPTIONS, 1)
        self.assertFalse(display.xpath('.//m:brk | .//m:aln', namespaces=t.NS))
        self.assertTrue(node.xpath('.//m:brk | .//m:aln', namespaces=t.NS))
        for mark in ('<m:brk/>', '<w:br/>', '<w:tab/>'):
            nested = ET.fromstring(omml(mr('x=') + '<m:sSub><m:e>' + mr('a', '<m:rPr>' + mark + '</m:rPr>')
                                        + '</m:e><m:sub>' + mr('i') + '</m:sub></m:sSub>'))
            with self.subTest(mark=mark), self.assertRaises(ValueError):
                layout.render(nested, OPTIONS, 1)

    def test_suffix_and_paragraph_size_match_local_formula_without_changing_body_size(self):
        self.formula(three_rows(terminal=''))
        _, _, _, document = self.create()
        formula = document.find('.//m:oMath', t.NS)
        paragraph = formula.getparent()
        self.assertEqual(t.word_text(paragraph), '；')
        for path in ('w:pPr/w:rPr', 'w:r/w:rPr'):
            props = paragraph.find(path, t.NS)
            self.assertEqual(props.find('w:sz', t.NS).get(t.qn('w:val')), '24')
            self.assertEqual(props.find('w:szCs', t.NS).get(t.qn('w:val')), '24')
        body_run = next(r for r in document.findall('.//w:r', t.NS) if t.word_text(r).startswith('本页检查'))
        self.assertEqual(body_run.find('w:rPr/w:sz', t.NS).get(t.qn('w:val')), '28')

    def test_readback_rejects_math_index_order_and_size_corruption(self):
        self.formula()
        context, target, _, _ = self.create()
        def index(root):
            root.find('.//m:oMath/m:sSub/m:sub/m:r/m:t', t.NS).text = 'r'
        def order(root):
            node = root.find('.//m:oMath', t.NS)
            child = node[0]
            node.remove(child)
            node.append(child)
        def size(root):
            root.find('.//m:oMath//w:sz', t.NS).set(t.qn('w:val'), '28')
        for change in (index, order, size):
            t.render_document(context, target)
            self.mutate(target, change)
            with self.subTest(change=change.__name__), self.assertRaises(t.AuthoringError):
                t.verify_document(context, target)

    def test_readback_rejects_paragraph_and_both_suffix_size_slots(self):
        self.formula(three_rows(terminal=''))
        context, target, _, _ = self.create()
        for path in ('w:pPr/w:rPr/w:sz', 'w:pPr/w:rPr/w:szCs', 'w:r/w:rPr/w:sz', 'w:r/w:rPr/w:szCs'):
            t.render_document(context, target)
            def corrupt(root):
                paragraph = root.find('.//m:oMath', t.NS).getparent()
                paragraph.find(path, t.NS).set(t.qn('w:val'), '28')
            self.mutate(target, corrupt)
            with self.subTest(path=path), self.assertRaises(t.AuthoringError):
                t.verify_document(context, target)

    def test_inspection_and_gate_reject_remaining_continuation_but_accept_reflowed_artifact(self):
        _, block = self.formula(options=None)
        _, target, _, _ = self.create()
        report = self.inspect_target(target)
        self.assertTrue(report['formulas'][0]['math_evidence']['layout']['single_equation_continuation_candidate'])
        self.assertIn('MATH_SINGLE_EQUATION_CONTINUATION', {issue['code'] for issue in report['issues']})
        hit = report['formula_prose_evidence']['single_equation_continuation_hits'][0]
        finding = dict(hit, rule_id='FORMULA-05', classification='source_math_identity', status='closed',
                       artifact_sha256=report['sha256'], source_sha256=t.sha(self.root / 'source.docx'),
                       registry_id='f1', source_locator='source.docx equation 1', reason='Source-bound test finding')
        with self.assertRaisesRegex(b.BatchError, 'one equation must use one line'):
            contract.check(report, self.spec['project_title'], [finding], strict_formulas=True,
                           source_hashes=[t.sha(self.root / 'source.docx')])
        block['single_line'] = deepcopy(OPTIONS)
        _, target, _, _ = self.create()
        report = self.inspect_target(target)
        self.assertFalse(report['formulas'][0]['math_evidence']['layout']['single_equation_continuation_candidate'])
        self.assertEqual(report['formula_prose_evidence']['single_equation_continuation_hits'], [])
        contract.check(report, self.spec['project_title'], strict_formulas=True)
        self.assertIn('single_line_math.py', scan.extractor_dependencies())

    def test_complete_multiequation_group_is_not_misclassified_as_single_continuation(self):
        node = ET.fromstring(array(mr('a=b，'), mr('c=d；')))
        self.assertIsNone(layout.continuation_rows(node))
        self.assertFalse(scan.math_layout_evidence(node)['single_equation_continuation_candidate'])


if __name__ == '__main__':
    unittest.main()
