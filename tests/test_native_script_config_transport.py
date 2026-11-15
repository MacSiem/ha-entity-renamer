"""Real native idle-cache cutover/abort, not a registry transaction acceptance."""
import unittest

import test_native_dependent_config_binding as binding


class NativeScriptConfigTransportTests(binding.NativeDependentConfigBindingTests):
    async def script_binding(self):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        (root / 'mixed_scripts.yaml').write_text('''mixed_source:
  variables:
    source: ''' + old + '''
    alias: "{{ source }}"
  sequence:
    - if: "{{ states(''' + repr(old) + ''') | int(0) > 0 }}"
      then:
        - event: mixed_yaml_script_result
          event_data:
            value: "{{ states(alias) }}"
      else:
        - event: mixed_yaml_script_result
          event_data:
            value: empty
''')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await self.stimulus(17)
        events = []
        remove = hass.bus.async_listen('mixed_yaml_script_result', lambda event: events.append(event.data['value']))
        self.addCleanup(remove)
        await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        self.assertEqual([str(value) for value in events], ['17'])
        entity = next(iter(hass.data[DATA_INSTANCES]['script'].entities))
        self.assertTrue(entity.script._if_data)
        self.assertTrue(getattr(entity.script, self.module('native_script_config_transport')._cache_names()[0]))
        await self._flush_documents()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
            guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
        bundle = await self.module('yaml_validation').async_validate_proposed_with_template_state_sources(
            hass, composition.yaml_plan, mapping, root.parent, with_state_program=True,
            with_dependent_program=True)
        self.assertEqual(bundle.receipt, baseline.receipt)
        rr = self.module('native_registry_receipts')
        store = rr.native_registry_receipt_store(hass, runtime.preparations)
        await store.initialize()
        chain = rr.RegistryReceiptChain.reopen(store, review['operation_id'], plan, baseline.receipt,
                                               dict(composition.prepared_digests))
        observer = self.module('dependent_config_binding').NativeDependentConfigReadback(
            hass, bundle.dependent_config_program, bundle.evidence, bundle.receipt, composition.yaml_plan)
        return observer, chain, mapping, entity, events

    async def test_script_real_primed_nested_native_caches_cutover_and_exact_abort(self):
        from homeassistant.core import Context
        observer, chain, mapping, entity, events = await self.script_binding()
        module = self.module('native_script_config_transport')
        script = entity.script
        before = observer.sample(chain)
        caches = tuple((name, getattr(script, name), tuple(getattr(script, name).items())) for name in module._cache_names())
        variables = script.variables
        service = self.f.hass.services._services['script']['mixed_source']
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        # No await and no registry effect between install and abort.
        transport.install()
        self.assertIs(entity.script, script)
        self.assertIs(script.variables, variables)
        self.assertIs(self.f.hass.services._services['script']['mixed_source'], service)
        self.assertTrue(all(not cache for _, cache, _ in caches))
        transport._check_rows(transport.after)
        transport.restore_before_attempt()
        self.assertEqual(observer.sample(chain), before)
        for name, cache, items in caches:
            self.assertIs(getattr(script, name), cache)
            self.assertEqual(tuple(cache.items()), items)
        await self.f.hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        self.assertEqual([str(value) for value in events], ['17', '17'])
        self.assertEqual(transport.phase, 'restored_before_attempt')

    async def test_script_real_cache_change_refuses_before_config_writes(self):
        observer, chain, mapping, entity, _ = await self.script_binding()
        module = self.module('native_script_config_transport')
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        # Native laziness may legitimately change during an await in the caller.
        # Evict a cache through its ordinary dict; install must recapture/refuse.
        cache = getattr(entity.script, module._cache_names()[0])
        original = tuple(cache.items())
        cache.clear()
        try:
            with self.assertRaisesRegex(self.module('transaction').Refused, 'cache_changed'):
                transport.install()
        finally:
            cache.update(original)
        self.assertEqual(transport.phase, 'captured')
        self.assertEqual(observer.sample(chain), transport.sample)

    async def test_script_real_equal_foreign_template_is_not_overwritten_on_abort(self):
        from homeassistant.helpers.template import Template
        observer, chain, mapping, _, _ = await self.script_binding()
        module = self.module('native_script_config_transport')
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        transport.install()
        container, key, owned = next((container, key, value)
            for kind, container, _, after in transport.operations if kind == 'dict'
            for key, value in after if type(value) is Template)
        foreign = Template(owned.template, owned.hass)
        container[key] = foreign
        try:
            with self.assertRaisesRegex(self.module('transaction').Refused, 'cleanup_(slot_replaced|live_edges_changed)'):
                transport.restore_before_attempt()
            self.assertIs(container[key], foreign)
            self.assertEqual(transport.phase, 'installed')
        finally:
            container[key] = owned
            transport.restore_before_attempt()
        self.assertEqual(observer.sample(chain), transport.sample)

    async def test_script_review_foreign_equal_template_before_install_is_preserved(self):
        from homeassistant.helpers.template import Template
        observer, chain, mapping, entity, _ = await self.script_binding()
        module = self.module('native_script_config_transport')
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        container = entity.script.sequence[0]['if'][0]
        original = container['value_template']
        foreign = Template(original.template, original.hass)
        container['value_template'] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.install()
            self.assertIs(container['value_template'], foreign)
            self.assertEqual(transport.phase, 'captured')
        finally:
            if transport.phase == 'installed':
                transport.restore_before_attempt()
            container['value_template'] = original
        self.assertEqual(observer.sample(chain), transport.sample)

    async def test_script_review_detached_variables_abort_has_no_partial_restore(self):
        observer, chain, mapping, entity, _ = await self.script_binding()
        module = self.module('native_script_config_transport')
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        transport.install()
        variables = entity.script.variables
        owned = variables.variables
        foreign = dict(owned)
        variables.variables = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.restore_before_attempt()
            self.assertIs(variables.variables, foreign)
            transport._check_rows(transport.after)  # no partial BEFORE image
        finally:
            variables.variables = owned
            try:
                observer.sample(chain)
            except (ValueError, self.module('transaction').Refused):
                transport.restore_before_attempt()
        self.assertEqual(observer.sample(chain), transport.sample)

    async def test_script_review_inner_native_cache_list_change_refuses(self):
        observer, chain, mapping, entity, _ = await self.script_binding()
        module = self.module('native_script_config_transport')
        transport = module.NativeScriptConfigTransport.capture(observer, chain, next(iter(mapping)), guard=lambda: None)
        data = next(iter(entity.script._if_data.values()))
        original = data['if_conditions']
        foreign = list(original)
        data['if_conditions'] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.install()
            self.assertIs(data['if_conditions'], foreign)
            self.assertEqual(transport.phase, 'captured')
        finally:
            if transport.phase == 'installed':
                transport.restore_before_attempt()
            data['if_conditions'] = original
        self.assertEqual(observer.sample(chain), transport.sample)
