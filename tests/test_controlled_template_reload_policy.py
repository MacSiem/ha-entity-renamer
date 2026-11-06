"""Unknown Template update listeners never grant a write policy."""
from types import SimpleNamespace
import unittest
from custom_components.ha_entity_renamer.helper_native import _controlled_template_explicit_reload


class ReloadPolicyTests(unittest.TestCase):
    def test_native_listener_or_none_and_foreign_refusals(self):
        def native(*args): pass
        def foreign(*args): pass
        self.assertTrue(_controlled_template_explicit_reload(SimpleNamespace(update_listeners=[]), native))
        self.assertTrue(_controlled_template_explicit_reload(SimpleNamespace(update_listeners=[]), None))
        self.assertFalse(_controlled_template_explicit_reload(SimpleNamespace(update_listeners=[native]), native))
        for listeners, known in (([foreign], native), ([native, foreign], native), ([foreign], None)):
            with self.subTest(listeners=len(listeners)), self.assertRaisesRegex(ValueError, 'reload_contract_unavailable'):
                _controlled_template_explicit_reload(SimpleNamespace(update_listeners=listeners), known)
