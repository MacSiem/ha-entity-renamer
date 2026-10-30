"""Private native Automation/Script prefix expectations, never effect authority.

Every image comes from the native schema on SAME ORIGINAL source bytes. This
finite program admits only the frozen registry order and its inverse, not an
arbitrary subset. It does not edit live objects or qualify running actions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

_DOMAINS = ('automation', 'script')


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _need(value, reason='dependent_config_program_rejected'):
    if not value:
        raise ValueError(reason) from None


def assert_reference_only(root, paths):
    """Refuse expanded-secret descriptors BEFORE native normalization/export."""
    from pathlib import Path
    import yaml
    from yaml.nodes import MappingNode, SequenceNode
    from .yaml_plan import _relative

    def visit(node, ancestry=()):
        if node is None or id(node) in ancestry:
            return
        _need(node.tag not in ('!secret', '!env_var'), 'dependent_config_program_secret_scope_unverified')
        ancestry += (id(node),)
        if isinstance(node, MappingNode):
            for key, value in node.value:
                visit(key, ancestry)
                visit(value, ancestry)
        elif isinstance(node, SequenceNode):
            for value in node.value:
                visit(value, ancestry)

    _need(type(paths) is tuple and bool(paths))
    for path in paths:
        for node in yaml.compose_all((Path(root) / _relative(path)).read_bytes()):
            visit(node)


async def native_prefix_images(before_root, mapping, proof, before, after, expected_runtime):
    """Worker-only: compile intermediate images from ORIGINAL, never cumulative."""
    from pathlib import Path
    import shutil
    import tempfile
    from .yaml_plan import plan_yaml

    order = tuple(sorted(mapping))
    paths = tuple(row[0] for row in proof['files'])
    _need(tuple(sorted(before)) == _DOMAINS and tuple(sorted(after)) == _DOMAINS)
    originals = {name: (Path(before_root) / name).read_bytes() for name in paths}
    images = [{'active': [], 'domains': [[d, before[d]] for d in _DOMAINS],
               'files': [[name, hashlib.sha256(originals[name]).hexdigest()] for name in paths]}]
    # One private copied bundle; each iteration resets EVERY planned source.
    # Native schema validation constructs no entities, actions or live services.
    with tempfile.TemporaryDirectory(prefix='dependent-prefix-', dir=Path(before_root).parent) as temporary:
        root = Path(temporary) / 'config'
        shutil.copytree(before_root, root)
        for count in range(1, len(order) + 1):
            active = order[:count]
            partial = plan_yaml(before_root, {old: mapping[old] for old in active})
            _need(partial.ready and tuple(sorted(row.path for row in partial.files)) == paths,
                  'dependent_config_prefix_plan_unqualified')
            changes = {patch.path: patch.proposed_bytes for patch in partial.patches}
            files = []
            for name in paths:
                content = changes.get(name, originals[name])
                (root / name).write_bytes(content)
                files.append([name, hashlib.sha256(content).hexdigest()])
            if count == len(order):
                _need(files == [[row[0], row[2]] for row in proof['files']])
                domains = after
            else:
                domains = {}
                await expected_runtime(str(root), _DOMAINS, dependent_descriptors=domains)
            _need(tuple(sorted(domains)) == _DOMAINS and all(domains[d] is not None for d in _DOMAINS),
                  'dependent_config_prefix_native_unqualified')
            images.append({'active': list(active), 'domains': [[d, domains[d]] for d in _DOMAINS],
                           'files': files})
    return {'profile': 'dependent_config_prefix_v1', 'proof': proof,
            'source_order': list(order), 'images': images}


@dataclass(frozen=True)
class DependentConfigProgram:
    receipt_json: bytes
    original_evidence_sha256: str
    original_graph_sha256: str
    mapping: tuple[tuple[str, str], ...]
    source_order: tuple[str, ...]
    descriptor_json: bytes
    definition_locations_json: bytes

    @property
    def migration_ready(self):
        return False

    @property
    def sha256(self):
        return _digest([self.receipt_json.decode(), self.original_evidence_sha256,
            self.original_graph_sha256, self.mapping, self.source_order,
            self.descriptor_json.decode(), self.definition_locations_json.decode()])

    def rows(self, active):
        _need(type(active) is frozenset and all(type(key) is str for key in active))
        for image in json.loads(self.descriptor_json)['images']:
            if frozenset(image['active']) == active:
                return tuple((domain, rows) for domain, rows in image['domains'])
        raise ValueError('dependent_config_prefix_unqualified')

    def assert_edge(self, before, after, source):
        _need(type(source) is str and source in self.source_order)
        self.rows(before)
        self.rows(after)
        _need(before ^ after == frozenset({source}) and abs(len(before) - len(after)) == 1,
              'dependent_config_edge_unqualified')

    def assert_sources(self, receipt, evidence, *, plan, mapping, source_order):
        _need(seal_dependent_config_program(receipt, evidence,
            json.loads(self.descriptor_json), plan=plan, mapping=mapping,
            source_order=source_order) == self, 'dependent_config_program_sources_changed')


def seal_dependent_config_program(receipt, evidence, descriptor, *, plan, mapping, source_order):
    """Bind private worker result to its original receipt/ledger and exact plan.

    This checks layout and binding, not provenance of arbitrary caller JSON.
    Only the trusted same-invocation producer may supply this adjunct; it is not
    a public decoder, a validation receipt or mutation grant.
    """
    from .native_input_baseline import _receipt_bytes
    from .yaml_validation import ValidationReceipt, assert_proposal_matches
    from .yaml_source_binding import OriginalSourceEvidence
    from .yaml_source_ledger import source_graph_digest
    from .yaml_plan import Plan, _mapping

    _need(type(receipt) is ValidationReceipt and type(evidence) is OriginalSourceEvidence
          and type(plan) is Plan and plan.ready and not evidence.ledger.findings)
    mapping = _mapping(mapping)
    _need(type(source_order) is tuple and source_order == tuple(sorted(mapping)) and bool(mapping))
    evidence.assert_receipt(receipt)
    assert_proposal_matches(receipt, plan)
    _need(evidence.ledger.original_graph_sha256 == source_graph_digest(plan))
    _need(type(descriptor) is dict and set(descriptor) == {'profile', 'proof', 'source_order', 'images'}
          and descriptor['profile'] == 'dependent_config_prefix_v1'
          and descriptor['source_order'] == list(source_order))
    proof = descriptor['proof']
    _need(type(proof) is dict and set(proof) == {'planner', 'mapping', 'files', 'variable_bindings_sha256'}
          and proof['planner'] == 'yaml_plan_source_reference_v1'
          and proof['mapping'] == [list(row) for row in sorted(mapping.items())])
    patches = {patch.path: patch.proposed_bytes for patch in plan.patches}
    _need(proof['files'] == [[row.path, row.sha256,
        hashlib.sha256(patches[row.path]).hexdigest() if row.path in patches else row.sha256]
        for row in sorted(plan.files, key=lambda row: row.path)])
    _need(proof['variable_bindings_sha256'] == _digest([
        [p.path, p.source_line, p.entity_id, p.producer_occurrences, p.yaml_occurrences,
         p.accounted_occurrences, list(p.consumer_counts), p.approved] for p in plan.variable_bindings]))
    expected = {d: (old, new) for d, old, new in receipt.runtime_expected}
    images = descriptor['images']
    _need(type(images) is list and len(images) == len(source_order) + 1)
    source_files = {row[0]: row[1:] for row in receipt.source_files}
    _need(all(source_files.get(row.path) == (row.sha256, row.device, row.inode) for row in plan.files))
    for index, image in enumerate(images):
        _need(type(image) is dict and set(image) == {'active', 'domains', 'files'}
              and image['active'] == list(source_order[:index])
              and type(image['domains']) is list and [row[0] for row in image['domains']] == list(_DOMAINS))
        _need(type(image['files']) is list and [row[0] for row in image['files']] == [row[0] for row in proof['files']]
              and all(type(row) is list and len(row) == 2 and type(row[1]) is str
                      and len(row[1]) == 64 and all(c in '0123456789abcdef' for c in row[1])
                      for row in image['files']))
        for domain, rows in image['domains']:
            _need(type(rows) is list and all(type(row) is list and len(row) == (9 if domain == 'automation' else 11)
                  and type(row[0]) is str for row in rows))
            keys = tuple(row[0] for row in rows)
            _need(keys == tuple(sorted(set(keys))))
            evidence.ledger.match_native_keys(domain, keys)
            if index == 0:
                native = next(row for row in evidence.domains if row.domain == domain)
                _need(_digest(rows) == native.projection
                      and tuple((row[0], _digest(row[1]), _digest(row)) for row in rows) == native.rows)
            if index in (0, len(source_order)):
                wanted = expected.get(domain, (evidence.domain_projection(domain),) * 2)[bool(index)]
                _need(_digest(rows) == wanted, 'dependent_config_endpoint_mismatch')
        if index == 0:
            _need(image['files'] == [[r[0], r[1]] for r in proof['files']])
        if index == len(source_order):
            _need(image['files'] == [[r[0], r[2]] for r in proof['files']])
    locations = _bytes([asdict(row) for row in evidence.ledger.definitions])
    return DependentConfigProgram(_receipt_bytes(receipt), evidence.sha256,
        source_graph_digest(plan), tuple(sorted(mapping.items())), source_order,
        _bytes(descriptor), locations)


def encode_dependent_config_program(program):
    """Private retention bytes; expected digest lives in the owning preparation."""
    _need(type(program) is DependentConfigProgram)
    return _bytes({'schema': 1, 'program_sha256': program.sha256,
                   'descriptor': json.loads(program.descriptor_json)})


def decode_dependent_config_program(payload, *, expected_sha256, receipt, evidence, plan, mapping, source_order):
    """Re-seal against ORIGINAL and independently pinned prepared digest."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _need(key not in value)
            value[key] = item
        return value
    _need(type(payload) is bytes and type(expected_sha256) is str)
    value = json.loads(payload, object_pairs_hook=unique)
    _need(type(value) is dict and set(value) == {'schema', 'program_sha256', 'descriptor'}
          and type(value['schema']) is int and value['schema'] == 1
          and value['program_sha256'] == expected_sha256)
    program = seal_dependent_config_program(receipt, evidence, value['descriptor'],
        plan=plan, mapping=mapping, source_order=source_order)
    _need(program.sha256 == expected_sha256, 'dependent_config_program_digest_changed')
    return program
