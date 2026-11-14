"""Diagnostic trace must preserve outcomes and correlate worker/owned tasks."""
import asyncio
import importlib
import logging
import unittest

from test_native_input_baseline import baseline
trace = importlib.import_module(baseline.__package__ + '.installed_cost_trace')


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class TraceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_enabled = trace._ENABLED
        trace._ENABLED = True
        self.capture = Capture()
        trace._LOG.addHandler(self.capture)

    async def asyncTearDown(self):
        trace._LOG.removeHandler(self.capture)
        trace._ENABLED = self.old_enabled

    async def test_phase_owned_worker_and_counts(self):
        @trace.trace_call('source_fence')
        def fence():
            return 42

        @trace.trace_call('reconstruct')
        def reconstruct():
            return fence()

        @trace.trace_owned
        async def owned():
            loop = asyncio.get_running_loop()
            return await baseline._drained(
                lambda fn: loop.run_in_executor(None, fn), reconstruct)

        @trace.trace_request('apply')
        async def request():
            return await asyncio.create_task(owned())

        self.assertEqual(await request(), 42)
        lines = self.capture.lines
        self.assertEqual(len(lines), 8)
        self.assertEqual(len({line.split('trace=')[1].split()[0] for line in lines}), 1)
        self.assertTrue(all('phase=apply' in line for line in lines))
        self.assertTrue(any('event=owned_end outcome=returned' in line for line in lines))
        self.assertTrue(any('kind=source_fence number=1 outcome=returned' in line for line in lines))
        self.assertIsNone(trace.current_trace())

    async def test_raised_and_cancelled_are_distinct_and_not_swallowed(self):
        @trace.trace_call('reconstruct')
        def fail():
            raise ValueError('private input must never appear')

        @trace.trace_request('prepare')
        async def failed():
            fail()

        with self.assertRaises(ValueError):
            await failed()
        self.assertTrue(any('kind=reconstruct number=1 outcome=raised' in x for x in self.capture.lines))
        self.assertFalse(any('private input' in x for x in self.capture.lines))

        @trace.trace_request('apply')
        async def cancelled():
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await cancelled()
        self.assertTrue(any('event=request_end outcome=cancelled' in x for x in self.capture.lines))
        self.assertIsNone(trace.current_trace())

    async def test_disabled_emits_nothing(self):
        trace._ENABLED = False
        @trace.trace_request('apply')
        async def request():
            return 'unchanged'
        self.assertEqual(await request(), 'unchanged')
        self.assertEqual(self.capture.lines, [])
