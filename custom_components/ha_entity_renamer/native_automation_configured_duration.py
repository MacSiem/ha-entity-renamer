"""Private exact configured-duration edges, owned by the one config journal.

Origin is an immutable canonical terminal, never a mutable object traversed
again. All side images, immutable leaf pins and authorized paths precede writes.
"""
from dataclasses import dataclass
from .native_automation_state_binding import _need, _value
from .native_script_config_transport import _graph


def _frozen(value):
    """Freeze the already typed normal form, without coercing native input."""
    if type(value) is dict:
        return tuple((_frozen(key), _frozen(child)) for key, child in value.items())
    if type(value) is list:
        return tuple(_frozen(child) for child in value)
    return type(value), value


def _template_pin(value):
    # Compiled/render caches may advance; configured body, hass and source-mode
    # metadata do not. Every detached original/replacement remains retained.
    _need(all(getattr(value, name) is None or getattr(value, name) is False
              for name in ('_limited', '_strict')),
          'duration_configured_template_source_mode_changed')
    return _graph(value), _value(value._log_fn)


def _pins(root):
    from .native_automation_scalar_duration import configured_templates
    result = []
    for value in configured_templates(root):
        if all(value is not old for old, _ in result):
            result.append((value, _template_pin(value)))
    return tuple(result)


@dataclass(frozen=True)
class ConfiguredCanonical:
    root_id: int
    graph: tuple
    normal: tuple


@dataclass(frozen=True)
class ConfiguredImage:
    template: object  # historical name: the complete admitted root, also dict
    graph: tuple
    pins: tuple


class ConfiguredOwner:
    __slots__ = ('binding', 'origin', 'canonical', 'cell', 'config_cell', 'config',
                 'snapshot', 'snapshot_items', 'image')

    def __init__(self, binding):
        from .native_automation_duration_binding import _cell
        from .yaml_validation_worker import _normal
        self.binding = binding
        self.config_cell = _cell(binding.listener, 'config')
        self.cell, root, graph, _, _ = binding._duration_scalar_image
        self.origin = root
        self.canonical = ConfiguredCanonical(id(root), graph, _frozen(_normal(root)))
        self.config, self.snapshot = binding.config, binding.listener_cells
        self.image = ConfiguredImage(root, graph, _pins(root))
        self.snapshot_items = tuple(self.snapshot.items())
        object.__setattr__(binding, '_duration_origin', self.canonical)
        binding._duration.configured_owners[id(binding)] = self
        binding._duration.configured_images[self] = self.image

    def check(self, view=None):
        from .native_automation_duration_binding import _cell
        b = self.binding
        _need(type(self) is ConfiguredOwner
              and b._duration.configured_owners.get(id(b)) is self
              and getattr(b, '_duration_config_owner', None) is self
              and self.origin is b._duration_scalar_image[1]
              and self.canonical is b._duration_origin
              and self.canonical.root_id == id(self.origin)
              and self.canonical.graph == b._duration_scalar_image[2]
              and self.cell is b._duration_scalar_image[0]
              and self.config is b.config and self.snapshot is b.listener_cells
              and _cell(b.listener, 'time_delta') is self.cell
              and _cell(b.listener, 'config') is self.config_cell
              and self.config_cell.cell_contents is self.config,
              'duration_configured_owner_changed')
        _need(tuple(self.snapshot) == tuple(key for key, _ in self.snapshot_items)
              and all(key == 'time_delta' or self.snapshot[key] is value
                      for key, value in self.snapshot_items),
              'duration_configured_snapshot_sibling_changed')
        image = self.image if view is None else view.image(self)
        if view is None:
            _need(b._duration.configured_images.get(self) is image,
                  'duration_configured_unapproved_image')
        slots = None if view is None else view.slots
        _need(type(image) is ConfiguredImage
              and all(_template_pin(value) == graph for value, graph in image.pins)
              and _graph(image.template, slots=slots) == image.graph,
              'duration_configured_template_graph_changed')
        cell = self.cell.cell_contents if view is None else view.read('cell_contents', self.cell)
        cfg = self.config['for'] if view is None else view.entry(self.config, 'for')
        snapshot = self.snapshot['time_delta'] if view is None else view.entry(self.snapshot, 'time_delta')
        _need(cell is image.template and cfg is image.template and snapshot is image.template,
              'duration_configured_edge_changed')
        return image

    def cell_value(self, cell, view=None):
        # Only the real native shared time_delta cell is an authorized terminal.
        if cell is self.cell:
            self.check(view)
            return self.canonical
        return cell.cell_contents

    def is_entry(self, table, key):
        return (table is self.config and key == 'for'
                or table is self.snapshot and key == 'time_delta')

    def entry_value(self, table, key, value, view=None):
        if self.is_entry(table, key):
            self.check(view)
            return self.canonical
        return value


