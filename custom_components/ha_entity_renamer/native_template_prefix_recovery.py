"""Private phase/scope predicates only; never a native admission capability.

Every consumer must separately verify actual full inputs, runtime, history and
chain receipts. These predicates neither change phases nor permit intent replay.
"""


def acknowledged_registry_prefix(record):
    from .transaction import Record
    if (type(record) is not Record or record.direction != 'forward'
            or record.status not in ('applying', 'unknown', 'ready')
            or len(record.plan.steps) < 2 or len(record.phases) != len(record.plan.steps)
            or tuple(step.adapter for step in record.plan.steps)
               != ('registry',) * (len(record.plan.steps) - 1) + ('yaml',)):
        return False
    count = record.phases.count('verified')
    return (0 < count < len(record.phases)
            and record.phases == ('verified',) * count + ('pending',) * (len(record.phases) - count))


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
        (PreparedMixedTemplateStateYAML, ScriptTemplateStateComposition))
