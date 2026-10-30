"""The gha-security-dast pack's "GYST callers and release" section (issue #70): each rule is present, the pack keeps
its public name and its verify section, and the section carries sources and no private values."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACK = "gha-security-dast"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"
HEADING = "## GYST callers and release"

# One regex per rule; each must match inside the section. Loose to collect, strict to assert.
RULES = {
    "one commit for every caller": r"one commit",
    "gate named CI green": r"`CI green`",
    "required check read from branch protection": r"branches/\S+/protection",
    "protection before the first merge": r"before the first merge",
    "conflicting PR gets no checks, read mergeable": r"`mergeable`",
    "orphaned CodeQL categories": r"orphaned CodeQL|retired .{0,40}categor",
    "pin by commit with the version comment": r"version comment",
    "re-pin every caller on a GYST bump": r"re-pin every caller",
    "one tested watcher pins the head SHA": r"one tested watcher.{0,80}head SHA",
    "secondary limit with quota headroom": r"secondary rate limit.{0,200}(?:headroom|quota)",
    "writes serialized at least 1 s apart": r"at least 1 s",
    "never list a whole board per card": r"whole board",
    "read through REST": r"REST",
    "honor retry-after": r"retry-after",
    "GitHub docs cited with a date": r"docs\.github\.com/\S+.{0,200}2026-\d\d-\d\d",
    "SAFO issue 204 cited": r"SAFO (?:issue )?#?204",
}
PRIVATE = re.compile(r"/ho" r"me/\w+|~/s" r"rc/|chiefgyk3d|renegade-penguin|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)


def section(text):
    """The GYST section's text, or '' when the heading is absent."""
    if HEADING not in text:
        return ""
    return text.split(HEADING, 1)[1].split("\n## ", 1)[0]


def missing(text):
    s = section(text)
    return [name for name, pat in RULES.items() if not re.search(pat, s, re.S | re.I)]


class GystSection(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")

    def test_pack_keeps_its_public_name_and_verify_section(self):
        self.assertIn(PACK, PACK_NAMES)
        self.assertTrue(self.text.startswith(f'---\nname: "{PACK}"\n'))
        self.assertIn("## How to verify", self.text)

    def test_section_exists_once_with_every_rule(self):
        self.assertEqual(self.text.count(HEADING), 1)
        self.assertTrue(section(self.text).strip())
        self.assertEqual(missing(self.text), [])

    def test_every_rule_names_where_it_was_measured(self):
        s = section(self.text)
        self.assertGreaterEqual(len(re.findall(r"\(Source: ", s)), 6)

    def test_section_is_in_both_generated_skills(self):
        for rel in (f".claude/skills/pack-{PACK}/SKILL.md", f".agents/skills/pack-{PACK}/SKILL.md"):
            with self.subTest(rel):
                self.assertIn(HEADING, (ROOT / rel).read_text(encoding="utf-8"))

    def test_no_private_values(self):
        self.assertIsNone(PRIVATE.search(section(self.text)))

    def test_guard_bites(self):
        # Negative cases: no section, a section with one rule removed, and a private value planted.
        self.assertEqual(len(missing("no section here")), len(RULES))
        body = section(self.text)
        cut = re.sub(r"at least 1 s", "later", body)
        self.assertEqual(missing(f"{HEADING}\n{cut}"), ["writes serialized at least 1 s apart"])
        self.assertIsNotNone(PRIVATE.search("see /ho" "me/someone/x"))


if __name__ == "__main__":
    unittest.main()
