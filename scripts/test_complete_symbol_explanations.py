"""Complete source quantities and explicit plain-symbol identity, not semantics."""
import unittest
from copy import deepcopy
from lxml import etree as ET
import authoring_toolkit as toolkit
import math_fragments as fragments
import test_authoring_formula_prose as fixtures


def run(text):return '<m:r><m:t>'+text+'</m:t></m:r>'
def sub(base,index):return '<m:sSub><m:e>'+run(base)+'</m:e><m:sub>'+run(index)+'</m:sub></m:sSub>'
def math(content):return '<m:oMath xmlns:m="'+toolkit.NS['m']+'">'+content+'</m:oMath>'
CALL=math(run('y=')+run('f')+'<m:d><m:e>'+sub('x','i')+'</m:e></m:d>')
FUNC=math('<m:func><m:fName>'+run('f')+'</m:fName><m:e><m:d><m:e>'+sub('x','i')+'</m:e></m:d></m:e></m:func>')


class CompleteSymbols(unittest.TestCase):
    setUp=fixtures.FormulaProseTests.setUp
    source=fixtures.FormulaProseTests.source
    create=fixtures.FormulaProseTests.create
    mutate=fixtures.FormulaProseTests.mutate

    def test_function_head_and_real_arguments_copy_and_readback(self):
        fid=self.source([CALL],['main_formula'])[0]
        self.spec['sections'][3]['blocks'] += [dict(id='main',type='formula',formula_id=fid),
            dict(id='explain',type='paragraph',role='body',runs=[dict(type='text',text='其中，'),
                 dict(type='source_symbol',formula_id=fid,node_paths=['./m:r[2]','./m:d[1]']),
                 dict(type='text',text='表示该参数对应的函数量。')])]
        before=toolkit.sha(self.root/'source.docx');spec=deepcopy(self.spec)
        ctx,target,result,doc=self.create()
        original=ET.fromstring(CALL);ref=doc.findall('.//m:oMath',toolkit.NS)[-1]
        # The body reference now has an explicit 14pt display base; every
        # non-size source node, real argument and source equation stays intact.
        self.assertEqual({n.get(toolkit.qn('w:val')) for n in ref.findall('.//w:sz',toolkit.NS)}, {'28'})
        structural=deepcopy(ref)
        for props in list(structural.findall('.//w:rPr',toolkit.NS)):
            for size in list(props):
                if size.tag in {toolkit.qn('w:sz'),toolkit.qn('w:szCs')}:props.remove(size)
            if not len(props):props.getparent().remove(props)
        self.assertEqual([toolkit.tree_signature(n) for n in structural],[toolkit.tree_signature(n) for n in list(original)[1:]])
        self.assertEqual(toolkit.tree_signature(doc.findall('.//m:oMath',toolkit.NS)[0]),toolkit.tree_signature(original))
        self.assertEqual(result['counts']['main_formula_occurrences'],1)
        self.assertEqual(before,toolkit.sha(self.root/'source.docx'));self.assertEqual(spec,self.spec)
        def truncate(root):
            last=root.findall('.//m:oMath',toolkit.NS)[-1];last.remove(last.find('m:d',toolkit.NS))
        self.mutate(target,truncate)
        with self.assertRaises(toolkit.AuthoringError):toolkit.verify_document(ctx,target)

    def test_head_or_delimiter_alone_cannot_represent_complete_call(self):
        node=ET.fromstring(CALL)
        for path in ('./m:r[2]','./m:d[1]', ['./m:r[2]'],['./m:d[1]'],
                     ['./m:d[1]','./m:r[2]']):
            with self.subTest(path=path),self.assertRaises(ValueError):fragments.symbol_fragment(node,path)

    def test_func_structure_is_preserved_and_name_cannot_be_separated(self):
        node=ET.fromstring(FUNC);ref=fragments.symbol_fragment(node,'./m:func[1]')
        self.assertEqual(toolkit.tree_signature(ref[0]),toolkit.tree_signature(node[0]))
        with self.assertRaises(ValueError):fragments.symbol_fragment(node,'./m:func[1]/m:fName[1]/m:r[1]')
        for missing in ('fName','e'):
            bad=ET.fromstring(FUNC);bad[0].remove(bad[0].find('m:'+missing,toolkit.NS))
            with self.assertRaises(ValueError):fragments.symbol_fragment(bad,'./m:func[1]')

    def test_argument_quantity_is_separate_complete_input_not_bare_base(self):
        for xml,path in [(CALL,'./m:d[1]/m:e[1]/m:sSub[1]'),
                         (FUNC,'./m:func[1]/m:e[1]/m:d[1]/m:e[1]/m:sSub[1]')]:
            node=ET.fromstring(xml);ref=fragments.symbol_fragment(node,path)
            self.assertIsNotNone(ref.find('m:sSub/m:sub',toolkit.NS))
            with self.assertRaises(ValueError):fragments.symbol_fragment(node,path+'/m:e[1]/m:r[1]')

    def test_multiargument_modified_head_preserves_every_source_node(self):
        xml=math('<m:acc><m:accPr><m:chr m:val="̂"/></m:accPr><m:e>'+sub('g','q')+
                 '</m:e></m:acc><m:d><m:e>'+run('k+h|k')+'</m:e><m:e>'+run('x')+'</m:e></m:d>')
        node=ET.fromstring(xml);ref=fragments.symbol_fragment(node,['./m:acc[1]','./m:d[1]'])
        self.assertEqual([toolkit.tree_signature(n) for n in ref],[toolkit.tree_signature(n) for n in node])
        with self.assertRaises(ValueError):fragments.symbol_fragment(node,'./m:acc[1]')

    def test_operator_placeholder_cannot_change_actual_function_call(self):
        node=ET.fromstring(CALL.replace('<m:t>f</m:t>','<m:t>sg</m:t>'))
        before=toolkit.tree_signature(node)
        quantity=fragments.symbol_fragment(node,['./m:r[2]','./m:d[1]'])
        operator=fragments.operator_reference(node,'./m:r[2]','./m:d[1]')
        self.assertEqual(''.join(quantity.xpath('.//m:t/text()',namespaces=toolkit.NS)),'sgxi')
        self.assertEqual(''.join(operator.xpath('.//m:t/text()',namespaces=toolkit.NS)),'sg·')
        self.assertEqual(before,toolkit.tree_signature(node))

    def test_plain_reference_with_bound_formula_cannot_truncate_or_change_identity(self):
        for content,letter,category in [(sub('p','i,k'),'p','symbol_reference'),
                                       ('<m:acc><m:e>'+run('p')+'</m:e></m:acc>','p','symbol_reference'),
                                       (run('i'),'j','symbol_reference'),(run('i'),'i','main_formula')]:
            fid=self.source([math(content)],[category])[0]
            self.spec['sections'][3]['blocks']=[dict(id='ref',type='paragraph',role='body',runs=[
                dict(type='symbol_reference',text=letter,formula_id=fid)])]
            with self.subTest(content=content),self.assertRaisesRegex(toolkit.AuthoringError,'complete registered bare symbol'):
                toolkit.validate(self.root,self.spec)

    def test_real_bare_index_and_unbound_index_text_remain_available(self):
        base=deepcopy(self.spec['sections'][3]['blocks'])
        for letter in ('i','λ'):
            fid=self.source([math(run(letter))],['symbol_reference'])[0]
            self.spec['sections'][3]['blocks']=base+[dict(id='ref',type='paragraph',role='body',runs=[
                dict(type='formula',formula_id=fid),dict(type='text',text='为源中已定义索引；'),
                dict(type='symbol_reference',text=letter,formula_id=fid),dict(type='text',text='表示独立索引。'),
                dict(type='symbol_reference',text='k'),dict(type='text',text='表示另一索引。')])]
            toolkit.validate(self.root,self.spec)

    def test_index_and_time_variants_do_not_collapse_into_same_quantity(self):
        fragments_seen=[]
        for index in ('i,k','j,k','i,k+1'):
            node=ET.fromstring(math(sub('p',index)))
            fragments_seen.append(toolkit.tree_signature(fragments.symbol_fragment(node,'./m:sSub[1]')))
        self.assertEqual(len(set(fragments_seen)),3)


if __name__=='__main__':unittest.main()
