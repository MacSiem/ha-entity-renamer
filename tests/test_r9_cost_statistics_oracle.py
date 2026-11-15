"""Read actual Recorder states and independently account native total resets."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json, sqlite3

def calculate(database, entity_id, baseline, target):
    start = float(baseline['start_ts']) + 300.0
    end = float(target['start_ts']) + 300.0
    assert end > start and baseline['last_reset_ts'] != 'None'
    old_reset = datetime.fromtimestamp(float(baseline['last_reset_ts'])).timestamp()
    old = current = Decimal(baseline['state'])
    total = Decimal(baseline['sum'])
    resets = valid = 0
    db = sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)
    try:
        rows = db.execute('''SELECT s.state, s.last_updated_ts, a.shared_attrs
            FROM states s JOIN states_meta m ON m.metadata_id=s.metadata_id
            LEFT JOIN state_attributes a ON a.attributes_id=s.attributes_id
            WHERE m.entity_id=? AND s.last_updated_ts>=? AND s.last_updated_ts<?
            ORDER BY s.last_updated_ts, s.state_id''', (entity_id, start, end)).fetchall()
    finally:
        db.close()
    for state, _, encoded in rows:
        try:
            value = Decimal(state)
            if not value.is_finite():
                continue
        except (InvalidOperation, TypeError):
            continue
        attrs = json.loads(encoded or '{}')
        assert attrs.get('state_class') == 'total'
        reset = attrs.get('last_reset')
        stamp = None if reset is None else datetime.fromisoformat(reset).timestamp()
        if stamp is not None and stamp != old_reset:
            total += current - old
            old, current, old_reset = Decimal(0), value, stamp
            resets += 1
        else:
            current = value
        valid += 1
    total += current - old
    return {'calculated_sum': str(total), 'recorder_sum': target['sum'],
            'recorder_state': target['state'], 'actual_last_state': str(current),
            'valid_states': valid, 'native_resets': resets,
            'start_ts': start, 'end_ts': end}

if __name__ == '__main__':
    from pathlib import Path
    import sys
    root = Path(sys.argv[1]); d = json.loads((root/'cold-mixed-migration-forward.json').read_text())
    old = d['before']['statistics']['sensor.public_sum_energy_cost']['short'][-1]
    end = d['recorded']['statistics']['sensor.public_sum_energy_cost']['short'][-1]
    proof = calculate(root/'cold-mixed-migration-forward-recorded.sqlite', 'sensor.public_sum_energy_cost', old, end)
    assert proof['valid_states'] and proof['native_resets'] and abs(Decimal(proof['calculated_sum']) - Decimal(proof['recorder_sum'])) < Decimal('0.000000001')
    print(json.dumps(proof, sort_keys=True))
