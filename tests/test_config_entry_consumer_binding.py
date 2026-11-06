"""UNIT source/consumer binding with copied discovery and synthetic Store/plans.

This fixture supplies no native physical-source, Recorder or runtime evidence.
"""
from copy import deepcopy
import importlib,json,sys,types,unittest
from pathlib import Path
from unittest.mock import patch

LOC=Path(__file__).resolve().parents[1]/'custom_components/ha_entity_renamer'
NAME='configentry_binding_isolated'
package=types.ModuleType(NAME)
package.__path__=[str(LOC)]
sys.modules[NAME]=package
discovery=importlib.import_module(NAME+'.discovery')

class File:
 def __init__(self,name,obj):
  self.key=name
  self.manager_json=json.dumps(obj,sort_keys=True).encode()
  self.raw=self.manager_json
  self.identity=(1,2,0o600)
  self.uid=0
  self.gid=0
class Native:
 def __init__(self,items,version='2026.10.2'):
  self.version=version
  self.files=(
   File('core.entity_registry',{'entities':[{'entity_id':'sensor.synthetic_old',
        'id':'stable-row','unique_id':'unique','platform':'synthetic','device_id':None,'disabled_by':None}]}),
   File('core.device_registry',{'devices':[]}),
   File('core.config_entries',{'entries':items}))
class Plan:
 def __init__(self,before):
  self.before=before
  self.markers=()
def item(key='cfg-1'):
 return {'entry_id':key,'domain':'powercalc','data':{'entity_id':'sensor.synthetic_old',
          'measurement':False},'options':{'foreign':{'nested':[1,False,None]}},
         'source':'user','unique_id':'stable-config','title':'Synthetic','version':1,
         'minor_version':5,'subentries':[{'subentry_id':'sub-1','subentry_type':'type',
         'unique_id':None,'title':'Child','data':{'sensor':'sensor.synthetic_old'}}],
         'modified_at':'synthetic-audit'}
def plan(row):
 return Plan({'domain':row['domain'],'identity':{'entry_id':row['entry_id'],
    'source':row.get('source'),'unique_id':row.get('unique_id'),'title':row.get('title'),
    'version':row.get('version'),'minor_version':row.get('minor_version'),
    'disabled_by':row.get('disabled_by'),'pref_disable_new_entities':False,
    'pref_disable_polling':False},
    'data':deepcopy(row['data']),'options':deepcopy(row['options']),
    'subentries':{sub['subentry_id']:{
      'identity':{'subentry_id':sub['subentry_id'],'subentry_type':sub.get('subentry_type'),
        'unique_id':sub.get('unique_id'),'title':sub.get('title')},
      'data':deepcopy(sub['data'])} for sub in row.get('subentries',[])}})
def fixture(saved=None,plans=None):
 saved=[item()] if saved is None else deepcopy(saved)
 if plans is None:plans=[plan(x) for x in saved]
 return Native(saved),tuple(plans)
def install(name,**kwargs):
 mod=types.ModuleType(NAME+'.'+name)
 vars(mod).update(kwargs)
 sys.modules[mod.__name__]=mod
