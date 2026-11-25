"""Issue #121: the strict front matter check agrees with a real YAML loader (PyYAML `safe_load`) on types.

PyYAML is a test-only dependency (CI installs it; the scripts stay standard library only). A value a real loader reads
as anything but a string (null, a boolean, a number, a date) must fail the strict check, because DoIA's own loader
reads every unquoted value as a string and the two readers would then disagree."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402  (path set up above)

NON_STRING = (
    "null", "Null", "NULL", "~", "true", "True", "TRUE", "false", "False", "yes", "Yes", "no", "No", "on", "On", "off",
    "Off", "123", "-7", "+7", "0", "017", "0x1F", "0b101", "1_000", "1:30", "1.5", ".5", "1.0e+3", ".inf",
    "-.inf", ".nan", ".NaN", "2026-11-25", "2026-11-25 12:30:00", "2026-11-25T12:30:00Z", "<<", "=",
)  # fmt: skip
STRING = (
    "plain words", "a-b_c.d", "name 2026-11-25 stamped", "v1.2.3", "yesterday", "nullable", "1.2.3",
    '"quoted"', '"123"', '"null"', '"a\\x41"', '"tab\\there"', "'single'", "'it''s'", '"2026-11-25"',
    "1E3", "1e3", "1e+3", "1.5e3",
)  # fmt: skip


def real_type_is_string(value):
    try:
        data = yaml.safe_load(f'name: "x"\ndescription: {value}\n')
    except (yaml.YAMLError, ValueError):  # PyYAML raises ValueError for an out-of-range code point
        return False
    return isinstance(data, dict) and isinstance(data.get("description"), str)


def strict_ok(value):
    text = f'---\nname: "x"\ndescription: {value}\n---\n'
    return validate.strict_frontmatter_errors(text, "fixture") == []


class AgreesWithARealLoader(unittest.TestCase):
    def test_the_corpus_is_what_the_real_loader_says(self):
        for value in NON_STRING:
            self.assertFalse(real_type_is_string(value), value)
        for value in STRING:
            self.assertTrue(real_type_is_string(value), value)

    def test_a_non_string_never_passes_the_strict_check(self):
        for value in NON_STRING:
            with self.subTest(value):
                self.assertFalse(strict_ok(value))

    def test_a_string_the_real_loader_reads_passes(self):
        for value in STRING:
            with self.subTest(value):
                self.assertTrue(strict_ok(value))

    def test_escapes_the_real_loader_rejects_fail_the_strict_check(self):
        bs = chr(92)
        for body in ("U00110000", "UFFFFFFFF"):
            value = '"' + bs + body + '"'
            with self.subTest(value):
                self.assertFalse(real_type_is_string(value))
                self.assertFalse(strict_ok(value))

    def test_empty_value_is_null_and_fails(self):
        self.assertIsNone(yaml.safe_load("description:\n")["description"])
        self.assertFalse(strict_ok(""))

    def test_whatever_the_check_passes_is_a_string_for_the_real_loader(self):
        extra = ("a: b", "x #y", "{a: 1}", "- x", "? k", "!!int 5", "*x", "&a b", "| x", "> x", "@x", "`x")
        passed = [v for v in NON_STRING + STRING + extra if strict_ok(v)]
        self.assertGreater(len(passed), 10)  # something was collected
        for value in passed:
            with self.subTest(value):
                self.assertTrue(real_type_is_string(value), value)

    def test_every_front_matter_in_the_repository_has_string_values(self):
        patterns = (
            "packs/*/PACK.md",
            "roles/*.md",
            ".claude/agents/*.md",
            ".github/agents/*.agent.md",
            ".claude/skills/*/SKILL.md",
            ".agents/skills/*/SKILL.md",
        )
        seen = 0
        for pattern in patterns:
            for f in sorted(ROOT.glob(pattern)):
                lines = f.read_text(encoding="utf-8").splitlines()
                data = yaml.safe_load("\n".join(lines[1 : lines.index("---", 1)]))
                for key, value in data.items():
                    seen += 1
                    # `tools` is a list in the Copilot agents and in the role sources; everything else is a string.
                    expected = list if key == "tools" and isinstance(value, list) else str
                    with self.subTest(f"{f.name}:{key}"):
                        self.assertIsInstance(value, expected)
                        if key == "tools" and expected is list:
                            self.assertTrue(all(isinstance(i, str) for i in value))
        self.assertGreater(seen, 200)


if __name__ == "__main__":
    unittest.main()
