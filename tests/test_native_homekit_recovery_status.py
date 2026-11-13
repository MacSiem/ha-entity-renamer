"""A terminal journal is insufficient until bridge setup has a receipt."""
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from custom_components.ha_entity_renamer.helper_transaction import prepare_helper
from custom_components.ha_entity_renamer.native_homekit_recovery_status import classify
from custom_components.ha_entity_renamer.transaction import Plan, Record, Refused, Step
from test_helper_plan import snapshot

class HomeKitRecoveryStatusTests(unittest.TestCase):
    def setup_case(self):
        before=snapshot('homekit', options={'filter':{'exclude_entities':['sensor.before']}})
        before['identity']['source']='import'
        change=prepare_helper(before, {'sensor.before':'sensor.after'})
        step=Step('helper','helpers',change.identity,change.before_revision,change.after_revision,'ref')
        plan=Plan('plan',('helpers',),('helpers',),(step,),'proof',('helpers',))
        record=Record('operation',plan,status='applied',phases=('verified',))
        composition=SimpleNamespace(prepared_digests=(('ref','digest'),))
        preparations=SimpleNamespace(resolve_composition=lambda ref:(SimpleNamespace(receipt=object()),composition),
                                     resolve=lambda ref,digest:object())
        return preparations,record,change

    def test_only_both_acknowledged_lifecycle_phases_are_complete(self):
        preparations,record,change=self.setup_case()
        status={'inactive_receipts_complete':True,'activation_receipts_complete':False,'pending':[]}
        witness=SimpleNamespace(inspect=lambda:status)
        with patch('custom_components.ha_entity_renamer.prepared.decode_change',return_value=change), \
             patch('custom_components.ha_entity_renamer.native_helper_lifecycle.validate_lifecycle_receipt_witness',
                   return_value=witness):
            self.assertFalse(classify(preparations,object(),record))
            status['activation_receipts_complete']=True
            self.assertTrue(classify(preparations,object(),record))
            status['pending']=[{'stage':'setup'}]
            self.assertFalse(classify(preparations,object(),record))

    def test_unfinished_journal_never_promotes(self):
        preparations,record,change=self.setup_case()
        record=Record(record.operation_id,record.plan,status='unknown',phases=('intent',))
        with patch('custom_components.ha_entity_renamer.prepared.decode_change',return_value=change):
            with self.assertRaises(Refused): classify(preparations,object(),record)

class HomeKitRecoveryInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_fails_closed_until_lifecycle_receipts_complete(self):
        from unittest.mock import AsyncMock
        from custom_components.ha_entity_renamer.recovery_controller import RecoveryController
        registry=Step('registry','registry','entity','old','new','registry')
        helper=Step('helper','helpers','bridge','old','new','helper')
        yaml=Step('yaml','yaml','files','old','new','yaml')
        scopes=('registry','helpers','yaml')
        plan=Plan('plan',scopes,scopes,(registry,helper,yaml),'proof',scopes)
        record=Record('operation',plan,status='applied',phases=('verified',)*3)
        journal=SimpleNamespace(initialize=AsyncMock(), all=AsyncMock(return_value=[record]))
        preparations=SimpleNamespace(initialize=AsyncMock())
        lifecycle=SimpleNamespace(initialize=AsyncMock())
        controller=RecoveryController(None,None,guard=lambda:None,refresh=AsyncMock(),status_only=True)
        controller.check=lambda:None
        with patch('custom_components.ha_entity_renamer.recovery_controller.native_journal',return_value=journal), \
             patch('custom_components.ha_entity_renamer.recovery_controller.native_prepared_store',return_value=preparations), \
             patch('custom_components.ha_entity_renamer.recovery_controller.native_helper_lifecycle_store',return_value=lifecycle), \
             patch('custom_components.ha_entity_renamer.native_homekit_recovery_status.classify') as classify_mock:
            for result, needed in ((False,True),(True,False),(None,True)):
                classify_mock.return_value=result
                status=await controller.status()
                self.assertEqual(status['recovery_required'],needed)
                self.assertEqual(status['operations'][0]['recovery_required'],needed)
                self.assertFalse(status['migration_ready'])
            classify_mock.side_effect=Refused('malformed receipt')
            self.assertTrue((await controller.status())['recovery_required'])
