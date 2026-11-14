"""M1.c read-only native ConfigEntry producer-to-consumer binding.

This binds existing immutable native Store capture to helper_native.inventory,
including disabled/unloaded entries. No I/O, readiness, authorization or write.
"""
from dataclasses import dataclass
import hashlib
import json
import math

class SourceBindingError(ValueError):
    """Value-free refusal, never render ConfigEntry metadata."""

@dataclass(frozen=True)
class BoundSource:
    entries: int
    source_digest: str
    consumer_digest: str
    can_write: bool = False
    can_promote: bool = False

def _fail():
    raise SourceBindingError('configentry_source_consumer_unbound') from None

def _pairs(pairs):
    out = {}
    for key, val in pairs:
        if key in out:
            _fail()
        out[key] = val
    return out

def _serial(value):
    """Compare the native JSON projection of already bounded input trees.

    Discovery's native parser and inventory's reference planner enforce their
    existing resource limits. Only the intersection of their key contracts is
    admitted here: plain str and native 64-bit int. Do not use an encoder/decoder
    round trip: HA's OPT_NON_STR_KEYS can silently erase colliding keys.
    Values retain exact bool/int/float types, independent of key projection.
    """
    def project(item):
        kind = type(item)
        if kind is dict:
            result = {}
            for key, child in item.items():
                if type(key) is str:
                    projected_key = key
                elif type(key) is int and -(1 << 63) <= key < (1 << 64):
                    # Native orjson signed-min/unsigned-max integer contract.
                    projected_key = str(key)
                else:
                    _fail()
                if projected_key in result:
                    _fail()
                result[projected_key] = project(child)
            return result
        if kind is list:
            return [project(child) for child in item]
        if item is None or kind in (str, bool, int):
            return item
        if kind is float and math.isfinite(item):
            return item
        _fail()
    return json.dumps(project(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()

def _id(value):
    return type(value) is str and bool(value.strip()) and not any(ord(c)<32 for c in value)

def _subentries(saved, current):
    if saved is None: saved=[]
    if type(saved) is not list or type(current) is not dict or len(saved)!=len(current): _fail()
    seen=set()
    for row in saved:
        if type(row) is not dict or not _id(row.get('subentry_id')) or 'data' not in row: _fail()
        key=row['subentry_id']
        if key in seen or key not in current: _fail()
        seen.add(key)
        other=current[key]
        if type(other) is not dict or set(other)!={'identity','data'} or type(other['identity']) is not dict: _fail()
        if other['identity'].get('subentry_id')!=key or _serial(row['data'])!=_serial(other['data']): _fail()
        for field in ('subentry_type','unique_id','title'):
            if field in row and _serial(row[field])!=_serial(other['identity'].get(field)): _fail()
    if set(current)!=seen: _fail()

def bind_source_consumer(native, plans):
    """Bind complete server source and consumer inventory with exact JSON semantics.

    Inputs come from the bounded native discovery parser and helper inventory.
    Caller retains native discovery guard and independently rechecks native disk
    and managers after awaiting other sources. No generated completeness claims.
    """
    try:
        if (native.version not in ('2025.6.0','2026.10.2', '2026.10.4') or
            type(native.files) is not tuple or len(native.files)!=3 or
            type(plans) is not tuple):
            _fail()
        keyed={}
        for item in native.files:
            if (not _id(item.key) or item.key in keyed or type(item.manager_json) is not bytes): _fail()
            keyed[item.key]=item
        if set(keyed)!={'core.entity_registry','core.device_registry','core.config_entries'}: _fail()
        raw=keyed['core.config_entries'].manager_json
        document=json.loads(raw.decode(),object_pairs_hook=_pairs,
                            parse_constant=lambda _: _fail())
        if type(document) is not dict or type(document.get('entries')) is not list: _fail()
        saved=document['entries']
        if len(saved)!=len(plans): _fail()
        origin={}
        for row in saved:
            if (type(row) is not dict or not _id(row.get('entry_id')) or
                not _id(row.get('domain')) or
                type(row.get('data')) is not dict or type(row.get('options')) is not dict): _fail()
            key=row['entry_id']
            if key in origin: _fail()
            origin[key]=row
        current={}
        for plan in plans:
            state=plan.before
            if (type(state) is not dict or set(state)!={'domain','identity','data','options','subentries'} or
                type(state['identity']) is not dict or
                not _id(state['identity'].get('entry_id')) or not _id(state['domain'])): _fail()
            key=state['identity']['entry_id']
            if key not in origin or key in current: _fail()
            current[key]=state
            store=origin[key]
            if (state['domain']!=store['domain'] or
                _serial(state['data'])!=_serial(store['data']) or
                _serial(state['options'])!=_serial(store['options'])): _fail()
            for field in ('unique_id','title','version','minor_version','source',
                          'disabled_by','pref_disable_new_entities','pref_disable_polling'):
                if field in store and _serial(state['identity'].get(field))!=_serial(store[field]): _fail()
            _subentries(store.get('subentries',[]),state['subentries'])
        if set(origin)!=set(current): _fail()
        digest=hashlib.sha256(_serial([current[k] for k in sorted(current)])).hexdigest()
        return BoundSource(len(origin),hashlib.sha256(raw).hexdigest(),digest)
    except SourceBindingError:
        raise
    except (AttributeError,KeyError,ValueError,TypeError,UnicodeError,OverflowError,RecursionError):
        _fail()
