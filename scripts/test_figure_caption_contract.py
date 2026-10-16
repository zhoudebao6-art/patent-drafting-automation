"""v38 final-figure annotations cannot be waived by generic layout findings."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from lxml import etree as ET
import batch_tool as batch
import inspect_docx as scan
import output_contract as final
import parallel_batch as queue
import authoring_toolkit as toolkit
import figure_caption_contract as captions
import test_page_flow_contract as flow_fixtures
import test_authoring_toolkit as authoring_fixtures
from test_page_flow_contract import paragraph, FALSE_FLOW, TITLE


IMAGE = paragraph('', FALSE_FLOW, '<w:r><w:drawing/></w:r>')
VALID = IMAGE + paragraph('图1', FALSE_FLOW)
NOTE = '物资量，单位为对应物资类别的计量单位。'


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=flow_fixtures.FlowEvidenceTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def report(self, final_content=VALID, **kw):
        return self.fixture.report(final_content=final_content, **kw)

    def accept(self, report, findings=(), **kw):
        return final.check(report, TITLE, findings, strict_layout=True, strict_captions=True, **kw)

    def generic_findings(self, report):
        return [dict(row, rule_id='FORMAT-03', classification='page_flow_reviewed', status='closed',
                     artifact_sha256=report['sha256'], reason='已查看页面')
                for row in report['page_flow_evidence']['review_candidates']]

    def test_valid_sections_retain_body_explanation_and_abstract_no_caption(self):
        r=self.report(body=paragraph('附图说明',FALSE_FLOW)+paragraph('图1为本发明的物资量图。',FALSE_FLOW)+
                      paragraph('具体实施方式',FALSE_FLOW)+paragraph(NOTE,FALSE_FLOW))
        self.assertEqual(self.accept(r)['figure_caption_contract'],captions.MODE)
        self.assertEqual([c['text'] for c in r['figure_caption_evidence']['captions']],['图1'])
        self.assertEqual(r['page_flow_evidence']['abstract_labels'],[])

    def test_outside_text_never_waived_even_if_styled_table_or_same_caption(self):
        variants=[VALID+paragraph(NOTE,FALSE_FLOW), paragraph(NOTE,FALSE_FLOW)+VALID,
                  IMAGE+paragraph('图1'+NOTE,FALSE_FLOW),
                  IMAGE+paragraph('图1',FALSE_FLOW,'<w:r><w:br/><w:t>'+NOTE+'</w:t></w:r>'),
                  VALID+paragraph(NOTE,FALSE_FLOW+'<w:outlineLvl w:val="0"/>'),
                  VALID+paragraph('具体实施方式',FALSE_FLOW)+paragraph(NOTE,FALSE_FLOW),
                  VALID+'<w:tbl><w:tr><w:tc>'+paragraph(NOTE,FALSE_FLOW)+'</w:tc></w:tr></w:tbl>']
        for content in variants:
            with self.subTest(content=content):
                r=self.report(content)
                with self.assertRaisesRegex(batch.BatchError,'only 图N'):
                    self.accept(r,self.generic_findings(r))

    def test_caption_breaks_fields_objects_and_generated_labels_fail(self):
        for content in ('<w:r><w:br/></w:r>','<w:r><w:cr/></w:r>','<w:r><w:tab/></w:r>',
                        '<w:fldSimple w:instr="DATE"/>','<m:oMath/>','<w:r><w:drawing/></w:r>'):
            with self.subTest(content=content):
                r=self.report(IMAGE+paragraph('图1',FALSE_FLOW,content))
                with self.assertRaises(batch.BatchError):self.accept(r,self.generic_findings(r))
        r=self.report(IMAGE+paragraph('图1',FALSE_FLOW+'<w:numPr><w:numId w:val="7"/></w:numPr>'))
        with self.assertRaises(batch.BatchError):self.accept(r,self.generic_findings(r))

    def test_caption_is_exact_number_only_and_sequence_is_not_invented(self):
        for text in ('图1。','图1物资量图','图 1','图一','图2'):
            with self.subTest(text=text):
                r=self.report(IMAGE+paragraph(text,FALSE_FLOW))
                with self.assertRaises(batch.BatchError):self.accept(r,self.generic_findings(r))
        self.accept(self.report(VALID+IMAGE+paragraph('图2',FALSE_FLOW)))

    def test_internal_graphic_labels_need_one_specific_source_and_visual_finding(self):
        for xml in ('<w:r><w:pict><v:shape><v:textbox><w:txbxContent>'+paragraph('物资量',FALSE_FLOW)+
                    '</w:txbxContent></v:textbox></v:shape></w:pict></w:r>',
                    '<w:r><w:drawing><a:t>物资量</a:t></w:drawing></w:r>'):
            r=self.report(paragraph('',FALSE_FLOW,xml)+paragraph('图1',FALSE_FLOW))
            hit=r['figure_caption_evidence']['review_candidates'][0]
            with self.assertRaises(batch.BatchError):self.accept(r,self.generic_findings(r))
            finding=dict(hit,rule_id='TXT-11',classification='figure_internal_label',status='closed',
                         artifact_sha256=r['sha256'],figure_number=1,label_role='axis_label',
                         source_sha256='source-hash',source_locator='source image axis',registry_id='fig1',
                         visual_locator='final page 5 inside plot left axis',reason='与源图一致的图内坐标名称')
            # No redundant page_flow_reviewed finding needed for the same label.
            self.accept(r,[finding],source_hashes=['source-hash'])
            for key,value in [('text','changed'),('artifact_sha256','old'),('source_sha256','unknown'),
                              ('figure_number',2),('label_role','outside_note'),('visual_locator','')]:
                bad=dict(finding,**{key:value})
                with self.assertRaises(batch.BatchError):self.accept(r,[bad],source_hashes=['source-hash'])

    def test_missing_stale_evidence_or_scope_never_passes_and_legacy_stays_legacy(self):
        r=self.report(VALID+paragraph(NOTE,FALSE_FLOW))
        final.check(r,TITLE,self.generic_findings(r),strict_layout=True)
        for key,value in [('figure_caption_evidence',None)]:
            bad=copy.deepcopy(r);bad[key]=value
            with self.assertRaises(batch.BatchError):self.accept(bad)
        r=self.report()
        for key,value in [('artifact_sha256','old'),('scope_resolved',False),('complete',False)]:
            bad=copy.deepcopy(r);bad['figure_caption_evidence'][key]=value
            with self.assertRaises(batch.BatchError):self.accept(bad)
        body=[p for part in r['parts'] if part['part']=='word/document.xml' for p in part['paragraphs']]
        for p in body:p['default_header']=''
        r['page_flow_evidence']['sections']=[]
        self.assertFalse(captions.evidence(r,body)['scope_resolved'])

    def test_no_figures_remains_possible_with_resolved_empty_section(self):
        self.accept(self.report(final_content=''))

    def test_policy_flag_scoped_and_dependency_hashes_include_contract(self):
        policy=json.loads((Path(__file__).resolve().parent.parent/'references/batch-policy.json').read_text(encoding='utf-8'))
        queue.validate_policy(policy)
        legacy=copy.deepcopy(policy);legacy.pop('figure_caption_contract');queue.validate_policy(legacy)
        for key,value in [('figure_caption_contract','wrong'),('layout_contract',None),('output_contract',None)]:
            bad=copy.deepcopy(policy);bad[key]=value
            with self.assertRaises(batch.BatchError):queue.validate_policy(bad)
        self.assertIn('figure_caption_contract.py',scan.extractor_dependencies())

    def test_verify_reuses_prepared_report_and_enables_flag(self):
        r=self.report();case={'title':TITLE,'sources':[]};attempt={'input_artifact_sha256':r['sha256']}
        verified={'handoff':{'input_inspection':True},'input_report':r}
        with patch('prepare_handoff.verify_prepared',return_value=verified), patch.object(scan,'inspect',side_effect=AssertionError('duplicate extraction')):
            value=final.verify({'policy':{'output_contract':final.MODE,'figure_caption_contract':captions.MODE}},case,attempt,{})
        self.assertEqual(value['figure_caption_contract'],captions.MODE)


class GeneratorCaptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=authoring_fixtures.ToolkitTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.add_figure()

    def test_generator_rejects_free_text_in_final_figures(self):
        f=self.fixture;f.spec['sections'][4]['blocks'].append(dict(id='note',type='paragraph',role='body',text=NOTE))
        with self.assertRaisesRegex(ValueError,'figure blocks only'):toolkit.validate(f.root,f.spec)

    def test_readback_rejects_caption_break_and_field_without_altering_image(self):
        f=self.fixture;ctx=toolkit.validate(f.root,f.spec);path=f.root/'test.docx'
        from docx import Document
        for tag in ('br','cr','tab','fldChar'):
            toolkit.render_document(ctx,path);doc=Document(path)
            cap=next(p for p in doc.paragraphs if p.text=='图1')
            cap._p.append(ET.fromstring(f'<w:r xmlns:w="{scan.NS["w"]}"><w:{tag}/></w:r>'))
            doc.save(path)
            with self.assertRaisesRegex(ValueError,'number-only'):toolkit.verify_document(ctx,path)


if __name__=='__main__':unittest.main()