async def evaluate(native,plans):
 async def observe(*a,**kw):return native
 install('native_discovery_inputs',async_observe_discovery_inputs=observe,
         assert_discovery_managers_current=lambda *a,**kw:None)
 install('helper_native',inventory=lambda *a:plans)
 # Optional runtime collection is outside this UNIT's structural binding scope.
 # Refuse it explicitly; do not extend helper_native with fake native readers.
 def powercalc_unavailable(*a,**kw):
  raise ValueError('synthetic_powercalc_not_in_unit_scope')
 def unexpected_powercalc_fence(*a,**kw):
  raise AssertionError('unit fixture has no Powercalc runtime capture to fence')
 install('powercalc_runtime_observer',capture_powercalc_runtime=powercalc_unavailable,
         assert_powercalc_runtime_current=unexpected_powercalc_fence)
 def yaml_unavailable(*a,**kw):raise ValueError('synthetic_yaml_unavailable')
 install('yaml_plan',plan_yaml=yaml_unavailable)
 async def drained(executor,callback):return await executor(callback)
 install('native_input_baseline',_drained=drained)
 class Recorder:
  def __init__(self,*a,**kw):raise ValueError('no synthetic recorder')
 install('recorder_guard',NativeRecorderGuard=Recorder,prepare=lambda *a,**kw:None)
 install('history_guard',NativeHistoryGuard=object)
 class Outcome:
  status='UNKNOWN'
  reason='not_in_offline_scope'
  value=None
 class Consumers:
  dashboard=Outcome()
  energy=Outcome()
 async def observe_consumers(*a,**kw):return Consumers()
 install('native_discovery_consumers',async_observe_discovery_consumers=observe_consumers)
 install('yaml_runtime_observer',NativeYAMLRuntimeReadback=object,
         _pending_native_work=lambda *a,**kw:True)
 install('yaml_validation_worker',_normal=lambda x:x,_fingerprint=lambda x:'no_native')
 ha=types.ModuleType('homeassistant');ha.__path__=[]
 c=types.ModuleType('homeassistant.const');c.__version__='2026.10.2'
 h=types.ModuleType('homeassistant.helpers');h.__path__=[]
 e=types.ModuleType('homeassistant.helpers.entity_component');e.DATA_INSTANCES='instances'
 ha_modules={'homeassistant':ha,'homeassistant.const':c,
    'homeassistant.helpers':h,'homeassistant.helpers.entity_component':e}
 class Hass:
  class Config:
   config_dir='/synthetic/does-not-exist'
  config=Config()
  data={}
  @staticmethod
  async def async_add_executor_job(callback):return callback()
 # Keep the REAL final continuation and synchronous structural checks. Only
 # the physical worker is an explicit UNIT seam: synthetic native has no root,
 # Store files, HA manager identity, or SQL authority. Never execute its closure
 # or manufacture successful native readback/physical evidence here.
 final_fence=importlib.import_module(NAME+'.discovery_final_fence')
 physical_requests=[]
 async def unit_without_physical_io(hass,callback):
  if not callable(callback):raise AssertionError('physical worker contract changed')
  physical_requests.append(callback)
 with patch.dict(sys.modules, ha_modules), patch.object(
     final_fence,'_settled_physical',new=unit_without_physical_io):
  capture=await discovery.async_discover(Hass(),{'entities':[{
    'entity_id':'sensor.synthetic_old','proposed_entity_id':'sensor.synthetic_new'}]},lambda:None)
  if len(physical_requests)!=1:
   raise AssertionError('UNIT fixture must exercise the final continuation once')
  outside_scope={'powercalc','history','statistics','yaml_sources',
                 'dashboard','energy','automation','script'}
  if any(row.status!='UNKNOWN' for row in capture.families if row.name in outside_scope):
   raise AssertionError('UNIT fixture must not promote unobserved native families')
  if discovery.compose_preview(capture)['plan_supported']:
   raise AssertionError('structural UNIT evidence must not become apply readiness')
  return next(row for row in capture.families if row.name=='config_entries'),capture

