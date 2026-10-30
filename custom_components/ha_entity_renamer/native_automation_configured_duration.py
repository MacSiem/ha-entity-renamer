"""Private configured scalar edge. One config journal, no occurrence edits.

Fingerprints use explicit edge projection to immutable origin leaves. Every
selected leaf is independently checked against its approved full Template graph;
this never masks an object globally or adopts an observed post-write image.
"""
from dataclasses import dataclass
from .native_automation_state_binding import _need, _value
from .native_script_config_transport import _graph


@dataclass(frozen=True)
class ConfiguredImage:
    template: object
    graph: tuple
    pins: tuple


class ConfiguredOwner:
    __slots__ = ('binding', 'origin', 'cell', 'config_cell', 'config', 'snapshot', 'snapshot_items', 'image')

    def __init__(self, binding):
        from .native_automation_duration_binding import _cell
        self.binding = binding
        self.config_cell = _cell(binding.listener, 'config')
        self.cell, template, graph, _, _ = binding._duration_scalar_image
        self.origin = template
        self.config, self.snapshot = binding.config, binding.listener_cells
        self.image = ConfiguredImage(template, graph, ((template, graph),))
        self.snapshot_items = tuple(self.snapshot.items())
        binding._duration.configured_owners[id(binding)] = self
        binding._duration.configured_images[self] = self.image

    def check(self, view=None):
        from .native_automation_duration_binding import _cell
        b = self.binding
        _need(type(self) is ConfiguredOwner
              and b._duration.configured_owners.get(id(b)) is self
              and getattr(b, '_duration_config_owner', None) is self
              and self.origin is b._duration_scalar_image[1]
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
        _need(type(image) is ConfiguredImage
              and all(_graph(value) == graph for value, graph in image.pins)
              and _graph(image.template) == image.graph,
              'duration_configured_template_graph_changed')
        if view is None:
            _need(self.cell.cell_contents is image.template
                  and self.config['for'] is image.template
                  and self.snapshot['time_delta'] is image.template,
                  'duration_configured_edge_changed')
        return image

    def cell_value(self, cell, view=None):
        # Only this exact native shared cell is a declared edit path.
        if cell is self.cell:
            self.check(view)
            return self.origin
        return cell.cell_contents

    def entry_value(self, table, key, value, view=None):
        if (table is self.config and key == 'for'
                or table is self.snapshot and key == 'time_delta'):
            self.check(view)
            return self.origin
        return value


class ConfiguredRollbackView:
    """Read-only BEFORE projection after the complete journal prefix validates.

    Never installed on a binding. A partial synchronous config prefix is not a
    live accepted state; this view exists solely during global undo planning.
    """
    def __init__(self, edges):
        self.images = {id(owner): (owner, before) for edge in edges
                       for owner, before, _ in edge.owners}

    def image(self, owner):
        if id(owner) not in self.images:
            # Unchanged scalar route: its real approved cfg/cell/snapshot edge
            # must still validate normally, without any projection allowance.
            return owner.check()
        _need(self.images[id(owner)][0] is owner, 'duration_configured_rollback_owner_unknown')
        return self.images[id(owner)][1]


@dataclass(frozen=True)
class ConfiguredEdge:
    binding: object
    before: object
    after: object
    owners: tuple
    pins: tuple

    @classmethod
    def capture(cls, binding, after, pending):
        from .native_automation_duration_binding import _OwnDuration
        b = binding
        owner = b._duration_config_owner
        owner.check()
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
        graph = _graph(after)
        owners = []
        pins = []
        for old in bindings:
            holder = old._duration_config_owner
            before = holder.check()
            _need(before.template is owner.image.template,
                  'duration_configured_own_side_changed')
            retained = before.pins
            if all(value is not after for value, _ in retained):
                retained = (*retained, (after, graph))
            target = ConfiguredImage(after, graph, retained)
            owners.append((holder, before, target))
            for value, expected in retained:
                if all(value is not previous for previous, _ in pins):
                    pins.append((value, expected))
        return cls(b, owner.image.template, after, tuple(owners), tuple(pins))

    def check_pins(self):
        _need(all(_graph(value) == graph for value, graph in self.pins),
              'duration_configured_retained_template_changed')

    def retain(self, log):
        self.check_pins()
        log.retain('cell_contents', self.binding._duration_config_owner.cell)
        for owner, _, _ in self.owners:
            log.retain('entry', (owner.snapshot, 'time_delta'))
            log.retain('image', owner)
            log.retain('entry', (owner.binding._duration.configured_images, owner))

    def install(self, log, written):
        self.check_pins()
        cell = self.binding._duration_config_owner.cell
        slot = ('cell_contents', id(cell))
        if slot not in written:
            log.write('cell_contents', cell, self.after,
                      lambda: setattr(cell, 'cell_contents', self.after))
            written.add(slot)
        for owner, before, after in self.owners:
            slot = ('snapshot', id(owner.snapshot))
            if slot not in written:
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
