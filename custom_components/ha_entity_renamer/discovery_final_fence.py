"""Private read continuation for discovery, Runtime and final WS output.

Retains the SAME original capture's source/check context across authorization IO.
This object only rechecks reads. It never authorizes any effect or serializes.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class _FinalFence:
    hass: object
    finish: object


def _attach(capture,fence):
    # Not a dataclass field: summary/digest/asdict remain detached data with the
    # unchanged public schema. A reconstructed data object gains no continuation.
    object.__setattr__(capture,'_read_fence',fence)
    return capture


class _Preview(dict):
    """Wire-compatible sanitized dict; private capture never becomes a JSON key."""
    def __init__(self, result, capture):
        super().__init__(result)
        self.capture = capture


async def async_finalize_discovery(hass, capture, guard):
    from .discovery import DiscoveryCapture
    guard()
    # Existing explicitly trusted alternate consumers/fake IO keep their API.
    if type(capture) is not DiscoveryCapture:
        return capture
    fence = getattr(capture,'_read_fence',None)
    if type(fence) is not _FinalFence or fence.hass is not hass:
        raise ValueError('discovery_final_fence_missing')
    result = await fence.finish(guard)
    guard()  # Synchronous suffix only; no authorization IO after the final read.
    return result


class _ConsumerFence:
    """Keep existing COMPLETE consumer bytes/identities through later auth IO."""
    def __init__(self,hass,observed,plain):
        from .native_discovery_consumers import _inventory,_energy_manager
        from .lovelace_native import NativeLovelace,revision
        from .energy_native import NativeEnergy
        self.hass,self.observed,self.plain = hass,observed,plain
        self.dashboard=self.energy=None
        if observed.dashboard.status=='COMPLETE':
            native=NativeLovelace(hass)
            token=_inventory(hass,native,observed.version)
            value=observed.dashboard.value
            if revision([[r[0],r[2],r[3]] for r in token[3]]) != value.inventory_revision:
                raise ValueError('discovery_dashboard_inventory_changed')
            self.dashboard=(native,token)
            self.collection_rows=sorted(revision(plain(manager.config))
                for manager in native._managers().values()
                if type(manager) is native._storage_class and manager.config is not None)
        if observed.energy.status=='COMPLETE':
            self.energy=NativeEnergy(hass,_energy_manager(hass))
        self.check()

    def check(self):
        from .native_discovery_consumers import _inventory,_energy_manager
        from .lovelace_native import revision
        if self.dashboard is not None:
            native,token=self.dashboard
            if _inventory(self.hass,native,self.observed.version)!=token:
                raise ValueError('discovery_dashboard_manager_changed')
            value=self.observed.dashboard.value
            if tuple((r,self.plain(m.config)) for r,m in native._managers().items()) != tuple(
                    (r,self.plain(c)) for r,c in value.registered_metadata):
                raise ValueError('discovery_dashboard_metadata_changed')
            for row in value.snapshots:
                manager=native._manager(row.dashboard)
                native._settled(manager)
                actual=None if manager._data is None else manager._data.get('config')
                if revision(actual)!=row.revision:
                    raise ValueError('discovery_dashboard_cache_changed')
        if self.energy is not None:
            self.energy._settled()
            if (_energy_manager(self.hass) is not self.energy.manager
                    or revision(self.energy.manager.data)!=self.observed.energy.value.revision):
                raise ValueError('discovery_energy_manager_changed')

    def physical(self):
        from pathlib import Path
        from .lovelace_native import durable_snapshot,revision
        from .native_discovery_consumers import _yaml_file
        if self.dashboard is not None:
            native,_=self.dashboard
            value=self.observed.dashboard.value
            collection,_=durable_snapshot(Path(self.hass.config.path('.storage','lovelace_dashboards')),
                                          'lovelace_dashboards')
            items=[] if collection is None else collection.get('items')
            if (type(items) is not list or any(type(item) is not dict for item in items)
                    or sorted(revision(item) for item in items)!=self.collection_rows):
                raise ValueError('discovery_dashboard_collection_changed')
            for row in value.snapshots:
                # Paths/keys are captured native descriptors, never request data.
                data,identity=durable_snapshot(Path(self.hass.config.path('.storage',row.dashboard.key)),row.dashboard.key)
                config=None if data is None else data.get('config')
                if identity!=row.file_identity or revision(config)!=row.revision:
                    raise ValueError('discovery_dashboard_disk_changed')
            for source in value.yaml_sources:
                if _yaml_file(self.hass.config.config_dir,source.filename)!=(source.file_identity,source.sha256):
                    raise ValueError('discovery_dashboard_yaml_changed')
        if self.energy is not None:
            data,identity=durable_snapshot(Path(self.energy._store.path),'energy',1,self.energy._minor_version)
            expected=self.observed.energy.value
            if identity!=expected.file_identity or revision(data)!=expected.revision:
                raise ValueError('discovery_energy_disk_changed')


async def _settled_physical(hass,function):
    import asyncio
    task=asyncio.ensure_future(hass.async_add_executor_job(function))
    cancelled=False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled=True
    value=task.result()
    if cancelled:
        raise asyncio.CancelledError
    return value
