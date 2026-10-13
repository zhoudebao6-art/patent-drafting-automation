"""Simulated worker receipts test stage gates, not model execution or visual quality."""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import batch_tool as b
import parallel_batch as p
import test_parallel_batch as existing
from test_batch_tool import docx


class Workflow:
    reserve = existing.ParallelTests.reserve
    receipt = existing.ParallelTests.receipt
    record = existing.ParallelTests.record

    def __init__(self, root, mode='review_fix_then_recheck'):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.src = self.root/'input'; self.src.mkdir()
        docx(self.src/'source.docx', 'source technical material')
        policy = copy.deepcopy(existing.POLICY)
        policy['delivery'] = {'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}
        if mode is not None: policy['review_mode'] = mode
        policy_path = self.root/'policy.json'; b.atomic(policy_path, policy)
        rules_path = self.root/'rules.json'; b.atomic(rules_path, {'version': 4, 'rules': []})
        with patch.object(p, 'POLICY', policy_path), patch.object(p, 'RULES', rules_path):
            self.manifest = p.init(self.src, self.root/'work', {'projects': [
                {'title': 'fixture', 'source_files': ['source.docx'], 'primary_source': 'source.docx'}]}, self.root/'desktop')
        self.batch = self.manifest.parent

    def start_review(self):
        draft = self.reserve(1); self.record(draft)
        return self.reserve(1)

    def revision_receipt(self, reserved):
        a = reserved['attempt']
        artifact = Path(a['directory'])/'revised.docx'; docx(artifact, 'corrected ' + a['id'])
        return dict(existing.POLICY['models']['review'], context=a['context'], stage='review',
            agent_id='/root/' + a['task_name'], verdict='revised',
            reviewed_sha256=a['input_artifact_sha256'], artifact=str(artifact), artifact_sha256=b.sha(artifact),
            checks={k: 'not_run' if k == 'rendered_pages' else 'checked_and_corrected' for k in b.CHECKS},
            findings=[{'status': 'resolved', 'location': 'word/document.xml P0001',
                       'correction_evidence': 'Fixture correction supported by the source; saved to independent revised.docx'}])

    def submit(self, reserved, receipt):
        return p.record(self.manifest, reserved['case_id'], reserved['attempt']['id'], receipt)


def run_flow(root, mode):
    workflow = Workflow(root, mode)
    review = workflow.start_review()
    if mode == 'review_fix_then_recheck':
        workflow.submit(review, workflow.revision_receipt(review))
    else:
        workflow.record(review, 'fix_required')
        revision = workflow.reserve(1); workflow.record(revision)
    review = workflow.reserve(1); workflow.record(review, 'pass')
    p.deliver(workflow.manifest)
    case = b.read(workflow.manifest)['projects'][0]
    stages = [a['stage'] for a in case['attempts']]
    return {'mode': mode or 'review_only (default)', 'stages': stages, 'total_dispatches': len(stages),
            'astra_dispatches': sum(stage in {'review', 'revise'} for stage in stages),
            'final_status': case['status'], 'scope': 'Simulated stage fixture only; no model execution or timing claim'}


class InlineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workflow = Workflow(self.root/'current')

    def reject(self, reserved, receipt):
        with self.assertRaises(b.BatchError): self.workflow.submit(reserved, receipt)

    def test_revised_waits_for_new_worker_pass_before_delivery(self):
        w = self.workflow; review = w.start_review()
        case = w.submit(review, w.revision_receipt(review))
        self.assertEqual(case['status'], 'revised')
        self.assertNotIn('passed_review_sha256', case)
        with self.assertRaisesRegex(b.BatchError, 'No accepted case'):
            p.deliver(w.manifest)
        self.assertEqual(list((w.root/'desktop').rglob('*.docx')), [])
        next_review = w.reserve(1)
        self.assertEqual(next_review['attempt']['stage'], 'review')
        self.assertNotEqual(next_review['attempt']['task_name'], review['attempt']['task_name'])
        self.assertEqual(next_review['attempt']['input_artifact_sha256'], case['artifact_sha256'])
        w.record(next_review, 'pass'); p.deliver(w.manifest)
        self.assertEqual(b.read(w.manifest)['projects'][0]['status'], 'delivered')
        self.assertEqual(len(list((w.root/'desktop').rglob('*.docx'))), 1)

    def test_legacy_policy_rejects_inline_revision(self):
        w = Workflow(self.root/'legacy', None); review = w.start_review()
        with self.assertRaisesRegex(b.BatchError, 'frozen policy'):
            w.submit(review, w.revision_receipt(review))

    def test_invalid_policy_values_rejected(self):
        for value in ('fast_pass', None, [], 1):
            with self.subTest(value=value), self.assertRaises(b.BatchError):
                p.validate_policy(dict(existing.POLICY, review_mode=value))

    def test_cross_case_attempt_context_sha_model_and_input_hash_rejected(self):
        w = self.workflow; review = w.start_review(); receipt = w.revision_receipt(review)
        foreign = w.batch/'cases'/'002'/'stages'/'foreign.docx'
        foreign.parent.mkdir(parents=True); docx(foreign, 'foreign')
        prior = Path(review['attempt']['input_artifact']).parent/'another.docx'; docx(prior, 'old attempt')
        changes = [{'artifact': str(foreign), 'artifact_sha256': b.sha(foreign)},
                   {'artifact': str(prior), 'artifact_sha256': b.sha(prior)},
                   {'artifact_sha256': 'fake'}, {'reviewed_sha256': 'fake'},
                   {'model': 'gpt-6.1-sol'}, {'reasoning_effort': 'low'},
                   {'context': dict(receipt['context'], attempt='foreign')}]
        for change in changes:
            with self.subTest(change=change): self.reject(review, dict(receipt, **change))

    def test_unchanged_bytes_and_input_tampering_rejected(self):
        w = self.workflow; review = w.start_review(); receipt = w.revision_receipt(review)
        artifact = Path(receipt['artifact']); source = Path(review['attempt']['input_artifact'])
        shutil.copyfile(source, artifact)
        self.reject(review, dict(receipt, artifact_sha256=b.sha(artifact)))
        receipt = w.revision_receipt(review)
        docx(source, 'tampered input')
        self.reject(review, receipt)

    def test_closed_findings_locations_corrections_and_six_checks_required(self):
        w = self.workflow; review = w.start_review(); receipt = w.revision_receipt(review)
        invalid = [dict(receipt, findings=[]), dict(receipt, checks={'rendered_pages': 'not_run'})]
        for key in ('status', 'location', 'correction_evidence'):
            finding = dict(receipt['findings'][0]); finding.pop(key)
            invalid.append(dict(receipt, findings=[finding]))
        for value in invalid:
            with self.subTest(value=value): self.reject(review, value)

    def test_pass_cannot_also_submit_revision_or_reuse_old_receipt(self):
        w = self.workflow; review = w.start_review(); revision = w.revision_receipt(review)
        passed = w.receipt(review, 'pass')
        self.reject(review, dict(passed, artifact=revision['artifact'], artifact_sha256=revision['artifact_sha256']))
        w.submit(review, revision)
        self.reject(review, passed)

    def test_handoff_fixture_saves_one_astra_dispatch(self):
        old = run_flow(self.root/'old-flow', None)
        new = run_flow(self.root/'new-flow', 'review_fix_then_recheck')
        self.assertEqual(old['stages'], ['draft', 'review', 'revise', 'review'])
        self.assertEqual(new['stages'], ['draft', 'review', 'review'])
        self.assertEqual(old['astra_dispatches'] - new['astra_dispatches'], 1)
        self.assertEqual((old['final_status'], new['final_status']), ('delivered', 'delivered'))


if __name__ == '__main__':
    unittest.main()
