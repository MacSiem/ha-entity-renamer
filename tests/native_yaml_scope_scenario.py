"""Execute rewritten synthetic YAML with HA's actual Script/variables engine."""
from collections import Counter
from pathlib import Path
import yaml


async def run_yaml_scopes(hass, root):
    from homeassistant.core import Context
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.script import Script
    from homeassistant.helpers.script_variables import ScriptVariables
    from custom_components.ha_entity_renamer.yaml_plan import plan_yaml

    cases = {
        'choose_if': '''
- choose:
  - conditions: '{{ true }}'
    sequence:
    - if: '{{ true }}'
      then:
      - variables:
          ref: light.old
- action: renamer_probe.capture
  data:
    value: '{{ states(ref) }}'
''',
        'repeat': '''
- repeat:
    count: 2
    sequence:
    - action: renamer_probe.capture
      data:
        value: '{{ states(ref) }}'
    - variables:
        ref: light.old
''',
        'parallel': '''
- parallel:
  - action: renamer_probe.capture
    data:
      value: '{{ states(ref) }}'
  - sequence:
    - action: renamer_probe.capture
      data:
        value: '{{ states(ref) }}'
''',
    }
    expected = {'choose_if': ['on'], 'repeat': ['42', 'on'], 'parallel': ['42', '42']}
    captured = []

    async def capture(call):
        captured.append(str(call.data['value']))

    hass.services.async_register('renamer_probe', 'capture', capture)
    mapping = {'sensor.foo': 'sensor.new', 'light.old': 'light.new'}
    try:
        for name, steps in cases.items():
            document = {'script': {'fixture': {'variables': {'ref': 'sensor.foo'},
                                              'sequence': yaml.safe_load(steps)}}}
            path = Path(root) / (name + '.yaml')
            path.write_text(yaml.safe_dump(document, sort_keys=False))
            plan = plan_yaml(root, mapping, entrypoints=(path.name,))
            assert plan.ready and len(plan.patches) == 1, (name, plan.issues)
            changed = yaml.safe_load(plan.patches[0].proposed_bytes)
            for phase, content in (('before', document), ('after', changed)):
                for entity_id in (*mapping, *mapping.values()):
                    hass.states.async_remove(entity_id)
                hass.states.async_set('sensor.foo' if phase == 'before' else 'sensor.new', '42')
                hass.states.async_set('light.old' if phase == 'before' else 'light.new', 'on')
                item = content['script']['fixture']
                captured.clear()
                script = Script(hass, cv.SCRIPT_SCHEMA(item['sequence']), name,
                                'renamer_probe', variables=ScriptVariables(item['variables']))
                await script.async_run(context=Context())
                assert Counter(captured) == Counter(expected[name]), (name, phase, captured)
        print({'native_script_scopes': list(cases), 'before_after_values': 'PASS',
               'live_config_access': False}, flush=True)
    finally:
        hass.services.async_remove('renamer_probe', 'capture')
        for entity_id in (*mapping, *mapping.values()):
            hass.states.async_remove(entity_id)
