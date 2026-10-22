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
        'map_items': '''
- action: renamer_probe.capture
  data:
    value: '{% for e, meta in entities.items() %}{{ states(e) }}|{{ meta.label }};{% endfor %}'
''',
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
    for branch in ('true', 'false'):
        cases['jinja_branch_' + branch] = (
            "- action: renamer_probe.capture\n  data:\n    value: >-\n"
            "      {% if " + branch + " %}{% set e='sensor.foo' %}"
            "{% elif true %}{% set e='light.old' %}"
            "{% else %}{% set e='sensor.foo' %}{% endif %}{{ states(e) }}\n")
    cases['jinja_with'] = ("- action: renamer_probe.capture\n  data:\n    value: >-\n"
        "      {% with e='sensor.foo' %}{{ states(e) }}{% endwith %}\n")
    cases['jinja_do'] = ("- action: renamer_probe.capture\n  data:\n    value: >-\n"
        "      {% set e='sensor.foo' %}{% do states(e) %}{{ states(e) }}\n")
    cases['jinja_loopcontrols'] = ("- action: renamer_probe.capture\n  data:\n    value: >-\n"
        "      {% for e in ['sensor.foo','light.old'] %}"
        "{% if loop.first %}{% continue %}{% endif %}{{ states(e) }}{% break %}{% endfor %}\n")
    expected = {'map_items': ['42|Sensor;on|Light;'], 'choose_if': ['on'],
                'jinja_branch_true': ['42'], 'jinja_branch_false': ['on'],
                'jinja_with': ['42'], 'jinja_do': ['42'], 'jinja_loopcontrols': ['on'],
                'repeat': ['42', 'on'], 'parallel': ['42', '42']}
    captured = []

    async def capture(call):
        captured.append(str(call.data['value']))

    hass.services.async_register('renamer_probe', 'capture', capture)
    mapping = {'sensor.foo': 'sensor.new', 'light.old': 'light.new'}
    try:
        for name, steps in cases.items():
            variables = ({'entities': {'sensor.foo': {'label': 'Sensor'},
                                        'light.old': {'label': 'Light'}}}
                         if name == 'map_items' else
                         {} if name.startswith('jinja_') else {'ref': 'sensor.foo'})
            document = {'script': {'fixture': {'variables': variables,
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
