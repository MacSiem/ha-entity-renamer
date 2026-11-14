"""Private phase/scope predicates only; never a native admission capability.

Every consumer must separately verify actual full inputs, runtime, history and
chain receipts. These predicates neither change phases nor permit intent replay.
"""


def _registry_only_shape(record):
    return (record.plan.required_scopes == record.plan.covered_scopes
            == record.plan.verification_adapters == ('registry',)
            and bool(record.plan.steps)
            and all(step.adapter == 'registry' and not step.caused_by for step in record.plan.steps))


def _registry_only_bound(chain, record):
    from .native_registry_receipts import RegistryReceiptChain
    from .native_template_state_composition import TemplateStateComposition
    if type(chain) is not RegistryReceiptChain or record.plan != chain.plan or chain.yaml_index is not None:
        return False
    try:
        baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
        if (type(composition) is not TemplateStateComposition or baseline != chain.baseline
                or baseline.receipt.proposed_files or baseline.receipt.changed_domains):
            return False
        composition.assert_baseline(baseline)
        return True
    except Exception:
        return False


def acknowledged_registry_prefix(record):
    from .transaction import Record
    if (type(record) is not Record or record.direction != 'forward'
            or record.status not in ('applying', 'unknown', 'ready')
            or len(record.plan.steps) < 2 or len(record.phases) != len(record.plan.steps)
            or (tuple(step.adapter for step in record.plan.steps)
               != ('registry',) * (len(record.plan.steps) - 1) + ('yaml',)
                and not _registry_only_shape(record))):
        return False
    count = record.phases.count('verified')
    return (0 < count < len(record.phases)
            and record.phases == ('verified',) * count + ('pending',) * (len(record.phases) - count))


def acknowledged_powercalc_registry_prefix(record):
    """Recognize shape only; receipt, native state and inverse admission are separate."""
    from .transaction import Record
    if (type(record) is not Record or record.direction != 'forward'
            or record.status not in ('applying', 'unknown', 'ready')
            or len(record.phases) != len(record.plan.steps)
            or record.plan.required_scopes != record.plan.covered_scopes
            or record.plan.required_scopes != record.plan.verification_adapters
            or any(step.caused_by for step in record.plan.steps)):
        return False
    adapters = tuple(step.adapter for step in record.plan.steps)
    registry, helpers = adapters.count('registry'), adapters.count('helpers')
    energy, dashboard = adapters.count('energy'), adapters.count('dashboard')
    scopes = (('registry',) + (('helpers',) if helpers else ()) +
              (('energy',) if energy else ()) + (('dashboard',) if dashboard else ()) + ('yaml',))
    if (not registry or energy > 1 or record.plan.required_scopes != scopes
            or adapters != (('registry',) * registry + ('helpers',) * helpers +
                            ('energy',) * energy + ('dashboard',) * dashboard + ('yaml',))):
        return False
    count = record.phases.count('verified')
    return (0 < count <= registry
            and record.phases == ('verified',) * count + ('pending',) * (len(record.phases) - count))


def acknowledged_powercalc_prefix_receipts(chain, record):
    """Verify durable handoff and registry receipts; never admit an inverse."""
    from .native_registry_receipts import RegistryReceiptChain, _event_id
    if (type(chain) is not RegistryReceiptChain or not acknowledged_powercalc_registry_prefix(record)
            or chain.plan != record.plan or chain.operation_id != record.operation_id
            or chain.writable or chain.active is not None):
        return False
    try:
        from .controlled_powercalc_composition import ControlledPowercalcComposition
        from .controlled_powercalc_setup_receipts import validate_handoff
        from .native_template_yaml_server import _controlled_powercalc_plan
        baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
        if (baseline != chain.baseline or type(composition) is not ControlledPowercalcComposition
                or not _controlled_powercalc_plan(chain.plan, composition)):
            return False
        state = chain.reconstruct_inputs()
        count = record.phases.count('verified')
        if chain._setup_positions.get(state.head) != {index: 'forward' for index in range(count)}:
            return False
        key = _event_id(['controlled_pc_population', chain.root_id, 'forward', 'receipt'])
        stored = chain.store.read(key)
        if stored is None:
            return False
        handoff = validate_handoff(chain, [key, stored['digest']], 'forward')
        # Older receipts did not preserve the actual pre-handoff PSS map.
        return handoff['schema'] in (3, 4)
    except Exception:
        return False


