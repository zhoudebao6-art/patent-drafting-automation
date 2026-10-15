"""Synthetic closeout fixtures; no real patent or visual acceptance claim."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_tool as b
import parallel_batch as q
import flat_delivery as flat
import render_docx_checked as renderer
import prepare_handoff as handoff
import output_contract as contract
import test_lean_execution as lean
import test_output_contract as wording


class CloseoutTests(unittest.TestCase):
    reserve=lean.LeanTests.reserve
    receipt=lean.LeanTests.receipt
    record=lean.LeanTests.record

    def setUp(self):
        lean.LeanTests.setUp(self)
        policy=b.read(q.POLICY);policy['post_review_optimization']='single_closeout_v25'
        b.atomic(q.POLICY,policy)
        selection={'projects':[{'title':f'项目{i}','source_files':[f'{i}.docx'],'primary_source':f'{i}.docx'} for i in range(1,4)]}
        self.manifest=q.init(self.src,self.root/'closeout-work',selection,self.root/'closeout-desktop')
        self.batch=self.manifest.parent

    def accepted(self):
        self.record(self.reserve(1));review=self.reserve(1);receipt=self.receipt(review,'pass')
        q.record(self.manifest,review['case_id'],review['attempt']['id'],receipt)
        return review,receipt

    def test_record_then_delivery_decodes_once_but_rechecks_all_bytes(self):
        self.record(self.reserve(1));review=self.reserve(1);receipt=self.receipt(review,'pass')
        from PIL import Image
        from pypdf import PdfReader
        with patch('PIL.Image.open',wraps=Image.open) as image,patch('pypdf.PdfReader',wraps=PdfReader) as pdf:
            q.record(self.manifest,review['case_id'],review['attempt']['id'],receipt)
            q.deliver(self.manifest)
        self.assertEqual(image.call_count,1);self.assertEqual(pdf.call_count,1)
        stored=q.load_manifest(self.manifest)['projects'][0]['receipts'][-1]
        self.assertIn('render_validation',stored)
        self.assertNotIn('render_validation',receipt)

    def test_worker_forged_validation_never_skips_first_format_check(self):
        self.record(self.reserve(1));review=self.reserve(1);receipt=self.receipt(review,'pass')
        evidence=Path(receipt['render']['evidence']);data=b.read(evidence)
        page=evidence.parent/data['pages'][0]['file'];page.write_bytes(b'not an image')
        data['pages'][0]['sha256']=b.sha(page);b.atomic(evidence,data)
        receipt['render']['evidence_sha256']=b.sha(evidence)
        receipt['render_validation']=q.render_validation(q.load_manifest(self.manifest),
            q.load_manifest(self.manifest)['projects'][0],review['attempt'],receipt['render'])
        with self.assertRaises(b.BatchError):q.record(self.manifest,review['case_id'],review['attempt']['id'],receipt)
        self.assertNotEqual(q.load_manifest(self.manifest)['projects'][0]['status'],'accepted')

    def test_changed_page_during_copy_blocks_publication(self):
        _,receipt=self.accepted();page=Path(receipt['render']['evidence']).parent/'page-1.png'
        original=flat.shutil.copyfileobj
        def change(src,out):
            original(src,out);page.write_bytes(b'changed during copy')
        with patch.object(flat.shutil,'copyfileobj',side_effect=change):
            with self.assertRaises(b.BatchError):q.deliver(self.manifest)
        self.assertFalse(list((self.root/'closeout-desktop').rglob('*.docx')))

    def test_manifest_swapped_before_inner_read_blocks_publication(self):
        _,receipt=self.accepted();evidence=Path(receipt['render']['evidence'])
        original=renderer.verify_render;swaps=[]
        def change(*args,**kwargs):
            data=b.read(evidence);page=evidence.parent/data['pages'][0]['file']
            page.write_bytes(b'not an image');data['pages'][0]['sha256']=b.sha(page)
            b.atomic(evidence,data);swaps.append(True)
            return original(*args,**kwargs)
        with patch.object(renderer,'verify_render',side_effect=change):
            with self.assertRaisesRegex(b.BatchError,'Consumed render evidence'):
                q.deliver(self.manifest)
        self.assertEqual(swaps,[True])
        self.assertEqual(q.load_manifest(self.manifest)['projects'][0]['status'],'accepted')
        self.assertFalse(list((self.root/'closeout-desktop').rglob('*.docx')))

    def test_manifest_changed_after_consumption_is_rejected_in_both_modes(self):
        review,receipt=self.accepted();evidence=Path(receipt['render']['evidence'])
        original_bytes=evidence.read_bytes();original=renderer.sha
        page=evidence.parent/'page-1.png'
        def change(path):
            result=original(path)
            if Path(path)==page:evidence.write_bytes(original_bytes+b' ')
            return result
        for check_format in (True,False):
            evidence.write_bytes(original_bytes)
            with self.subTest(check_format=check_format),patch.object(renderer,'sha',side_effect=change):
                with self.assertRaisesRegex(renderer.RenderError,'changed during verification'):
                    renderer.verify_render(evidence.parent,review['attempt']['input_artifact'],
                        check_format=check_format,expected_evidence_sha256=receipt['render']['evidence_sha256'])

    def test_original_source_checked_once_per_new_publication(self):
        self.accepted()
        with patch.object(q,'verify_original',wraps=q.verify_original) as verify:q.deliver(self.manifest)
        self.assertEqual(verify.call_count,1)

    def test_changed_source_isolated_without_blocking_other_accepted_case(self):
        self.accepted();self.accepted()
        (self.src/'1.docx').write_bytes(b'changed input')
        dest=Path(q.deliver(self.manifest))
        m=q.load_manifest(self.manifest)
        self.assertEqual(m['projects'][0]['status'],'needs_input')
        self.assertEqual(m['projects'][1]['status'],'delivered')
        self.assertEqual(len(list(dest.glob('*.docx'))),1)

    def test_source_changed_during_copy_is_quarantined_before_publication(self):
        self.accepted();original=flat.shutil.copyfileobj
        def change(src,out):
            original(src,out);(self.src/'1.docx').write_bytes(b'changed input')
        with patch.object(flat.shutil,'copyfileobj',side_effect=change):dest=Path(q.deliver(self.manifest))
        self.assertFalse(list(dest.glob('*.docx')))
        self.assertEqual(q.load_manifest(self.manifest)['projects'][0]['status'],'needs_input')

    def test_closure_and_recovery_still_use_full_render_decoding(self):
        self.accepted();q.deliver(self.manifest)
        q.defer(self.manifest,'002','needs_input','fixture missing source')
        q.defer(self.manifest,'003','failed','fixture worker failure')
        from PIL import Image
        with patch('PIL.Image.open',wraps=Image.open) as image:q.deliver(self.manifest)
        self.assertEqual(image.call_count,1)
        with patch('PIL.Image.open',wraps=Image.open) as image:q.deliver(self.manifest,audit_history=True)
        self.assertEqual(image.call_count,1)

    def test_corrupt_controller_binding_or_receipt_is_rejected(self):
        review,receipt=self.accepted();m=q.load_manifest(self.manifest);c=m['projects'][0];a=c['attempts'][-1]
        proof=copy.deepcopy(c['receipts'][-1]['render_validation']);proof['context']['case_id']='999'
        with self.assertRaisesRegex(b.BatchError,'binding changed'):
            q.verify_render(m,c,a,receipt['render'],validation=proof)
        saved=Path(a['directory'])/'receipt.json';data=b.read(saved);data['render_validation']=proof;b.atomic(saved,data)
        with self.assertRaises(b.BatchError):q.deliver(self.manifest)

    def test_interrupted_publish_recovery_is_full_and_does_not_copy_again(self):
        self.accepted();atomic=b.atomic
        def interrupted(path,value):
            if Path(path)==self.manifest and value.get('delivery_path'):raise OSError('commit interrupted')
            return atomic(path,value)
        with patch.object(b,'atomic',side_effect=interrupted):
            with self.assertRaises(OSError):q.deliver(self.manifest)
        from PIL import Image
        with patch('PIL.Image.open',wraps=Image.open) as image, \
             patch.object(flat.shutil,'copyfileobj',side_effect=AssertionError('duplicate copy')):q.deliver(self.manifest)
        self.assertEqual(image.call_count,1)


class CheckedReportReuseTests(unittest.TestCase):
    setUp=wording.QueueContractTests.setUp
    reserve=wording.QueueContractTests.reserve
    receipt=wording.QueueContractTests.receipt
    review_attempt=wording.QueueContractTests.review_attempt

    def test_contract_reuses_just_verified_json_without_second_read(self):
        review,receipt=self.review_attempt()
        handoff_path=Path(review['attempt']['prepared_handoff']['path'])
        inspection=Path(b.read(handoff_path)['input_inspection']['inspection'])
        original=b.read
        def read(path):
            if Path(path) in {inspection,handoff_path}:raise AssertionError('Redundant JSON read after handoff verification')
            return original(path)
        with patch.object(b,'read',side_effect=read):
            q.record(self.manifest,review['case_id'],review['attempt']['id'],receipt)
            q.deliver(self.manifest)
        self.assertEqual(q.load_manifest(self.manifest)['projects'][0]['status'],'delivered')


if __name__=='__main__':unittest.main()
