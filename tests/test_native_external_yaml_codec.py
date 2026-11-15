"""External evidence codecs are exact and do not grant activation."""
from dataclasses import asdict, replace
from hashlib import sha256
import copy
import unittest
from custom_components.ha_entity_renamer.native_external_yaml import PreparedExternalStateYAML
from custom_components.ha_entity_renamer.prepared import encode_change, decode_change
from custom_components.ha_entity_renamer.yaml_transaction import ValidationReceipt, YAMLFile, decode_yaml

class ExternalYAMLCodecTests(unittest.TestCase):
    def make(self):
        before, after=b'template: before\n', b'template: after\n'
        digest=lambda raw:sha256(raw).hexdigest()
        receipt=ValidationReceipt('/config', (('configuration.yaml',digest(before),1,2),),
            (('.',(('configuration.yaml',33152,1,2),),('configuration.yaml',)),),
            (('configuration.yaml',digest(after)),), ('homekit','template','utility_meter'),
            ('homekit','template','utility_meter'), '2026.11.1',
            (('homekit','b'*64,'c'*64),('template',None,None),('utility_meter','d'*64,'e'*64)))
        return PreparedExternalStateYAML(receipt,
            (YAMLFile('configuration.yaml',before.hex(),after.hex(),1,2),),'a'*64)

    def test_exact_codec_is_not_activation(self):
        change=self.make().checked()
        encoded=encode_change(change)
        self.assertEqual(encoded['kind'],'template_external_state_yaml')
        self.assertEqual(decode_change(encoded),change)
        self.assertFalse(change.receipt.ready_for_apply)
        with self.assertRaises(ValueError): decode_yaml(asdict(change))
        with self.assertRaises(ValueError): decode_change(dict(encoded,kind='template_mixed_state_yaml'))

    def test_external_seals_and_refusals_cannot_be_removed(self):
        raw=encode_change(self.make())
        mutations=(lambda d:d['receipt'].update(unsupported_domains=['template','utility_meter']),
                   lambda d:d['receipt'].update(unsupported_domains=['homekit','template','utility_meter','utility_meter']),
                   lambda d:d['receipt']['runtime_expected'][0].__setitem__(1,None),
                   lambda d:d['receipt']['runtime_expected'][2].__setitem__(2,'d'*64),
                   lambda d:d['receipt'].update(changed_domains=['homekit','scene','template','utility_meter']),
                   lambda d:d.update(source_program_sha256='bad'))
        for mutate in mutations:
            altered=copy.deepcopy(raw);mutate(altered['data'])
            with self.subTest(altered=altered), self.assertRaises(ValueError): decode_change(altered)

    def test_composition_requires_every_source_proof(self):
        from custom_components.ha_entity_renamer.native_template_state_composition import ExternalStateComposition
        receipt=self.make().receipt
        composition=object.__new__(ExternalStateComposition)
        object.__setattr__(composition, 'external_source_proof', (('homekit',True),('utility_meter',True)))
        composition._assert_changed_scope(receipt)
        for proof in ((('homekit',True),), (('homekit',True),('utility_meter',False))):
            object.__setattr__(composition, 'external_source_proof', proof)
            with self.assertRaises(ValueError): composition._assert_changed_scope(receipt)

    def test_candidate_revalidation_requires_source_proofs_and_oriented_seals(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.native_candidate_validation import _compare_external_state
        receipt=self.make().receipt
        result={'runtime_expected':[list(row) for row in receipt.runtime_expected],
                'changed_domains':list(receipt.changed_domains),
                'unsupported_domains':list(receipt.unsupported_domains),
                'external_source_proof':[['homekit',True],['utility_meter',True]]}
        composition=SimpleNamespace(external_source_proof=(('homekit',True),('utility_meter',True)))
        with patch('custom_components.ha_entity_renamer.native_candidate_validation._compare_state_full',
                   return_value='native-template-proof'):
            forward=_compare_external_state(result,receipt,False,object(),composition)
            reverse_result=copy.deepcopy(result)
            for row in reverse_result['runtime_expected']:
                row[1],row[2]=row[2],row[1]
            reverse=_compare_external_state(reverse_result,receipt,True,object(),composition)
            self.assertNotEqual(forward,reverse)
            reverse_result['external_source_proof'][0][1]=False
            with self.assertRaises(ValueError):
                _compare_external_state(reverse_result,receipt,True,object(),composition)
