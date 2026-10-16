"""Observable short-head clues; semantic scope never becomes an automatic pass."""
import hashlib
import tempfile
import unittest
from xml.sax.saxutils import escape

import test_inspect_docx as fixtures
from inspect_docx import NS, inspect, precheck


def paragraph(text):
    return '<w:p><w:r><w:t>'+escape(text)+'</w:t></w:r></w:p>'


class AntecedentWordingTests(unittest.TestCase):
    def read(self, body, kind='draft', extra=None):
        with tempfile.TemporaryDirectory() as directory:
            path = fixtures.InspectionTests().make_docx(directory, body, extra)
            original = path.read_bytes()
            result = inspect(path)
            precheck(result, kind)
            self.assertEqual(hashlib.sha256(original).hexdigest(), result['sha256'])
            self.assertEqual(path.read_bytes(), original)
            return result

    def test_short_nouns_are_located_across_word_runs(self):
        text = '存在执行量未确认的动作时，停止下发依赖所述动作的后续进气增量；保留所述记录。'
        split = text.index('述')
        body = '<w:p><w:r><w:t>'+text[:split]+'</w:t></w:r><w:r><w:t>'+text[split:]+'</w:t></w:r></w:p>'
        result = self.read(body)
        rows = result['antecedent_occurrences']
        self.assertEqual([r['two_character_prefix_candidate']['text'] for r in rows], ['动作', '记录'])
        for row in rows:
            clue = row['two_character_prefix_candidate']
            self.assertEqual(text[clue['offset']:clue['end_offset']], clue['text'])
            self.assertEqual(text[row['offset']:row['end_offset']], '所述')
        self.assertEqual(sum(i['code'] == 'ANTECEDENT_SHORT_NOUN_REVIEW' for i in result['issues']), 2)

    def test_complete_long_names_are_not_truncated_to_first_two_characters(self):
        result = self.read(paragraph('所述动作序列的执行顺序、所述动作时间、所述运行状态和所述参数集合。'))
        self.assertEqual(len(result['antecedent_occurrences']), 4)
        self.assertTrue(all('two_character_prefix_candidate' not in r for r in result['antecedent_occurrences']))
        self.assertTrue(all(r['status'].startswith('requires_') for r in result['antecedent_occurrences']))

    def test_particle_boundary_is_only_a_clue_not_a_compound_noun_decision(self):
        result = self.read(paragraph('记录动作的执行时点，核对所述动作的执行时点。'))
        row = result['antecedent_occurrences'][0]
        self.assertEqual(row['two_character_prefix_candidate']['text'], '动作')
        self.assertIn('not_a_noun_or_antecedent_verdict', row['two_character_prefix_candidate']['status'])
        self.assertNotIn('passed', row['status'])
        self.assertEqual(result['parts'][0]['paragraphs'][0]['text'], '记录动作的执行时点，核对所述动作的执行时点。')

    def test_claim_header_and_other_parent_occurrence_never_auto_establish_basis(self):
        texts = ['权利要求书', '1.检测方法，其特征在于，形成控制动作序列。',
                 '2.根据权利要求1所述的检测方法，其特征在于，形成执行记录。',
                 '3.根据权利要求1所述的检测方法，其特征在于，读取所述执行记录，停止所述动作。', '技术领域']
        result = self.read(''.join(map(paragraph, texts)))
        self.assertEqual(result['claims_detected'][2]['parents_detected'], [1])
        self.assertEqual(result['claims_detected'][2]['antecedent_count_evidence']['literal_count'], 3)
        self.assertTrue(all(r['status'].startswith('requires_') for r in result['antecedent_occurrences']))
        self.assertEqual([r['two_character_prefix_candidate']['text'] for r in result['antecedent_occurrences']
                          if r.get('two_character_prefix_candidate')], ['动作'])

    def test_discourse_and_header_locations_are_kept_but_comments_are_not_clean_prose(self):
        header = f'<w:hdr xmlns:w="{NS["w"]}">{paragraph("所述记录。")}</w:hdr>'
        comments = f'<w:comments xmlns:w="{NS["w"]}"><w:comment>{paragraph("所述参数。")}</w:comment></w:comments>'
        result = self.read(paragraph('下文所述方式。'), extra={'word/header1.xml': header, 'word/comments.xml': comments})
        rows = result['antecedent_occurrences']
        self.assertEqual({r['part'] for r in rows}, {'word/document.xml', 'word/header1.xml'})
        self.assertEqual([r['two_character_prefix_candidate']['text'] for r in rows], ['方式', '记录'])

    def test_source_documents_are_read_only_without_target_rule_warning(self):
        result = self.read(paragraph('所述动作。'), kind='proposal')
        self.assertEqual(result['antecedent_occurrences'][0]['two_character_prefix_candidate']['text'], '动作')
        self.assertFalse(any(i['code'] == 'ANTECEDENT_SHORT_NOUN_REVIEW' for i in result['issues']))

    def test_math_and_ambiguous_short_heads_remain_unmodified_and_require_review(self):
        body = paragraph('所述动作执行后，核对所述AB。') + '<w:p><m:oMath><m:r><m:t>AB=2</m:t></m:r></m:oMath></w:p>'
        result = self.read(body)
        self.assertEqual(len(result['formulas']), 1)
        self.assertEqual(len(result['antecedent_occurrences']), 2)
        self.assertTrue(all(r['status'].startswith('requires_') for r in result['antecedent_occurrences']))
        self.assertFalse(any(r.get('two_character_prefix_candidate') for r in result['antecedent_occurrences']))


if __name__ == '__main__':
    unittest.main()
