"""Exact private codec for Template YAML with HomeKit/Utility evidence.

External domains remain marked unsupported. Decoding these immutable bytes is
not authorization to mutate configuration or native consumer lifecycles.
"""
from dataclasses import asdict, dataclass
from .native_template_state_yaml import _identity_payload
from .yaml_transaction import PreparedYAML, _decode_yaml, _digest, _sha


@dataclass(frozen=True)
class PreparedExternalStateYAML(PreparedYAML):
    source_program_sha256: str

    @property
    def identity(self):
        return 'template_external_state_yaml:' + _digest(_identity_payload(self))

    def checked(self):
        if decode_external_state_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_external_state_yaml')
        return self


def decode_external_state_yaml(data):
    if type(data) is not dict or set(data) != {'receipt', 'files', 'source_program_sha256'}:
        raise ValueError('invalid_prepared_external_state_yaml')
    program = _sha(data['source_program_sha256'])
    base = _decode_yaml({key:data[key] for key in ('receipt', 'files')},
                        external_state_mode=True, dependent_owner_mode=True)
    return PreparedExternalStateYAML(base.receipt, base.files, program)


def capture_external_state_yaml(bundle, plan, mapping):
    from .native_template_state_yaml import _capture_template_state_yaml
    return _capture_template_state_yaml(bundle, plan, mapping, PreparedExternalStateYAML)
