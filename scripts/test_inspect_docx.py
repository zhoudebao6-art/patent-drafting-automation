import tempfile
import unittest
import zipfile
from pathlib import Path
from inspect_docx import inspect, precheck, NS


class InspectionTests(unittest.TestCase):
    def make_docx(self, root, body, extra=None):
        path=Path(root)/'fixture.docx'
        ns=' '.join(f'xmlns:{k}="{v}"' for k,v in NS.items())
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('word/document.xml',f'<w:document {ns}><w:body>{body}</w:body></w:document>')
            z.writestr('word/media/','')
            for name,data in (extra or {}).items(): z.writestr(name,data)
        return path

    def test_math_structure_and_revision_preserved(self):
        body='<w:p><w:del><w:r><w:t>deleted</w:t></w:r></w:del><w:ins><w:r><w:t>kept</w:t></w:r></w:ins><m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'proposal')
            self.assertEqual(data['parts'][0]['paragraphs'][0]['text'],'keptab')
            self.assertIn('num',data['formulas'][0]['omml_xml'])
            self.assertEqual(data['media'],[])
            self.assertTrue(any(x['code']=='REVISIONS' for x in data['issues']))

    def test_math_literal_spaces_and_invisible_padding_are_reported(self):
        body='<w:p><m:oMath><m:r><m:t xml:space="preserve">a =b\u00a0+\u2009c\u200b</m:t></m:r></m:oMath></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'proposal')
            points={x['codepoint'] for x in data['formulas'][0]['math_evidence']['literal_whitespace']}
            self.assertEqual(points, {'U+0020','U+00A0','U+2009','U+200B'})
            self.assertTrue(any(x['code']=='MATH_LITERAL_WHITESPACE' for x in data['issues']))

    def test_math_native_structure_is_not_literal_whitespace(self):
        body='<w:p><m:oMath><m:f><m:num><m:r><m:t>x</m:t></m:r></m:num><m:den><m:r><m:t>y</m:t></m:r></m:den></m:f><m:r><m:t>+sin\u2061θ</m:t></m:r></m:oMath></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'proposal')
            self.assertFalse(data['formulas'][0]['math_evidence']['literal_whitespace'])
            self.assertFalse(any(x['code']=='MATH_LITERAL_WHITESPACE' for x in data['issues']))
            self.assertIn('num',data['formulas'][0]['math_evidence']['structure_elements'])

    def test_math_font_and_full_symbol_properties_are_preserved(self):
        body='<w:p><m:oMath><m:acc><m:accPr><m:chr m:val="̂"/></m:accPr><m:e><m:sSubSup><m:e><m:r><m:rPr><m:sty m:val="p"/><m:scr m:val="script"/></m:rPr><w:rPr><w:rFonts w:ascii="Cambria Math"/><w:sz w:val="28"/></w:rPr><m:t>C</m:t></m:r></m:e><m:sub><m:r><m:t>t</m:t></m:r></m:sub><m:sup><m:r><m:t>⊺</m:t></m:r></m:sup></m:sSubSup></m:e></m:acc></m:oMath></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body))
            ev=data['formulas'][0]['math_evidence']; first=ev['runs'][0]
            self.assertEqual(first['fonts_explicit']['ascii'],'Cambria Math')
            self.assertEqual(first['size_half_points_explicit'],'28')
            self.assertEqual(first['math_run_properties_explicit'],{'sty':'p','scr':'script'})
            self.assertTrue({'acc','sSubSup','sub','sup'}<=set(ev['structure_elements']))
            self.assertIsNone(ev['runs'][1]['size_half_points_explicit'])

    def test_every_suoshu_occurrence_is_exposed_without_false_semantic_pass(self):
        text='2.根据权利要求1所述的方法，其特征在于，在保持所述参考轨迹位置的周期顺序后，形成与参考轨迹位置的对应关系。'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,'<w:p><w:r><w:t>'+text+'</w:t></w:r></w:p>'))
            precheck(data,'draft'); items=data['antecedent_occurrences']
            self.assertEqual(len(items),2)
            self.assertTrue(items[1]['following'].startswith('参考轨迹位置'))
            self.assertEqual(text[items[1]['offset']:items[1]['offset']+2],'所述')
            self.assertTrue(all(x['status'].startswith('requires_') for x in items))

    def test_inline_symbol_objects_are_not_claimed_as_source_formula_count(self):
        body='<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'proposal')
            self.assertEqual(len(data['formulas']),1)
            self.assertEqual(data['formula_count_status'],'unclassified_math_objects_not_source_formula_count')

    def test_nested_textbox_objects_belong_only_to_nearest_paragraph(self):
        body='<w:p><w:r><w:t>外段</w:t><w:pict><w:txbxContent><w:p><m:oMath><m:r><m:t>y=ax</m:t></m:r></m:oMath><w:r><w:t>所述新对象</w:t></w:r></w:p></w:txbxContent></w:pict><w:t>末尾</w:t></w:r></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'draft')
            self.assertEqual(len(data['formulas']),1)
            self.assertEqual(data['formulas'][0]['paragraph'],2)
            paras=data['parts'][0]['paragraphs']
            self.assertEqual([p['p'] for p in paras],[1,2])
            self.assertEqual(paras[0]['text'],'外段末尾')
            self.assertEqual(paras[0]['formulas'],[])
            self.assertEqual(len(data['antecedent_occurrences']),1)
            self.assertEqual(data['antecedent_occurrences'][0]['paragraph'],2)

    def test_claim_parent_ranges_expand_all_branches_without_legal_approval(self):
        texts=[f'{n}.检测方法，其特征在于，获得数据。' for n in range(1,6)]
        texts.append('6.根据权利要求1至3或5所述的检测方法，其特征在于，处理数据。')
        body=''.join(f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>' for t in texts)
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'draft')
            claim=data['claims_detected'][-1]
            self.assertEqual(claim['parents_detected'],[1,2,3,5])
            self.assertEqual(claim['parent_parse_status'],'structural_only_requires_legal_path_review')

    def test_invalid_or_oversized_parent_ranges_remain_unresolved(self):
        for reference in ['3至1','1至1000000','1至'+'9'*5000]:
            with self.subTest(reference=reference[:30]), tempfile.TemporaryDirectory() as root:
                body='<w:p><w:r><w:t>1.方法，其特征在于，操作。</w:t></w:r></w:p>'
                body+='<w:p><w:r><w:t>2.根据权利要求'+reference+'所述的方法，其特征在于，操作。</w:t></w:r></w:p>'
                data=inspect(self.make_docx(root,body)); precheck(data,'draft')
                claim=data['claims_detected'][-1]
                self.assertEqual(claim['parent_parse_status'],'requires_manual_reference_review')
                self.assertTrue(claim['parent_parse_issues'])
                self.assertLessEqual(len(claim['parents_detected']),1000)
                self.assertTrue(any(i['code']=='CLAIM_REFERENCE_PARSE' for i in data['issues']))

    def test_vml_shape_is_not_broken_image(self):
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,'<w:p><v:imagedata o:title=""/></w:p>'))
            precheck(data,'proposal')
            self.assertFalse(any(x['code']=='MISSING_REL' for x in data['issues']))

    def test_font_encoded_sign_is_not_silently_lost(self):
        body='<w:p><w:r><w:t>0.40+0.12</w:t><w:sym w:font="Symbol" w:char="00B1"/><w:t>0.02</w:t></w:r></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'proposal')
            para=data['parts'][0]['paragraphs'][0]
            self.assertEqual(para['text'],'0.40+0.12[w:sym Symbol:00B1]0.02')
            self.assertEqual(para['font_encoded_symbols'][0]['char'],'00B1')
            self.assertTrue(any(x['code']=='FONT_SYMBOL' for x in data['issues']))

    def test_external_media_not_fetched_and_image_reuse(self):
        rels='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="image" Target="media/a.png"/><Relationship Id="r2" Type="image" Target="https://example.invalid/no-download.png" TargetMode="External"/></Relationships>'
        body='<w:p><a:blip r:embed="r1"/></w:p><w:p><a:blip r:embed="r1"/><a:blip r:link="r2"/></w:p>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body,{'word/_rels/document.xml.rels':rels,'word/media/a.png':b'opaque-test-media'}))
            precheck(data,'proposal')
            self.assertEqual(len(data['media']),1)
            self.assertEqual(data['parts'][0]['paragraphs'][0]['media'][0]['part'],'word/media/a.png')
            self.assertTrue(any(x['code']=='EXTERNAL_MEDIA' for x in data['issues']))

    def test_claim_future_parent_and_figure_mismatch_reported(self):
        texts=['本发明涉及检测技术领域，公开检测方法。','1.检测方法，其特征在于，获得数据。','2.根据权利要求3所述的检测方法，其特征在于，处理数据。','技术领域','本发明涉及检测技术领域，公开检测方法。','背景技术','背景。','发明内容','内容。','附图说明','图1为本发明的流程图；','图2为本发明的结果图。','具体实施方式','请参考图2，处理；请参考图1，输出。','图1']
        body=''.join(f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>' for t in texts)
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body)); precheck(data,'draft')
            codes={x['code'] for x in data['issues']}
            self.assertTrue({'CLAIM_REFERENCE','FIGURE_CAPTIONS','FIGURE_MENTIONS'}<=codes)
            self.assertNotIn('FIELD_ABSTRACT',codes)
            self.assertEqual([c['number'] for c in data['claims_detected']],[1,2])

    def test_claims_stop_at_header_section_before_description_title(self):
        # Mirrors the self-check DOCX: the claims section ends before the
        # description title and note, both of which precede 技术领域.
        para=lambda text: f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
        claim1='1.检测方法，其特征在于，获得数据。'
        claim2='2.根据权利要求1所述的检测方法，其特征在于，处理数据。'
        body=para(claim1)+para(claim2)
        body+='<w:p><w:pPr><w:sectPr><w:headerReference w:type="default" r:id="h1"/></w:sectPr></w:pPr></w:p>'
        body+=para('检测方法虚拟自检样稿')+para('本稿仅用于自检。')+para('技术领域')
        body+='<w:sectPr><w:headerReference w:type="default" r:id="h2"/></w:sectPr>'
        extra=self.claim_section_headers()
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body,extra)); precheck(data,'draft')
            self.assertEqual([c['text'].strip() for c in data['claims_detected']],[claim1,claim2])
            self.assertEqual(data['claims_detected'][1]['parents_detected'],[1])

    def claim_section_headers(self):
        return {
            'word/_rels/document.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="h1" Type="header" Target="header1.xml"/><Relationship Id="h2" Type="header" Target="header2.xml"/></Relationships>',
            'word/header1.xml':f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:t>权利要求书  虚拟自检样稿</w:t></w:r></w:p></w:hdr>',
            'word/header2.xml':f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:t>说明书  虚拟自检样稿</w:t></w:r></w:p></w:hdr>',
        }

    def test_multiparagraph_claim_continues_across_inherited_claims_section(self):
        para=lambda text: f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
        head='1.检测方法，其特征在于，包括：'
        first='获取数据；'
        second='处理所述数据。'
        # A sectPr belongs to the section ending at that paragraph. A later
        # section without a header reference inherits the preceding header.
        body=para(head)
        body+=f'<w:p><w:pPr><w:sectPr><w:headerReference w:type="default" r:id="h1"/></w:sectPr></w:pPr><w:r><w:t>{first}</w:t></w:r></w:p>'
        body+=f'<w:p><w:pPr><w:sectPr/></w:pPr><w:r><w:t>{second}</w:t></w:r></w:p>'
        body+=para('说明书标题')+para('技术领域')
        body+='<w:sectPr><w:headerReference w:type="default" r:id="h2"/></w:sectPr>'
        with tempfile.TemporaryDirectory() as root:
            data=inspect(self.make_docx(root,body,self.claim_section_headers())); precheck(data,'draft')
            self.assertEqual(len(data['claims_detected']),1)
            self.assertEqual(data['claims_detected'][0]['text'],'\n'.join([head,first,second]))


if __name__=='__main__': unittest.main(verbosity=2)
