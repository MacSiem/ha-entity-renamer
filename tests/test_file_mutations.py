from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from custom_components.ha_entity_renamer.file_mutations import FileConflict, apply_file_patch
from custom_components.ha_entity_renamer import file_mutations
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

    def test_source_chmod_during_staging_refuses_without_reverting_foreign_mode(self):
        original_write = file_mutations._write_all

        def raced_write(fd, data):
            original_write(fd, data)
            if data == self.patch.proposed_bytes:
                self.file.chmod(0o600)  # Same inode and content, new mode.

        with mock.patch.object(file_mutations, '_write_all', raced_write):
            with self.assertRaisesRegex(FileConflict, 'source_changed_before_replace'):
                self.apply()
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_staged_chmod_after_stat_refuses_and_preserves_source(self):
        original_read = file_mutations._Reader.read
        read_count = 0

        def raced_read(reader, path):
            nonlocal read_count
            result = original_read(reader, path)
            read_count += 1
            if read_count == 2:
                # Second source read is after fstat(temp), before stat(temp).
                pending = list(self.root.glob('.renamer-op1-*.pending'))
                self.assertEqual(len(pending), 1)
                pending[0].chmod(0o600)
            return result

        with mock.patch.object(file_mutations._Reader, 'read', raced_read):
            with self.assertRaisesRegex(FileConflict, 'staged_metadata_changed'):
                self.apply()
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)

    def test_last_guard_cannot_hide_foreign_source_or_staged_chmod(self):
        for target, reason in (('source', 'source_changed_before_replace'),
                               ('staged', 'staged_metadata_changed')):
            with self.subTest(target=target):
                self.file.chmod(0o640)
                calls = 0
                def guard():
                    nonlocal calls
                    calls += 1
                    if calls == 4:
                        if target == 'source':
                            self.file.chmod(0o600)
                        else:
                            pending = list(self.root.glob('.renamer-op1-*.pending'))
                            pending[-1].chmod(0o600)
                with self.assertRaisesRegex(FileConflict, reason):
                    apply_file_patch(self.root, self.patch, self.snapshot, 'op1', guard=guard)
                self.assertEqual(calls, 4)
                self.assertEqual(self.file.read_bytes(), self.original)
                self.assertEqual(self.file.stat().st_mode & 0o777,
                                 0o600 if target == 'source' else 0o640)
                # Preserve failed staging artifacts outside the next attempt's glob.
                for pending in self.root.glob('.renamer-op1-*.pending'):
                    pending.rename(pending.with_suffix('.quarantined'))


if __name__ == '__main__': unittest.main()
