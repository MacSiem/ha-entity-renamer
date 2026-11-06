"""Cold generated-cost metadata admits only the qualified native timestamp."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.controlled_powercalc_cold_registry import (
    apply_projection, qualify_projection)
from custom_components.ha_entity_renamer.transaction import Refused


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class ColdRegistryTests(unittest.TestCase):
    def setUp(self):
        self.cost = {'registry_id': 'cost-id', 'entity_id': 'sensor.cost',
                     'unique_id': 'source-id_grid_cost', 'source_registry_id': 'source-id'}
        self.document = {'data': {'entities': [
            {'id': 'cost-id', 'entity_id': 'sensor.cost', 'platform': 'energy',
             'unique_id': 'source-id_grid_cost', 'modified_at': '2026-11-06T08:00:00+00:00',
             'original_name': 'Cost'},
            {'id': 'foreign', 'entity_id': 'sensor.foreign', 'name': 'kept'}]}}
        self.states = {'core.entity_registry': {'document': self.document,
            'sha256': hashlib.sha256(raw(self.document)).hexdigest(), 'identity': [1, 2, 33152]},
            'core.device_registry': {'document': {'data': {'devices': []}}, 'identity': [1, 4, 33152]},
            'core.config_entries': {'document': {'data': {'entries': []}}, 'identity': [1, 5, 33152]}}
        for key in ('core.device_registry', 'core.config_entries'):
            self.states[key]['sha256'] = hashlib.sha256(raw(self.states[key]['document'])).hexdigest()
        self.history = [('head', {'kind': 'powercalc_energy_receipt',
            'binding': {'direction': 'forward'}, 'generated_cost_after': [self.cost]})]
        self.chain = SimpleNamespace(version='2026.10.4', original_receipt=SimpleNamespace(config_root='/config'),
            plan=SimpleNamespace(steps=[object()]), _setup_history=self.history,
            _input_sources=lambda states: {'.storage/core.entity_registry':
                [states['core.entity_registry']['sha256'], 1, 2, 33152, 501, 20]})
        self.current = deepcopy(self.document)
        self.current['data']['entities'][0]['modified_at'] = '2026-11-06T09:00:00+00:00'

    def observed(self, doc=None, *, entity_identity=(1, 3, 33152), uid=501):
        files = []
        for key, state in self.states.items():
            document = (self.current if doc is None else doc) if key == 'core.entity_registry' else state['document']
            identity = entity_identity if key == 'core.entity_registry' else tuple(state['identity'])
            files.append(SimpleNamespace(key=key, raw=raw(document), identity=identity, uid=uid, gid=20))
        return SimpleNamespace(version='2026.10.4', config_root='/config', files=files)

    def test_qualified_timestamp_and_strict_foreign(self):
        projection = qualify_projection(self.chain, self.states, 'head', {0: 'forward'}, None, self.observed())
        applied = apply_projection(self.states, 'head', self.history, projection)
        self.assertEqual(applied['core.entity_registry']['document'], self.current)
        self.assertEqual(applied['core.entity_registry']['identity'], [1, 3, 33152])
        for field, value in (('name', 'foreign edit'), ('entity_id', 'sensor.foreign_cost'),
                             ('unique_id', 'foreign_grid_cost'), ('modified_at', '2026-11-06T07:00:00+00:00')):
            foreign = deepcopy(self.current)
            foreign['data']['entities'][0][field] = value
            with self.assertRaises(Refused):
                qualify_projection(self.chain, self.states, 'head', {0: 'forward'}, None, self.observed(foreign))
        foreign = deepcopy(self.current)
        foreign['data']['entities'][1]['name'] = 'foreign edit'
        with self.assertRaises(Refused):
            qualify_projection(self.chain, self.states, 'head', {0: 'forward'}, None, self.observed(foreign))
        with self.assertRaises(Refused):
            qualify_projection(self.chain, self.states, 'head', {0: 'forward'}, None, self.observed(uid=999))
        with self.assertRaises(Refused):
            qualify_projection(self.chain, self.states, 'head', {0: 'forward'}, ('pending',), self.observed())
        with self.assertRaises(Refused):
            apply_projection(self.states, 'other-head', self.history, projection)
