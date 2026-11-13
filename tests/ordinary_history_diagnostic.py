"""Record only synthetic Recorder metadata identities on a strict mismatch."""


def history_identity_diff(metadata, snapshot):
    result = {}
    for label, rows, key, expected in (
        ('states', metadata.get('states', {}), 'metadata_id', snapshot.get('state_ids', [])),
        ('statistics', metadata.get('statistics', {}), 'id', snapshot.get('statistic_ids', [])),
    ):
        actual_ids = sorted({row[key] for row in rows.values()})
        expected_ids = sorted(expected)
        result[label] = {'actual': actual_ids, 'expected': expected_ids,
                         'new': sorted(set(actual_ids) - set(expected_ids)),
                         'missing': sorted(set(expected_ids) - set(actual_ids))}
    return result


def history_from_frame(frame):
    while frame is not None:
        if frame.f_code.co_name == 'compare' and 'metadata' in frame.f_locals and 'snapshot' in frame.f_locals:
            return history_identity_diff(frame.f_locals['metadata'], frame.f_locals['snapshot'])
        frame = frame.f_back
    return None
