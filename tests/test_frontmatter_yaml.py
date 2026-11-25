"""Issue #121: every pack and agent front matter parses with a strict YAML reading, not only with DoIA's own loader.

`lib.parse_frontmatter` falls back to the bare text when a value is not JSON, so an unquoted description such as
`description: Rules for X: each plan ...` loaded here but fails a strict YAML parser (a plain scalar may not contain
`: `). The strict check below is `validate.strict_frontmatter_errors`; the standard library has no YAML loader, so it
reads the flat `key: value` subset these files use by the YAML 1.2 plain and quoted scalar rules."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402  (path set up above)
from lib import PACK_NAMES, ROLE_NAMES  # noqa: E402

DOC = '---\nname: "x"\ndescription: {value}\n---\n\nBody.\n'


def errors(value):
    return validate.strict_frontmatter_errors(DOC.format(value=value), "fixture")


class StrictCheckBites(unittest.TestCase):
    def test_plain_scalar_with_colon_space_fails(self):
        self.assertTrue(errors("Rules for Linux work: each plan names a target"))

    def test_plain_scalar_with_comment_marker_fails(self):
        self.assertTrue(errors("a rule # not a comment"))

    def test_indicator_start_and_trailing_colon_fail(self):
        for value in (
            "[unclosed list",
            "{unclosed map",
            "*alias",
            "&anchor x",
            "!tag x",
            "@at",
            "%dir",
            "| block",
            "ends:",
        ):
            with self.subTest(value):
                self.assertTrue(errors(value))

    def test_broken_quotes_fail(self):
        for value in ('"no end', '"bad \\q escape"', "'no end", '"a" trailing'):
            with self.subTest(value):
                self.assertTrue(errors(value))

    def test_duplicate_key_missing_end_and_nested_value_fail(self):
        self.assertTrue(validate.strict_frontmatter_errors('---\nname: "x"\nname: "y"\n---\n', "f"))
        self.assertTrue(validate.strict_frontmatter_errors('---\nname: "x"\n', "f"))
        self.assertTrue(validate.strict_frontmatter_errors("---\nname: x\n  nested: y\n---\n", "f"))
        self.assertTrue(validate.strict_frontmatter_errors("no front matter\n", "f"))

    def test_good_values_pass(self):
        for value in (
            '"Rules for Linux work: each plan names a target"',
            "'single: quoted'",
            '["a", "b"]',
            "plain words, commas ok",
            "a-b_c.d",
        ):
            with self.subTest(value):
                self.assertEqual(errors(value), [])


class EveryFrontMatterParses(unittest.TestCase):
    def collect(self):
        patterns = (
            "packs/*/PACK.md",
            "roles/*.md",
            ".claude/agents/*.md",
            ".github/agents/*.agent.md",
            ".claude/skills/*/SKILL.md",
            ".agents/skills/*/SKILL.md",
        )
        return {p: sorted(ROOT.glob(p)) for p in patterns}

    def test_counts_are_what_the_repository_declares(self):
        got = {p: len(v) for p, v in self.collect().items()}
        roles, packs = len(ROLE_NAMES), len(PACK_NAMES)
        self.assertEqual(
            got,
            {
                "packs/*/PACK.md": packs,
                "roles/*.md": roles,
                ".claude/agents/*.md": roles,
                ".github/agents/*.agent.md": roles,
                ".claude/skills/*/SKILL.md": packs,
                ".agents/skills/*/SKILL.md": packs,
            },
        )

    def test_every_file_parses_strictly(self):
        for files in self.collect().values():
            for f in files:
                with self.subTest(str(f.relative_to(ROOT))):
                    self.assertEqual(validate.strict_frontmatter_errors(f.read_text(encoding="utf-8"), f.name), [])

    def test_validator_runs_the_check_on_a_broken_tree(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            shutil.copytree(
                ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".ruff_cache"), symlinks=True
            )
            pack = root / "packs/linux-platform/PACK.md"
            text = pack.read_text(encoding="utf-8")
            line = next(ln for ln in text.splitlines() if ln.startswith("description: "))
            pack.write_text(
                text.replace(line, "description: " + line[len("description: ") :].strip('"')), encoding="utf-8"
            )
            self.assertTrue(any("front matter" in e and "linux-platform" in e for e in validate.validate(root)))


if __name__ == "__main__":
    unittest.main()
