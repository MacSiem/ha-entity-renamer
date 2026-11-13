"""Bounded native Lovelace schema planner (frontend 20260930.4).

Markers are BLOCKERS, including unknown/custom semantics without a literal hit.
This module never claims that arbitrary card JavaScript is refactorable.
"""
from __future__ import annotations

from dataclasses import replace
import re

from .reference_planner import Consumer, Context, Marker, Scope, Syntax, Change, plan_references
from .dashboard_custom import plan_button_expression

PASSIVE = frozenset('''type title name description label icon theme color unit attribute
path url url_path navigation_path image alt_text state_image style aspect_ratio
show_name show_icon show_state state_color show_header_toggle columns square
hours_to_show days_to_show period stat_type stat_types chart_type min max severity
needle segments graph detail limits show_names logarithmic_scale min_y_axis
max_y_axis fit_y_data hide_legend expand_legend energy_date_selection collection_key
camera_view fit_mode dark_mode_image dark_mode_filter show_entity_picture
show_current_as_primary show_current show_forecast forecast_type forecast_slots
secondary_info_attribute round_temperature hide_completed hide_create sort
hide_section_headers due_date_period time_format vertical features_position
hide_state state_content icon_height secondary_info action_name prefix suffix
show_last_changed text new_tab download view dashboard hide_if_unavailable
show_empty initial_view show_add_event add_event_style add_event_size
badges_position badges_wrap max_columns dense section_mode column_span
grid_options layout_options card_size subview back_path panel theme_mode
background visible header footer show_zone_radius default_zoom auto_fit fit_zones
dark_mode show_all map_style scale_ruler cluster label_mode focus
allow_open_top_navigation allow disable_sandbox hide_background text_only
show_camera display_type sensor_classes alert_classes area
'''.split())
# Restrict discovery to these native types. Unknown types cannot inherit native
# semantics just because their property happens to be named "entity".
CARDS = frozenset('''alarm-panel button entity entities glance gauge light sensor
media-control thermostat humidifier weather-forecast plant-status history-graph
statistics-graph statistic calendar logbook map markdown picture picture-entity
picture-glance picture-elements horizontal-stack vertical-stack grid conditional
entity-filter tile todo-list iframe area heading
'''.split())
ROWS = frozenset('''attribute button buttons conditional divider section weblink
text cast call-service perform-action simple-entity'''.split())
ELEMENTS = frozenset({'state-badge', 'state-icon', 'state-label', 'image', 'icon', 'conditional'})
ACTIONS = frozenset({'tap_action', 'hold_action', 'double_tap_action',
                     'icon_tap_action', 'icon_hold_action', 'icon_double_tap_action',
                     'image_tap_action'})
SINGLE_ENTITY = frozenset('''alarm-panel button entity gauge light sensor media-control
thermostat humidifier weather-forecast plant-status picture-entity tile todo-list
'''.split())
ENTITY_LIST = frozenset({'entities', 'glance', 'history-graph', 'statistics-graph',
                         'statistic', 'calendar', 'logbook', 'map', 'picture-glance', 'entity-filter'})
