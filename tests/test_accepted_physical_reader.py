"""Anchored physical-reader parity; no Home Assistant state is accessed."""
import importlib
import os
from pathlib import Path
import tempfile
import time
import json
import unittest
from unittest.mock import patch
import test_native_input_baseline as fixture

subject = importlib.import_module(fixture.PACKAGE + '.accepted_input_state')
plan = importlib.import_module(fixture.PACKAGE + '.yaml_plan')

class PhysicalReaderTests(unittest.TestCase):
    def test_anchored_reader_preserves_file_and_directory_symlink_refusals(self):
        cls = getattr(subject, '_PhysicalReader', None)
        self.assertTrue(callable(cls), 'fast physical reader required')
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / 'real').mkdir()
            (p / 'real' / 'source').write_bytes(b'synthetic')
            (p / 'alias').symlink_to('real', target_is_directory=True)
            (p / 'real' / 'link').symlink_to('source')
            reader = cls(root, plan.Limits())
            try:
                raw, info = reader.read('real/source')
                self.assertEqual(raw, b'synthetic')
                self.assertEqual(info.st_ino, (p / 'real' / 'source').stat().st_ino)
                for path in ('alias/source', 'real/link', '../source', '/etc/passwd'):
                    with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                        reader.read(path)
            finally:
                os.close(reader.root)

    def test_opened_file_and_directory_descriptors_are_not_inheritable(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            (p / 'dir').mkdir()
            (p / 'dir' / 'source').write_bytes(b'synthetic')
            reader = subject._PhysicalReader(root, plan.Limits())
            try:
                for fallback in (False, True):
                    with self.subTest(fallback=fallback), patch.object(
                            subject.sys, 'platform', 'fallback' if fallback else subject.sys.platform):
                        for path, directory in [('dir', True), ('dir/source', False)]:
                            fd = reader.open(path, directory=directory)
                            try:
                                self.assertFalse(os.get_inheritable(fd))
                            finally:
                                os.close(fd)
            finally:
                os.close(reader.root)

    def test_small_comparable_source_scan(self):
        cls = getattr(subject, '_PhysicalReader', None)
        self.assertTrue(callable(cls))
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            paths = []
            for group in range(18):
                directory = base / f'group_{group}'
                directory.mkdir()
                for item in range(134):
                    path = directory / f'source_{item}'
                    path.write_bytes(b'synthetic source\n')
                    paths.append(path.relative_to(base).as_posix())
            times = {'portable': [], 'physical': []}
            for label, reader_type in [('portable', plan._Reader), ('physical', cls),
                                       ('physical', cls), ('portable', plan._Reader)]:
                reader = reader_type(root, plan.Limits())
                try:
                    started = time.monotonic()
                    for path in paths:
                        data, _ = reader.read(path)
                        self.assertEqual(data, b'synthetic source\n')
                    times[label].append(time.monotonic() - started)
                finally:
                    os.close(reader.root)
            print('PHYSICAL_READER_SAMPLE ' + json.dumps({'files': len(paths), 'seconds': times}))
