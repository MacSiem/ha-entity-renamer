"""Serve the bundled card and register one unambiguous Lovelace resource."""
from pathlib import Path
from urllib.parse import urlsplit

from .const import DOMAIN, VERSION

CARD_NAME = 'ha-entity-renamer.js'
CARD_PATH = f'/{DOMAIN}/{CARD_NAME}'
STATIC_PATH = f'/{DOMAIN}'
DATA_STATIC = DOMAIN + '.static_registered'
DATA_EXTRA = DOMAIN + '.extra_url'
LEGACY_PATHS = frozenset({
    '/hacsfiles/ha-entity-renamer/ha-entity-renamer.js',
    '/local/community/ha-entity-renamer/ha-entity-renamer.js',
})


def _path(url):
    if not isinstance(url, str):
        return ''
    parts = urlsplit(url)
    return parts.path if not parts.scheme and not parts.netloc else ''


async def async_register_frontend(hass):
    """Register exactly one card; do not alter ambiguous or YAML resources."""
    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig

    www = Path(__file__).parent / 'www'
    if not await hass.async_add_executor_job((www / CARD_NAME).is_file):
        raise ValueError('bundled_card_missing')
    if not hass.data.get(DATA_STATIC):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_PATH, str(www), cache_headers=False)])
        hass.data[DATA_STATIC] = True

    lovelace = hass.data.get('lovelace')
    resources = getattr(lovelace, 'resources', None)
    if resources is None and isinstance(lovelace, dict):
        resources = lovelace.get('resources')
    if resources is None or not hasattr(resources, 'async_items'):
        raise ValueError('lovelace_resources_unavailable')
    if not getattr(resources, 'loaded', True):
        await resources.async_load()
    rows = list(resources.async_items())
    relevant = [r for r in rows if _path(r.get('url')) in LEGACY_PATHS | {CARD_PATH}]
    foreign = [r for r in rows if _path(r.get('url')).rsplit('/', 1)[-1] == CARD_NAME
               and _path(r.get('url')) not in LEGACY_PATHS | {CARD_PATH}]
    if foreign or len(relevant) > 1:
        raise ValueError('ambiguous_card_resources_manual_resolution_required')
    url = f'{CARD_PATH}?v={VERSION}'
    # YAML resource collections have no writable Store. Never edit YAML or
    # load a second copy; a legacy or stale URL requires explicit migration.
    if getattr(resources, 'store', None) is None:
        if relevant:
            if relevant[0].get('url') != url:
                raise ValueError('yaml_card_resource_manual_update_required')
            return 'yaml_resource'
        if not hass.data.get(DATA_EXTRA):
            frontend.add_extra_js_url(hass, url)
            hass.data[DATA_EXTRA] = url
        return 'extra_js_url'
    if relevant:
        row = relevant[0]
        if row.get('url') != url or row.get('type', row.get('res_type')) != 'module':
            await resources.async_update_item(row['id'], {'res_type': 'module', 'url': url})
    else:
        await resources.async_create_item({'res_type': 'module', 'url': url})
    return 'storage_resource'


async def async_unregister_frontend(hass):
    """Remove only ephemeral extra JS; preserve authored storage resources."""
    from homeassistant.components import frontend
    if url := hass.data.pop(DATA_EXTRA, None):
        frontend.remove_extra_js_url(hass, url)
