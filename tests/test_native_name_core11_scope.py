"""Do not write a name if Core's related-entity side effect is unreceipted."""
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.native_name_observers import _core11_name_rewrite_unchanged


class Core11NameScopeTests(unittest.TestCase):
    def test_name_compat_and_device_related_entity_side_effects_refuse(self):
        entity = SimpleNamespace(compat_name=None, has_entity_name=False, name=None,
                                 original_name='Light', original_name_unprefixed=None)
        change = SimpleNamespace(kind='device', before={'name': 'Old'}, after={'name_by_user': 'New'})
        unchanged = lambda name, device: None
        self.assertTrue(_core11_name_rewrite_unchanged(entity, change, unchanged))
        entity.compat_name = 'Legacy'
        self.assertFalse(_core11_name_rewrite_unchanged(entity, change, unchanged))
        entity.compat_name = None
        entity.original_name_unprefixed = 'Light'
        self.assertFalse(_core11_name_rewrite_unchanged(entity, change, lambda name, device: 'Light'))
        entity.name = 'User chosen'
        self.assertTrue(_core11_name_rewrite_unchanged(entity, change, lambda name, device: 'Light'))
        self.assertFalse(_core11_name_rewrite_unchanged(entity, change,
                         lambda name, device: 'Light' if device == 'Old' else None))
