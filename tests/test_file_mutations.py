from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from custom_components.ha_entity_renamer.file_mutations import FileConflict, apply_file_patch
from custom_components.ha_entity_renamer.yaml_plan import plan_yaml


class FileMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='renamer-file-test-', dir='/Volumes/Samsung_SSD' if Path('/Volumes/Samsung_SSD').is_dir() else None)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'.storage').mkdir()
        self.file = self.root/'configuration.yaml'
        self.original = b'# retain\nentity_id: sensor.old\n'
        self.file.write_bytes(self.original)
        self.file.chmod(0o640)
        self.plan = plan_yaml(self.root, {'sensor.old': 'sensor.new'})
        self.patch, self.snapshot = self.plan.patches[0], self.plan.files[0]

    def apply(self, patch=None):
        return apply_file_patch(self.root, patch or self.patch, self.snapshot, 'op1')

    def test_verified_write_preserves_bytes_mode_and_recovery_copy(self):
        receipt = self.apply()
        self.assertEqual(self.file.read_bytes(), self.original.replace(b'sensor.old', b'sensor.new'))
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root/receipt['backup']).read_bytes(), self.original)
        with self.assertRaises(FileConflict): self.apply()

    def test_foreign_edit_is_preserved(self):
        self.file.write_bytes(b'# somebody else\nentity_id: sensor.foreign\n')
        with self.assertRaisesRegex(FileConflict, 'source_changed'): self.apply()
        self.assertIn(b'foreign', self.file.read_bytes())

    def test_same_content_replaced_identity_is_not_accepted(self):
        other = self.root/'other.yaml'; other.write_bytes(self.original); other.replace(self.file)
        with self.assertRaisesRegex(FileConflict, 'source_changed'): self.apply()

    def test_proposed_content_must_follow_original_edit_spans(self):
        with self.assertRaisesRegex(FileConflict, 'invalid_patch_content'):
            self.apply(replace(self.patch, proposed_bytes=b'unsafe: change\n'))
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_backup_symlink_cannot_escape_config_root(self):
        (self.root/'.storage/ha_entity_renamer_backups').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(FileConflict): self.apply()
        self.assertEqual(self.file.read_bytes(), self.original)


if __name__ == '__main__': unittest.main()
