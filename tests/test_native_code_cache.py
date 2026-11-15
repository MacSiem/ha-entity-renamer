"""Compilation reuse does not reuse a source-validation verdict."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


path = Path(__file__).parents[1] / 'custom_components/ha_entity_renamer/native_code_cache.py'
spec = importlib.util.spec_from_file_location('native_code_cache_tested', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
compiled = module.compiled_code_objects


class NativeCodeCacheTests(unittest.TestCase):
    def setUp(self):
        compiled.cache_clear()

    def test_repeated_exact_source_compiles_once_and_keeps_nested_code(self):
        source = b'def outer():\n    def inner():\n        return 17\n    return inner\n'
        with patch.object(module, 'compile', wraps=compile, create=True) as compiler:
            first = compiled(source, '/native/source.py', 0)
            for _ in range(10):
                self.assertIs(compiled(source, '/native/source.py', 0), first)
            self.assertEqual(compiler.call_count, 1)
        self.assertEqual([code.co_qualname for code in first],
                         ['<module>', 'outer', 'outer.<locals>.inner'])

    def test_changed_bytes_do_not_match_previous_function_code(self):
        before = compiled(b'def value(): return 17\n', '/native/source.py', 0)
        after = compiled(b'def value(): return 19\n', '/native/source.py', 0)
        self.assertNotEqual(before[1], after[1])
        self.assertIs(compiled(b'def value(): return 17\n', '/native/source.py', 0), before)

    def test_filename_and_optimization_are_part_of_compiler_input(self):
        source = b'def value():\n    assert False\n    return 17\n'
        first = compiled(source, '/native/first.py', 0)
        other = compiled(source, '/native/other.py', 0)
        optimized = compiled(source, '/native/first.py', 1)
        self.assertEqual(other[1].co_filename, '/native/other.py')
        self.assertIsNot(first, other)
        self.assertNotEqual(first[1], optimized[1])

    def test_invalid_replacement_source_still_raises(self):
        compiled(b'def value(): return 17\n', '/native/source.py', 0)
        with self.assertRaises(SyntaxError):
            compiled(b'def broken(', '/native/source.py', 0)

    def test_cache_is_bounded(self):
        for value in range(40):
            compiled(f'value = {value}\n'.encode(), '/native/source.py', 0)
        self.assertEqual(compiled.cache_info().currsize, 32)


if __name__ == '__main__':
    unittest.main()
