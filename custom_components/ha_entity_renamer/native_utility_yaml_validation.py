"""Run a private isolated Utility source checker without granting activation."""
import asyncio
import json
from pathlib import Path
import re
import sys

from .yaml_validation import YAMLValidationError


async def utility_projection(before, after, mapping, stage, version, *, timeout):
    request = Path(stage) / 'utility-reference-mapping.json'
    request.write_text(json.dumps(mapping, sort_keys=True, separators=(',', ':'), allow_nan=False))
    request.chmod(0o600)
    worker = str(Path(__file__).with_name('native_utility_yaml_worker.py'))
    process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', worker,
        str(before), str(after), str(request), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    try:
        async with asyncio.timeout(timeout):
            stdout, _ = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    try:
        result = json.loads(stdout)
        row = result['runtime_expected']
        if (process.returncode != 0 or result.get('status') != 'valid'
                or result.get('version') != version or type(row) is not list or len(row) != 3
                or row[0] != 'utility_meter'
                or any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None
                       for value in row[1:]) or row[1] == row[2]):
            raise ValueError('utility_checker_result')
        return row
    except (KeyError, TypeError, ValueError):
        raise YAMLValidationError('utility_checker_rejected') from None
