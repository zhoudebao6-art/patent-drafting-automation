import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import inspect_docx as inspection


class InspectionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.docx'
        self.cache = self.root/'case-cache'
        self.write_source('first')

    def write_source(self, text):
        with zipfile.ZipFile(self.source, 'w') as archive:
            archive.writestr('word/document.xml',
                f'<w:document xmlns:w="{inspection.NS["w"]}"><w:body>'
                f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
            archive.writestr('word/media/image1.png', b'fixture-media-bytes')

    def run_cache(self, **kwargs):
        return inspection.run_inspection(self.source, self.cache, reuse=True, **kwargs)

    def test_cold_hot_parse_once_and_hot_does_not_open_zip(self):
        with patch.object(inspection, 'inspect', wraps=inspection.inspect) as parse:
            cold = self.run_cache()
            with patch.object(inspection.zipfile, 'ZipFile', side_effect=AssertionError('hot opened ZIP')):
                hot = self.run_cache()
            self.assertEqual(parse.call_count, 1)
        self.assertEqual(cold['cache_status'], 'miss')
        self.assertEqual(hot['cache_status'], 'hit')
        self.assertEqual(cold['inspection'], hot['inspection'])
        self.assertEqual(hot['status'], 'structural_precheck_only')

    def test_source_change_and_options_create_separate_generations(self):
        paths = [self.run_cache()['output_directory']]
        self.write_source('different')
        paths.append(self.run_cache()['output_directory'])
        paths.append(self.run_cache(kind='proposal')['output_directory'])
        paths.append(self.run_cache(kind='proposal', extract_media=True)['output_directory'])
        self.assertEqual(len(set(paths)), 4)
        self.assertTrue(all(Path(p).is_dir() for p in paths))

    def test_same_bytes_different_source_path_does_not_misbind(self):
        first = self.run_cache()
        other = self.root/'another.docx'
        shutil.copyfile(self.source, other)
        second = inspection.run_inspection(other, self.cache, reuse=True)
        self.assertEqual(second['cache_status'], 'miss')
        self.assertNotEqual(first['output_directory'], second['output_directory'])
        self.assertEqual(json.loads(Path(second['inspection']).read_text(encoding='utf-8'))['input'], str(other.resolve()))

    def test_extractor_change_invalidates_key(self):
        first = self.run_cache()
        original = inspection.file_sha
        def changed(path):
            return 'changed-tool-hash' if Path(path).resolve() == Path(inspection.__file__).resolve() else original(path)
        with patch.object(inspection, 'file_sha', side_effect=changed):
            second = self.run_cache()
        self.assertEqual(second['cache_status'], 'miss')
        self.assertNotEqual(first['output_directory'], second['output_directory'])

    def test_tamper_and_missing_artifact_preserve_old_evidence(self):
        first = self.run_cache()
        content = Path(first['content'])
        content.write_text('user-edited-evidence', encoding='utf-8')
        second = self.run_cache()
        self.assertEqual(second['cache_status'], 'miss')
        self.assertEqual(second['cache_reason'], 'no_valid_generation')
        self.assertEqual(content.read_text(encoding='utf-8'), 'user-edited-evidence')
        Path(second['inspection']).unlink()
        third = self.run_cache()
        self.assertEqual(third['cache_status'], 'miss')
        self.assertEqual(len({first['output_directory'], second['output_directory'], third['output_directory']}), 3)

    def test_media_hash_checked_on_hot_path(self):
        first = self.run_cache(extract_media=True)
        self.assertEqual(self.run_cache(extract_media=True)['cache_status'], 'hit')
        media = next((Path(first['output_directory'])/'media').iterdir())
        media.write_bytes(b'changed-media')
        second = self.run_cache(extract_media=True)
        self.assertEqual(second['cache_status'], 'miss')
        self.assertEqual(media.read_bytes(), b'changed-media')

    def test_non_object_cache_or_report_preserved_and_regenerated(self):
        for filename in ('cache.json', 'inspection.json'):
            for invalid in ([], None):
                with self.subTest(filename=filename, invalid=invalid):
                    cache = self.root / (filename + ('-array' if invalid == [] else '-null'))
                    first = inspection.run_inspection(self.source, cache, reuse=True)
                    directory = Path(first['output_directory'])
                    target = directory/filename
                    damaged = json.dumps(invalid)
                    target.write_text(damaged, encoding='utf-8')
                    if filename == 'inspection.json':
                        metadata_path = directory/'cache.json'
                        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                        for record in metadata['outputs']:
                            if record['file'] == filename:
                                record['sha256'] = inspection.file_sha(target)
                        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
                    second = inspection.run_inspection(self.source, cache, reuse=True)
                    self.assertEqual(second['cache_status'], 'miss')
                    self.assertEqual(second['cache_reason'], 'no_valid_generation')
                    self.assertNotEqual(first['output_directory'], second['output_directory'])
                    self.assertEqual(target.read_text(encoding='utf-8'), damaged)
                    third = inspection.run_inspection(self.source, cache, reuse=True)
                    self.assertEqual(third['cache_status'], 'hit')
                    self.assertEqual(third['output_directory'], second['output_directory'])

    def test_path_escape_in_cache_inventory_is_rejected_without_touching_target(self):
        first = self.run_cache()
        metadata_path = Path(first['output_directory'])/'cache.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        outside = self.root/'unrelated.txt'
        outside.write_text('keep', encoding='utf-8')
        metadata['outputs'][0]['file'] = str(outside)
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        second = self.run_cache()
        self.assertEqual(second['cache_status'], 'miss')
        self.assertEqual(outside.read_text(encoding='utf-8'), 'keep')

    def test_source_changed_during_inspection_rejected_without_cache_commit(self):
        original = inspection.inspect
        def changing(path):
            result = original(path)
            self.write_source('changed while extracting')
            return result
        with patch.object(inspection, 'inspect', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'Source changed during inspection'):
                self.run_cache()
        self.assertEqual(list(self.cache.rglob('cache.json')), [])

    def test_legacy_output_paths_remain_compatible(self):
        out = self.root/'legacy'
        result = inspection.run_inspection(self.source, out)
        self.assertEqual(result['cache_status'], 'disabled')
        self.assertTrue((out/'inspection.json').is_file())
        self.assertTrue((out/'content.txt').is_file())


if __name__ == '__main__':
    unittest.main()
