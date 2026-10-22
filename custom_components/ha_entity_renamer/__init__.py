"""Native lifecycle for Entity Renamer's durable migration backend.

Imports remain lazy so pure planners can run without Home Assistant installed.
"""
from dataclasses import dataclass, field
import asyncio

from .const import DATA_RUNTIME, DATA_WEBSOCKETS, DOMAIN


def CONFIG_SCHEMA(config):
    from homeassistant.helpers.config_validation import config_entry_only_config_schema
    return config_entry_only_config_schema(DOMAIN)(config)


@dataclass
class Runtime:
    entry_id: str
    journal: object
    preparations: object
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loaded: bool = True
    unloading: bool = False


async def async_setup(hass, config):
    return True


async def async_setup_entry(hass, entry):
    from homeassistant.exceptions import ConfigEntryNotReady
    from .journal import native_journal
    from .prepared import native_prepared_store
    from .websocket_api import async_register

    if hass.data.get(DATA_RUNTIME) is not None:
        raise ConfigEntryNotReady('An Entity Renamer backend is already loaded')
    journal, preparations = native_journal(hass), native_prepared_store(hass)
    # Never turn a damaged or unconfirmed journal into an empty history.
    try:
        await journal.initialize()
        await preparations.initialize()
    except Exception as error:
        raise ConfigEntryNotReady('Migration recovery data could not be verified') from error
    runtime = Runtime(entry.entry_id, journal, preparations)
    entry.runtime_data = runtime
    if not hass.data.get(DATA_WEBSOCKETS):
        async_register(hass)
        hass.data[DATA_WEBSOCKETS] = True
    hass.data[DATA_RUNTIME] = runtime
    return True


async def async_unload_entry(hass, entry):
    runtime = hass.data.get(DATA_RUNTIME)
    if runtime is None or runtime.entry_id != entry.entry_id:
        return False
    # Returning False leaves native HA in FAILED_UNLOAD. Drain the current
    # operation while refusing new work, then let HA complete its lifecycle.
    runtime.unloading = True
    try:
        async with runtime.operation_lock:
            runtime.loaded = False
            del hass.data[DATA_RUNTIME]
    finally:
        runtime.unloading = False
    # Private preparations/journal survive reload/removal for explicit recovery.
    return True