class ConfiguredRollbackView:
    """Frozen BEFORE selection using the shadow produced by the whole log plan.

    Construct only after log.plan(shadow) accepted every actual prefix slot.
    No live baseline is observed/adopted and no synthetic writes are performed.
    """
    def __init__(self, edges, shadow):
        self.images = {id(owner): (owner, before) for edge in edges
                       for owner, before, _ in edge.owners}
        self.shadow = dict(shadow)
        self.slots = {key[1]: value for key, value in shadow.items()
                      if key[0] in ('dict', 'list')}

    def read(self, kind, owner):
        from .native_automation_duration_binding import _PreEffectLog
        key = ((kind, id(owner[0]), id(owner[1])) if kind == 'entry'
               else (kind, id(owner)))
        return self.shadow[key] if key in self.shadow else _PreEffectLog.read(kind, owner)

    def entry(self, table, key):
        slot = ('entry', id(table), id(key))
        if slot in self.shadow:
            return self.shadow[slot]
        return next(value for name, value in self.read('dict', table) if name == key)

    def image(self, owner):
        if id(owner) not in self.images:
            return owner.check()  # unchanged route retains its actual whole proof
        _need(self.images[id(owner)][0] is owner, 'duration_configured_rollback_owner_unknown')
        return self.images[id(owner)][1]


def _authorized(binding, after, before_normal, after_normal, operations, projected):
    """Proof of each exact shallow slot; a slots view never grants authority."""
    from homeassistant.helpers.template import Template
    from .native_automation_scalar_duration import configured_duration
    from .yaml_validation_worker import _normal
    root = binding.config['for']
    configured_duration({'for': root}, binding.hass)
    _need(_frozen(_normal(root)) == _frozen(before_normal),
          'duration_configured_program_before_changed')
    cached = projected.get(id(root))
    _need(cached is not None and _frozen(cached[0]) == _frozen(before_normal)
          and _frozen(cached[1]) == _frozen(after_normal) and cached[2] is after,
          'duration_configured_projector_proof_missing')
    prefix = (id(binding.entity), binding.index, id(binding.config), 'for', id(root))
    if type(root) is Template:
        _need(type(after) is Template and _frozen(_normal(after)) == _frozen(after_normal),
              'duration_configured_projected_template_changed')
        return (), ((*prefix, (), id(root), _frozen(before_normal),
                     _frozen(after_normal), id(after)),)
    _need(type(root) is dict and after is root, 'duration_configured_container_replaced')
    matches = [operation for operation in operations if operation[1] is root]
    _need(len(matches) == 1 and matches[0][0] == 'dict',
          'duration_configured_container_operation_missing')
    _, _, before, target = matches[0]
    _need(tuple((id(k), id(v)) for k, v in root.items())
          == tuple((id(k), id(v)) for k, v in before)
          and len(before) == len(target), 'duration_configured_container_before_changed')
    paths = []
    for ordinal, ((key, old), (new_key, new)) in enumerate(zip(before, target, strict=True)):
        _need(key is new_key, 'duration_configured_unit_key_changed')
        a, z = _normal(old), _normal(new)
        if _frozen(a) != _frozen(z):
            record = projected.get(id(old))
            _need(type(old) is Template and type(new) is Template and record is not None
                  and _frozen(record[0]) == _frozen(a)
                  and _frozen(record[1]) == _frozen(z) and record[2] is new,
                  'duration_configured_leaf_projector_proof_missing')
        else:
            _need(old is new, 'duration_configured_unchanged_leaf_replaced')
        # Include every alternate occurrence of a shared Template, not just ids.
        if type(old) is Template:
            paths.append((*prefix, ((id(root), ordinal, id(key), key, 'value'),),
                          id(old), _frozen(a), _frozen(z), id(new)))
    _need(_frozen({'$map': [[key, _normal(value)] for key, value in target]})
          == _frozen(after_normal),
          'duration_configured_program_after_changed')
    # Only this verified operation may contribute to the root's AFTER image.
    return ((id(root), target),), tuple(paths)


