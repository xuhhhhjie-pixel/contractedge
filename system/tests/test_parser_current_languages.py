import unittest
from contractedge.source.parsers import parse_source_units


class CurrentLanguageTests(unittest.TestCase):
    def test_python_ast_finds_async_function_and_direct_callee(self):
        text = '@decorate\nasync def reload_config(value):\n    return apply_config(value)\n'
        units = parse_source_units('watch.py', text, 'S0')
        self.assertEqual(units[0]['parser_kind'], 'PYTHON_AST_DECLARATION')
        self.assertEqual(units[0]['symbol'], 'reload_config')
        self.assertIn('apply_config', units[0]['references'])
        self.assertIn('@decorate', units[0]['text'])

    def test_c_function_excludes_comment_brace_from_boundary(self):
        text = 'static int route_key(int key) {\n  /* } */ return hash_key(key);\n}\n'
        units = parse_source_units('route.c', text, 'S0')
        self.assertEqual(units[0]['parser_kind'], 'C_LEXICAL_DECLARATION')
        self.assertEqual(units[0]['symbol'], 'route_key')
        self.assertIn('hash_key', units[0]['references'])

    def test_java_method_has_explicit_lexical_provenance(self):
        text = 'public static String transform(String value) {\n  return normalize(value);\n}\n'
        units = parse_source_units('Transform.java', text, 'S0')
        self.assertEqual(units[0]['parser_kind'], 'JAVA_LEXICAL_DECLARATION')
        self.assertEqual(units[0]['symbol'], 'transform')

    def test_invalid_python_uses_marked_fallback(self):
        units = parse_source_units('broken.py', 'def broken(:\n  pass\n', 'S0')
        self.assertEqual(units[0]['parser_kind'], 'BOUNDED_TEXT_FALLBACK')
