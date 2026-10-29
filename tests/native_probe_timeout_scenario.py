"""Opt-in native harness timeout regression; excluded from test*.py discovery."""
import sys
import time
import unittest


class NativeProbeTimeoutScenario(unittest.TestCase):
    def test_timeout_preserves_output_and_reaps_probe(self):
        print('NATIVE_PROBE_TIMEOUT_STDOUT', flush=True)
        print('NATIVE_PROBE_TIMEOUT_STDERR', file=sys.stderr, flush=True)
        time.sleep(30)
