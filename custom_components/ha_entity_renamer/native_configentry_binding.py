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
    seen = [0]
    def check(item, depth):
        seen[0] += 1
        if depth > 48 or seen[0] > 80000: _fail()
        if type(item) is dict:
            if len(item) > 10000: _fail()
            for k,v in item.items():
                if type(k) is not str: _fail()
                check(v,depth+1)
        elif type(item) is list:
            if len(item) > 10000: _fail()
            for v in item: check(v,depth+1)
        elif item is None or type(item) in (str,bool,int):
            if type(item) is str and len(item) > 2_000_000: _fail()
        elif type(item) is float:
            if not math.isfinite(item): _fail()
        else: _fail()
    check(value,0)
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),
                   ensure_ascii=True,allow_nan=False).encode()
    if len(raw)>24_000_000: _fail()
    return raw

def _id(value):
    return type(value) is str and bool(value.strip()) and len(value)<=256 and not any(ord(c)<32 for c in value)

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

    Caller retains native discovery guard and independently rechecks native disk
    and managers after awaiting other sources. No generated completeness claims.
    """
    try:
        if (native.version not in ('2025.6.0','2026.10.2') or
            type(native.files) is not tuple or len(native.files)!=3 or
            type(plans) is not tuple or len(plans)>10000):
            _fail()
        keyed={}
        for item in native.files:
            if (not _id(item.key) or item.key in keyed or type(item.manager_json) is not bytes
                or len(item.manager_json)>32_000_000): _fail()
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
