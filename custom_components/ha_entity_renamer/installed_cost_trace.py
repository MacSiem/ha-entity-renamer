"""Opt-in diagnostic trace for one installed YAML review/apply.

Only static labels, random ephemeral correlation IDs, counters, monotonic
elapsed times and allowlisted outcome labels are logged. Never source values.
This module has no authority over transaction or admission decisions.
"""
from contextvars import ContextVar
from functools import wraps
import asyncio
import logging
import os
from threading import Lock
from time import monotonic_ns
from uuid import uuid4

_ENABLED = os.environ.get('RENAMER_INSTALLED_COST_TRACE') == '1'
_LOG = logging.getLogger(__name__)
_CURRENT = ContextVar('renamer_installed_cost_trace', default=None)
_STATES = frozenset(('prepared', 'applying', 'applied', 'rolling_back',
                     'rolled_back', 'unknown', 'ready', 'failed'))


class _Trace:
    def __init__(self, phase):
        self.id = uuid4().hex
        self.phase = phase
        self.lock = Lock()
        self.counts = {'reconstruct': 0, 'source_fence': 0, 'validation_fence': 0}

    def number(self, kind):
        with self.lock:
            self.counts[kind] += 1
            return self.counts[kind]


def _emit(trace, event, **fields):
    # Instrumentation must not turn a completed effect into a request failure.
    try:
        suffix = ' '.join(f'{k}={v}' for k, v in fields.items())
        _LOG.warning('renamer_cost_trace trace=%s phase=%s event=%s %s',
                     trace.id, trace.phase, event, suffix)
    except Exception:
        pass


def current_trace():
    return _CURRENT.get() if _ENABLED else None


def run_with_trace(trace, function):
    if trace is None:
        return function()
    token = _CURRENT.set(trace)
    try:
        return function()
    finally:
        _CURRENT.reset(token)


def _outcome(value):
    try:
        status = value.get('state') if type(value) is dict else getattr(value, 'status', None)
        return status if type(status) is str and status in _STATES else 'returned'
    except Exception:
        return 'returned'


def trace_request(phase):
    def decorate(function):
        @wraps(function)
        async def wrapper(*args, **kwargs):
            if not _ENABLED:
                return await function(*args, **kwargs)
            trace = _Trace(phase)
            token = _CURRENT.set(trace)
            start = monotonic_ns()
            _emit(trace, 'request_start')
            outcome = 'raised'
            try:
                result = await function(*args, **kwargs)
                outcome = _outcome(result)
                return result
            except asyncio.CancelledError:
                outcome = 'cancelled'
                raise
            finally:
                _emit(trace, 'request_end', outcome=outcome,
                      elapsed_ms=(monotonic_ns() - start) // 1_000_000)
                _CURRENT.reset(token)
        return wrapper
    return decorate


def trace_owned(function):
    @wraps(function)
    async def wrapper(*args, **kwargs):
        trace = current_trace()
        if trace is None:
            return await function(*args, **kwargs)
        start = monotonic_ns()
        _emit(trace, 'owned_start')
        outcome = 'raised'
        try:
            result = await function(*args, **kwargs)
            outcome = _outcome(result)
            return result
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        finally:
            _emit(trace, 'owned_end', outcome=outcome,
                  elapsed_ms=(monotonic_ns() - start) // 1_000_000)
    return wrapper


def trace_call(kind):
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            trace = current_trace()
            if trace is None:
                return function(*args, **kwargs)
            number = trace.number(kind)
            start = monotonic_ns()
            _emit(trace, 'call_start', kind=kind, number=number)
            outcome = 'raised'
            try:
                result = function(*args, **kwargs)
                outcome = 'returned'
                return result
            finally:
                _emit(trace, 'call_end', kind=kind, number=number, outcome=outcome,
                      elapsed_ms=(monotonic_ns() - start) // 1_000_000)
        return wrapper
    return decorate
