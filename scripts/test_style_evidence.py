"""Meaningful font inheritance and image-location evidence; not visual acceptance."""
import tempfile
import unittest
from inspect_docx import inspect, precheck, NS, background_reader
from xml.etree import ElementTree as ET
import test_inspect_docx as fixtures


class StyleEvidenceTests(unittest.TestCase):
    def extract(self, body, styles='', extra=None):
        with tempfile.TemporaryDirectory() as root:
            parts = dict(extra or {})
            if styles:
                parts['word/styles.xml'] = f'<w:styles xmlns:w="{NS["w"]}">{styles}</w:styles>'
            result = inspect(fixtures.InspectionTests().make_docx(root, body, parts))
            precheck(result, 'draft')
            return result

    def test_unused_red_style_is_not_a_red_explanation(self):
        styles='<w:style w:styleId="Unused"><w:rPr><w:color w:val="FF0000"/></w:rPr></w:style>'
        result=self.extract('<w:p><w:r><w:t>正文解释</w:t></w:r></w:p>',styles)
        self.assertEqual(result['text_color_evidence']['review_runs'],[])
        self.assertFalse(any(i['code']=='TEXT_COLOR_REVIEW' for i in result['issues']))

    def test_direct_red_body_math_and_header_are_located(self):
        body='<w:p><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>技术解释</w:t></w:r><m:oMath><m:r><w:rPr><w:color w:val="FF0000"/></w:rPr><m:t>x</m:t></m:r></m:oMath></w:p>'
        header=f'<w:hdr xmlns:w="{NS["w"]}"><w:p><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>页眉</w:t></w:r></w:p></w:hdr>'
        result=self.extract(body,extra={'word/header1.xml':header})
        rows=result['text_color_evidence']['review_runs']
        self.assertEqual(len(rows),3)
        self.assertEqual({r['part'] for r in rows},{'word/document.xml','word/header1.xml'})
        self.assertIn('技术解释',result['parts'][0]['paragraphs'][0]['text'])

    def test_paragraph_basedon_and_character_override(self):
        styles='<w:style w:styleId="Base"><w:rPr><w:color w:val="FF0000"/></w:rPr></w:style><w:style w:styleId="Child"><w:basedOn w:val="Base"/></w:style><w:style w:styleId="Black"><w:rPr><w:color w:val="000000"/></w:rPr></w:style>'
        body='<w:p><w:pPr><w:pStyle w:val="Child"/></w:pPr><w:r><w:t>继承红字</w:t></w:r><w:r><w:rPr><w:rStyle w:val="Black"/></w:rPr><w:t>黑字覆盖</w:t></w:r></w:p>'
        rows=self.extract(body,styles)['text_color_evidence']['review_runs']
        self.assertEqual([r['text'] for r in rows],['继承红字'])
        self.assertEqual(rows[0]['resolved_rgb'],'FF0000')

    def test_theme_actual_red_overrides_black_fallback(self):
        rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="theme" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>'
        theme=f'<a:theme xmlns:a="{NS["a"]}"><a:themeElements><a:clrScheme name="test"><a:accent1><a:srgbClr val="FF0000"/></a:accent1></a:clrScheme></a:themeElements></a:theme>'
        body='<w:p><w:r><w:rPr><w:color w:val="000000" w:themeColor="accent1"/></w:rPr><w:t>主题色</w:t></w:r></w:p>'
        rows=self.extract(body,extra={'word/_rels/document.xml.rels':rel,'word/theme/theme1.xml':theme})['text_color_evidence']['review_runs']
        self.assertEqual(rows[0]['resolved_rgb'],'FF0000')

    def test_unknown_theme_is_not_reported_black(self):
        body='<w:p><w:r><w:rPr><w:color w:val="000000" w:themeColor="accent1"/></w:rPr><w:t>未知主题</w:t></w:r></w:p>'
        row=self.extract(body)['text_color_evidence']['review_runs'][0]
        self.assertIsNone(row['resolved_rgb'])
        self.assertIn('unresolved_theme_color',row['unresolved'])

    def test_default_color_and_cycle_do_not_silently_pass(self):
        styles='<w:docDefaults><w:rPrDefault><w:rPr><w:color w:val="FF0000"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:styleId="Cycle"><w:basedOn w:val="Cycle"/></w:style>'
        body='<w:p><w:pPr><w:pStyle w:val="Cycle"/></w:pPr><w:r><w:t>有问题的继承</w:t></w:r></w:p>'
        row=self.extract(body,styles)['text_color_evidence']['review_runs'][0]
        self.assertEqual(row['resolved_rgb'],'FF0000')
        self.assertIn('style_cycle:Cycle',row['unresolved'])

    def test_textbox_is_not_double_counted(self):
        body='<w:p><w:r><w:t>外层</w:t><w:pict><w:txbxContent><w:p><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>红色文本框</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>'
        rows=self.extract(body)['text_color_evidence']['review_runs']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['paragraph'],2)

    def test_empty_red_carrier_does_not_color_black_textbox_text(self):
        body='<w:p><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:pict><w:txbxContent><w:p><w:r><w:rPr><w:color w:val="000000"/></w:rPr><w:t>独立黑字文本框</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>'
        self.assertEqual(self.extract(body)['text_color_evidence']['review_runs'],[])

    def test_figure_description_reference_and_caption_locations_are_distinct(self):
        body=''.join(f'<w:p><w:r><w:t>{x}</w:t></w:r></w:p>' for x in ['附图说明','图1为本发明的整体流程图。','具体实施方式','请参考图1，在一个实施例中，执行处理。'])
        body+='<w:p><w:r><w:drawing><a:blip r:embed="rId9"/></w:drawing></w:r></w:p><w:p><w:r><w:t>图1</w:t></w:r></w:p>'
        rel='<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId9" Type="image" Target="media/imageA.png"/></Relationships>'
        result=self.extract(body,extra={'word/_rels/document.xml.rels':rel,'word/media/imageA.png':b'fixture-not-visual-proof'})
        data=result['figure_placement_evidence']
        self.assertEqual(data['description_paragraphs'][0]['paragraph'],2)
        self.assertEqual(data['reference_paragraphs'][0]['paragraph'],4)
        self.assertEqual(data['image_occurrences'][0]['media'][0]['part'],'word/media/imageA.png')
        self.assertEqual(data['caption_paragraphs'][0]['paragraph'],6)


    def test_plain_paper_direct_formats_and_story_locations(self):
        bodies={
          'paragraph':'<w:p><w:pPr><w:shd w:val="clear" w:fill="CCFFCC"/></w:pPr><w:r><w:t>正文</w:t></w:r></w:p>',
          'run':'<w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>正文</w:t></w:r></w:p>',
          'math':'<w:p><m:oMath><m:r><w:rPr><w:shd w:fill="CCFFCC"/></w:rPr><m:t>x</m:t></m:r></m:oMath></w:p>',
          'cell':'<w:tbl><w:tr><w:tc><w:tcPr><w:shd w:fill="CCFFCC"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>',
          'textbox':'<w:p><w:r><w:pict><w:txbxContent><w:p><w:pPr><w:shd w:fill="CCFFCC"/></w:pPr><w:r><w:t>框内</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>'}
        for label,body in bodies.items():
            with self.subTest(label=label):
                data=self.extract(body);e=data['background_evidence']
                self.assertEqual(len(e['hits']),1)
                self.assertEqual(e['artifact_sha256'],data['sha256'])
                self.assertTrue(e['extraction_complete'])
                precheck(data,'draft',rule_version=81)
                self.assertTrue(any(i['code']=='FINAL_BACKGROUND' for i in data['issues']))
        for story in ('header','footer'):
            xml=f'<w:{"hdr" if story=="header" else "ftr"} xmlns:w="{NS["w"]}">{bodies["run"]}</w:{"hdr" if story=="header" else "ftr"}>'
            e=self.extract('<w:p/>',extra={f'word/{story}1.xml':xml})['background_evidence']
            self.assertEqual(e['hits'][0]['part'],f'word/{story}1.xml')
        root=ET.fromstring(f'<w:document xmlns:w="{NS["w"]}"><w:background w:color="CCFFCC"/><w:body/></w:document>')
        e=background_reader(ET.fromstring(f'<w:styles xmlns:w="{NS["w"]}"/>'))(root,'word/document.xml')
        self.assertEqual(e['hits'][0]['reason'],'page_background_setting')

    def test_plain_paper_effective_inheritance_and_explicit_none(self):
        styles='<w:style w:type="paragraph" w:styleId="Base"><w:pPr><w:shd w:fill="CCFFCC"/></w:pPr><w:rPr><w:highlight w:val="yellow"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/></w:style><w:style w:type="character" w:styleId="Char"><w:rPr><w:shd w:fill="CCFFCC"/></w:rPr></w:style>'
        for direct,expected in [(False,3),(True,0)]:
            with self.subTest(direct=direct):
                p='<w:shd w:val="nil"/>' if direct else ''
                r='<w:shd w:val="clear" w:fill="auto"/><w:highlight w:val="none"/>' if direct else ''
                body=f'<w:p><w:pPr><w:pStyle w:val="Child"/>{p}</w:pPr><w:r><w:rPr><w:rStyle w:val="Char"/>{r}</w:rPr><w:t>文字</w:t></w:r></w:p>'
                self.assertEqual(len(self.extract(body,styles)['background_evidence']['hits']),expected)
        self.assertEqual(self.extract('<w:p><w:r><w:t>普通文字</w:t></w:r></w:p>',styles)['background_evidence']['hits'],[])
        defaults='<w:docDefaults><w:rPrDefault><w:rPr><w:highlight w:val="yellow"/></w:rPr></w:rPrDefault></w:docDefaults>'
        self.assertEqual(self.extract('<w:p><w:r><w:t>x</w:t></w:r></w:p>',defaults)['background_evidence']['hits'][0]['reason'],'default_background')
        for unresolved in ('Missing','Cycle'):
            cycle='<w:style w:styleId="Cycle"><w:basedOn w:val="Cycle"/></w:style>'
            e=self.extract(f'<w:p><w:pPr><w:pStyle w:val="{unresolved}"/></w:pPr></w:p>',cycle)['background_evidence']
            self.assertTrue(e['review_candidates'])

    def test_plain_paper_table_candidates_and_technical_fill_boundary(self):
        styles='<w:style w:type="table" w:styleId="T"><w:tblStylePr w:type="firstRow"><w:tcPr><w:shd w:fill="CCFFCC"/></w:tcPr></w:tblStylePr></w:style>'
        table='<w:tbl><w:tblPr><w:tblStyle w:val="T"/></w:tblPr><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>'
        e=self.extract(table,styles)['background_evidence']
        self.assertFalse(e['hits']);self.assertTrue(e['review_candidates'])
        body='<w:p><w:r><w:drawing><a:solidFill><a:srgbClr val="AAAAAA"/></a:solidFill></w:drawing></w:r></w:p>'
        e=self.extract(body,styles)['background_evidence']
        self.assertEqual(e['hits'],[]);self.assertEqual(e['review_candidates'],[])


if __name__ == '__main__': unittest.main()
