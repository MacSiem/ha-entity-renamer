"""Pure synthetic regression: executor Future can end before its worker thread.

No Home Assistant, containers, runtime config, product tests, or external files.
"""
import asyncio
import threading
import unittest

from test_native_input_baseline import baseline


class DrainSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_executor_future_must_not_release_active_worker(self):
        loop = asyncio.get_running_loop()
        returned = loop.create_future()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        closed = threading.Event()
        worker_errors = []
        threads = []

        def work():
            started.set()
            if not release.wait(timeout=3):
                worker_errors.append('release_timeout')
            if closed.is_set():
                worker_errors.append('worker_ran_after_resource_closed')
            return 'captured'

        def executor(function):
            def run():
                try:
                    function()
                except BaseException as error:
                    worker_errors.append(type(error).__name__)
                finally:
                    finished.set()
                    loop.call_soon_threadsafe(
                        lambda: returned.set_result('captured') if not returned.done() else None)
            worker = threading.Thread(target=run, daemon=True)
            threads.append(worker)
            worker.start()
            return returned

        async def caller():
            try:
                return await baseline._drained(executor, work)
            finally:
                closed.set()  # Models _Pinned.close() in the public caller.

        outer = asyncio.create_task(caller())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            returned.cancel()  # Executor future ended, but thread still owns file fds.
            await asyncio.sleep(0.05)
            self.assertFalse(closed.is_set(),
                'danger: _drained returned before the worker stopped')
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(outer, timeout=2)
            self.assertTrue(finished.is_set())
            self.assertEqual(worker_errors, [])
        finally:
            release.set()
            if not outer.done():
                try:
                    await asyncio.wait_for(outer, 2)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            for worker in threads:
                await asyncio.to_thread(worker.join, 2)

    async def test_late_worker_is_inert_after_future_cancellation(self):
        future = asyncio.get_running_loop().create_future()
        callbacks = []
        executed = []
        def executor(callback):
            callbacks.append(callback)
            return future
        job = asyncio.create_task(baseline._drained(
            executor, lambda: executed.append('unsafe_access')))
        await asyncio.sleep(0)
        self.assertEqual(len(callbacks), 1)
        future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job
        self.assertIsNone(callbacks[0]())
        self.assertEqual(executed, [])

    async def test_repeated_outer_cancellation_drains_thread_and_preserves_metadata(self):
        started, release, finished, closed = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        class Callback:
            def capture(self):
                started.set()
                if not release.wait(3):
                    raise AssertionError('release_timeout')
                self.finished = True
                finished.set()
                return 7
        owner = Callback()
        async def executor(callback):
            self.assertEqual(callback.__name__, 'capture')
            self.assertIs(callback.__self__, owner)
            return await asyncio.to_thread(callback)
        async def caller():
            try:
                return await baseline._drained(executor, owner.capture)
            finally:
                closed.set()
        task = asyncio.create_task(caller())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(closed.is_set())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            self.assertTrue(finished.is_set())
        finally:
            release.set()

    async def test_normal_executor_result_and_error(self):
        async def executor(callback):
            return await asyncio.to_thread(callback)
        self.assertEqual(await baseline._drained(executor, lambda: 19), 19)
        def error():
            raise ValueError('synthetic_failure')
        with self.assertRaisesRegex(ValueError, 'synthetic_failure'):
            await baseline._drained(executor, error)

if __name__ == '__main__':
    unittest.main()
