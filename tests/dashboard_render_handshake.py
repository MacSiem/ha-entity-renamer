"""Bounded handoff to an actual isolated browser; no authority in reports."""
import asyncio
import hashlib
import json
from pathlib import Path


def validate_render(request, result, directory):
    if (type(result) is not dict or result.get('label') != request['label']
            or result.get('source') != request['source'] or len(result.get('routes', ())) != 2):
        raise ValueError('render_result_identity_mismatch')
    for row, (path, kind) in zip(result['routes'], (('/r6-map/0', 'map'), ('/r8-map/0', 'entity')), strict=True):
        image = directory / ('render-' + request['label'] + '-' + kind + '.png')
        if (row.get('path') != path or row.get('type') != kind or row.get('present') is not True
                or row.get('visible') is not True or row.get('references') != [request['source']]
                or row.get('state') != request['state'] or row.get('latitude') != 52
                or row.get('longitude') != 21 or (kind == 'map' and row.get('markers', 0) < 1)
                or image.is_symlink() or not image.is_file()
                or hashlib.sha256(image.read_bytes()).hexdigest() != row.get('screenshot_sha256')):
            raise ValueError('render_observation_unqualified:' + kind)
    return result


async def observe_render(directory, label, source, state, port):
    from mixed_fixture_census import _network_evidence
    root = Path(directory)
    request = {'label': label, 'source': source, 'state': state, 'port': port}
    pending = root / 'render-request.tmp'
    pending.write_text(json.dumps(request, sort_keys=True))
    pending.replace(root / 'render-request.json')
    ack = root / ('render-' + label + '-done.json')
    failure = None
    for _ in range(2400):
        if ack.is_file():
            result = json.loads(ack.read_text())
            try:
                validate_render(request, result, root / 'readback')
            except ValueError as error:
                failure = error
            break
        await asyncio.sleep(.1)
    else:
        raise TimeoutError('render_observation_timeout')
    # Browser has exited. Wait until its loopback TCP connections have aged
    # out; the following independent census must see no active connections.
    stable = 0
    for _ in range(1200):
        network = _network_evidence()
        quiet = (network['interfaces_up'] == ['lo'] and not network['loopback_connections']
                 and not network['nonloopback_sockets'] and not network['unix_sockets']
                 and not network['foreign_processes'] and not network['unreadable'])
        stable = stable + 1 if quiet else 0
        if stable >= 30:
            if failure is not None:
                raise failure
            return result
        await asyncio.sleep(.1)
    raise TimeoutError('render_network_not_quiescent')
