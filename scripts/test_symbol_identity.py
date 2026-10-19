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

    def test_source_symbol_auto_and_math_use_body_for_ordinary_letters_keep_special_glyph(self):
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
        self.assertEqual(len(refs),3)
        original=ET.fromstring(source)
        self.assertEqual(t.tree_signature(refs[0]),t.tree_signature(t.upright_math_display(original)))
        for ref in refs[1:]:
            self.assertEqual(''.join(ref.xpath('.//m:t/text()',namespaces=t.NS)),'ℓ')
            self.assertEqual(ref.find('m:r/m:rPr/m:sty',t.NS).get(t.qn('m:val')),'p')
        plain=[row for row in result['source_objects'] if row['kind']=='source_text']
        self.assertEqual([row['text'] for row in plain],['I','I','l','l','i','i'])
        self.assertEqual(result['counts']['source_text_occurrences'],6)
        self.assertTrue(all(row['source_tree_sha256']==t.tree_signature(original) for row in plain))
        def change_style(root):
            root.findall('.//m:oMath',t.NS)[-1].find('m:r/m:rPr/m:sty',t.NS).set(t.qn('m:val'),'i')
        self.mutate(target,change_style)
        with self.assertRaises(t.AuthoringError):t.verify_document(ctx,target)

    def test_registered_and_bound_bare_symbol_share_body_style_and_source_identity(self):
        source=math(styled('I'))
        fid=self.source([source],['symbol_reference'])[0]
        self.spec['sections'][3]['blocks'].append(dict(id='refs',type='paragraph',role='body',runs=[
            dict(type='formula',formula_id=fid),dict(type='text',text='表示已核定量；'),
            dict(type='symbol_reference',formula_id=fid,text='I'),dict(type='text',text='仍为同一量。')]))
        before=deepcopy(self.spec)
        _,_,result,doc=self.create()
        refs=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(len(refs),0)
        self.assertEqual(result['counts']['source_text_occurrences'],2)
        for row in result['source_objects']:
            self.assertEqual(row['text'],'I')
            self.assertEqual(row['tree_sha256'],t.tree_signature(ET.fromstring(source)))
        for run in [r for r in doc.findall('.//w:r',t.NS) if t.word_text(r)=='I']:
            self.assertEqual(run.find('w:rPr/w:sz',t.NS).get(t.qn('w:val')),'28')
            self.assertEqual(run.find('w:rPr/w:i',t.NS).get(t.qn('w:val')),'0')
        self.assertEqual(before,self.spec)

    def test_bound_reference_accepts_body_alias_but_rejects_unknown_style_and_lookalikes(self):
        fid=self.source([math(styled('I'))],['symbol_reference'])[0]
        original_blocks=deepcopy(self.spec['sections'][3]['blocks'])
        for style in ('body','reference_letter'):
            self.spec['sections'][3]['blocks']=original_blocks+[dict(id='ref',type='paragraph',role='body',runs=[
                dict(type='symbol_reference',formula_id=fid,text='I',style=style)])]
            _,_,result,_=self.create()
            self.assertEqual(result['counts']['source_text_occurrences'],1)
        for extra in ({'style':'arbitrary'},{'text':'l'},{'text':'i'},{'text':'1'}):
            item=dict(type='symbol_reference',formula_id=fid,text='I');item.update(extra)
            self.spec['sections'][3]['blocks']=[dict(id='ref',type='paragraph',role='body',runs=[item])]
            message='Unknown plain symbol style' if 'style' in extra else 'complete registered bare symbol'
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

    def test_all_ordinary_latin_and_greek_paths_use_body_runs(self):
        letters=['m','a','b','q','k','j','d','n','θ','Ω']
        ids=self.source([math(styled(letter).replace('</w:rPr>',
            '<w:vertAlign w:val="baseline"/><w:position w:val="0"/><w:spacing w:val="0"/></w:rPr>'))
            for letter in letters],['symbol_reference']*len(letters))
        for index,(letter,fid) in enumerate(zip(letters,ids)):
            self.spec['sections'][3]['blocks'].append(dict(id=f'letters{index}',type='paragraph',role='body',runs=[
                dict(type='formula',formula_id=fid),dict(type='text',text='、'),
                dict(type='symbol_reference',formula_id=fid,text=letter),dict(type='text',text='、'),
                dict(type='symbol_reference',text=letter,style='reference_letter'),dict(type='text',text='表示对应量。')]))
        _,_,result,doc=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],0)
        self.assertEqual(result['counts']['source_text_occurrences'],20)
        for run in doc.findall('.//w:r',t.NS):
            if t.word_text(run) in letters:
                self.assertEqual(run.find('w:rPr/w:sz',t.NS).get(t.qn('w:val')),'28')
                self.assertEqual(run.find('w:rPr/w:i',t.NS).get(t.qn('w:val')),'0')

    def test_complex_reference_resizes_display_only_and_rejects_tamper(self):
        symbol='<m:sSubSup><m:e>'+styled('W')+'</m:e><m:sub>'+styled('k,m')+'</m:sub><m:sup>'+styled('new')+'</m:sup></m:sSubSup>'
        source=math(symbol+styled('=0')).replace('w:val="28"','w:val="23"')
        fid=self.source([source],['main_formula'])[0]
        self.spec['sections'][3]['blocks'] += [dict(id='eq',type='formula',formula_id=fid),
            dict(id='ref',type='paragraph',role='body',runs=[dict(type='source_symbol',formula_id=fid,node_path='./m:sSubSup[1]'),
                dict(type='text',text='表示提交后的预留工作量。')])]
        source_sha=t.sha(self.root/'source.docx')
        ctx,target,result,doc=self.create()
        main,ref=doc.findall('.//m:oMath',t.NS)
        self.assertEqual(set(main.xpath('.//w:sz/@w:val',namespaces=t.NS)),{'23'})
        self.assertEqual(set(ref.xpath('.//w:sz/@w:val',namespaces=t.NS)),{'28'})
        self.assertEqual(t.tree_signature(main),t.tree_signature(t.upright_math_display(ET.fromstring(source))))
        self.assertEqual([ET.QName(n).localname for n in ref],['sSubSup'])
        self.assertEqual(''.join(ref.xpath('.//m:t/text()',namespaces=t.NS)),'Wk,mnew')
        self.assertEqual(ref.xpath('.//m:sty/@m:val',namespaces=t.NS),['p','p','p'])
        row=result['source_objects'][-1]
        self.assertNotEqual(row['tree_sha256'],row['display_tree_sha256'])
        self.assertEqual(row['source_tree_sha256'],self.spec['formulas'][0]['tree_sha256'])
        self.assertEqual(row['source_sha256'],source_sha)
        self.assertEqual(t.sha(self.root/'source.docx'),source_sha)
        self.assertEqual(result['counts']['main_formula_occurrences'],1)
        def change(root):
            root.findall('.//m:oMath',t.NS)[1].find('.//m:r/m:rPr/m:sty',t.NS).set(t.qn('m:val'),'i')
        self.mutate(target,change)
        with self.assertRaisesRegex(t.AuthoringError,'content/structure changed'):t.verify_document(ctx,target)
        self.spec['formulas'][0]['tree_sha256']='0'*64
        with self.assertRaisesRegex(t.AuthoringError,'tree hash mismatch'):t.validate(self.root,self.spec)

    def test_special_font_and_math_alphabet_are_not_flattened(self):
        variants=[math(styled('x').replace('Cambria Math','Symbol')),math(styled('ℕ')),
                  math(styled('x').replace('m:val="i"','m:val="b"')),
                  math(styled('R').replace('</m:rPr>','<m:scr m:val="script"/></m:rPr>'))]
        ids=self.source(variants,['symbol_reference']*len(variants))
        self.spec['sections'][3]['blocks'].append(dict(id='special',type='paragraph',role='body',runs=[
            dict(type='formula',formula_id=fid) for fid in ids]))
        _,_,result,_=self.create()
        self.assertEqual(result['counts']['omml_occurrences'],4)
        self.assertEqual(result['counts']['source_text_occurrences'],0)


if __name__=='__main__':unittest.main()
