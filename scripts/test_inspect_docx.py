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
