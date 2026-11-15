import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from dashboard_render_handshake import validate_render


class RenderHandshakeTest(unittest.TestCase):
    def test_identity_routes_state_marker_and_screenshot_are_all_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = {'label': 'after', 'source': 'sensor.new', 'state': '5', 'port': 8123}
            routes = []
            for path, kind in (('/r6-map/0', 'map'), ('/r8-map/0', 'entity')):
                raw = kind.encode()
                (root / ('render-after-' + kind + '.png')).write_bytes(raw)
                routes.append({'path': path, 'type': kind, 'present': True, 'visible': True,
                    'references': ['sensor.new'], 'state': '5', 'latitude': 52, 'longitude': 21,
                    'markers': 1, 'screenshot_sha256': hashlib.sha256(raw).hexdigest()})
            result = {'label': 'after', 'source': 'sensor.new', 'routes': routes}
            self.assertEqual(validate_render(request, result, root), result)
            for change in ('label', 'reference', 'state', 'marker', 'image'):
                bad = copy.deepcopy(result)
                if change == 'label': bad['label'] = 'before'
                if change == 'reference': bad['routes'][1]['references'] = ['sensor.old']
                if change == 'state': bad['routes'][0]['state'] = '13'
                if change == 'marker': bad['routes'][0]['markers'] = 0
                if change == 'image': bad['routes'][1]['screenshot_sha256'] = '0' * 64
                with self.subTest(change=change), self.assertRaises(ValueError):
                    validate_render(request, bad, root)
