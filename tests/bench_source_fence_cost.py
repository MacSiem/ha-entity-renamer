"""Bounded synthetic comparison, no HA, disk graph, or Apply. Run via FIFO."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location('_bench_mixed',
    Path(__file__).with_name('test_native_mixed_input_receipts.py'))
mixed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixed)
r = mixed.receipts

async def main():
    f = await mixed.mixed_fixture()
    payload = deepcopy(f['preparations'].plans['yaml-change']['payload'])
    receipt = payload['data']['receipt']
    for i in range(2395):
        name = f'synthetic_{i:04}.py'
        receipt['source_files'].append([name, 'a' * 64, 7, 10000 + i])
        receipt['source_directories'][0][1].append([name, 33188, 7, 10000 + i])
    digest = r._digest(payload)
    change = r.decode_change(deepcopy(payload))
    yaml_digest = r._digest(json.loads(r._json(asdict(change.checked()))))
    evidence = r._freeze_json(payload)
    def old():
        incoming = deepcopy(payload)
        decoded = r.decode_change(incoming)
        assert r._digest(incoming) == digest
        assert r._digest(json.loads(r._json(asdict(decoded.checked())))) == yaml_digest
        assert decoded == change
        return decoded
    def new():
        incoming = deepcopy(payload)
        assert r._exact_json_equal(incoming, evidence)
        return deepcopy(change)
    assert old() == new()
    for name, fn in (('old', old), ('new', new)):
        start = time.perf_counter()
        for _ in range(5):
            fn()
        print(f'{name}: {(time.perf_counter() - start) / 5:.6f} s/call, 2395 synthetic extra files')

asyncio.run(main())
