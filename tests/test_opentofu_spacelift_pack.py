"""The opentofu-spacelift pack (issue #82, under #78): each required rule is present, every rule names where it was
measured, the sources carry fetch dates, the pack says what it does not cover, and it holds no private identifier."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACK = "opentofu-spacelift"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"
MAX_BYTES = 15000  # the largest existing pack is under this; there is no enforced cap, so keep to the existing range

# One regex per rule; each must match inside the pack. Loose to collect, strict to assert.
RULES = {
    "plan before apply": r"plan before (?:you )?apply",
    "blast radius named": r"blast radius",
    "rollback named": r"rollback",
    "one explicit stanza per stack": r"one explicit stanza",
    "no shared for_each for per-stack safety": r"for_each",
    "never apply to a live system without the maintainer": r"never apply to a live system without the maintainer",
    "MCP read-only by default": r"mcp:read.{0,200}(?:default|read-only)",
    "MCP write needs a named task": r"mcp:write.{0,300}named task",
    "mutate and intent are the write tools": r"`mutate`.{0,60}`intent`",
    "state handling": r"tofu state",
    "refresh-only for drift": r"-refresh-only",
    "drift detection in Spacelift": r"drift detection",
    "drift_detection policy input": r"drift_detection",
    "no-op first tracked run after a refactor": r"no-op",
    "run state read before a trigger": r"run state",
    "destructive Spacelift steps go to the maintainer": r"stackDelete",
    "fork trust boundary is a push policy": r"push policy",
    "saved plan applied as written": r"-out=",
    "limits section": r"## Limits",
    "does-not-cover section": r"## What this pack does not cover",
    "sources section": r"## Sources",
    "benchmark pending": r"benchmark.{0,80}#73.{0,80}pending|#73.{0,120}pending",
    "VERIFIED label": r"\bVERIFIED\b",
    "INFERRED label": r"\bINFERRED\b",
    "Spacelift MCP page cited with its fetch date": (
        r"docs\.spacelift\.io/concepts/intelligence/spacelift-mcp\.html.{0,60}2026-10-30"
    ),
    "Spacelift drift page cited with its fetch date": (
        r"spacelift\.io/concepts/stack/drift-detection\.html.{0,60}2026-10-30"
    ),
    "OpenTofu state page cited with its fetch date": r"opentofu\.org/docs/language/state/.{0,120}2026-10-30",
    "OpenTofu plan page cited with its fetch date": r"opentofu\.org/docs/cli/commands/plan/.{0,120}2026-10-30",
    "OpenTofu test page cited with its fetch date": r"opentofu\.org/docs/cli/commands/test/.{0,120}2026-10-30",
}
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|chiefgyk3d|renegade|penguin|clouds.against|\bcah-\d|\btor1\b|twingate_|doppler_token"
    r"|[a-z0-9-]+\.app\.(?:us\.)?spacelift\.io|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}",
    re.I,
)


def missing(text):
    return [name for name, pat in RULES.items() if not re.search(pat, text, re.S | re.I)]


class OpenTofuSpaceliftPack(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")

    def test_pack_is_registered_and_keeps_its_shape(self):
        self.assertIn(PACK, PACK_NAMES)
        self.assertTrue(self.text.startswith(f'---\nname: "{PACK}"\n'))
        self.assertIn("## How to verify", self.text)
        self.assertLessEqual(len(self.text.encode("utf-8")), MAX_BYTES)

    def test_every_rule_is_present(self):
        self.assertEqual(missing(self.text), [])

    def test_lessons_name_where_they_were_measured(self):
        self.assertGreaterEqual(len(re.findall(r"\(Source: ", self.text)), 10)

    def test_lessons_are_in_the_generated_skills(self):
        for rel in (f".claude/skills/pack-{PACK}/SKILL.md", f".agents/skills/pack-{PACK}/SKILL.md"):
            with self.subTest(rel):
                self.assertIn("## What this pack does not cover", (ROOT / rel).read_text(encoding="utf-8"))

    def test_no_private_values(self):
        self.assertIsNone(PRIVATE.search(self.text), PRIVATE.search(self.text))

    def test_guard_bites(self):
        # Negative cases: an empty pack misses every rule, one removed phrase misses exactly its rule, and each kind
        # of private value is caught by the sweep.
        self.assertEqual(len(missing("nothing here")), len(RULES))
        cut = re.sub(r"-refresh-only", "-x", self.text)
        self.assertEqual(missing(cut), ["refresh-only for drift"])
        for planted in (
            "/ho" + "me/someone/x",
            "acme.app." + "spacelift.io",
            "host 10.1." + "2.3",
            "user" + "@" + "example.com",
            "ca" + "h-00-foundation",
            "chiefgyk" + "3d",
        ):
            with self.subTest(planted):
                self.assertIsNotNone(PRIVATE.search(planted))


if __name__ == "__main__":
    unittest.main()
