"""strict_json refuses deep nesting by counting brackets itself, so the answer does not depend on how the running
Python's JSON scanner fails (RecursionError before 3.14, a JSONDecodeError after; issue #81)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evals_matrix as em  # noqa: E402  (path set up above)


class DepthCap(unittest.TestCase):
    def test_deep_array_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nested too deeply"):
            em.strict_json("[" * 100000)

    def test_deep_object_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nested too deeply"):
            em.strict_json('{"a":' * 1000 + "1" + "}" * 1000)

    def test_mixed_nesting_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nested too deeply"):
            em.strict_json('[{"a":' * 500)

    def test_moderate_nesting_still_parses(self):
        self.assertEqual(em.strict_json("[" * 50 + "]" * 50), eval("[" * 50 + "]" * 50))

    def test_brackets_inside_strings_do_not_count(self):
        text = '{"k": "' + "[" * 5000 + '", "e": "\\"' + "{" * 5000 + '"}'
        self.assertEqual(em.strict_json(text)["k"], "[" * 5000)

    def test_an_escaped_quote_does_not_end_the_string(self):
        text = '["\\\\", "\\"' + "[" * 5000 + '"]'
        self.assertEqual(len(em.strict_json(text)), 2)


if __name__ == "__main__":
    unittest.main()
