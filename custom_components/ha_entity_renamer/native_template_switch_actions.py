"""Cut over the two native idle Template switch scripts with the state binding.

The root supplies sealed normalized action images; this helper grants no write
or registry authority. A running action is refused rather than raced.
"""
from types import SimpleNamespace

from .native_script_config_transport import (NativeScriptConfigTransport,
    _idle_census, _graph, _need)


class SwitchActionTransfer:
    @classmethod
    def capture(cls, hass, pairs, entities):
        obj = cls()
        obj.hass, obj.rows, obj.phase = hass, [], 'captured'
        if not pairs:
            return obj
        from homeassistant.components.template.switch import StateSwitchEntity
        from homeassistant.helpers.script import Script
        from .yaml_validation_worker import _normal
        seen = set()
        for entity, images in pairs:
            _need(type(entity) is StateSwitchEntity and entity in entities
                  and id(entity) not in seen and type(entity._action_scripts) is dict,
                  'template_switch_action_owner_changed')
            seen.add(id(entity))
            _need(tuple(name for name, _, _ in images) == ('turn_on', 'turn_off'))
            scripts = dict(entity._action_scripts)
            _need(set(scripts) == {name for name, before, _ in images if before is not None},
                  'template_switch_action_inventory_changed')
            census = {}
            for name, before, after in images:
                _need((before is None) == (after is None), 'template_switch_action_shape_changed')
                if before is not None:
                    script = scripts[name]
                    _need(type(script) is Script and script._hass is hass and script.domain == 'template'
                          and script.sequence is entity._config[name] and _normal(script.sequence) == before,
                          'template_switch_action_source_changed')
                    census[name] = _idle_census(script)
            obj.rows.append((entity, images, scripts, census, _graph(entity._action_scripts, include_scripts=True)))
        return obj

    def install(self):
        from .yaml_validation_worker import _normal
        _need(self.phase == 'captured')
        self.phase = 'installing'
        for entity, images, scripts, census, graph in self.rows:
            _need(entity._action_scripts == scripts and _graph(entity._action_scripts, include_scripts=True) == graph
                  and all(_idle_census(script) == census[name] for name, script in scripts.items()),
                  'template_switch_action_live_changed')
            # _config was cloned by the parent binding; the old Script and its
            # cached graph still own the original sequence for exact rollback.
            for name, before, after in images:
                if before is None:
                    continue
                value = entity._config[name]
                _need(_normal(value) == before, 'template_switch_action_config_changed')
                projector = object.__new__(NativeScriptConfigTransport)
                projector.observer = SimpleNamespace(hass=self.hass)
                projector._projected, projector.operations = {}, []
                projector._project(value, before, after)
                for operation in projector.operations:
                    projector._write(operation)
                _need(_normal(value) == after, 'template_switch_action_projection_changed')
                entity.add_script(name, value, entity._attr_name, 'template')
        self.phase = 'installed'

    def restore(self):
        if self.phase not in ('installing', 'installed'):
            return
        for entity, _, scripts, _, _ in reversed(self.rows):
            for name, current in tuple(entity._action_scripts.items()):
                if current is not scripts.get(name):
                    current._async_unload()
            entity._action_scripts.clear()
            entity._action_scripts.update(scripts)
        self.phase = 'restored'

    def commit(self):
        _need(self.phase == 'installed')
        for entity, _, scripts, _, _ in self.rows:
            for script in scripts.values():
                script._async_unload()
        self.phase = 'committed'
