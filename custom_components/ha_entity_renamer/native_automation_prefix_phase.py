"""Root-owned Automation/Script prefix phases, separate from native reload tickets.

This is a runtime/configuration fence, never a replacement for the outer
SAME ORIGINAL physical input, durable intent, actual API and receipt proofs.
A live node can bridge its own synchronous cutover until its real receipt ACK;
accepted masks advance only from the independent full dependent readback.
"""
import asyncio

from .transaction import Position, Refused
from .dependent_config_binding import NativeDependentConfigReadback
from .dependent_config_program import _digest
from .native_automation_config_transport import NativeAutomationConfigTransport


def _need(value, reason='automation_prefix_phase_unconfirmed'):
    if not value:
        raise Refused(reason) from None


class NativeAutomationPrefixPhase:
    def __init__(self, root):
        from .native_template_state_server import NativeTemplateStateServer
        from .native_automation_program_composition import AutomationTemplateStateComposition
        _need(type(root) is NativeTemplateStateServer
              and type(root.composition) is AutomationTemplateStateComposition)
        self.root, self.active, self.unresolved = root, None, False
        self.program = root.composition.dependent_config_program
        from .native_automation_owned_graph import OwnedAutomationGraph
        self.graph = OwnedAutomationGraph(root.hass)
        self.observer = NativeDependentConfigReadback(root.hass, self.program,
            root.evidence, root.receipt, root.composition.yaml_plan, owned_automation=self.graph)
        sample = self.observer.sample(root.chain, input_state=root.observation_state)
        self.mask = sample.prefix.active
        from .native_automation_config_transport import assert_idle_automation_program_targets
        assert_idle_automation_program_targets(root.hass, self.program,
            active=self.mask if self.mask else None)
        self.sample = sample.registry_rows, (), sample.native_tokens
        _need(self._read(self.mask) == self.sample)

    def _read(self, mask):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        from .yaml_source_binding import _runtime_rows_sample
        expected = self.program.rows(mask)
        native = NativeYAMLRuntimeReadback(self.root.hass, owned_automation=self.graph)
        for domain, rows in expected:
            component = self.root.hass.data.get(DATA_INSTANCES, {}).get(domain)
            projection = native._projection(domain)[0] if component is not None else []
            _need(_normal(projection) == rows, 'automation_prefix_phase_native_rows_changed')
        result = _runtime_rows_sample(self.root.hass, self.root.evidence,
            self.root.chain.accepted_documents()['core.entity_registry']['data']['entities'],
            expected_projections=tuple((domain, _digest(rows)) for domain, rows in expected),
            owned_automation=self.graph)
        _need(not result[1], 'automation_prefix_phase_native_inventory_changed')
        return result

    def _registry_mask(self):
        # This narrow phase check authenticates accepted documents through the
        # owning chain. Physical/source fences remain in the outer root/node.
        registry = self.root.chain.accepted_documents()['core.entity_registry']['data']['entities']
        result, mapping, order = set(), {}, []
        for index, step in enumerate(self.root.plan.steps):
            if step.adapter != 'registry':
                continue
            change = self.root.chain._original_change(index)
            for old, new in change.mapping.items():
                matches = [row for row in registry if row['id'] == change.identity]
                _need(len(matches) == 1 and matches[0]['entity_id'] in (old, new))
                _need(old not in mapping)
                mapping[old] = new
                order.append(old)
                if matches[0]['entity_id'] == new:
                    result.add(old)
        _need(mapping == dict(self.program.mapping) and tuple(order) == self.program.source_order)
        self.program.rows(frozenset(result))
        return frozenset(result)

    @property
    def positions(self):
        mask = self.mask
        if self.active is not None:
            _, transfer, target = self.active
            if transfer is None or transfer.phase in ('installed', 'attempted'):
                mask = target
        position = (Position.BEFORE if not mask else Position.AFTER
                    if mask == frozenset(self.program.source_order) else Position.UNKNOWN)
        return (('automation', position), ('script', position))

    def check(self):
        _need(not self.unresolved)
        accepted = self._registry_mask()
        wanted = self.mask
        if self.active is None:
            _need(accepted == wanted, 'automation_prefix_phase_accepted_mask_changed')
        else:
            node, transfer, target = self.active
            _need(node.root is self.root and asyncio.current_task() is node.task
                  and node.phase in ('captured', 'registry_attempted', 'observed_effect', 'failed_before_attempt'))
            _need(accepted == self.mask or accepted == target and node.phase == 'observed_effect')
            if self.root.chain.active is not None:
                _need(self.root.chain.active is node.intent)
            else:
                _need(accepted == target and node.phase == 'observed_effect')
            if transfer is None or transfer.phase in ('installed', 'attempted'):
                wanted = target
        _need(self._read(wanted) == self.sample, 'automation_prefix_phase_identity_changed')

    def admit_record(self, direction):
        self.root._scope()
        _need(direction in ('forward', 'rollback') and self.active is None
              and self.root.chain.active is None
              and asyncio.current_task() is self.root.task)
        from .native_automation_config_transport import assert_idle_automation_program_targets
        assert_idle_automation_program_targets(self.root.hass, self.program,
            active=self.mask if direction == 'rollback' else None)

    def capture(self, node):
        from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
        self.check()
        _need(self.active is None and type(node) is NativeTemplateStateRegistryEffect
              and node.root is self.root and self.root.chain.active is node.intent
              and node.phase == 'captured' and asyncio.current_task() is node.task)
        source = next(iter(node.change.mapping))
        reverse = node.context.record.direction == 'rollback'
        target = self.mask - {source} if reverse else self.mask | {source}
        self.program.assert_edge(self.mask, target, source)
        before, after = dict(self.program.rows(self.mask)), dict(self.program.rows(target))
        transfer = None
        if before != after:
            transfer = NativeAutomationConfigTransport.capture(self.observer, self.root.chain,
                source, reverse=reverse, guard=node.guard, input_state=node.state)
        self.graph.node, self.graph.transfer = node, transfer
        self.active = node, transfer, target

    def install(self, node):
        _need(self.active is not None and self.active[0] is node)
        transfer = self.active[1]
        if transfer is not None:
            transfer.install(node.witness)
        else:
            self.graph.install(node.witness)
        # Wrapper/route tokens change openly. Full rows and retained native
        # lineage are checked by the typed graph before accepting this image.
        self.sample = self._read(self.active[2])
        self.check()

    def mark_attempt(self, node):
        _need(self.active is not None and self.active[0] is node)
        transfer = self.active[1]
        if transfer is not None:
            transfer.retain_after_attempt()

    def abort(self, node):
        _need(self.active is not None and self.active[0] is node
              and not node._registry_attempted())
        transfer = self.active[1]
        if transfer is not None and transfer.phase == 'installed':
            transfer.restore_before_attempt()
        elif transfer is None:
            self.graph.retire(before_attempt=True)
        self.active = None
        self.sample = self._read(self.mask)
        self.check()

    def accepted(self, node):
        _need(self.active is not None and self.active[0] is node
              and node.phase == 'observed_effect' and self.root.chain.active is None)
        target = self.active[2]
        _need(not self.graph.live and not self.graph.routes)
        self.sample = self._read(target)
        sample = self.observer.sample(self.root.chain)
        _need(sample.prefix.active == target
              and (sample.registry_rows, (), sample.native_tokens) == self.sample)
        self.mask, self.active = target, None
        self.check()

    def finish_native(self, node):
        _need(self.active is not None and self.active[0] is node)
        transfer = self.active[1]
        if transfer is not None:
            transfer._rows(transfer.after)
        self.graph.check()
        self.graph.retire()
        if transfer is not None:
            transfer._rows(transfer.after)
        self.sample = self._read(self.active[2])
        self.check()

    def poison(self, node):
        _need(self.active is not None and self.active[0] is node)
        self.unresolved = True

    def close(self):
        if self.graph.live:
            self.graph.retire(before_attempt=not self.graph.witness.attempted)
        else:
            self.graph.routes.clear()
        self.graph.prepared = ()
        if self.active is not None:
            node = self.active[0]
            self.unresolved = True
            if node.witness is not None:
                node.witness.close()
