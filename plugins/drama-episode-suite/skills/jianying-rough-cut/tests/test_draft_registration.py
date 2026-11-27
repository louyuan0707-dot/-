import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))
from register_draft import register


class DraftRegistrationTests(unittest.TestCase):
    def test_registers_once_and_keeps_existing_index_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'new-draft'
            folder.mkdir()
            media = folder / 'shot.mp4'
            media.write_bytes(b'video')
            (folder / 'draft_content.json').write_text(
                json.dumps({'materials': {'videos': [{'path': str(media)}]}}), encoding='utf-8')
            (folder / 'draft_meta_info.json').write_text(
                json.dumps({'draft_name': 'new-draft', 'draft_id': 'NEW-ID', 'tm_duration': 10_000_000}),
                encoding='utf-8')
            index = {
                'root_path': str(root.resolve()), 'draft_ids': 1,
                'all_draft_store': [{'draft_name': 'older-draft', 'draft_id': 'OLD-ID'}],
            }
            index_path = root / 'root_meta_info.json'
            index_path.write_text(json.dumps(index), encoding='utf-8')
            self.assertEqual(register(root, 'new-draft', check_process=False), 'registered')
            self.assertEqual(register(root, 'new-draft', check_process=False), 'already-registered')
            saved = json.loads(index_path.read_text(encoding='utf-8'))
            self.assertEqual(saved['draft_ids'], 2)
            self.assertEqual([x['draft_id'] for x in saved['all_draft_store']], ['NEW-ID', 'OLD-ID'])

    def test_rejects_missing_media_without_mutating_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'new-draft'
            folder.mkdir()
            (folder / 'draft_content.json').write_text(
                json.dumps({'materials': {'videos': [{'path': str(folder / 'missing.mp4')}]}}),
                encoding='utf-8')
            (folder / 'draft_meta_info.json').write_text(
                json.dumps({'draft_name': 'new-draft', 'draft_id': 'NEW-ID', 'tm_duration': 1}),
                encoding='utf-8')
            index_path = root / 'root_meta_info.json'
            original = json.dumps({'root_path': str(root.resolve()), 'draft_ids': 1,
                                   'all_draft_store': [{'draft_name': 'older-draft', 'draft_id': 'OLD-ID'}]})
            index_path.write_text(original, encoding='utf-8')
            with self.assertRaises(FileNotFoundError):
                register(root, 'new-draft', check_process=False)
            self.assertEqual(index_path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