def unstarted_rollback_complete(chain, record):
    """Coordinator leaves genuinely unstarted steps pending after compensation.

    Only exact selective Template and Script-prefix profiles have admission. Matching
    full receipts are still mandatory in each caller's actual chain check.
    """
    from .transaction import Record
    from .native_registry_receipts import RegistryReceiptChain
    from .native_template_state_yaml import (PreparedTemplateStateYAML,
        PreparedMixedTemplateStateYAML, assert_state_yaml_composition)
    from .native_template_state_composition import TemplateStateComposition
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    if (type(chain) is RegistryReceiptChain and type(record) is Record and record.direction == 'rollback'
            and record.status in ('rolling_back', 'rolled_back') and _registry_only_shape(record)
            and record.operation_id == chain.operation_id and _registry_only_bound(chain, record)):
        count = record.phases.count('undone')
        return (0 < count < len(record.phases)
                and record.phases == ('undone',) * count + ('pending',) * (len(record.phases) - count))
    if (type(chain) is not RegistryReceiptChain or type(record) is not Record
            or record.plan != chain.plan or record.operation_id != chain.operation_id
            or record.direction != 'rollback' or record.status not in ('rolling_back', 'rolled_back')
            or len(record.phases) != len(record.plan.steps) or len(record.plan.steps) < 2
            or record.plan.required_scopes != ('registry', 'yaml')
            or tuple(step.adapter for step in record.plan.steps)
               != ('registry',) * (len(record.plan.steps) - 1) + ('yaml',)
            or any(step.caused_by for step in record.plan.steps)
            or chain.yaml_index != len(record.plan.steps) - 1):
        return False
    count = record.phases.count('undone')
    if not (0 < count < len(record.phases)
            and record.phases == ('undone',) * count + ('pending',) * (len(record.phases) - count)):
        return False
    change = chain._original_change(chain.yaml_index)
    if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML):
        return False
    return (type(change), type(assert_state_yaml_composition(chain, change))) in (
        (PreparedTemplateStateYAML, TemplateStateComposition),
        (PreparedMixedTemplateStateYAML, ScriptTemplateStateComposition),
        (PreparedMixedTemplateStateYAML, AutomationTemplateStateComposition))


def complete_forward_unknown(record):
    """Snapshot shape only; never evidence that an UNKNOWN operation succeeded."""
    from .transaction import Record
    if (type(record) is Record and record.direction == 'forward'
            and record.status == 'unknown' and record.reason == 'final_verification_uncertain'
            and _registry_only_shape(record)
            and record.phases == ('verified',) * len(record.plan.steps)):
        return True
    return (type(record) is Record and record.direction == 'forward'
            and record.status == 'unknown' and record.reason == 'final_verification_uncertain'
            and len(record.plan.steps) >= 2
            and record.phases == ('verified',) * len(record.plan.steps)
            and record.plan.required_scopes == record.plan.covered_scopes
                == record.plan.verification_adapters == ('registry', 'yaml')
            and tuple(step.adapter for step in record.plan.steps)
                == ('registry',) * (len(record.plan.steps) - 1) + ('yaml',)
            and not any(step.caused_by for step in record.plan.steps))


def complete_forward_unknown_scope(chain, record):
    """Exact existing selective profiles only; caller still proves all native IO.

    Original preparation/composition binding is mandatory even for the candidate
    verifier's phase gate. Partial chains and outstanding intent refuse in the
    caller's independent reconstruct/expected checks, never through replay.
    """
    from .native_registry_receipts import RegistryReceiptChain
    from .native_template_state_yaml import (PreparedTemplateStateYAML,
        PreparedMixedTemplateStateYAML, assert_state_yaml_composition)
    from .native_template_state_composition import TemplateStateComposition
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    if (type(chain) is RegistryReceiptChain and complete_forward_unknown(record)
            and _registry_only_shape(record)):
        return (record.operation_id == chain.operation_id and _registry_only_bound(chain, record))
    if (not complete_forward_unknown(record) or type(chain) is not RegistryReceiptChain
            or record.plan != chain.plan or record.operation_id != chain.operation_id
            or chain.yaml_index != len(record.plan.steps) - 1):
        return False
    change = chain._original_change(chain.yaml_index)
    if type(change) not in (PreparedTemplateStateYAML, PreparedMixedTemplateStateYAML):
        return False
    return (type(change), type(assert_state_yaml_composition(chain, change))) in (
        (PreparedTemplateStateYAML, TemplateStateComposition),
        (PreparedMixedTemplateStateYAML, ScriptTemplateStateComposition),
        (PreparedMixedTemplateStateYAML, AutomationTemplateStateComposition))
