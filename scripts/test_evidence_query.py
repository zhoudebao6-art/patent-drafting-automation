import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
import evidence_query as q


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'inspection.json'
        self.data = {'input': 'source.docx', 'sha256': 'a'*64,
                     'formulas': [{'id': 'F1', 'omml_xml': '<m:sSub>完整下标和负号−</m:sSub>'},
                                  {'id': 'F2', 'omml_xml': '<m:f>长表达式</m:f>'}],
                     'parts': [{'part': 'word/document.xml', 'paragraphs': [{'p': i, 'text': str(i)} for i in range(1, 7)]}],
                     'issues': [{'code': 'example'}]}
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def test_selected_formula_is_complete_and_does_not_touch_file(self):
        before = self.path.read_bytes()
        result = q.query(self.path, 'formulas', ['F1'])
        self.assertEqual(result['data'], self.data['formulas'][:1])
        self.assertEqual(result['status'], 'navigation_only_not_semantic_approval')
        self.assertEqual(before, self.path.read_bytes())

    def test_ranges_paginate_without_overlap_or_missing_rows(self):
        first = q.query(self.path, 'paragraphs', start=2, end=5, limit=2)
        second = q.query(self.path, 'paragraphs', start=2, end=5, offset=first['next_offset'], limit=2)
        self.assertEqual([r['p'] for r in first['data']+second['data']], [2,3,4,5])
        self.assertIsNone(second['next_offset'])

    def test_unknown_ids_or_parts_fail_instead_of_claiming_no_math(self):
        for opts in [dict(section='formulas', ids=['bad']), dict(section='formulas', ids=['F1','F1']),
                     dict(section='paragraphs', part='wrong', start=1, end=2), dict(section='paragraphs')]:
            with self.assertRaises(ValueError): q.query(self.path, **opts)

    def test_cli_multi_field_is_single_snapshot_with_independent_complete_pages(self):
        def run(*names, offset=0):
            cmd = [sys.executable, '-X', 'utf8', q.__file__, str(self.path), '--section', 'field',
                   '--offset', str(offset), '--limit', '1']
            for name in names:
                cmd.extend(['--field', name])
            return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')

        self.data['nested'] = {'empty': []}
        self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        for offset in (0, 1):
            names = ['formulas', 'issues', 'nested.empty']
            batch = run(*names, offset=offset)
            self.assertEqual(batch.returncode, 0, batch.stderr)
            payload = json.loads(batch.stdout)
            for name in names:
                single = q.query(self.path, 'field', field=name, offset=offset, limit=1)
                self.assertEqual(payload['inspection_sha256'], single['inspection_sha256'])
                self.assertEqual(payload['fields'][name], {k:single[k] for k in ('offset','total','data','next_offset')})
        self.assertEqual(json.loads(run('formulas').stdout), q.query(self.path, 'field', field='formulas', limit=1))
        for names in [('formulas','unknown'), ('issues','issues')]:
            invalid = run(*names)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(invalid.stdout, '')

    def test_snapshot_hash_tracks_data_and_no_success_check_is_invented(self):
        before = q.query(self.path)
        self.path.write_text(self.path.read_text(encoding='utf-8')+' ', encoding='utf-8')
        self.assertNotEqual(before['inspection_sha256'], q.query(self.path)['inspection_sha256'])
        self.assertNotIn('checks', before)

    def test_nested_field_catalog_and_pages_preserve_complete_evidence(self):
        rows=[{'code':'symbol','scope':'P'+str(i),'full_math':'<m:sSub>−'+str(i)+'</m:sSub>'} for i in range(5)]
        self.data['formula_prose_evidence']={'review_candidates':rows,'empty':[],'scalar':'not an array'}
        self.path.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        before=self.path.read_bytes();summary=q.query(self.path)
        self.assertEqual(summary['available_fields']['formula_prose_evidence.review_candidates'],5)
        self.assertNotIn('full_math',json.dumps(summary))
        actual=[];offset=0
        while True:
            page=q.query(self.path,'field',field='formula_prose_evidence.review_candidates',offset=offset,limit=2)
            actual.extend(page['data']);offset=page['next_offset']
            if offset is None:break
        self.assertEqual(actual,rows)
        self.assertEqual(q.query(self.path,'field',field='formula_prose_evidence.empty')['data'],[])
        self.assertEqual(before,self.path.read_bytes())
        self.assertNotIn('checks',page)
        for field in (None,'','missing','formula_prose_evidence.scalar','formula_prose_evidence..empty'):
            with self.subTest(field=field),self.assertRaises(ValueError):q.query(self.path,'field',field=field)
        with self.assertRaises(ValueError):q.query(self.path,'summary',field='issues')


if __name__ == '__main__': unittest.main()