_ID = re.compile(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z')


def plan_dashboard(config, mapping):
    """Plan only known semantic leaves; leave labels/URLs byte-for-byte intact.

    The reference engine validates/clones the entire input first, before schema
    discovery, so adversarial cycles/depth/types fail without recursive hangs.
    Full HA schema validation remains a separate prerequisite at integration.
    """
    clean = plan_references(config, mapping, Context(Consumer.DASHBOARD, ())).value
    if type(clean) is not dict:
        raise ValueError('invalid_dashboard')
    scopes, markers, expressions = [], [], []

    def block(path, code='unsupported_dashboard_schema'):
        markers.append(Marker(path, code, 'Dashboard coverage requires a semantic adapter'))

    def ref(value, path, many=False, statistic=False):
        values = value if many and type(value) is list else [value]
        if not values:
            return
        for i, item in enumerate(values):
            p = path + (i,) if many and type(value) is list else path
            # External statistic IDs remain untouched, never infer a new ID.
            if type(item) is not str or not (_ID.fullmatch(item) or
                    (statistic and ':' in item and '{{' not in item)):
                block(p, 'dynamic_dashboard_reference')
            else:
                scopes.append(Scope(p, Syntax.ENTITY_TREE))

    def target(value, path):
        if type(value) is not dict:
            block(path); return
        for key, child in value.items():
            if key == 'entity_id':
                ref(child, path + (key,), many=True)
            elif key not in {'device_id', 'area_id', 'floor_id', 'label_id'}:
                block(path + (key,))

    def action(value, path):
        if type(value) is not dict:
            block(path); return
        kind = value.get('action')
        if kind not in {'none', 'toggle', 'more-info', 'navigate', 'url', 'assist',
                        'call-service', 'perform-action'}:
            block(path, 'custom_dashboard_action'); return
        allowed = {'action', 'confirmation'}
        if kind == 'more-info': allowed |= {'entity'}
        if kind == 'navigate': allowed |= {'navigation_path', 'navigation_replace'}
        if kind == 'url': allowed |= {'url_path'}
        if kind == 'assist': allowed |= {'pipeline_id', 'start_listening'}
        if kind in {'call-service', 'perform-action'}:
            allowed |= {'service', 'perform_action', 'target', 'data', 'service_data'}
        for key, child in value.items():
            p = path + (key,)
            if key not in allowed: block(p)
            elif key == 'entity': ref(child, p)
            elif key == 'target': target(child, p)
            elif key in {'data', 'service_data'}:
                # Service data is domain-specific. Only the legacy entity_id
                # target has generic semantics; all other keys need an adapter.
                if type(child) is not dict: block(p); continue
                for field, item in child.items():
                    if field == 'entity_id': ref(item, p + (field,), many=True)
                    else: block(p + (field,), 'unknown_service_data')

    def conditions(value, path):
        if type(value) is list:
            for i, child in enumerate(value): conditions(child, path + (i,))
            return
        if type(value) is not dict:
            block(path); return
        kind = value.get('condition', 'state')
        allowed = {
            'state': {'entity', 'entity_id', 'state', 'state_not', 'attribute'},
            'numeric_state': {'entity', 'entity_id', 'above', 'below', 'attribute'},
            'and': {'conditions'}, 'or': {'conditions'}, 'not': {'conditions'},
            'screen': {'media_query'}, 'user': {'users'},
            'time': {'after', 'before', 'weekdays'},
            'view_columns': {'min', 'max'}, 'location': {'locations'},
        }.get(kind)
        if allowed is None:
            block(path, 'unsupported_dashboard_condition'); return
        for key, child in value.items():
            p = path + (key,)
            if key == 'condition': continue
            if key not in allowed: block(p)
            elif key in {'entity', 'entity_id'}: ref(child, p, many=True)
            elif key == 'conditions': conditions(child, p)
            elif key in {'state', 'state_not', 'above', 'below'}:
                # Lovelace resolves complete entity IDs in comparisons. Plain
                # state strings and numeric bounds are preserved.
                if type(child) is str and _ID.fullmatch(child): ref(child, p)
                elif type(child) is list:
                    for i, item in enumerate(child):
                        if type(item) is str and _ID.fullmatch(item): ref(item, p + (i,))
                elif type(child) not in (str, int, float): block(p)

    def sequence(value, path, role, **kwargs):
        if type(value) is not list:
            block(path); return
        for i, child in enumerate(value): node(child, path + (i,), role, **kwargs)

    def custom(value, path, kind):
        # Explicitly owned component slots; unrecognized options are refused.
        passive = PASSIVE | {'styles', 'show_label', 'show_entity_picture', 'size',
                             'show_icon', 'show_name', 'show_state', 'button_type',
                             'card_type', 'scrolling_effect', 'show_attribute'}
        for key, child in value.items():
            p = path + (key,)
            if key == 'type': continue
            if key == 'entity': ref(child, p)
            elif key == 'triggers_update' and kind == 'custom:button-card': ref(child, p, many=True)
            elif key == 'variables' and kind == 'custom:button-card':
                if type(child) is not dict: block(p); continue
                # Only source-proven variables whose values are reference trees.
                for field, item in child.items():
                    q = p + (field,)
                    if field not in {'entity_ids', 'floor_entity_ids', 'bound_entity',
                                     'source_entities', 'cluster_bindings'}:
                        block(q, 'unsupported_button_variable'); continue
                    variable_refs(item, q)
            elif key == 'label' and kind == 'custom:button-card' and type(child) is str and child.startswith('[[['):
                expressions.append((p, child))
            elif key in ACTIONS: action(child, p)
            elif key in {'conditions', 'visibility'}: conditions(child, p)
            elif key == 'sub_button' and kind == 'custom:bubble-card':
                if type(child) is not list: block(p); continue
                for i, button in enumerate(child):
                    if type(button) is not dict: block(p + (i,)); continue
                    for field, item in button.items():
                        q = p + (i, field)
                        if field == 'entity': ref(item, q)
                        elif field in {'conditions', 'visibility'}: conditions(item, q)
                        elif field in ACTIONS: action(item, q)
                        elif field not in passive: block(q)
            elif key == 'custom_fields' and kind == 'custom:button-card':
                if type(child) is not dict: block(p); continue
                for field, item in child.items():
                    q = p + (field,)
                    if type(item) is dict and set(item) == {'card'}: node(item['card'], q + ('card',), 'card')
                    else: block(q, 'unsupported_button_custom_field')
            elif key == 'cards' and kind == 'custom:button-card': sequence(child, p, 'card')
            elif key not in passive: block(p)

    def variable_refs(value, path):
        if type(value) is str:
            ref(value, path)
        elif type(value) is list:
            for i, item in enumerate(value): variable_refs(item, path + (i,))
        elif type(value) is dict:
            for key, item in value.items(): variable_refs(item, path + (key,))
        else: block(path, 'unsupported_button_variable')

    def node(value, path, role, statistic=False):
        if type(value) is str and role in {'row', 'badge', 'graph'}:
            ref(value, path, statistic=statistic); return
        if type(value) is not dict:
            block(path); return
        kind = value.get('type')
        if role == 'card' and kind == 'custom:ha-entity-renamer':
            # Our card reads registry/state afresh; these config fields only
            # control presentation (HAEntityRenamer.setConfig/render). Never
            # extend this admission to arbitrary custom cards or unknown keys.
            tabs = {'devices', 'automatic', 'queue', 'log'}
            for key, child in value.items():
                p = path + (key,)
                if key == 'type':
                    continue
                if key in {'title', 'language'} and type(child) is str:
                    continue
                if key in {'show_help', 'show_support'} and type(child) is bool:
                    continue
                if key == 'default_tab' and type(child) is str and child in tabs:
                    continue
                if key == 'tabs' and type(child) is list and all(
                        type(tab) is str and tab in tabs for tab in child):
                    continue
                if key == 'visibility':
                    conditions(child, p)
                    continue
                if key == 'grid_options' and type(child) is dict and all(
                        field in {'columns', 'rows', 'min_columns', 'max_columns', 'min_rows', 'max_rows'}
                        and (type(size) is int and size > 0
                             or field == 'columns' and size == 'full'
                             or field == 'rows' and size == 'auto')
                        for field, size in child.items()):
                    continue
                block(p, 'unsupported_renamer_card_config')
            return
        if role in {'card', 'element'} and kind in {'custom:button-card', 'custom:bubble-card'}:
            custom(value, path, kind)
            return
        known = (role in {'root', 'view', 'section'} or
                 role == 'card' and kind in CARDS or
                 role in {'row', 'graph'} and (kind is None or kind in ROWS) or
                 role == 'element' and kind in ELEMENTS or
                 role == 'badge' and kind in {None, 'entity'})
        if not known:
            block(path, 'custom_or_unknown_dashboard_component'); return
        for key, child in value.items():
            p = path + (key,)
            if key == 'strategy': block(p, 'dynamic_dashboard_strategy')
            elif key == 'views' and role == 'root': sequence(child, p, 'view')
            elif key == 'sections' and role == 'view': sequence(child, p, 'section')
            elif key == 'cards' and (role in {'view', 'section'} or kind in {'grid', 'horizontal-stack', 'vertical-stack'}):
                sequence(child, p, 'card')
            elif key == 'card' and kind in {'conditional', 'entity-filter'}:
                node(child, p, 'card')
            elif key == 'row' and kind == 'conditional': node(child, p, 'row')
            elif key == 'badges' and role == 'view': sequence(child, p, 'badge')
            elif key == 'elements' and kind in {'picture-elements', 'conditional'}:
                sequence(child, p, 'element')
            elif key == 'entity' and (role in {'row', 'badge', 'element', 'graph'} or kind in SINGLE_ENTITY or kind in {'picture-elements', 'picture-glance'}):
                ref(child, p, statistic=statistic)
            elif key == 'entities' and (kind in ENTITY_LIST or kind == 'buttons'):
                sequence(child, p, 'row', statistic=kind in {'statistics-graph', 'statistic'})
            elif key == 'target' and kind == 'logbook': target(child, p)
            elif key in {'image_entity', 'camera_image'} and kind in {'picture', 'picture-entity', 'picture-glance', 'picture-elements'}:
                ref(child, p)
            elif key == 'exclude_entities' and kind == 'area': ref(child, p, many=True)
            elif key == 'entity_ids' and kind == 'markdown': ref(child, p, many=True)
            elif key == 'content' and kind == 'markdown':
                if type(child) is not str: block(p)
                else: scopes.append(Scope(p, Syntax.JINJA))
            elif key in ACTIONS: action(child, p)
            elif key in {'conditions', 'visibility'}: conditions(child, p)
            elif key == 'features':
                # Features have their own extensible schemas; never traverse
                # arbitrary feature data as if it were reference-only.
                if child: block(p, 'unsupported_dashboard_features')
            elif key in {'header', 'footer'}:
                # Includes action-bearing header/footer types.
                block(p, 'unsupported_dashboard_header_footer')
            elif key not in PASSIVE: block(p)
    node(clean, (), 'root')
    result = plan_references(clean, mapping, Context(Consumer.DASHBOARD, tuple(scopes)))
    extra_changes = []
    for path, original in expressions:
        updated, error = plan_button_expression(original, mapping)
        if error:
            markers.append(Marker(path, error, 'Button JavaScript requires a bounded static expression'))
        elif updated != original:
            target = result.value
            for part in path[:-1]: target = target[part]
            target[path[-1]] = updated
            extra_changes.append(Change(path, 'value', original, updated))
    return replace(result, changes=result.changes + tuple(extra_changes), markers=tuple(m for m in result.markers
                   if m.code != 'no_supported_scopes') + tuple(markers),
                   coverage='native_lovelace_known_schema_frontend_20260930.4')
