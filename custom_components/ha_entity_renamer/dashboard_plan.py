"""Bounded native Lovelace schema planner (frontend 20260930.4).

Markers are BLOCKERS, including unknown/custom semantics without a literal hit.
This module never claims that arbitrary card JavaScript is refactorable.
"""
from __future__ import annotations

from dataclasses import replace
import re

from .reference_planner import Consumer, Context, Marker, Scope, Syntax, Change, plan_references
from .dashboard_custom import plan_button_expression, unchanged_computed_target, unchanged_finite_jinja, unchanged_button_style, unchanged_bubble_style, unchanged_button_field

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
background visible header footer heading heading_style dense_section_placement top_margin refresh_interval show_zone_radius default_zoom auto_fit fit_zones
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


def _passive_template(value, mapping):
    # CSS/presentation and source-proven button templates cannot consume an
    # entity ID when neither selected IDs nor dynamic state/target access occur.
    if type(value) is dict:
        return all(type(k) is str and _passive_template(k, mapping) and _passive_template(v, mapping) for k, v in value.items())
    if type(value) is list:
        return all(_passive_template(v, mapping) for v in value)
    if type(value) is str:
        return '[[[' not in value and not any(identity in value for identity in (*mapping.keys(), *mapping.values())) and not re.search(
            r'\b(?:states|hass|callService|fire-dom-event)\s*[.(\[]|\{[%{]', value)
    return value is None or type(value) in (int, float, bool)



def _foreign_static(value, mapping):
    if type(value) is dict:
        return all(type(k) is str and _foreign_static(k, mapping) and _foreign_static(v, mapping) for k, v in value.items())
    if type(value) is list:
        return all(_foreign_static(v, mapping) for v in value)
    if type(value) is str:
        if any(identity in value for identity in (*mapping.keys(), *mapping.values())) or 'states[' in value or 'states.' in value or '[[[' in value:
            return False
        if '{{' in value or '{%' in value:
            plan = plan_references(value, mapping, Context(Consumer.DASHBOARD, (Scope((), Syntax.JINJA),)))
            return not plan.changes and not plan.markers
        return True
    return value is None or type(value) in (int, float, bool)


def _foreign_filter_safe(kind, card, mapping):
    import fnmatch
    if not _foreign_static(card, mapping): return False
    if kind == 'custom:auto-entities':
        filters = card.get('filter')
        return (type(filters) is dict and set(filters) <= {'include', 'exclude'}
                and filters.get('exclude', []) == [] and type(filters.get('include')) is list
                and all(type(item) is dict and set(item) == {'domain'} and type(item['domain']) is str
                        for item in filters['include'])
                and all(old.split('.')[0] == new.split('.')[0] for old, new in mapping.items()))
    if kind == 'custom:flex-table-card':
        entities = card.get('entities')
        if type(entities) is not dict or set(entities) != {'include'} or type(entities['include']) is not str: return False
        pattern = entities['include']
        return all(not fnmatch.fnmatchcase(old, pattern) and not fnmatch.fnmatchcase(new, pattern)
                   for old, new in mapping.items())
    if kind == 'custom:battery-state-card':
        filters = card.get('filter')
        if type(filters) is not dict or set(filters) - {'include', 'exclude'}: return False
        for group in filters.values():
            if type(group) is not list: return False
            for rule in group:
                if type(rule) is not dict or rule.get('name') not in {'entity_id', 'attributes.device_class', 'state'}: return False
                if rule.get('name') != 'entity_id': continue
                value, op = rule.get('value'), rule.get('operator')
                if type(value) is not str or op not in (None, 'contains'): return False
                try:
                    regex = re.compile(value)
                except re.error: return False
                for old, new in mapping.items():
                    if ((value in old) != (value in new) or fnmatch.fnmatchcase(old, value) != fnmatch.fnmatchcase(new, value)
                            or bool(regex.search(old)) != bool(regex.search(new))): return False
        return True
    return False


