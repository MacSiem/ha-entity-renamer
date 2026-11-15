"""Capture synthetic Energy boundary exceptions without changing control flow."""
from pathlib import Path
import traceback


BOUNDARY = frozenset({'energy_native.py', 'powercalc_energy_transaction.py',
                      'powercalc_energy_receipts.py', 'transaction.py'})


def energy_exception(code, error, *, component=False):
    path = Path(code.co_filename)
    if not isinstance(error, Exception) or not (
        path.name in BOUNDARY or (component and "ha_entity_renamer" in path.parts)
    ):
        return None
    return {'file': Path(code.co_filename).name, 'function': code.co_name,
            'type': type(error).__name__, 'message': str(error),
            'chain': ''.join(traceback.format_exception(error, chain=True)),
            'stack': [[Path(frame.filename).name, frame.name, frame.lineno]
                      for frame in traceback.extract_tb(error.__traceback__)]}
