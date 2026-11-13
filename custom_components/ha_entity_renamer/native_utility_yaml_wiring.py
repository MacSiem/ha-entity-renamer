"""Guarded registry adapter selection for a source-bound Utility YAML handoff."""
from .native_utility_yaml_receipt import UtilityYAMLRegistryAdapter, _id
from .transaction import Refused


def registry_adapter(root, registry, resolver, controlled):
    """Wrap only a server-proven live binding; cold sidecars never bypass it."""
    binding = getattr(root, 'utility_yaml_binding', None)
    if binding is None:
        if root.preparations.plans is None or any(
                _id(root.operation_id, root.plan.fingerprint, step.key, direction, kind)
                in root.preparations.plans for step in root.plan.steps
                if step.adapter == 'registry' for direction in ('forward', 'rollback')
                for kind in ('intent', 'receipt')):
            raise Refused('utility_yaml_reopen_required')
        return registry
    from .native_utility_yaml_binding import UtilityYAMLBinding
    if not (type(binding) is UtilityYAMLBinding and binding.hass is root.hass
            and binding.mapping == root.mapping and not controlled):
        raise Refused('utility_yaml_binding_or_powercalc_scope_unsupported')
    return UtilityYAMLRegistryAdapter(registry, binding.handoff, root.preparations,
        resolver, root.operation_id, root.plan.fingerprint, root._scope)
