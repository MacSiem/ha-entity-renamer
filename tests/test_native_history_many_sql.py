"""Real HA Recorder ORM readers against a private in-memory SQLite fixture."""
import asyncio
import importlib
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA required')
class NativeHistoryManySQLTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_ceilings_shared_attributes_and_verify(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from homeassistant.components.recorder.db_schema import (States, StatesMeta,
            StateAttributes, Statistics, StatisticsShortTerm, StatisticsMeta)
        from homeassistant.components.recorder.util import session_scope
        history = importlib.import_module(fixtures.PACKAGE + '.history_guard')
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        for model in (StatesMeta, StateAttributes, States, StatisticsMeta, Statistics, StatisticsShortTerm):
            model.__table__.create(engine)
        Session = sessionmaker(bind=engine)
        with Session.begin() as session:
            session.add_all([StatesMeta(metadata_id=1, entity_id='sensor.a'),
                             StatesMeta(metadata_id=2, entity_id='sensor.b'),
                             StateAttributes(attributes_id=1, shared_attrs='{"shared":true}'),
                             States(state_id=1, metadata_id=1, attributes_id=1, state='one'),
                             States(state_id=2, metadata_id=2, attributes_id=1, state='two'),
                             States(state_id=3, metadata_id=1, attributes_id=1, state='three')])
        class Recorder:
            def get_session(self): return Session()
            async def async_add_executor_job(self, job, *args): return job(*args)
        class Guard:
            instance = Recorder()
            async def snapshot(self, mapping):
                ids = set(mapping) | set(mapping.values())
                return {'states': {key: {'metadata_id': value} for key, value in
                    [('sensor.a', 1), ('sensor.b', 2)] if key in ids}, 'statistics': {}}
        guard = history.NativeHistoryGuard(Guard())
        mappings = [{'sensor.a': 'sensor.a_new'}, {'sensor.b': 'sensor.b_new'}]
        individual = [await guard.capture(mapping) for mapping in mappings]
        batch = await guard.capture_many(mappings, lambda: None)
        self.assertEqual(batch, individual)
        self.assertEqual([item['content']['states']['ceiling'] for item in batch], [3, 2])
        self.assertEqual(batch[0]['content']['attributes'], batch[1]['content']['attributes'])
        self.assertEqual(await guard.verify_many(batch, lambda: None), [True, True])
        with Session.begin() as session:
            session.get(States, 2).state = 'altered'
        self.assertEqual(await guard.verify_many(batch, lambda: None), [True, False])
