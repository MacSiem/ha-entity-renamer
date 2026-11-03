"""Pure projection checks; native public admission remains the authority."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
from custom_components.ha_entity_renamer.recovery_controller import _yaml_frozen_review
from custom_components.ha_entity_renamer.transaction import Refused


class FrozenReviewTests(unittest.TestCase):
    def session(self, phases=('verified', 'verified')):
        before = {'id': 'uuid', 'platform': 'mqtt', 'unique_id': 'unique', 'device_id': 'dev',
                  'entity_id': 'sensor.old', 'name': None}
        after = {**before, 'entity_id': 'sensor.new'}
        change = PreparedRegistryChange('entity', 'uuid', json.dumps(before), json.dumps(after))
        return SimpleNamespace(record=SimpleNamespace(phases=phases), registry_changes=(change,),
            receipt=SimpleNamespace(changed_domains=('template',), proposed_files=('template.yaml',)),
            composition=object(), evidence=SimpleNamespace(template_sources=SimpleNamespace(
                ledger=SimpleNamespace(definitions=(SimpleNamespace(domain='template.sensor'),)))))

    def test_complete_review_projects_only_original_change_and_dependency_evidence(self):
        files = [{'path': 'template.yaml', 'edit_count': 1,
                  'references': [{'before': 'sensor.old', 'after': 'sensor.new', 'occurrences': 1}]}]
        with patch('custom_components.ha_entity_renamer.dependency_review.dependency_files', return_value=files):
            review = _yaml_frozen_review(self.session())
        self.assertEqual(review['changes'][0]['fields']['entity_id'], {'before': 'sensor.old', 'after': 'sensor.new'})
        self.assertEqual(review['identities'], [['uuid', 'mqtt', 'unique', 'dev']])
        self.assertEqual(review['dependencies']['files'], files)
        self.assertNotIn('eligible', review)

    def test_incomplete_or_unknown_phase_never_projects_full_review(self):
        for phase in ('pending', 'intent', 'undone'):
            with self.subTest(phase=phase), self.assertRaises(Refused):
                _yaml_frozen_review(self.session(('verified', phase)))


class RestoreIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_marker_precedes_effect_and_blocks_second_attempt(self):
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.ha_entity_renamer.recovery_controller import RecoveryController
        from custom_components.ha_entity_renamer.transaction import Plan, Record, Step
        plan = Plan('p', ('registry', 'yaml'), ('registry', 'yaml'),
            (Step('r', 'registry', 'i', 'a', 'b', 'r'), Step('y', 'yaml', 'i', 'a', 'b', 'y')),
            'proof', ('registry', 'yaml'))
        record = Record('op', plan, status='applied', phases=('verified', 'verified'), restore_attempted=False)
        class Journal:
            current = record
            async def load(self, _): return self.current
            async def save(self, value): self.current = value
        journal = Journal()
        session = SimpleNamespace(record=record, journal=journal, close=MagicMock())
        async def effect():
            self.assertIs(journal.current.restore_attempted, True)
            return SimpleNamespace(status='rolled_back')
        session.rollback = AsyncMock(side_effect=effect)
        factory = SimpleNamespace(bind_reverse=AsyncMock(return_value=session))
        controller = RecoveryController(None, None, guard=lambda: None, refresh=AsyncMock())
        controller.check = lambda: None
        controller._read = AsyncMock(return_value=(journal, object()))
        with patch('custom_components.ha_entity_renamer._yaml_factory_for_plan', return_value=factory), \
             patch('custom_components.ha_entity_renamer.recovery_controller._yaml_summary', return_value={}):
            first = await controller.restore('op', plan.fingerprint)
            second = await controller.restore('op', plan.fingerprint)
        self.assertEqual(first['state'], 'rolled_back')
        self.assertEqual((second['state'], second['reason']), ('UNKNOWN', 'restore_attempt_unresolved'))
        self.assertEqual(session.rollback.await_count, 1)

    async def test_lost_intent_save_ack_never_enters_effect_and_refuses_replay(self):
        from unittest.mock import AsyncMock, MagicMock
        from custom_components.ha_entity_renamer.recovery_controller import RecoveryController
        from custom_components.ha_entity_renamer.transaction import Plan, Record, Step
        plan = Plan('p', ('registry', 'yaml'), ('registry', 'yaml'),
            (Step('r', 'registry', 'i', 'a', 'b', 'r'), Step('y', 'yaml', 'i', 'a', 'b', 'y')),
            'proof', ('registry', 'yaml'))
        record = Record('op', plan, status='applied', phases=('verified', 'verified'), restore_attempted=False)
        class Journal:
            current = record
            async def load(self, _): return self.current
            async def save(self, value):
                self.current = value
                raise OSError('lost ACK after durable intent')
        journal = Journal()
        session = SimpleNamespace(record=record, journal=journal, rollback=AsyncMock(), close=MagicMock())
        factory = SimpleNamespace(bind_reverse=AsyncMock(return_value=session))
        controller = RecoveryController(None, None, guard=lambda: None, refresh=AsyncMock())
        controller.check = lambda: None
        controller._read = AsyncMock(return_value=(journal, object()))
        with patch('custom_components.ha_entity_renamer._yaml_factory_for_plan', return_value=factory), \
             patch('custom_components.ha_entity_renamer.recovery_controller._yaml_summary', return_value={}):
            first = await controller.restore('op', plan.fingerprint)
            second = await controller.restore('op', plan.fingerprint)
        self.assertEqual(first['state'], 'UNKNOWN')
        self.assertEqual((second['state'], second['reason']), ('UNKNOWN', 'restore_attempt_unresolved'))
        session.rollback.assert_not_awaited()