def _legacy_plan_dashboard(config, mapping):
    """Plan only known semantic leaves; leave labels/URLs byte-for-byte intact.

    The reference engine validates/clones the entire input first, before schema
    discovery, so adversarial cycles/depth/types fail without recursive hangs.
    Full HA schema validation remains a separate prerequisite at integration.
    """
    clean = plan_references(config, mapping, Context(Consumer.DASHBOARD, ())).value
    if type(clean) is not dict:
        raise ValueError('invalid_dashboard')
    scopes, markers, expressions = [], [], []
    templates = clean.get('button_card_templates', {})

    def block(path, code='unsupported_dashboard_schema'):
        markers.append(Marker(path, code, 'Dashboard coverage requires a semantic adapter'))

    def ref(value, path, many=False, statistic=False):
        values = value if many and type(value) is list else [value]
        if not values:
            return
        for i, item in enumerate(values):
            p = path + (i,) if many and type(value) is list else path
            # External statistic IDs remain untouched, never infer a new ID.
            if unchanged_computed_target(item, mapping):
                continue
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
        if kind == 'fire-dom-event':
            if set(value) - {'action', 'browser_mod', 'haptic'}:
                block(path, 'custom_dashboard_action'); return
            event = value.get('browser_mod')
            if type(event) is not dict or set(event) != {'service', 'data'} or event['service'] != 'browser_mod.popup':
                block(path, 'custom_dashboard_action'); return
            data = event['data']
            if type(data) is not dict or set(data) - {'title', 'content'} or 'content' not in data:
                block(path, 'custom_dashboard_action'); return
            node(data['content'], path + ('browser_mod', 'data', 'content'), 'card')
            return
        if kind not in {'none', 'toggle', 'more-info', 'navigate', 'url', 'assist',
                        'call-service', 'perform-action'}:
            block(path, 'custom_dashboard_action'); return
        allowed = {'action', 'confirmation', 'haptic'}
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
                    elif field == 'content' and value.get('service', value.get('perform_action')) == 'browser_mod.popup':
                        node(item, p + (field,), 'card')
                    elif (field == 'option' and value.get('service', value.get('perform_action')) == 'input_select.select_option' and type(item) is str
                          or field in {'cycle', 'skip_condition'} and type(item) is bool
                          or field in {'duration', 'godziny'} and (type(item) in (int, float) or field == 'duration' and type(item) is str and re.fullmatch(r'\d{1,3}:\d{2}:\d{2}', item))
                          or field == 'title' and value.get('service', value.get('perform_action')) == 'browser_mod.popup' and type(item) is str):
                        continue
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

    def styles_safe(value, variables):
        if type(value) is dict:
            return all(type(key) is str and _passive_template(key, mapping) and styles_safe(item, variables) for key,item in value.items())
        if type(value) is list:
            return all(styles_safe(item, variables) for item in value)
        if type(value) is str and '[[[' in value:
            return unchanged_button_style(value, variables, mapping)
        return _passive_template(value, mapping)

    def custom(value, path, kind):
        # Explicitly owned component slots; unrecognized options are refused.
        passive = PASSIVE | {'styles', 'show_label', 'show_entity_picture', 'size',
                             'show_icon', 'show_name', 'show_state', 'button_type',
                             'card_type', 'scrolling_effect', 'show_attribute', 'card_layout',
                             'force_icon', 'modules', 'slider_live_update', 'hide_main_background',
                             'color_type', 'hash', 'use_accent_color', 'temperature_colouring',
                             'show_last_updated', 'RAG_colors_buttons_per_text_or_value', 'card_mod', 'show_background', 'state_background',
                             'justify_content', 'buttons_layout', 'sub_button_type', 'extra_styles'}
        for key, child in value.items():
            p = path + (key,)
            if key == 'type': continue
            if key in {'styles', 'extra_styles', 'style'}:
                if not (kind == 'custom:bubble-card' and unchanged_bubble_style(child, mapping)) and not styles_safe(child, value.get('variables', {})): block(p, 'dynamic_button_style')
            elif key == 'entity': ref(child, p)
            elif key == 'triggers_update' and kind == 'custom:button-card': ref(child, p, many=True)
            elif key == 'template' and kind == 'custom:button-card':
                names = [child] if type(child) is str else child
                if type(templates) is not dict or type(names) is not list or any(type(n) is not str or n not in templates for n in names):
                    block(p, 'unknown_button_template')
            elif key == 'variables' and kind == 'custom:button-card':
                if type(child) is not dict: block(p); continue
                # Only source-proven variables whose values are reference trees.
                for field, item in child.items():
                    q = p + (field,)
                    if field not in {'entity_ids', 'floor_entity_ids', 'bound_entity',
                                     'source_entities', 'cluster_bindings', 'temperature_entity_id',
                                     'humidity_entity_id', 'temperature_ids', 'humidity_ids', 'source_entity'}:
                        if field in {'v4_role', 'exact_point_binding', 'left_percent', 'top_percent',
                                     'cluster_ids', 'cluster_size', 'display_name', 'manual_point',
                                     'mobile_stagger_group', 'point_code', 'point_floor', 'point_id',
                                     'point_type', 'source_label', 'source_x', 'source_y', 'verification_status',
                                     'bound_device_id', 'current_relay_verification', 'relay_verified_at',
                                     'measurement', 'source_left', 'source_top', 'group_id', 'point_ids',
                                     'room_confidence', 'visual_left_percent', 'visual_top_percent',
                                     'confirmed_module_count', 'confidence_text', 'evidence',
                                     'verification_method', 'floor', 'floor_point_counts', 'binding_note',
                                     'verified_total', 'v4_layer', 'v4_owner', 'v4_schema', 'v4_home_merged',
                                     'cluster_order', 'connectivity_status', 'position_accuracy', 'physical_description'}:
                            continue
                        block(q, 'unsupported_button_variable'); continue
                    variable_refs(field, item, q)
            elif key == 'label' and kind == 'custom:button-card' and type(child) is str and child.startswith('[[['):
                expressions.append((p, child))
            elif key in ACTIONS: action(child, p)
            elif key in {'conditions', 'visibility'}: conditions(child, p)
            elif key == 'sub_button' and kind == 'custom:bubble-card':
                bubble_buttons(child, p, passive)
            elif key == 'button_action' and kind == 'custom:bubble-card':
                if type(child) is not dict: block(p); continue
                for field, item in child.items():
                    if field in ACTIONS: action(item, p + (field,))
                    else: block(p + (field,))
            elif key == 'custom_fields' and kind == 'custom:button-card':
                if type(child) is not dict: block(p); continue
                for field, item in child.items():
                    q = p + (field,)
                    if type(item) is dict and set(item) <= {'card', 'do_not_eval'} and 'card' in item and (
                            'do_not_eval' not in item or type(item['do_not_eval']) is bool):
                        node(item['card'], q + ('card',), 'card')
                    elif type(item) is str and '[[[' in item:
                        if not unchanged_button_field(item, value.get('variables', {}), mapping):
                            block(q, 'unsupported_button_custom_field')
                    elif type(item) is str and not any(identity in item for identity in (*mapping.keys(), *mapping.values())):
                        continue
                    else: block(q, 'unsupported_button_custom_field')
            elif key == 'state' and kind == 'custom:bubble-card':
                if type(child) not in (str, int, float, bool, list, dict): block(p)
            elif key == 'state' and kind == 'custom:button-card':
                if type(child) is not list: block(p); continue
                for i, item in enumerate(child):
                    if type(item) is not dict or set(item) - {'value', 'styles'}:
                        block(p + (i,)); continue
                    if 'styles' in item and not styles_safe(item['styles'], value.get('variables', {})):
                        block(p + (i, 'styles'), 'dynamic_button_style')
            elif key == 'cards': sequence(child, p, 'card')
            elif key not in passive: block(p)

    def bubble_buttons(value, path, passive):
        if type(value) is dict and set(value) <= {'main', 'bottom'}:
            for key, items in value.items(): bubble_buttons(items, path + (key,), passive)
            return
        if type(value) is not list: block(path); return
        for i, button in enumerate(value):
            q = path + (i,)
            if type(button) is not dict: block(q); continue
            for field, item in button.items():
                p = q + (field,)
                if field == 'entity': ref(item, p)
                elif field in {'conditions', 'visibility'}: conditions(item, p)
                elif field in ACTIONS: action(item, p)
                elif field == 'group': bubble_buttons(item, p, passive)
                elif field not in passive: block(p)

    def variable_refs(field, value, path):
        if field in {'bound_entity', 'temperature_entity_id', 'humidity_entity_id', 'source_entity'}:
            if value is not None: ref(value, path)
        elif field in {'entity_ids', 'source_entities', 'temperature_ids', 'humidity_ids'}:
            if type(value) is not list: block(path); return
            ref(value, path, many=True)
        elif field == 'floor_entity_ids':
            if type(value) is not dict: block(path); return
            for floor, entities in value.items():
                if type(entities) is not list: block(path + (floor,)); continue
                ref(entities, path + (floor,), many=True)
        elif field == 'cluster_bindings':
            if type(value) is not dict: block(path); return
            for name, binding in value.items():
                p = path + (name,)
                if type(binding) is not dict: block(p); continue
                for key, item in binding.items():
                    if key == 'entity_id': ref(item, p + (key,))
                    elif key in {'name', 'device_id'} and type(item) is str: continue
                    else: block(p + (key,), 'unsupported_button_variable')

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
        # Known passive foreign card families with static configuration. No
        # IDs are rewritten here; any selected token/dynamic access blocks.
        passive_foreign = {'custom:anchor-card', 'custom:mini-graph-card',
                           'custom:apexcharts-card', 'custom:advanced-camera-card',
                           'custom:mushroom-fan-card', 'custom:mushroom-humidifier-card',
                           'energy-usage-graph', 'energy-sources-table', 'energy-devices-graph'}
        if kind in {'custom:auto-entities', 'custom:flex-table-card', 'custom:battery-state-card'}:
            if not _foreign_filter_safe(kind, value, mapping): block(path, 'foreign_filter_membership_unverified')
            return
        if kind in {'custom:android-tv-card', 'custom:fold-entity-row', 'custom:private-voice-text-card'}:
            if not _foreign_static(value, mapping): block(path, 'foreign_component_dynamic')
            return
        if kind in passive_foreign:
            if not _passive_template(value, mapping): block(path, 'foreign_component_dynamic')
            return
        if role == 'card' and kind == 'custom:mod-card':
            for key, child in value.items():
                if key == 'card': node(child, path + (key,), 'card')
                elif key not in {'type', 'grid_options', 'card_mod', 'visibility'}: block(path + (key,))
                elif key == 'visibility': conditions(child, path + (key,))
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
            if key == 'button_card_templates' and role == 'root':
                if type(child) is not dict: block(p)
                else:
                    for name, definition in child.items():
                        if type(definition) is not dict or not _passive_template(definition, mapping):
                            block(p + (name,), 'dynamic_button_template')
            elif key == 'strategy': block(p, 'dynamic_dashboard_strategy')
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
                # Empty header/footer has no component or action to execute.
                if child not in ({}, None): block(p, 'unsupported_dashboard_header_footer')
            elif key in {'entity', 'swipe_nav'} and role == 'root':
                if not _passive_template(child, mapping): block(p)
            elif key in {'card_mod', 'variables'} and role in {'root', 'view', 'section', 'card', 'element'}:
                if not _passive_template(child, mapping): block(p)
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
    retained = []
    for marker in result.markers:
        if marker.code == 'no_supported_scopes':
            continue
        if marker.code == 'dynamic_reference':
            value = clean
            for part in marker.path: value = value[part]
            if type(value) is str and unchanged_finite_jinja(value, mapping):
                continue
        retained.append(marker)
    return replace(result, changes=result.changes + tuple(extra_changes), markers=tuple(retained) + tuple(markers),
                   coverage='native_lovelace_known_schema_frontend_20260930.4')


