"""General source symbols, grouped equations and visible whitespace boundaries."""
from copy import deepcopy
import unittest
from lxml import etree as ET
import authoring_toolkit as t
import math_fragments as fragments
import inspect_docx
import test_authoring_formula_prose as prose
from test_authoring_formula_prose import flat


def sub(letter,index):
    return '<m:sSub><m:e><m:r><m:rPr><m:sty m:val="b"/></m:rPr><m:t>'+letter+'</m:t></m:r></m:e><m:sub><m:r><m:t>'+index+'</m:t></m:r></m:sub></m:sSub>'


PAIRED=('<m:oMath xmlns:m="'+t.NS['m']+'">'+sub('p','i,k+ℓ+1')+
        '<m:r><m:t>=</m:t></m:r>'+sub('p','i,k+ℓ')+
        '<m:r><m:t>，</m:t></m:r>'+sub('v','i,k+ℓ+1')+
        '<m:r><m:t>=</m:t></m:r>'+sub('v','i,k+ℓ')+'</m:oMath>')


class MathDetailsTests(unittest.TestCase):
    setUp=prose.FormulaProseTests.setUp
    source=prose.FormulaProseTests.source
    create=prose.FormulaProseTests.create
    mutate=prose.FormulaProseTests.mutate

    def pair(self):
        fid=self.source([PAIRED],['main_formula'])[0]
        self.spec['formulas'][0]['equation_count']=2
        self.spec['formula_policy']['source_main_count']=2
        self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid,display_breaks=[4]))
        return fid

    def test_complete_source_symbol_and_stacking_preserve_identity_and_count(self):
        fid=self.pair();before=deepcopy(self.spec);source_hash=t.sha(self.root/'source.docx')
        self.spec['sections'][3]['blocks'].append(dict(id='explain',type='paragraph',role='body',runs=[
            dict(type='text',text='其中，'),dict(type='source_symbol',formula_id=fid,node_path='./m:sSub[1]'),
            dict(type='text',text='表示该索引对应的位置向量。')]))
        context,_,result,doc=self.create()
        self.assertEqual(source_hash,t.sha(self.root/'source.docx'))
        self.assertEqual(self.spec['formulas'],before['formulas'])
        self.assertEqual(result['counts']['main_formula_occurrences'],2)
        self.assertEqual(result['counts']['omml_occurrences'],2)
        display,symbol=doc.findall('.//m:oMath',t.NS)
        rows=display.findall('m:eqArr/m:e',t.NS)
        self.assertEqual(len(rows),2)
        self.assertEqual([t.tree_signature(n) for row in rows for n in row][:-1],
                         [t.tree_signature(n) for n in ET.fromstring(PAIRED)])
        self.assertEqual(''.join(rows[-1][-1].xpath('.//m:t/text()',namespaces=t.NS)),'；')
        self.assertEqual(''.join(symbol.xpath('.//m:t/text()',namespaces=t.NS)),'pi,k+ℓ+1')
        self.assertEqual(symbol.find('.//m:sty',t.NS).get(t.qn('m:val')),'b')
        trace=result['source_objects'][-1]
        self.assertEqual(trace['source_fragment_path'],'./m:sSub[1]')
        self.assertEqual(trace['source_tree_sha256'],self.spec['formulas'][0]['tree_sha256'])
        self.assertEqual(t.word_text(display.getparent()),'')
        evidence=inspect_docx.formula_prose_paragraph_evidence(display.getparent(),'word/document.xml',1,{id(display):'test-equation'})
        self.assertTrue(evidence['standalone_formulas'][0]['terminal_semicolon'])

    def test_bare_base_selection_cannot_drop_indices_or_modifier(self):
        node=ET.fromstring(PAIRED)
        for path in ('./m:sSub[1]/m:e[1]/m:r[1]','./m:sSub[1]/m:sub[1]/m:r[1]',
                     './/m:r','./m:sSub[9]','./m:r[1]'):
            with self.subTest(path=path),self.assertRaises(ValueError):fragments.symbol_fragment(node,path)

    def test_incomplete_or_invalid_display_split_rejected(self):
        node=ET.fromstring(PAIRED)
        for boundaries,count in [([1],2),([2],2),([4],1),([4,4],3),([False],2),([0],2),([7],2)]:
            with self.subTest(boundaries=boundaries,count=count),self.assertRaises(ValueError):
                fragments.stacked_equations(node,boundaries,count)

    def test_prescripts_and_attached_modifiers_cannot_be_truncated(self):
        prescript='<m:sPre><m:sub><m:r><m:t>i</m:t></m:r></m:sub><m:sup><m:r><m:t>j</m:t></m:r></m:sup><m:e><m:r><m:t>x</m:t></m:r></m:e></m:sPre>'
        node=ET.fromstring('<m:oMath xmlns:m="'+t.NS['m']+'">'+prescript+'</m:oMath>')
        selected=fragments.symbol_fragment(node,'./m:sPre[1]')
        self.assertEqual(t.tree_signature(selected[0]),t.tree_signature(node[0]))
        with self.assertRaises(ValueError):fragments.symbol_fragment(node,'./m:sPre[1]/m:e[1]/m:r[1]')
        for wrapper in ('limLow','limUpp','phant','borderBox','box'):
            wrapped=ET.fromstring('<m:oMath xmlns:m="'+t.NS['m']+'"><m:'+wrapper+'><m:e><m:r><m:t>x</m:t></m:r></m:e></m:'+wrapper+'></m:oMath>')
            with self.subTest(wrapper=wrapper),self.assertRaises(ValueError):
                fragments.symbol_fragment(wrapped,'./m:'+wrapper+'[1]/m:e[1]/m:r[1]')

    def test_nested_relation_or_missing_operand_is_not_complete_equation(self):
        for incomplete in (sub('x','i=1'),'<m:r><m:t>=x</m:t></m:r>','<m:r><m:t>x=</m:t></m:r>'):
            xml='<m:oMath xmlns:m="'+t.NS['m']+'">'+incomplete+'<m:r><m:t>，</m:t></m:r><m:r><m:t>y=z</m:t></m:r></m:oMath>'
            with self.subTest(incomplete=incomplete),self.assertRaises(ValueError):
                fragments.stacked_equations(ET.fromstring(xml),[2],2)

    def test_existing_array_final_semicolon_stays_on_last_row_without_duplicates(self):
        array=fragments.stacked_equations(ET.fromstring(PAIRED),[4],2)
        for already_punctuated in (False,True):
            if already_punctuated:
                mark=ET.SubElement(array.findall('m:eqArr/m:e',t.NS)[-1],t.qn('m:r'))
                ET.SubElement(mark,t.qn('m:t')).text='；'
            fid=self.source([ET.tostring(array).decode()],['main_formula'])[0]
            self.spec['sections'][3]['blocks']=[b for b in self.spec['sections'][3]['blocks'] if b['id']!='eq']
            self.spec['sections'][3]['blocks'].append(dict(id='eq',type='formula',formula_id=fid))
            context,_,result,doc=self.create()
            rendered=doc.find('.//m:oMath',t.NS)
            self.assertEqual(''.join(rendered.xpath('.//m:t/text()',namespaces=t.NS)).count('；'),1)
            self.assertTrue(''.join(rendered.findall('m:eqArr/m:e',t.NS)[-1].xpath('.//m:t/text()',namespaces=t.NS)).endswith('；'))
            self.assertEqual(t.word_text(rendered.getparent()),'')

    def test_readback_rejects_removed_symbol_index_and_reordered_rows(self):
        fid=self.pair()
        self.spec['sections'][3]['blocks'].append(dict(id='ref',type='paragraph',role='body',runs=[
            dict(type='text',text='其中，'),dict(type='source_symbol',formula_id=fid,node_path='./m:sSub[1]')]))
        context,target,_,_=self.create()
        def remove(root):
            symbol=root.findall('.//m:oMath',t.NS)[-1]
            subscript=symbol.find('.//m:sub',t.NS);subscript.getparent().remove(subscript)
        self.mutate(target,remove)
        with self.assertRaises(t.AuthoringError):t.verify_document(context,target)
        t.render_document(context,target)
        def reorder(root):
            array=root.find('.//m:eqArr',t.NS);rows=array.findall('m:e',t.NS)
            array.remove(rows[0]);array.append(rows[0])
        self.mutate(target,reorder)
        with self.assertRaises(t.AuthoringError):t.verify_document(context,target)

    def test_literal_whitespace_rejected_without_silent_word_joining(self):
        for text in ['中文 空格','中文\u3000空格','中文\u00a0空格','中文\u202f空格','中文\u200b空格','中文\ufeff空格','model name']:
            with self.subTest(text=text):
                self.spec['sections'][3]['blocks'][-1]=dict(id='body',type='paragraph',role='body',text=text)
                with self.assertRaisesRegex(t.AuthoringError,'literal whitespace'):t.validate(self.root,self.spec)

    def test_source_math_whitespace_rejected_without_mutating_source(self):
        self.source([flat('a =b')],['main_formula'])
        before=t.sha(self.root/'source.docx')
        with self.assertRaisesRegex(t.AuthoringError,'mathematical whitespace'):t.validate(self.root,self.spec)
        self.assertEqual(before,t.sha(self.root/'source.docx'))

    def test_claims_auto_spacing_disabled_and_tampering_rejected(self):
        context,target,_,doc=self.create()
        self.assertFalse(doc.xpath('//w:pPr/w:autoSpaceDE[@w:val="1"] | //w:pPr/w:autoSpaceDN[@w:val="1"]',namespaces=t.NS))
        def alter(root):root.find('.//w:autoSpaceDE',t.NS).set(t.qn('w:val'),'1')
        self.mutate(target,alter)
        with self.assertRaisesRegex(t.AuthoringError,'Automatic extra content spacing'):t.verify_document(context,target)


if __name__=='__main__':unittest.main()
