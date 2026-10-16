"""Small synthetic protocol scenarios; no real patent or model execution."""
import copy
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import batch_tool as b
import incremental_review as reuse
import parallel_batch as queue


def document(path, texts, extra=None):
    from xml.sax.saxutils import escape
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
            ''.join('<w:p><w:r><w:t>' + escape(t) + '</w:t></w:r></w:p>' for t in texts) +
            '</w:body></w:document>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


class IncrementalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.case_root = self.root / 'cases' / '001'
        self.case_root.mkdir(parents=True)
        self.m = {'batch_dir': str(self.root), 'batch_id': 'batch', 'rules_sha256': 'rules',
                  'policy': {'review_reuse': reuse.MODE, 'review_mode': 'review_only'}}
        self.case = {'id': '001', 'source_digest': 'source', 'receipts': [], 'attempts': []}
        self.counter = 0

    def attempt(self, texts, extra=None, nested=False):
        self.counter += 1
        writer = self.case_root / 'stages' / f'write-{self.counter}'
        writer.mkdir(parents=True)
        if nested:
            writer = writer / 'output/subdir'; writer.mkdir(parents=True)
        artifact = writer / 'output.docx'; document(artifact, texts, extra)
        folder = self.case_root / 'stages' / f'review-{self.counter}'
        out = folder / 'prepared'; out.mkdir(parents=True)
        context = {'batch_id': 'batch', 'case_id': '001', 'source_digest': 'source',
                   'rules_sha256': 'rules', 'stage': 'review', 'attempt': folder.name,
                   'input_artifact_sha256': b.sha(artifact)}
        attempt = {'id': folder.name, 'directory': str(folder), 'stage': 'review',
                   'state': 'bound', 'context': context, 'input_artifact': str(artifact),
                   'input_artifact_sha256': b.sha(artifact), 'agent_id': 'cli:' + folder.name}
        dispatch = folder / 'dispatch.json'; b.atomic(dispatch, {'synthetic_test_dispatch': context})
        attempt.update(dispatch_evidence=str(dispatch), dispatch_sha256=b.sha(dispatch))
        pointer = reuse.prepare(self.m, self.case, attempt, out)
        handoff = out / 'handoff.json'; b.atomic(handoff, {'context': context, 'review_reuse': pointer})
        attempt['prepared_handoff'] = {'path': str(handoff), 'sha256': b.sha(handoff)}
        self.case['attempts'].append(attempt)
        return attempt, reuse.load_plan(pointer), pointer

    def receipt(self, attempt, pointer, *, final=False):
        return {'stage': 'review', 'context': attempt['context'], 'agent_id': attempt['agent_id'],
                'reviewed_sha256': attempt['input_artifact_sha256'],
                'verdict': 'pass' if final else 'fix_required', 'findings': [],
                'checks': {k: 'passed' if k != 'rendered_pages' else 'not_run' for k in b.CHECKS},
                'review_coverage': {'schema_version': 1, 'plan_sha256': pointer['plan_sha256'],
                    'checks': {k: {'fresh': ['*'], 'passed': ['*'], 'inherited': [], 'impact_review': ''}
                               for k in reuse.CHECKS}, 'pages': []}}

    def record(self, attempt, receipt):
        receipt = copy.deepcopy(receipt)
        receipt['review_reuse_validation'] = reuse.validate(self.m, self.case, attempt, receipt)
        b.atomic(Path(attempt['directory']) / 'receipt.json', receipt)
        attempt.update(state='completed', receipt_sha256=reuse.digest(receipt))
        self.case['receipts'].append(receipt)
        return receipt

    def inherited_receipt(self, attempt, plan, pointer):
        receipt = self.receipt(attempt, pointer)
        for check, row in receipt['review_coverage']['checks'].items():
            inherited = reuse.expand(plan['eligible_by_check'][check], plan['current_scopes'])
            fresh = sorted(set(plan['current_scopes']) - inherited)
            row.update(fresh=fresh, passed=fresh, inherited=sorted(inherited),
                       impact_review='Compared changed text with definitions, claims and formula/figure references; unrelated reviewed scopes retain their meaning.')
        return receipt

    def render(self, attempt, colors):
        from PIL import Image
        from pypdf import PdfWriter
        root = Path(attempt['directory']) / 'render'; root.mkdir(exist_ok=True)
        pages = []
        for index, color in enumerate(colors, 1):
            path = root / f'page-{index}.png'; Image.new('RGB', (16, 16), color).save(path)
            pages.append({'file': path.name, 'sha256': b.sha(path)})
        pdf = root / 'output.pdf'; writer = PdfWriter()
        for _ in colors:
            writer.add_blank_page(width=16, height=16)
        with pdf.open('wb') as stream:
            writer.write(stream)
        path = root / 'render.json'
        b.atomic(path, {'status': 'rendered_pending_visual_review', 'input': attempt['input_artifact'],
                       'input_sha256': attempt['input_artifact_sha256'], 'runtime_fingerprint': 'test-runtime',
                       'pdf': {'file': pdf.name, 'sha256': b.sha(pdf)}, 'pages': pages})
        return {'artifact_sha256': attempt['input_artifact_sha256'], 'evidence': str(path),
                'evidence_sha256': b.sha(path)}

    def test_local_change_preserves_explicit_passed_scopes_and_category_without_whole_review(self):
        first, old, pointer = self.attempt(['stable A', 'stable B', 'needs repair'])
        receipt = self.receipt(first, pointer)
        # A failed category may still contain explicitly passed local units.
        receipt['checks']['source_support'] = 'last paragraph unsupported'
        scopes = old['current_scopes']
        receipt['review_coverage']['checks']['source_support'].update(
            fresh=['*'], passed={'all_except': ['word/document.xml#p3']})
        self.record(first, receipt)
        second, plan, pointer = self.attempt(['stable A', 'stable B', 'supported repair'])
        self.assertEqual(plan['changed_scopes'], ['word/document.xml#p3'])
        self.assertFalse(plan['global_changes'])
        for check in reuse.CHECKS:
            eligible = reuse.expand(plan['eligible_by_check'][check], plan['current_scopes'])
            self.assertNotIn('word/document.xml#p3', eligible)
            self.assertIn('word/document.xml#p1', eligible)
        brief = reuse.summary(plan)
        self.assertNotIn('inventory', brief)
        self.assertEqual(brief['must_review_by_check']['source_support'], ['word/document.xml#p3'])
        current = self.inherited_receipt(second, plan, pointer)
        self.record(second, current)
        third, unchanged, pointer = self.attempt(['stable A', 'stable B', 'supported repair'])
        self.assertTrue(all(v == ['*'] for v in unchanged['eligible_by_check'].values()))
        current = self.inherited_receipt(third, unchanged, pointer)
        self.assertTrue(all(not r['fresh'] for r in current['review_coverage']['checks'].values()))
        self.record(third, current)

    def test_paragraph_shift_reuses_only_unique_order_preserved_passed_content(self):
        first, _, pointer = self.attempt(['A', 'unpassed B', 'C', 'D'])
        receipt = self.receipt(first, pointer)
        receipt['checks']['formulas'] = 'B remains unresolved'
        receipt['review_coverage']['checks']['formulas']['passed'] = {'all_except': ['word/document.xml#p2']}
        self.record(first, receipt)
        for texts, expected, must_check in (
            (['inserted', 'A', 'unpassed B', 'C', 'D'], {'word/document.xml#p2', 'word/document.xml#p4', 'word/document.xml#p5'}, {'word/document.xml#p1', 'word/document.xml#p3'}),
            (['A', 'unpassed B', 'D'], {'word/document.xml#p1', 'word/document.xml#p3'}, {'word/document.xml#p2'}),
            (['C', 'unpassed B', 'A', 'D'], set(), {'word/document.xml#p1', 'word/document.xml#p2', 'word/document.xml#p3', 'word/document.xml#p4'}),
        ):
            with self.subTest(texts=texts):
                attempt, plan, pointer = self.attempt(texts)
                eligible = reuse.expand(plan['eligible_by_check']['formulas'], plan['current_scopes'])
                self.assertEqual(eligible - {'@package'}, expected)
                self.assertTrue(must_check.isdisjoint(eligible))
                if texts[0] == 'inserted':
                    self.assertEqual(plan['added_scopes'], ['word/document.xml#p1'])
                    self.assertEqual(reuse.summary(plan)['relocated_scopes'], [
                        {'part': 'word/document.xml', 'old_start': 1, 'new_start': 2, 'count': 4}])
                    self.assertEqual(plan['scope_mapping']['word/document.xml#p3'], 'word/document.xml#p2')
                elif texts[0] == 'C':
                    self.assertEqual(plan['reordered_parts'], ['word/document.xml'])
                    self.assertIn('paragraph_order:word/document.xml', plan['global_changes'])
                # The old failed unit becomes passed only after this round's actual
                # fresh check; validate, but do not replace the fixture's baseline.
                reuse.validate(self.m, self.case, attempt, self.inherited_receipt(attempt, plan, pointer))
        # Same paragraph order and table structure do not justify reuse if
        # an unchanged paragraph moves to a different cell or text container.
        def pxml(value):
            return '<w:p><w:r><w:t>' + value + '</w:t></w:r></w:p>'
        for container_kind in ('cells', 'textboxes'):
            with self.subTest(container=container_kind):
                def shell(left, right):
                    if container_kind == 'cells':
                        inner = '<w:tbl><w:tr><w:tc>' + left + '</w:tc><w:tc>' + right + '</w:tc></w:tr></w:tbl>'
                    else:
                        def box(content):
                            return '<w:p><w:r><w:pict><w:txbxContent>' + content + '</w:txbxContent></w:pict></w:r></w:p>'
                        inner = box(left) + box(right)
                    return '<w:document xmlns:w="' + reuse.W[1:-1] + '"><w:body>' + inner + '</w:body></w:document>'
                before_xml = shell(pxml('A') + pxml(''), pxml('') + pxml('B'))
                after_xml = shell(pxml(''), pxml('A') + pxml('') + pxml('B'))
                paths = [self.root / 'before-container.docx', self.root / 'after-container.docx']
                for path, xml in zip(paths, (before_xml, after_xml)):
                    with ZipFile(path, 'w') as archive:
                        archive.writestr('word/document.xml', xml)
                before_inventory, after_inventory = map(reuse.inventory, paths)
                self.assertEqual(before_inventory['global_parts'], after_inventory['global_parts'])
                mapped, _ = reuse.match_scopes(before_inventory['scopes'], after_inventory['scopes'])
                import xml.etree.ElementTree as ET
                paragraphs = ET.fromstring(after_xml).iter(reuse.W + 'p')
                current_a = next('word/document.xml#p' + str(i) for i, paragraph in enumerate(paragraphs, 1)
                                 if paragraph.findtext('./' + reuse.W + 'r/' + reuse.W + 't') == 'A')
                self.assertNotIn(current_a, mapped)
        for before, after, expected in (
            ({'a#p1': 'X', 'a#p2': 'X'}, {'a#p1': 'new', 'a#p2': 'X', 'a#p3': 'X'}, {}),
            ({'a#p1': 'X', 'a#p2': 'X'}, {'a#p1': 'X', 'a#p2': 'X'}, {'a#p1': 'a#p1', 'a#p2': 'a#p2'}),
            ({'a#p1': 'X'}, {'b#p1': 'X'}, {}),
        ):
            with self.subTest(before=before, after=after):
                self.assertEqual(reuse.match_scopes(before, after)[0], expected)

    def test_missing_coverage_failed_scopes_and_structural_changes_cannot_inherit(self):
        first, old, pointer = self.attempt(['A', 'B'])
        receipt = self.receipt(first, pointer)
        receipt['checks']['formulas'] = 'not_run'
        receipt['review_coverage']['checks']['formulas'].update(fresh=[], passed=[])
        self.record(first, receipt)
        second, plan, _ = self.attempt(['A', 'B', 'new paragraph'])
        self.assertTrue(plan['added_scopes'])
        self.assertFalse(plan['global_changes'])
        self.assertFalse(plan['eligible_by_check']['formulas'])
        self.assertIn('word/document.xml#p1', reuse.expand(plan['eligible_by_check']['figures'], plan['current_scopes']))
        global_attempt, global_plan, _ = self.attempt(['A', 'B'], {'word/styles.xml': '<styles changed="yes"/>'})
        self.assertTrue(global_plan['global_changes'])
        self.assertTrue(all(not v for v in global_plan['eligible_by_check'].values()))
        # A summary-only legacy receipt never supplies implicit local approval.
        self.case['attempts'].remove(second)
        self.case['attempts'].remove(global_attempt)
        old_receipt = self.case['receipts'][-1]
        old_receipt.pop('review_coverage'); old_receipt.pop('review_reuse_validation')
        b.atomic(Path(first['directory']) / 'receipt.json', old_receipt)
        first['receipt_sha256'] = reuse.digest(old_receipt)
        _, plan, _ = self.attempt(['A', 'B'])
        self.assertIsNone(plan['previous'])
        # Package properties may feed visible fields. Only pure edit metadata
        # without such fields may be ignored; no new standalone test family.
        core = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
                '<dc:title>{title}</dc:title><dcterms:modified>{stamp}</dcterms:modified></cp:coreProperties>')
        for kind in ('custom_property', 'title', 'save_timestamp', 'property_field_timestamp'):
            with self.subTest(package_change=kind):
                before_path, after_path = self.root / 'property-before.docx', self.root / 'property-after.docx'
                extras_before = {'docProps/core.xml': core.format(title='same', stamp='before')}
                extras_after = {'docProps/core.xml': core.format(title='changed' if kind == 'title' else 'same', stamp='after')}
                if kind == 'custom_property':
                    extras_before['docProps/custom.xml'] = '<Properties><p>A</p></Properties>'
                    extras_after['docProps/custom.xml'] = '<Properties><p>B</p></Properties>'
                if kind == 'property_field_timestamp':
                    field = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                             '<w:p><w:fldSimple w:instr=" SAVEDATE "><w:r><w:t>display</w:t></w:r></w:fldSimple></w:p>'
                             '</w:body></w:document>')
                    # Use a header member to exercise the same parser without a
                    # duplicate document.xml ZIP member in these fixtures.
                    extras_before['word/header1.xml'] = extras_after['word/header1.xml'] = field
                document(before_path, ['same'], extras_before); document(after_path, ['same'], extras_after)
                before_inventory, after_inventory = reuse.inventory(before_path), reuse.inventory(after_path)
                self.assertEqual(before_inventory == after_inventory, kind == 'save_timestamp')

    def test_coverage_holes_changed_inheritance_and_empty_dependency_decision_are_rejected(self):
        first, _, pointer = self.attempt(['A', 'B'])
        self.record(first, self.receipt(first, pointer))
        second, plan, pointer = self.attempt(['A', 'B changed'])
        valid = self.inherited_receipt(second, plan, pointer)
        def stale(r):
            r['review_coverage']['checks']['formulas'].update(fresh=[], passed=[], inherited=['*'])
        def hole(r):
            r['review_coverage']['checks']['formulas']['passed'] = []
        def impact(r):
            r['review_coverage']['checks']['formulas']['impact_review'] = ''
        def false_pass(r):
            r['review_coverage']['checks']['formulas']['passed'] = ['*']
        def malformed_checks(r):
            r['checks'] = 'not an object'
        def empty_complement(r):
            r['checks']['formulas'] = 'failed'
            r['review_coverage']['checks']['formulas'].update(fresh=['*'], passed={'all_except': []}, inherited=[])
        for change in (stale, hole, impact, false_pass, malformed_checks, empty_complement):
            with self.subTest(change=change.__name__):
                candidate = copy.deepcopy(valid); change(candidate)
                with self.assertRaises(b.BatchError):
                    reuse.validate(self.m, self.case, second, candidate)

    def test_identical_reviewed_pages_inherit_changed_and_unseen_pages_require_view(self):
        first, _, pointer = self.attempt(['A', 'B'])
        receipt = self.receipt(first, pointer); receipt['render'] = self.render(first, ['white', 'white', 'white'])
        receipt['review_coverage']['pages'] = [{'page': 1, 'mode': 'fresh', 'status': 'passed'},
                                             {'page': 2, 'mode': 'fresh', 'status': 'passed'},
                                             {'page': 3, 'mode': 'fresh', 'status': 'not_run'}]
        self.record(first, receipt)
        second, plan, pointer = self.attempt(['A', 'B corrected'], nested=True)
        current = self.inherited_receipt(second, plan, pointer)
        current['render'] = self.render(second, ['white', 'black', 'white'])
        mapping = reuse.page_map(plan, current['render']['evidence'])
        self.assertEqual(mapping['eligible_inherited_pages'], [1])
        self.assertEqual(mapping['must_view_pages'], [2, 3])
        current['checks']['rendered_pages'] = 'passed'; current['verdict'] = 'pass'
        current['review_coverage']['pages'] = [{'page': n, 'mode': 'inherited' if n == 1 else 'fresh', 'status': 'passed'} for n in (1, 2, 3)]
        for page in (2, 3):
            bad = copy.deepcopy(current); bad['review_coverage']['pages'][page - 1]['mode'] = 'inherited'
            with self.assertRaisesRegex(b.BatchError, 'previously unreviewed page'):
                reuse.validate(self.m, self.case, second, bad)
        self.record(second, current)
        # The validated inherited page can continue through another real review.
        third, plan, _ = self.attempt(['A', 'B corrected again'])
        render = self.render(third, ['white', 'black', 'white'])
        self.assertEqual(reuse.page_map(plan, render['evidence'])['eligible_inherited_pages'], [1, 2, 3])

    def test_prior_receipt_artifact_plan_and_cross_rules_tampering_rejected(self):
        for kind in ('receipt', 'artifact', 'plan', 'rules'):
            with self.subTest(kind=kind):
                # Isolate each tamper so an earlier failure cannot hide another.
                with tempfile.TemporaryDirectory() as directory:
                    oldroot, oldcase, oldm, counter = self.root, self.case, self.m, self.counter
                    self.root = Path(directory).resolve(); self.case_root = self.root / 'cases/001'
                    self.case_root.mkdir(parents=True)
                    self.case = {'id': '001', 'source_digest': 'source', 'receipts': [], 'attempts': []}
                    self.m = dict(oldm, batch_dir=str(self.root)); self.counter = 0
                    first, _, pointer = self.attempt(['A'])
                    self.record(first, self.receipt(first, pointer))
                    if kind == 'receipt':
                        Path(first['directory'], 'receipt.json').write_text('{}')
                    elif kind == 'artifact':
                        document(Path(first['input_artifact']), ['tampered'])
                    elif kind == 'plan':
                        Path(pointer['path']).write_text('{}')
                    else:
                        self.m['rules_sha256'] = 'other rules'
                    with self.assertRaises(b.BatchError):
                        self.attempt(['A fixed'])
                    self.root, self.case, self.m, self.counter = oldroot, oldcase, oldm, counter
                    self.case_root = self.root / 'cases/001'

    def test_policy_is_opt_in_and_old_receipts_keep_previous_contract(self):
        from test_parallel_batch import POLICY
        legacy = copy.deepcopy(POLICY); queue.validate_policy(legacy)
        self.assertIsNone(reuse.validate({'policy': legacy}, {}, {'stage': 'review'}, {}))
        bad = dict(legacy, review_reuse='unknown')
        with self.assertRaisesRegex(b.BatchError, 'incremental review contract'):
            queue.validate_policy(bad)
        valid = dict(legacy, review_reuse=reuse.MODE, worker_preparation='frozen_stage_packet_v1',
                     review_mode='review_only', execution_optimization='lean_execution_v24')
        valid['delivery'] = {'folder_prefix': '专利撰写完成', 'layout': 'flat_final_files', 'timing': 'after_each_acceptance'}
        queue.validate_policy(valid)
        valid['review_mode'] = 'review_fix_then_recheck'
        with self.assertRaisesRegex(b.BatchError, 'independent read-only review'):
            queue.validate_policy(valid)

    def test_real_reservation_payload_cli_job_and_receipt_helper_preserve_reuse_contract(self):
        import cli_pool
        import worker_receipt
        attempt, plan, pointer = self.attempt(['synthetic A', 'synthetic B'])
        root = Path(attempt['directory'])
        source = self.case_root / 'source.docx'; document(source, ['synthetic source'])
        self.case.update(title='合成增量协议', primary_source=str(source),
                         sources=[{'original_path': str(source), 'snapshot_path': str(source), 'sha256': b.sha(source)}])
        model = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'}
        self.m['policy'].update(models={'review': model}, execution_optimization='lean_execution_v24')
        attempt['task_name'] = attempt['id']
        reservation = root / 'reservation.json'; b.atomic(reservation, attempt)
        attempt['reservation_sha256'] = b.sha(reservation)
        prepared = {'review_reuse': pointer, 'pipeline': {'execution_optimization': 'lean_execution_v24',
                    'root': str(self.root), 'stage_guide': 'stage', 'math_guide': 'math',
                    'toolkit_guide': 'toolkit', 'toolkit': 'tool'}}
        payload = queue.reservation_payload(self.m, self.case, attempt, prepared)
        self.assertEqual(payload['worker_context']['review_reuse'], reuse.MODE)
        executable = str(self.root / 'codex.exe')
        job_path = cli_pool.prepare_job(payload, executable)
        job = b.read(job_path)
        self.assertEqual(job['receipt_contract']['review_reuse'], reuse.MODE)
        dispatch = {'tool': 'codex.exec', 'context': attempt['context'], 'agent_id': attempt['agent_id'],
                    'result': {'pid': 123, 'created': 987654, 'executable': executable},
                    'prompt_sha256': b.sha(root / 'prompt.txt'),
                    'arguments': {'cwd': str(root), **model, 'argv': job['argv']}}
        b.atomic(root / 'dispatch.json', dispatch)
        decisions = self.receipt(attempt, pointer)
        original_plan = Path(pointer['path']).read_bytes()
        assembled = worker_receipt.assemble(job_path, decisions)
        self.assertEqual(assembled['review_coverage'], decisions['review_coverage'])
        self.assertEqual(assembled['reviewed_sha256'], attempt['input_artifact_sha256'])
        self.assertEqual(Path(pointer['path']).read_bytes(), original_plan)
        self.assertNotIn('review_reuse_validation', assembled)
        self.assertFalse((root / 'receipt.json').exists())
        self.assertFalse((root / 'worker-final.json').exists())


if __name__ == '__main__':
    unittest.main()