# Fields with shared Lovelace entity-reference semantics.  Do not treat an
# arbitrary exact-looking value or dictionary key as an entity reference.
_REFERENCE_SCALARS = frozenset({
    'entity', 'entity_id', 'image_entity', 'camera_image', 'source_entity',
    'bound_entity', 'temperature_entity_id', 'humidity_entity_id',
})
_REFERENCE_LISTS = frozenset({
    'entities', 'entity_ids', 'exclude_entities', 'triggers_update',
    'source_entities', 'temperature_ids', 'humidity_ids',
})
_NON_ENTITY_TARGETS = frozenset({'device_id', 'area_id', 'floor_id', 'label_id'})
_TEXT_FIELDS = PASSIVE | frozenset({'language', 'default_tab', 'tabs', 'state', 'state_not'})
_ENTITY_TOKEN = re.compile(r'(?<![A-Za-z0-9_.])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![A-Za-z0-9_.])', re.ASCII)
_ACTIVE_ACCESS = re.compile(r'\b(?:states|hass|callService)\s*[.(\[]', re.ASCII)


def plan_dashboard(config, mapping):
    """Plan bounded, typed references across a dashboard independent of card type.

    Unknown active semantics produce path-specific coverage markers; labels and
    URLs are never substituted. No JavaScript is executed.
    """
    clean = plan_references(config, mapping, Context(Consumer.DASHBOARD, ())).value
    if type(clean) is not dict:
        raise ValueError('invalid_dashboard')
    scopes, markers, expressions = [], [], []
    selected = set(mapping) | set(mapping.values())

    def mark(path, code, detail):
        markers.append(Marker(path, code, detail))

    def scope(path, syntax):
        scopes.append(Scope(path, syntax))

    def reference_tree(value, path):
        if type(value) is dict:
            for field, child in value.items():
                reference_tree(child, path + (field,))
        elif type(value) is list:
            for i, child in enumerate(value):
                reference_tree(child, path + (i,))
        else:
            reference(value, path)

    def reference(value, path):
        if value is None:
            return
        if type(value) is str:
            if unchanged_computed_target(value, mapping):
                return
            if '{{' in value or '{%' in value or '[[[' in value:
                mark(path, 'dynamic_dashboard_reference', 'Computed entity target is not a static ID')
            elif _ID.fullmatch(value):
                scope(path, Syntax.ENTITY_TREE)
            elif ':' in value and not _ACTIVE_ACCESS.search(value) and not any(x in value for x in selected):
                return  # external statistic identifier
            else:
                mark(path, 'dynamic_dashboard_reference', 'Reference is not a static entity ID')
        else:
            mark(path, 'dynamic_dashboard_reference', 'Reference is not a scalar entity ID')

    def walk(value, path=(), key=None, list_reference=False, parent=None, variables=None):
        if type(value) is dict:
            # Strategies load a source outside the configuration tree.
            if key == 'strategy' and value:
                mark(path, 'dynamic_dashboard_strategy', 'Dashboard strategy loads an external source')
            if type(value.get('variables')) is dict:
                variables = value['variables']
            if value.get('name') == 'entity_id':
                import fnmatch
                pattern, operator = value.get('value'), value.get('operator')
                if (type(pattern) is not str or len(pattern) > 256 or operator not in (None, 'contains')
                        or not re.fullmatch(r'\^?(?:[A-Za-z0-9_.*?]|\\[.])+(?:\$)?', pattern)):
                    mark(path + ('value',), 'dynamic_dashboard_selector', 'Entity filter pattern is unsupported')
                else:
                    # Restrict regex to an inexpensive subset; evaluate all semantics
                    # used by supported selector consumers.
                    try:
                        regex = re.compile(pattern)
                    except re.error:
                        mark(path + ('value',), 'dynamic_dashboard_selector', 'Invalid entity filter pattern')
                    else:
                        if any((pattern in old) != (pattern in new)
                               or fnmatch.fnmatchcase(old, pattern) != fnmatch.fnmatchcase(new, pattern)
                               or bool(regex.search(old)) != bool(regex.search(new))
                               for old, new in mapping.items()):
                            mark(path + ('value',), 'dynamic_dashboard_selector', 'Entity filter membership changes')
            for field, child in value.items():
                p = path + (field,)
                if field in selected:
                    mark(p, 'ambiguous_dashboard_key', 'Selected entity ID used as an unclassified key')
                if field in _NON_ENTITY_TARGETS:
                    if type(child) in (dict, list):
                        walk(child, p, field, False, value, variables)
                    continue
                if field == 'floor_entity_ids':
                    reference_tree(child, p)
                elif field in _REFERENCE_SCALARS:
                    if type(child) is list and field == 'entity_id':
                        for i, item in enumerate(child): reference(item, p + (i,))
                    else:
                        reference(child, p)
                elif field in _REFERENCE_LISTS and type(child) is list:
                    walk(child, p, field, True, value, variables)
                elif field in _REFERENCE_LISTS and type(child) is dict:
                    import fnmatch
                    for selector in ('include', 'exclude'):
                        pattern = child.get(selector)
                        if type(pattern) is str and any(fnmatch.fnmatchcase(old, pattern) != fnmatch.fnmatchcase(new, pattern) for old, new in mapping.items()):
                            mark(p + (selector,), 'dynamic_dashboard_selector', 'Entity selector membership changes')
                        elif pattern is not None and type(pattern) is not str:
                            mark(p + (selector,), 'dynamic_dashboard_selector', 'Entity selector cannot be resolved')
                    walk(child, p, field, False, value, variables)
                elif field in {'state', 'state_not', 'above', 'below'} and any(part in {'conditions', 'visibility'} for part in path if type(part) is str):
                    for i, item in enumerate(child if type(child) is list else [child]):
                        if type(item) is str and _ID.fullmatch(item):
                            reference(item, p + (i,) if type(child) is list else p)
                else:
                    walk(child, p, field, False, value, variables)
        elif type(value) is list:
            for i, child in enumerate(value):
                p = path + (i,)
                if list_reference and type(child) is str:
                    reference(child, p)
                else:
                    walk(child, p, key, False, parent, variables)
        elif type(value) is str:
            if key in {'url', 'url_path', 'navigation_path', 'image', 'path'}:
                return
            if key in {'style', 'styles', 'extra_styles'} and (unchanged_bubble_style(value, mapping) or unchanged_button_style(value, variables, mapping)):
                return
            if '[[[' in value and unchanged_button_field(value, variables, mapping):
                return
            if '{{' in value or '{%' in value:
                scope(path, Syntax.JINJA)
            elif '[[[' in value:
                expressions.append((path, value))
            elif key not in _TEXT_FIELDS:
                tokens = {m.group(1) for m in _ENTITY_TOKEN.finditer(value)}
                if tokens & selected:
                    mark(path, 'ambiguous_dashboard_reference', 'Selected entity token in an unclassified field')
                elif _ACTIVE_ACCESS.search(value):
                    mark(path, 'dynamic_dashboard_reference', 'Unclassified state access may select an entity')

    walk(clean)
    result = plan_references(clean, mapping, Context(Consumer.DASHBOARD, tuple(scopes)))
    changes = list(result.changes)
    for path, original in expressions:
        updated, error = plan_button_expression(original, mapping)
        if error:
            mark(path, error, 'JavaScript reference is outside the supported static forms')
        elif updated != original:
            target = result.value
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = updated
            changes.append(Change(path, 'value', original, updated))
    for m in result.markers:
        if m.code == 'no_supported_scopes':
            continue
        if m.code == 'dynamic_reference':
            value = clean
            for part in m.path:
                value = value[part]
            if unchanged_finite_jinja(value, mapping):
                continue
        markers.append(m)
    return replace(result, changes=tuple(changes), markers=tuple(markers),
                   coverage='dashboard_typed_fields_and_bounded_templates_v1')
