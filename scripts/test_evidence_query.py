import json
import tempfile
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

    def test_snapshot_hash_tracks_data_and_no_success_check_is_invented(self):
        before = q.query(self.path)
        self.path.write_text(self.path.read_text(encoding='utf-8')+' ', encoding='utf-8')
        self.assertNotEqual(before['inspection_sha256'], q.query(self.path)['inspection_sha256'])
        self.assertNotIn('checks', before)


if __name__ == '__main__': unittest.main()
