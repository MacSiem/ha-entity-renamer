"""Fresh installed reverse scenario; owner executes on actual pinned HA via FIFO.

Forward delegates to the qualified numeric-entry fixture. Only detached expected
output bytes/IDs cross the boundary; no forward receipt/chain/observer is passed.
"""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch


async def fresh_reverse_scenario(test, module, operation, fingerprint, baseline_digest,
                                 original_receipt_json, consumer_identity, original, mapping, fault, *,
                                 native_backend_lifecycle=False):
    hass = test.f.hass
    import importlib
    if native_backend_lifecycle:
        # Opt-in TEST ONLY: public native ConfigEntry reload, after the caller
        # already proved every forward proof object's weak reference dead.
        # The returned importer belongs to the actually loaded component.
        module = await test._reload_backend_for_reverse(operation, fingerprint,
                                                        baseline_digest, original_receipt_json)
    backend = importlib.import_module(module('const').__package__)
    # Baseline unit fixtures pre-create a namespace-only package. Execute the
    # ACTUAL installed backend __init__ in that same package before constructing
    # its real Runtime; do not inject a Runtime double or replace native code.
    if native_backend_lifecycle:
        test.assertTrue(hasattr(backend, 'Runtime'))
    elif not hasattr(backend, 'Runtime'):
        import importlib.util
        from pathlib import Path
        source = Path(module('const').__file__).with_name('__init__.py')
        spec = importlib.util.spec_from_file_location(backend.__name__, source,
                    submodule_search_locations=list(backend.__path__))
        test.assertIsNotNone(spec)
        test.assertIsNotNone(spec.loader)
        backend.__spec__, backend.__loader__ = spec, spec.loader
        backend.__package__, backend.__file__ = backend.__name__, str(source)
        spec.loader.exec_module(backend)
    test.assertEqual(backend.Runtime.__module__, backend.__name__)
    prep, journal_api, rr, lm = (module(n) for n in
        ('prepared', 'journal', 'native_registry_receipts', 'native_helper_lifecycle'))
    if native_backend_lifecycle:
        # No hand-made Runtime or hass.data insertion on this branch.
        backend_entry = hass.config_entries.async_get_entry(test.backend_entry_id)
        test.assertIsNotNone(backend_entry)
        runtime = backend_entry.runtime_data
        test.assertIs(type(runtime), backend.Runtime)
        test.assertIs(hass.data.get(backend.DATA_RUNTIME), runtime)
        fresh_preps, fresh_journal = runtime.preparations, runtime.journal
    else:
        fresh_preps, fresh_journal = prep.native_prepared_store(hass), journal_api.native_journal(hass)
        await fresh_preps.initialize()
        await fresh_journal.initialize()
        runtime = backend.Runtime('numeric-reverse-fixture', fresh_journal, fresh_preps)
        hass.data[backend.DATA_RUNTIME] = runtime
    record = await fresh_journal.load(operation)
    before_documents = await test._flush_documents()
    entry = hass.config_entries.async_get_entry(original['identity']['entry_id'])
    lease_api = module('native_helper_activation_lease').NativeHelperActivationLease
    revoked = False
    refresh_calls = 0

    def guard():
        if revoked:
            raise PermissionError('fixture_admin_revoked')
        if (hass.data.get(backend.DATA_RUNTIME) is not runtime or runtime.unloading
                or not runtime.loaded or not runtime.operation_lock.locked()
                or hass.config_entries.async_get_entry(entry.entry_id) is not entry
                or entry.disabled_by is not None):
            raise PermissionError('fixture_scope_lost')

    async def refresh():
        nonlocal refresh_calls, revoked
        guard()  # Trusted authorization IO seam; not a native source proof.
        refresh_calls += 1
        if fault == 'revoked' and refresh_calls == 2:
            # Revoke at the final refresh; the installed bind's own synchronous
            # suffix must catch it. This callback does not raise for the product.
            revoked = True

    # Corruptions below touch only our own migration evidence; no native state,
    # fake receipt constructor, expected State/cache or synthetic consumer proof.
    if fault == 'wrong_phase':
        await fresh_journal.save(replace(record, status='ready'))
    if fault in ('incomplete_activation', 'pending_intent'):
        store = (lm.native_helper_lifecycle_store(hass, fresh_preps)
                 if fault == 'incomplete_activation' else rr.native_registry_receipt_store(hass, fresh_preps))
        await store.initialize()
        events = deepcopy(store.rows)
        wanted = ('helper_lifecycle_receipt' if fault == 'incomplete_activation'
                  else 'helper_setup_receipt')
        keys = [k for k, row in events.items() if row['payload'].get('kind') == wanted
                and (fault != 'incomplete_activation' or row['payload'].get('stage') == 'setup')]
        test.assertEqual(len(keys), 1)
        del events[keys[0]]
        await store.store.save({'schema': 1, 'events': events})
    if fault == 'tampered_composition':
        payload = fresh_preps.resolve(record.plan.native_inputs[0], record.plan.native_inputs[1])
        baseline, _ = fresh_preps.resolve_composition(record.plan.native_inputs)
        codec = module('native_template_composition')
        corruptions = []
        changed = deepcopy(payload['composition'])
        changed['evidence']['fields']['tokens'] = []
        corruptions.append(changed)
        changed = deepcopy(payload['composition'])
        changed['source_history']['state_ids'] = [True]
        corruptions.append(changed)
        changed = deepcopy(payload['composition'])
        changed['prepared_digests'][0][1] = 'bad'
        corruptions.append(changed)
        for changed in corruptions:
            with test.assertRaises(ValueError):
                codec.decode_composition(changed, baseline)
        # A persisted payload change cannot escape the original preparation pin.
        rows = deepcopy(fresh_preps.plans)
        changed = deepcopy(payload)
        changed['composition']['coverage_sha256'] = '0' * 64
        rows[record.plan.native_inputs[0]] = {'payload': changed, 'digest': prep.canonical(changed)[1]}
        await fresh_preps.store.save({'schema': 1, 'plans': rows})
    if fault == 'foreign_source':
        test.f.entity.async_update_entity(test.producer.entity_id, name='foreign edit after forward')
        await module('native_registry').NativeRegistryIO(hass).flush('entity')
        before_documents = await test._flush_documents()
    session = None
    lease = None
    try:
        async with runtime.operation_lock:
            lease = await lease_api.acquire(hass, hass.config_entries, (entry,), guard=guard)
            if fault not in (None, 'reverse_ack', 'reverse_replay'):
                kwargs = dict(activation_lease=lease, guard=guard, refresh=refresh)
                if fault == 'wrong_lease':
                    lease.release()
                if fault == 'wrong_head':
                    kwargs['expected_head'] = '0' * 64
                with test.assertRaises((ValueError, PermissionError, module('transaction').Refused)):
                    await rr.RegistryReceiptChain.async_bind_template_reverse(hass, operation,
                        '0' * 64 if fault == 'wrong_fingerprint' else fingerprint, **kwargs)
                revoked = False
                test._same_native_documents(before_documents, await test._flush_documents())
                test.assertEqual(entry.state.value, 'loaded')
                test.assertEqual(test.producer.entity_id, next(iter(mapping.values())))
                return
            baseline_before, composition_before = fresh_preps.resolve_composition(record.plan.native_inputs)
            readonly_store_before = rr.native_registry_receipt_store(hass, fresh_preps)
            await readonly_store_before.initialize()
            readonly_before = rr.RegistryReceiptChain.reopen(readonly_store_before, operation,
                record.plan, baseline_before.receipt, dict(composition_before.prepared_digests))
            test.assertFalse(readonly_before.writable)
            session = await rr.RegistryReceiptChain.async_bind_template_reverse(hass, operation,
                fingerprint, activation_lease=lease, guard=guard, refresh=refresh)
            test.assertFalse(readonly_before.writable)
            test.assertIsNot(session.journal, fresh_journal)
            test.assertIsNot(session.preparations, fresh_preps)
            test.assertEqual(session.plan.native_inputs[2], baseline_digest)
            test.assertEqual(session.chain.baseline.receipt_json, original_receipt_json)
            readonly = rr.RegistryReceiptChain.reopen(session.receipt_store, operation,
                session.plan, session.receipt, session.digests)
            test.assertFalse(readonly.writable)
            if fault == 'reverse_ack':
                native_save = session.receipt_store.store.save
                async def lose_ack(document):
                    await native_save(document)
                    if any(row['payload'].get('kind') == 'registry_receipt'
                           and row['payload'].get('binding', {}).get('direction') == 'rollback'
                           for row in document['events'].values()):
                        raise OSError('fresh reverse receipt ACK lost')
                with patch.object(session.receipt_store.store, 'save', lose_ack):
                    with test.assertRaises((ValueError, OSError, module('transaction').Refused)):
                        await session.rollback()
                with test.assertRaises((ValueError, module('transaction').Refused)):
                    await session.rollback()
                fresh = journal_api.native_journal(hass)
                await fresh.initialize()
                uncertain = await fresh.load(operation)
                test.assertNotEqual(uncertain.status, 'rolled_back')
                # No cleanup/setup after uncertain compensation. Fresh product
                # admission rejects nonterminal journal before granting a writer.
                with test.assertRaises((ValueError, module('transaction').Refused)):
                    await rr.RegistryReceiptChain.async_bind_template_reverse(hass, operation,
                        fingerprint, activation_lease=session.lease, guard=guard, refresh=refresh)
                return
            result = await session.rollback()
            test.assertEqual(result.status, 'rolled_back')
            if fault == 'reverse_replay':
                before = await test._flush_documents()
                with test.assertRaises(module('transaction').Refused):
                    await session.rollback()
                test._same_native_documents(before, await test._flush_documents())
            test.assertFalse(readonly.writable)
            test.assertFalse(readonly_before.writable)
            for value in (5, 7):
                await test.emit(value)  # Actual source events and consumer 10/14.
            current = test.f.entity.async_get(consumer_identity[0])
            test.assertEqual((current.entity_id, current.id, current.unique_id,
                current.config_entry_id, current.hidden_by, current.name), consumer_identity)
            test.assertEqual(module('helper_native').read_entry(hass, entry.entry_id), original)
            test.assertEqual(test.producer.entity_id, next(iter(mapping)))
            expected_source_uuid = next(row['id'] for row in session.observer.documents
                ['core.entity_registry']['entities'] if row['entity_id'] == next(iter(mapping)))
            test.assertEqual(test.f.entity.async_get(test.producer.entity_id).id, expected_source_uuid)
            test.assertTrue(await session.history.verify(session.composition.source_history))
            test.assertTrue(await session.history.verify(session.composition.consumer_history))
            # Reopen every durable reader again after reverse/setup. No object
            # from the forward operation is used, and readers remain readonly.
            post_preps = prep.native_prepared_store(hass)
            await post_preps.initialize()
            baseline, composition = post_preps.resolve_composition(result.plan.native_inputs)
            test.assertEqual(baseline.receipt_json, original_receipt_json)
            test.assertEqual(baseline.digest, baseline_digest)
            post_store = rr.native_registry_receipt_store(hass, post_preps)
            await post_store.initialize()
            post = rr.RegistryReceiptChain.reopen(post_store, operation, result.plan,
                                                  baseline.receipt, dict(composition.prepared_digests))
            test.assertFalse(post.writable)
            test.assertTrue(post.reconstruct_inputs().same_evidence(session.chain.reconstruct_inputs()))
            post_life = lm.native_helper_lifecycle_store(hass, post_preps)
            await post_life.initialize()
            readonly_life = lm.validate_lifecycle_receipt_witness(post_life, operation,
                result.plan, baseline.receipt, dict(composition.prepared_digests),
                root_id=session.lifecycle.root_id, source_authorization=session.authorization)
            test.assertFalse(readonly_life.writable)
            test.assertEqual(readonly_life.inspect()['pending'], [])
            test.assertTrue(readonly_life.inspect()['activation_receipts_complete'])
            if native_backend_lifecycle:
                test.assertIs(backend_entry.runtime_data, runtime)
                test.assertIs(hass.data.get(backend.DATA_RUNTIME), runtime)
                test.assertEqual(type(session).__module__, backend.__name__ + '.native_template_reverse')
                test.assertEqual(type(composition.evidence).__module__,
                                 backend.__name__ + '.yaml_source_binding')
                test._assert_backend_original_row(baseline)

    finally:
        if session is not None:
            session.close()
        elif lease is not None:
            lease.release()
