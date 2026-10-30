"""Only the not-yet-qualified retained-duration whole Root variants.

Reuse real fixture and acceptance bodies without inheriting completed cases.
Execute through the owner's FIFO on each actual pinned Core image.
"""
import unittest

import test_native_automation_duration_prefix_root as duration


@unittest.skipUnless(duration.mixed.server.activation.fixture.fixtures.HAS_HA,
                     'actual pinned HA required')
class NativeAutomationDurationRetainedMatrixTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = duration.NativeAutomationDurationPrefixRootTests.asyncSetUp
    _before_start = duration.NativeAutomationDurationPrefixRootTests._before_start
    _flush_documents = duration.NativeAutomationDurationPrefixRootTests._flush_documents
    prepare = duration.NativeAutomationDurationPrefixRootTests.prepare
    emit = duration.NativeAutomationDurationPrefixRootTests.emit
    _runtime = duration.NativeAutomationDurationPrefixRootTests._runtime
    module = duration.NativeAutomationDurationPrefixRootTests.module
    stimulus = duration.NativeAutomationDurationPrefixRootTests.stimulus
    data = duration.NativeAutomationDurationPrefixRootTests.data
    _capability = duration.NativeAutomationDurationPrefixRootTests._capability
    _duration_case = duration.NativeAutomationDurationPrefixRootTests._duration_case

    async def test_retained_neutral_native_timer(self):
        await self._duration_case(mode='pending')

    async def test_retained_from_only_native_predicate(self):
        await self._duration_case(mode='from_only')

    async def test_retained_attribute_native_history(self):
        await self._duration_case(mode='attribute')

    async def test_retained_meaningful_own_timer(self):
        await self._duration_case(mode='meaningful')
