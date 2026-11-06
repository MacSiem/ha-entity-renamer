"""Original receipt output-scope preflight; no filesystem effects or refresh."""
import importlib
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

# Match receipt regression isolation; do not import the HA integration entrypoint.
COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                 str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_staging_output_scope_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
_assert_backup_membership = importlib.import_module(PACKAGE + '.accepted_input_state')._assert_backup_membership
RegistryReceiptError = importlib.import_module(PACKAGE + '.native_registry_receipts').RegistryReceiptError


class StagingOutputScopeTests(unittest.TestCase):
    def check_scope(self, directories, files=()):
        original = SimpleNamespace(source_directories=directories, source_files=files)
        change = SimpleNamespace(files=(), identity='scope-test')
        return _assert_backup_membership(original, change)

    def test_original_selective_storage_excludes_output(self):
        self.check_scope((('.storage', (), ('core.entity_registry',)),))

    def test_original_selected_pending_creation_refuses(self):
        with self.assertRaisesRegex(RegistryReceiptError, 'yaml_backup_membership_unprepared'):
            self.check_scope((('.storage', (), ('ha_entity_renamer_pending',)),))

    def test_existing_pending_root_identity_without_children_is_stable(self):
        self.check_scope((('.storage', (('ha_entity_renamer_pending', 0o40700, 7, 77),),
                           ('ha_entity_renamer_pending',)),))

    def test_original_observed_pending_directory_or_descendant_refuses(self):
        for path in ('.storage/ha_entity_renamer_pending',
                     '.storage/ha_entity_renamer_pending/nested'):
            for names in (None, (), ('previous.pending',)):
                with self.subTest(path=path, names=names):
                    with self.assertRaisesRegex(RegistryReceiptError, 'yaml_staging_membership_unprepared'):
                        self.check_scope(((path, (), names),))

    def test_original_observed_pending_source_refuses(self):
        with self.assertRaisesRegex(RegistryReceiptError, 'yaml_staging_membership_unprepared'):
            self.check_scope((), (('.storage/ha_entity_renamer_pending/previous.pending',
                                   'a' * 64, 7, 77),))


if __name__ == '__main__':
    unittest.main()
