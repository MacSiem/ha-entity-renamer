"""Install backend sources only in a fresh isolated HA runtime on the owned SSD mount."""
from pathlib import Path
import argparse
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'), default='ha-renamer-qa222')
args = parser.parse_args()
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
assert info['State']['Running']
mounts = [m['Source'] for m in info['Mounts'] if m['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0]
component = ROOT/'custom_components/ha_entity_renamer'
sources = {p.name: p.read_text() for p in component.iterdir() if p.suffix in ('.py', '.json')}
program = r'''
import asyncio, os, socket, tempfile
from pathlib import Path
from homeassistant.core import HomeAssistant
from homeassistant import loader, auth
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config_entries import ConfigEntries, ConfigEntryState
from homeassistant.setup import async_setup_component
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.storage import Store

class Connection:
    def __init__(self, user):
        self.user = user
        self.output = None
        self.done = asyncio.Event()
    def send_result(self, ident, result):
        self.output = ('result', result); self.done.set()
    def send_error(self, ident, code, message):
        self.output = ('error', code); self.done.set()
    def async_handle_exception(self, message, error):
        self.output = ('exception', type(error).__name__); self.done.set()

async def run(root):
    os.chdir(root)
    hass = HomeAssistant(root)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config = {'http': {'server_host': '127.0.0.1', 'server_port': port}}
    try:
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, config)
        await loader.async_get_custom_components(hass)
        assert (await async_load_base_functionality(hass)) is not False
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        assert await async_setup_component(hass, 'http', config)
        from homeassistant.components.config import automation, script, scene
        for editor in (automation, script, scene):
            assert editor.async_setup(hass)
        await hass.async_start()
        from custom_components.ha_entity_renamer.yaml_editor_locks import NativeYAMLEditorLocks
        leases = NativeYAMLEditorLocks(hass)
        native_editors = leases.enumerate()
        assert len(native_editors) == 3, len(native_editors)
        entered_editor = asyncio.Event()
        async def edit():
            async with native_editors[0][3]:
                entered_editor.set()
        async with leases.hold(lambda: None):
            assert all(row[3].locked() for row in native_editors)
            pending_editor = asyncio.create_task(edit())
            await asyncio.sleep(0)
            assert not entered_editor.is_set()
        await pending_editor
        assert entered_editor.is_set() and not any(row[3].locked() for row in native_editors)
        calls = 0
        def revoked_guard():
            nonlocal calls
            calls += 1
            if calls > 1: raise PermissionError('revoked fixture')
        try:
            async with leases.hold(revoked_guard):
                raise AssertionError('revocation allowed entry')
        except PermissionError:
            pass
        assert not any(row[3].locked() for row in native_editors)
        result = await hass.config_entries.flow.async_init('ha_entity_renamer', context={'source': 'user'})
        assert result['type'] == 'create_entry', result
        entry = result['result']
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED, entry.state
        duplicate = await hass.config_entries.flow.async_init('ha_entity_renamer', context={'source': 'user'})
        assert duplicate['type'] == 'abort' and duplicate['reason'] in ('already_configured', 'single_instance_allowed'), duplicate
        from custom_components.ha_entity_renamer.const import DATA_RUNTIME
        from custom_components.ha_entity_renamer.websocket_api import ws_status
        owner = await hass.auth.async_create_user('Fixture owner', group_ids=[GROUP_ID_ADMIN])
        assert owner.is_owner
        admin = await hass.auth.async_create_user('Fixture admin', group_ids=[GROUP_ID_ADMIN])
        assert not admin.is_owner
        household = await hass.auth.async_create_user('Fixture household', group_ids=[])
        async def status(user):
            connection = Connection(user)
            ws_status(hass, connection, {'id': 1, 'type': 'ha_entity_renamer/status'})
            await asyncio.wait_for(connection.done.wait(), 3)
            return connection.output
        output = await status(admin)
        assert output[0] == 'result' and output[1]['migration_ready'] is False, output
        try:
            await status(household)
        except Unauthorized:
            pass
        else:
            raise AssertionError('household status was authorized')
        runtime = hass.data[DATA_RUNTIME]
        entered, resume = asyncio.Event(), asyncio.Event()
        original = runtime.journal.active
        async def delayed():
            entered.set(); await resume.wait(); return await original()
        runtime.journal.active = delayed
        pending = asyncio.create_task(status(admin))
        await entered.wait()
        await hass.auth.async_update_user(admin, group_ids=[])
        resume.set()
        revoked = await pending
        assert revoked == ('error', 'unauthorized'), revoked
        runtime.journal.active = original
        await hass.auth.async_update_user(admin, group_ids=[GROUP_ID_ADMIN])
        async with runtime.operation_lock:
            unloading = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
            for _ in range(100):
                if runtime.unloading: break
                await asyncio.sleep(0)
            assert runtime.unloading and not unloading.done()
            assert await status(admin) == ('error', 'unavailable')
        assert await unloading
        assert await status(admin) == ('error', 'unavailable')
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert (await status(admin))[0] == 'result'
        assert await hass.config_entries.async_unload(entry.entry_id)
        # Synthetic malformed recovery state must fail setup, not become empty.
        await Store(hass, 1, 'ha_entity_renamer.migrations', minor_version=1).async_save({'schema': 1, 'operations': []})
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert DATA_RUNTIME not in hass.data
        assert await status(admin) == ('error', 'unavailable')
        print({'native_config_flow': 'PASS', 'duplicate_rejected': 'PASS',
               'admin_household_and_late_revocation': 'PASS', 'reload_and_busy_unload': 'PASS',
               'corrupt_journal_refused': 'PASS', 'native_yaml_editor_lock_lease': 'PASS', 'mutation_api': 'NOT_IMPLEMENTED',
               'live_config_access': False}, flush=True)
    finally:
        await hass.async_stop(force=True)
        os.chdir('/')

with tempfile.TemporaryDirectory(prefix='renamer-lifecycle-', dir='/config') as directory:
    component = Path(directory)/'custom_components/ha_entity_renamer'
    component.mkdir(parents=True)
    for name, source in SOURCES.items(): (component/name).write_text(source)
    asyncio.run(run(directory))
'''
result = subprocess.run(['docker','exec','-i',args.container,'python','-B','-'],
    input='SOURCES = '+repr(sources)+'\n'+program, text=True, capture_output=True, timeout=90)
print(result.stdout)
print(result.stderr)
result.check_returncode()
