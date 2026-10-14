"""Meaningful font inheritance and image-location evidence; not visual acceptance."""
import tempfile
import unittest
from inspect_docx import inspect, precheck, NS
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


if __name__ == '__main__': unittest.main()
