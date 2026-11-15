"""Source-contract boundaries for explicit custom LUT inputs; no runtime acceptance."""
import importlib
import os
from pathlib import Path
import unittest
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'installed YAML dependency required; absence is not PASS')
class CustomProfileInputContractTests(unittest.TestCase):
    def setUp(self):
        self.inputs = importlib.import_module(fixture.PACKAGE + '.powercalc_profile_inputs')

    def test_legacy_input_stays_exact_and_custom_locator_is_explicit(self):
        cfg = {'entity_id':'light.source', 'manufacturer':'signify', 'model':'LWA001',
               'create_energy_sensor':True}
        self.assertEqual(self.inputs.source_config(cfg),
                         {'entity_id':'light.source', 'manufacturer':'signify', 'model':'LWA001'})
        locator = 'powercalc/profiles/owner/model'
        self.assertEqual(self.inputs.source_config({**cfg, 'custom_model_directory':locator}),
                         {'entity_id':'light.source', 'manufacturer':'signify', 'model':'LWA001',
                          'custom_model_directory':locator})

    def test_custom_path_does_not_normalize_unsafe_input_into_acceptance(self):
        paths = ['/powercalc/profiles/model', 'powercalc-custom-models/model',
                 'powercalc/profiles', 'powercalc/profiles/../model',
                 'powercalc/profiles/./model', 'powercalc/profiles//model',
                 'powercalc/profiles/model/', 'powercalc/profiles/model:alias',
                 'powercalc/profiles/model\\alias', 'powercalc/profiles/model\x00alias', 1, None]
        for value in paths:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, 'powercalc_profile_custom_directory_'):
                    self.inputs.custom_profile_locator(value)

    def test_existing_reader_refuses_custom_symlink_and_fifo_without_opening_them(self):
        for kind in ('symlink', 'fifo'):
            with self.subTest(kind=kind), fixture.scratch() as scratch:
                root = Path(scratch)
                model = root/'powercalc/profiles/owner/model'
                model.mkdir(parents=True)
                regular = model/'model.json'
                regular.write_text('{}')
                if kind == 'symlink':
                    (model/'alias').symlink_to(regular)
                else:
                    os.mkfifo(model/'pipe')
                with self.assertRaisesRegex(ValueError, 'powercalc_profile_profile_file_type'):
                    self.inputs.read_bundle(str(root))

    def test_native_cache_key_cannot_mix_distinct_custom_directories(self):
        def row(directory):
            return {'manufacturer':'signify', 'model':'LWA001', 'directory':directory,
                    'variants':[{'name':None, 'modes':{'brightness':{}}}]}
        first = row('powercalc/profiles/owner/a')
        self.inputs.assert_profile_cache_directories([first, dict(first)])
        with self.assertRaisesRegex(ValueError, 'powercalc_profile_cache_key_directory_conflict'):
            self.inputs.assert_profile_cache_directories([first, row('powercalc/profiles/owner/b')])