@dataclass(frozen=True)
class ConfiguredEdge:
    binding: object
    before: object
    after: object
    owners: tuple
    pins: tuple
    paths: tuple
    before_graph: tuple
    after_graph: tuple

    @classmethod
    def capture(cls, binding, after, pending, *, before_normal, after_normal,
                operations, projected):
        from .native_automation_duration_binding import _OwnDuration
        b = binding
        owner = b._duration_config_owner
        owner.check()
        local_slots, paths = _authorized(b, after, before_normal, after_normal, operations, projected)
        before_graph, after_graph = _graph(owner.image.template), _graph(after, slots=dict(local_slots))
        bindings = [b]
        for p in pending:
            own = p['own']
            if own is None:
                continue
            _need(type(own) is _OwnDuration, 'duration_configured_own_type')
            own.check()  # authenticated code, occurrence history and period
            old = own.binding
            _need(old.hass is b.hass and old.entity is b.entity and old.index == b.index
                  and old.config is b.config and old.listener is b.listener
                  and old.action_job is b.action_job
                  and old.listener_cells['period'] is b.listener_cells['period']
                  and old._duration_scalar_image[0] is owner.cell,
                  'duration_configured_own_inventory_changed')
            if all(old is not prior for prior in bindings):
                bindings.append(old)
        # Pins derive from ORIGINAL and the projector's actual target leaves,
        # before any mutable container write, never from a post-write baseline.
        targets = (after,) if type(after) is not dict else tuple(value for _, value in dict(local_slots)[id(after)])
        from homeassistant.helpers.template import Template
        for value in targets:
            if type(value) is Template:
                b._duration.scalar.check_template(value)
        target_pins = tuple((value, _template_pin(value)) for value in targets if type(value) is Template)
        owners, pins = [], []
        for old in bindings:
            holder = old._duration_config_owner
            before = holder.check()
            _need(before.template is owner.image.template and before.graph == before_graph,
                  'duration_configured_own_side_changed')
            retained = list(before.pins)
            for value, graph in target_pins:
                if all(value is not prior for prior, _ in retained):
                    retained.append((value, graph))
            target = ConfiguredImage(after, after_graph, tuple(retained))
            owners.append((holder, before, target))
            for value, expected in retained:
                prior = next((graph for previous, graph in pins if value is previous), None)
                _need(prior is None or prior == expected, 'duration_configured_shared_leaf_pin_conflict')
                if prior is None:
                    pins.append((value, expected))
        return cls(b, owner.image.template, after, tuple(owners), tuple(pins), paths,
                   before_graph, after_graph)

    def check_pins(self):
        _need(all(_template_pin(value) == graph for value, graph in self.pins),
              'duration_configured_retained_template_changed')

    def retain(self, log):
        self.check_pins()
        # Same-root dict does not get an independent cell/snapshot undo. Its
        # nested operation is already in the one configuration journal.
        if self.before is not self.after:
            log.retain('cell_contents', self.binding._duration_config_owner.cell)
        for owner, _, _ in self.owners:
            if self.before is not self.after:
                log.retain('entry', (owner.snapshot, 'time_delta'))
            log.retain('image', owner)
            log.retain('entry', (owner.binding._duration.configured_images, owner))

    def install(self, log, written):
        self.check_pins()
        cell = self.binding._duration_config_owner.cell
        slot = ('cell_contents', id(cell))
        if self.before is not self.after and slot not in written:
            log.write('cell_contents', cell, self.after,
                      lambda: setattr(cell, 'cell_contents', self.after))
            written.add(slot)
        for owner, before, after in self.owners:
            slot = ('snapshot', id(owner.snapshot))
            if self.before is not self.after and slot not in written:
                log.write('entry', (owner.snapshot, 'time_delta'), self.after,
                          lambda: owner.snapshot.__setitem__('time_delta', self.after))
                written.add(slot)
            slot = ('holder', id(owner))
            if slot not in written:
                _need(owner.image is before, 'duration_configured_holder_before_changed')
                log.write('image', owner, after, lambda: setattr(owner, 'image', after))
                log.write('entry', (owner.binding._duration.configured_images, owner), after,
                          lambda: owner.binding._duration.configured_images.__setitem__(owner, after))
                written.add(slot)

    def check_after(self):
        self.check_pins()
        for owner, _, after in self.owners:
            _need(owner.image is after, 'duration_configured_holder_after_changed')
            owner.check()
