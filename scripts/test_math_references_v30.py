"""Source-preserving grouped punctuation and complete inline references."""
from copy import deepcopy
import unittest
from lxml import etree as ET
import authoring_toolkit as t
import math_fragments as f
import test_authoring_formula_prose as fixtures
from test_authoring_formula_prose import flat


def mr(text):return '<m:r><m:t>'+text+'</m:t></m:r>'
def math(text):return '<m:oMath xmlns:m="'+t.NS['m']+'">'+text+'</m:oMath>'
def array(a,b):return math('<m:eqArr><m:e>'+a+'</m:e><m:e>'+b+'</m:e></m:eqArr>')
OPERATOR=math(mr('y=')+mr('sg')+'<m:d><m:e><m:sSub><m:e>'+mr('Z')+'</m:e><m:sub>'+mr('i')+'</m:sub></m:sSub></m:e></m:d>')


class ReferenceTests(unittest.TestCase):
    setUp=fixtures.FormulaProseTests.setUp
    source=fixtures.FormulaProseTests.source
    create=fixtures.FormulaProseTests.create
    mutate=fixtures.FormulaProseTests.mutate

    def test_missing_group_comma_is_added_without_changing_source_or_equation_count(self):
        original=array(mr('a=b'),mr('c=d；'))
        fid=self.source([original],['main_formula'])[0]
        self.spec['formulas'][0]['equation_count']=2
        self.spec['formula_policy']['source_main_count']=2
        self.spec['sections'][3]['blocks'].append(dict(id='group',type='formula',formula_id=fid))
        before=t.sha(self.root/'source.docx');spec=deepcopy(self.spec)
        context,target,result,doc=self.create()
        rows=doc.findall('.//m:eqArr/m:e',t.NS)
        self.assertEqual([''.join(r.xpath('.//m:t/text()',namespaces=t.NS)) for r in rows],['a=b，','c=d；'])
        self.assertEqual(t.sha(self.root/'source.docx'),before)
        self.assertEqual(self.spec,spec)
        self.assertEqual(result['counts']['main_formula_occurrences'],2)
        self.assertNotEqual(result['source_objects'][0]['tree_sha256'],result['source_objects'][0]['display_tree_sha256'])
        def remove(root):
            row=root.find('.//m:eqArr/m:e',t.NS);row.remove(row[-1])
        self.mutate(target,remove)
        with self.assertRaises(t.AuthoringError):t.verify_document(context,target)

    def test_group_punctuation_is_idempotent_and_does_not_enter_arguments(self):
        for end in ['',',','，',';','；']:
            node=ET.fromstring(array(mr('a=b'+end),mr('c=d')))
            result=f.equation_group_punctuation(node,2)
            self.assertEqual(t.tree_signature(result),t.tree_signature(f.equation_group_punctuation(result,2)))
            self.assertEqual(''.join(result.findall('m:eqArr/m:e',t.NS)[0].xpath('.//m:t/text()',namespaces=t.NS))[-1],',' if end==',' else '，')
        wrapped=ET.fromstring(math('<m:d><m:e><m:eqArr><m:e>'+mr('x=1')+'</m:e><m:e>'+mr('y=2')+'</m:e></m:eqArr></m:e></m:d>'))
        self.assertIs(f.equation_group_punctuation(wrapped,2),wrapped)
        for a,b in [('x=','y=z'),('x=y','+z'),('x=y,ifz>0','q=r')]:
            with self.assertRaises(ValueError):f.equation_group_punctuation(ET.fromstring(array(mr(a),mr(b))),2)

    def test_complete_composite_symbol_explanation_keeps_source_fragment_identity(self):
        fid=self.source([math(mr('Δ')+mr('ζ')+mr('=a-b'))],['main_formula'])[0]
        self.spec['sections'][3]['blocks'] += [dict(id='eq',type='formula',formula_id=fid),
            dict(id='example',type='paragraph',role='body',runs=[dict(type='text',text='其中，'),
                 dict(type='source_symbol',formula_id=fid,node_paths=['./m:r[1]','./m:r[2]']),
                 dict(type='text',text='表示原式中的完整量。')])]
        _,_,result,doc=self.create()
        last=doc.findall('.//m:oMath',t.NS)[-1]
        self.assertEqual(''.join(last.xpath('.//m:t/text()',namespaces=t.NS)),'Δζ')
        self.assertEqual(t.word_text(last.getparent()),'其中，表示原式中的完整量。')
        self.assertEqual(result['counts']['main_formula_occurrences'],1)
        self.assertEqual(result['source_objects'][-1]['source_fragment_path'],['./m:r[1]','./m:r[2]'])
        for paths in [[],['./m:r[2]','./m:r[1]'],['./m:r[1]','./m:r[3]'],['./m:r[1]','./m:r[1]']]:
            with self.assertRaises(ValueError):f.symbol_fragment(ET.fromstring(math(mr('Δ')+mr('ζ')+mr('=a-b'))),paths)

    def test_ambiguous_group_punctuation_and_matrix_conditions_are_not_automatically_edited(self):
        for first,last in [(mr('a=b,，'),mr('c=d；')),(mr('a=b,')+mr(';'),mr('c=d')),
                           (mr('a=b'),mr('c=d；；'))]:
            node=ET.fromstring(array(first,last));before=t.tree_signature(node)
            with self.assertRaises(ValueError):f.equation_group_punctuation(node,2)
            self.assertEqual(t.tree_signature(node),before)
        for operand in ['<m:m><m:mr><m:e>'+mr('1')+'</m:e></m:mr></m:m>',
                        '<m:d><m:dPr><m:begChr m:val="{"/></m:dPr><m:e>'+mr('a')+'</m:e></m:d>']:
            with self.assertRaises(ValueError):f.equation_group_punctuation(ET.fromstring(array(mr('x=')+operand,mr('c=d'))),2)

    def test_operator_reference_keeps_name_and_argument_placeholder_not_actual_argument(self):
        fid=self.source([OPERATOR],['main_formula'])[0]
        self.spec['sections'][3]['blocks'] += [dict(id='eq',type='formula',formula_id=fid),
            dict(id='explain',type='paragraph',role='body',runs=[dict(type='text',text='其中，'),
                 dict(type='source_operator',formula_id=fid,name_path='./m:r[2]',argument_path='./m:d[1]'),
                 dict(type='text',text='表示该算子。')])]
        before=t.sha(self.root/'source.docx')
        context,target,result,doc=self.create()
        main,ref=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(t.tree_signature(main),t.tree_signature(t.upright_math_display(ET.fromstring(OPERATOR))))
        self.assertEqual(''.join(ref.xpath('.//m:t/text()',namespaces=t.NS)),'sg·')
        self.assertIsNotNone(ref.find('m:d/m:e/m:r/m:t',t.NS))
        self.assertEqual(result['source_objects'][-1]['source_transform'],'operator_argument_placeholder')
        self.assertEqual(t.sha(self.root/'source.docx'),before)
        self.assertEqual(result['counts']['main_formula_occurrences'],1)
        def alter(root):root.findall('.//m:oMath',t.NS)[-1].find('m:d/m:e/m:r/m:t',t.NS).text='x'
        self.mutate(target,alter)
        with self.assertRaises(t.AuthoringError):t.verify_document(context,target)

    def test_operator_reference_rejects_nonadjacent_and_multiargument_selection(self):
        source=ET.fromstring(OPERATOR)
        for name,arg in [('./m:r[1]','./m:d[1]'),('./m:r[2]','./m:d[2]')]:
            with self.assertRaises(ValueError):f.operator_reference(source,name,arg)
        source=ET.fromstring(math(mr('sg')+'<m:d><m:e>'+mr('x,y')+'</m:e></m:d>'))
        with self.assertRaises(ValueError):f.operator_reference(source,'./m:r[1]','./m:d[1]')

    def test_legacy_split_assignment_cannot_force_bare_letter_into_math(self):
        fid=self.source([math(mr('x')+mr('=y'))],['main_formula'])[0]
        self.spec['sections'][3]['blocks'] += [dict(id='eq',type='formula',formula_id=fid),
            dict(id='refs',type='paragraph',role='body',runs=[
                dict(type='source_symbol',formula_id=fid,node_path='./m:r[1]'),dict(type='text',text='表示索引，当'),
                dict(type='source_symbol',formula_id=fid,node_path='./m:r[1]',representation='math'),dict(type='text',text='=4时继续。')])]
        _,_,result,doc=self.create()
        refs=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(len(refs),1)
        paragraph=next(p for p in doc.findall('.//w:p',t.NS) if t.word_text(p)=='x表示索引，当x=4时继续。')
        self.assertEqual(result['counts']['source_text_occurrences'],2)
        self.assertEqual(result['counts']['main_formula_occurrences'],1)
        import inspect_docx
        evidence=inspect_docx.formula_prose_paragraph_evidence(paragraph,'word/document.xml',1,{id(n):f'm{i}' for i,n in enumerate(refs)})
        self.assertEqual([h['text'] for h in evidence['review_candidates']],['x=4'])

    def test_operator_name_italic_word_override_is_preserved_in_reference(self):
        node=ET.fromstring(OPERATOR)
        name=node.findall('m:r',t.NS)[1]
        props=ET.SubElement(name,t.qn('w:rPr'));ET.SubElement(props,t.qn('w:i')).set(t.qn('w:val'),'1')
        before=t.tree_signature(node)
        ref=f.operator_reference(node,'./m:r[2]','./m:d[1]')
        self.assertEqual(ref.find('m:r/w:rPr/w:i',t.NS).get(t.qn('w:val')),'1')
        self.assertEqual(t.tree_signature(node),before)

    def test_symbolic_inline_formula_with_numeric_constant_stays_complete(self):
        sources=[flat('L=log(1+exp(−Δζ))'),flat('x=−4')]
        ids=self.source(sources,['main_formula']*2)
        self.spec['sections'][3]['blocks'].append(dict(id='inline',type='paragraph',role='body',runs=[
            dict(type='text',text='按'),dict(type='formula',formula_id=ids[0]),dict(type='text',text='计算，当'),
            dict(type='formula',formula_id=ids[1]),dict(type='text',text='时继续。')]))
        _,_,result,doc=self.create()
        nodes=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(len(nodes),2)
        for node,source in zip(nodes,sources):
            self.assertEqual(t.tree_signature(node),t.tree_signature(t.upright_math_display(ET.fromstring(source))))
        self.assertEqual(t.word_text(nodes[0].getparent()),'按计算，当时继续。')
        self.assertEqual(result['counts']['main_formula_occurrences'],2)


if __name__=='__main__':unittest.main()