class Pipeline(unittest.IsolatedAsyncioTestCase):
 async def test_matching_store_and_manager_keeps_separate_readonly_preview(self):
  native,plans=fixture()
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'COMPLETE',fam.reason)
  self.assertIsNotNone(cap.bound_manifest_json)
  self.assertFalse(cap.summary()['migration_ready'])
  self.assertFalse(discovery.compose_preview(cap)['plan_supported'])
 async def test_manager_options_drift_blocks_and_discards_bound(self):
  native,plans=fixture();plans[0].before['options']['foreign']['nested'][1]=True
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)
 async def test_missing_disabled_entry_cannot_be_mislabeled_complete(self):
  saved=[item('cfg-1'),item('cfg-2')];saved[1]['disabled_by']='user'
  native,plans=fixture(saved,[plan(saved[0])])
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)
 async def test_unexpected_manager_only_configentry_refuses(self):
  native,plans=fixture([item()],[plan(item()),plan(item('phantom'))])
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)
 async def test_subentry_data_drift_is_not_ignored(self):
  native,plans=fixture()
  plans[0].before['subentries']['sub-1']['data']['sensor']='sensor.other'
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)
 async def test_wrong_manager_domain_is_not_source_matched(self):
  native,plans=fixture();plans[0].before['domain']='template'
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)
 async def test_boolean_must_not_match_integer_zero(self):
  native,plans=fixture();plans[0].before['data']['measurement']=0
  fam,cap=await evaluate(native,plans)
  self.assertEqual(fam.status,'UNKNOWN')
  self.assertIsNone(cap.bound_manifest_json)

class Pure(unittest.TestCase):
 def bind(self,n,p):
  module=importlib.import_module(NAME+'.native_configentry_binding')
  return module.bind_source_consumer(n,p)
 def refuses(self,n,p):
  with self.assertRaises(ValueError) as err:self.bind(n,p)
  self.assertEqual(str(err.exception),'configentry_source_consumer_unbound')
 def test_matching_minimum_core_source_supported_as_structural_readonly(self):
  n,p=fixture();n.version='2025.6.0'
  result=self.bind(n,p)
  self.assertEqual(result.entries,1)
  self.assertIs(result.can_write,False)
  self.assertIs(result.can_promote,False)
 def test_reordered_complete_plan_set_does_not_duplicate(self):
  source=[item('cfg-1'),item('cfg-2')]
  n,p=fixture(source,[plan(source[1]),plan(source[0])])
  self.assertEqual(self.bind(n,p).entries,2)
 def test_reject_duplicate_source_entry(self):
  n,p=fixture([item(),item()],[plan(item()),plan(item('cfg-2'))])
  self.refuses(n,p)
 def test_reject_duplicate_manager_entry(self):
  source=[item('cfg-1'),item('cfg-2')]
  n,p=fixture(source,[plan(source[0]),plan(source[0])])
  self.refuses(n,p)
 def test_reject_unknown_core_version_instead_of_guessing_schema(self):
  n,p=fixture();n.version='2026.11.0b1';self.refuses(n,p)
 def test_reject_missing_native_store_document(self):
  n,p=fixture();n.files=n.files[:2];self.refuses(n,p)
 def test_reject_malformed_subentry_collection(self):
  n,p=fixture();obj=json.loads(n.files[-1].manager_json)
  obj['entries'][0]['subentries']={'sub-1':{'data':{}}}
  n.files=n.files[:2]+(File('core.config_entries',obj),)
  self.refuses(n,p)
 def test_reject_extra_unrecognized_manager_field(self):
  n,p=fixture();p[0].before['unsafe_extra']='secret';self.refuses(n,p)
 def test_reject_non_json_manager_field(self):
  n,p=fixture();p[0].before['data']['ghost']={1,2};self.refuses(n,p)
 def test_reject_identical_duplicate_json_object_keys(self):
  n,p=fixture()
  file=File('core.config_entries',{})
  file.manager_json=b'{"entries":[],"entries":[]}'
  n.files=n.files[:2]+(file,)
  self.refuses(n,p)
 def test_modified_at_not_reinterpreted_as_mutation_authority(self):
  n,p=fixture();result=self.bind(n,p)
  self.assertEqual(result.entries,1)
  self.assertFalse(result.can_write)
 def test_negative_result_never_leaks_config_value(self):
  n,p=fixture();p[0].before['options']['private']='never-leak'
  with self.assertRaises(ValueError) as cm:self.bind(n,p)
  self.assertNotIn('never-leak',str(cm.exception))
if __name__=='__main__':unittest.main(verbosity=2)
