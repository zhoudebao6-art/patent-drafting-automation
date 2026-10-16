"""Bound notation must survive generation and exact DOCX readback."""
from copy import deepcopy
import unittest
from lxml import etree as ET
import authoring_toolkit as t
import math_fragments as f
import test_authoring_formula_prose as fixtures


def styled(text):
    return ('<m:r><m:rPr><m:sty m:val="i"/></m:rPr><w:rPr>'
            '<w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/>'
            '<w:sz w:val="28"/><w:i w:val="1"/></w:rPr><m:t>'+text+'</m:t></m:r>')


def math(content):
    return '<m:oMath xmlns:m="'+t.NS['m']+'" xmlns:w="'+t.NS['w']+'">'+content+'</m:oMath>'


class SymbolIdentity(unittest.TestCase):
    setUp=fixtures.FormulaProseTests.setUp
    source=fixtures.FormulaProseTests.source
    create=fixtures.FormulaProseTests.create
    mutate=fixtures.FormulaProseTests.mutate

    def test_source_symbol_auto_and_math_preserve_character_and_style(self):
        source=math(styled('I')+styled('l')+styled('i')+styled('ℓ'))
        fid=self.source([source],['main_formula'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
        for i in range(1,5):
            for representation in ('auto','math'):
                self.spec['sections'][3]['blocks'].append(dict(id=f'ref{i}{representation}',type='paragraph',role='body',runs=[
                    dict(type='source_symbol',formula_id=fid,node_path=f'./m:r[{i}]',representation=representation),
                    dict(type='text',text='表示源中对应量。')]))
        ctx,target,result,doc=self.create()
        refs=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(len(refs),9)
        original=ET.fromstring(source)
        for i,ref in enumerate(refs[1:]):
            self.assertEqual(t.tree_signature(ref[0]),t.tree_signature(original[i//2]))
        self.assertEqual(result['counts']['source_text_occurrences'],0)
        def change_style(root):
            root.findall('.//m:oMath',t.NS)[-1].find('m:r/m:rPr/m:sty',t.NS).set(t.qn('m:val'),'p')
        self.mutate(target,change_style)
        with self.assertRaises(t.AuthoringError):t.verify_document(ctx,target)

    def test_registered_and_bound_bare_symbol_reuse_source_not_body_font(self):
        source=math(styled('I'))
        fid=self.source([source],['symbol_reference'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='refs',type='paragraph',role='body',runs=[
            dict(type='formula',formula_id=fid),dict(type='text',text='表示已核定量；'),
            dict(type='symbol_reference',formula_id=fid,text='I'),dict(type='text',text='仍为同一量。')]))
        before=deepcopy(self.spec)
        _,_,result,doc=self.create()
        refs=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(len(refs),2)
        for ref in refs:self.assertEqual(t.tree_signature(ref),t.tree_signature(ET.fromstring(source)))
        self.assertEqual(result['counts']['source_text_occurrences'],0)
        self.assertEqual(before,self.spec)

    def test_bound_reference_cannot_override_source_style_or_use_lookalike(self):
        fid=self.source([math(styled('I'))],['symbol_reference'])[0]
        for extra in ({'style':'body'},{'style':'reference_letter'},{'text':'l'},{'text':'i'},{'text':'1'}):
            item=dict(type='symbol_reference',formula_id=fid,text='I');item.update(extra)
            self.spec['sections'][3]['blocks']=[dict(id='ref',type='paragraph',role='body',runs=[item])]
            message='style override' if 'style' in extra else 'complete registered bare symbol'
            with self.subTest(extra=extra),self.assertRaisesRegex(t.AuthoringError,message):t.validate(self.root,self.spec)

    def test_operator_name_keeps_style_while_only_argument_becomes_placeholder(self):
        node=ET.fromstring(math(styled('Bit')+'<m:d><m:e>'+styled('x')+'</m:e></m:d>'))
        before=t.tree_signature(node)
        ref=f.operator_reference(node,'./m:r[1]','./m:d[1]')
        self.assertEqual(t.tree_signature(ref[0]),t.tree_signature(node[0]))
        self.assertEqual(''.join(ref.xpath('.//m:t/text()',namespaces=t.NS)),'Bit·')
        self.assertEqual(t.tree_signature(node),before)

    def test_source_power_is_not_silently_stripped_to_obtain_a_quantity(self):
        node=ET.fromstring(math('<m:sSup><m:e>'+styled('I')+'</m:e><m:sup>'+styled('2')+'</m:sup></m:sSup>'
                               '<m:d><m:e>'+styled('t')+'</m:e></m:d>'))
        before=t.tree_signature(node)
        complete=f.symbol_fragment(node,['./m:sSup[1]','./m:d[1]'])
        self.assertEqual([t.tree_signature(n) for n in complete],[t.tree_signature(n) for n in node])
        self.assertEqual(''.join(complete.xpath('.//m:t/text()',namespaces=t.NS)),'I2t')
        with self.assertRaises(ValueError):f.symbol_fragment(node,'./m:sSup[1]/m:e[1]/m:r[1]')
        self.assertEqual(t.tree_signature(node),before)

    def test_unbound_ordinary_index_and_numeric_prose_stay_text(self):
        self.spec['sections'][3]['blocks'].append(dict(id='plain',type='paragraph',role='body',runs=[
            dict(type='symbol_reference',text='i'),dict(type='text',text='为独立索引，3−0+1=4。')]))
        _,_,result,doc=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],0)
        self.assertIn('3−0+1=4',''.join(doc.xpath('//w:t/text()',namespaces=t.NS)))


if __name__=='__main__':unittest.main()
