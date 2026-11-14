"""Check the real Recorder ORM reader against isolated, in-memory fixture tables.

This does not contact HA's running instance or its configured database, and is
not a substitute for the registry-event/continuity test on an installed candidate.
Run through the owner receipt on the two explicitly owned QA containers.
"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'custom_components/ha_entity_renamer/recorder_guard.py').read_text()
probe = '''
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from homeassistant.components.recorder.db_schema import StatesMeta, StatisticsMeta
from homeassistant.const import __version__
engine = create_engine('sqlite:///:memory:')
StatesMeta.__table__.create(engine)
StatisticsMeta.__table__.create(engine)
Session = sessionmaker(bind=engine)
class FixtureRecorder:
    def get_session(self): return Session()
fixture = FixtureRecorder()
with Session() as session:
    session.add(StatesMeta(metadata_id=41, entity_id='sensor.fixture_old'))
    session.add(StatisticsMeta(id=17, statistic_id='sensor.fixture_old', source='recorder', unit_of_measurement='kWh', has_mean=False, has_sum=True))
    session.commit()
mapping = {'sensor.fixture_old': 'sensor.fixture_new'}
ids = tuple(mapping) + tuple(mapping.values())
snapshot = _read(fixture, ids)
assert snapshot['states']['sensor.fixture_old']['metadata_id'] == 41
assert snapshot['statistics']['sensor.fixture_old']['id'] == 17
assert snapshot['statistics']['sensor.fixture_old']['unit_of_measurement'] == 'kWh'
assert prepare(mapping, snapshot) == snapshot
with Session() as session:
    session.add(StatesMeta(metadata_id=42, entity_id='sensor.fixture_new'))
    session.commit()
try:
    prepare(mapping, _read(fixture, ids))
except RecorderConflict:
    pass
else:
    raise AssertionError('historical collision was not blocked')
engine.dispose()
print({'ha': __version__, 'native_reader': 'PASS', 'orphan_collision': 'PASS', 'live_database_access': False})
'''

for container in ('ha-renamer-qa222', 'ha-renamer-min222'):
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    assert inspected['State']['Running'], 'Own QA container must already be running'
    mounts = [item.get('Source', '') for item in inspected['Mounts'] if item['Destination'] == '/config']
    assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0], 'Unexpected QA mount'
    completed = subprocess.run(['docker', 'exec', '-i', container, 'python', '-B', '-'],
                               input=source + '\n' + probe, text=True, check=True,
                               capture_output=True, timeout=30)
    print(container, completed.stdout.strip())
