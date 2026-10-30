"""The cyber-tier-review pack (issue #58): wiring into the personas, presence in the generated output, evidence
labels on every CLI claim, and a sweep that no maintainer-specific value ships in it."""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evals_matrix as em  # noqa: E402
from lib import PACK_NAMES, ROLE_NAMES  # noqa: E402  (path set up above)

PACK = "cyber-tier-review"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"

# Shapes that would make the pack or its persona hooks maintainer-specific. The validator sweeps hostnames, addresses
# and home paths tree-wide; this adds account handles, enrollment wording and the private config locations.
PRIVATE_PATTERNS = {
    "account handle": r"chiefgyk3d|renegade-penguin",
    "work account": r"\bghsw\b|\bghwho\b",
    "enrollment wording": (
        r"individually approved|my (?:cyber|daybreak) (?:program )?(?:access|enrol)|enrol(?:l|led|lment) (?:in|for) "
    ),
    "home path": r"/ho" r"me/\w+|/Us" r"ers/\w+|~/dot" r"files|~/s" r"rc/",
    "host or address": r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b[\w-]+\.(?:lan|local|internal|home\.arpa)\b",
}
BRITISH = re.compile(
    r"\b(?:behaviour|colour|authoris\w*|defence|organis\w*|recognis\w*|analyse\w*|licence|catalogue)\b", re.I
)
LABEL = re.compile(r"\b(?:VERIFIED|INFERRED)\b")


def sweep(text):
    """Names of the private-value shapes found in text."""
    return sorted(name for name, pat in PRIVATE_PATTERNS.items() if re.search(pat, text, re.I))


def unlabeled_claims(text):
    """Rows of the pack's evidence table (lines starting with '| ' under the 'Evidence' heading) with no label."""
    section = text.split("## Evidence table", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in section.splitlines() if r.startswith("| ") and not r.startswith(("| Claim", "| ---"))]
    return rows, [r for r in rows if not LABEL.search(r)]


class Wiring(unittest.TestCase):
    def test_pack_is_registered_and_on_disk(self):
        self.assertIn(PACK, PACK_NAMES)
        self.assertTrue(PACK_PATH.is_file())

    def test_exactly_the_adversarial_reviewer_and_the_lead_reference_the_pack(self):
        sources = {r: (ROOT / "roles" / f"{r}.md").read_text(encoding="utf-8") for r in ROLE_NAMES}
        sources["lead"] = (ROOT / "templates" / "lead.md").read_text(encoding="utf-8")
        have = sorted(k for k, v in sources.items() if PACK in v)
        self.assertEqual(have, ["adversarial-reviewer", "lead"])

    def test_generated_output_contains_the_pack_and_the_persona_reference(self):
        for rel in (f".claude/skills/pack-{PACK}/SKILL.md", f".agents/skills/pack-{PACK}/SKILL.md"):
            with self.subTest(rel):
                self.assertIn("## How to verify", (ROOT / rel).read_text(encoding="utf-8"))
        for rel in (
            ".claude/agents/adversarial-reviewer.md",
            ".github/agents/adversarial-reviewer.agent.md",
            ".codex/agents/adversarial-reviewer.toml",
        ):
            with self.subTest(rel):
                self.assertIn(PACK, (ROOT / rel).read_text(encoding="utf-8"))

    def test_docs_list_the_pack(self):
        self.assertIn(f"packs/{PACK}/PACK.md", (ROOT / "docs" / "packs.md").read_text(encoding="utf-8"))

    def test_changelog_unreleased_mentions_the_pack(self):
        unreleased = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split("## [0.", 1)[0]
        self.assertIn(PACK, unreleased)


class PersonaTextCarriesNoValues(unittest.TestCase):
    def test_tier_model_and_flag_values_stay_out_of_the_personas(self):
        for rel in ("roles/adversarial-reviewer.md", "templates/lead.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for value in ("daybreak", "cyber-access-program", "gpt-", "access_programs"):
                with self.subTest(rel=rel, value=value):
                    self.assertNotIn(value, text.lower())


class Evidence(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")

    def test_every_claim_row_carries_a_label_and_something_was_collected(self):
        rows, bad = unlabeled_claims(self.text)
        self.assertGreaterEqual(len(rows), 8, rows)
        self.assertEqual(bad, [])

    def test_label_guard_bites_on_an_unlabeled_row(self):
        # plant the row inside the evidence section, just before the section that follows it
        section_end = self.text.split("## Evidence table", 1)[1].split("\n## ", 1)[0]
        planted = self.text.replace(
            section_end, section_end.rstrip("\n") + "\n| a claim with no label | nowhere |\n", 1
        )
        _, bad = unlabeled_claims(planted)
        self.assertEqual(bad, ["| a claim with no label | nowhere |"])

    def test_vendor_pages_are_cited_with_the_check_date(self):
        self.assertIn("2026-10-26", self.text)
        self.assertGreaterEqual(len(re.findall(r"https://developers\.openai\.com/\S+", self.text)), 3)

    def test_american_spelling(self):
        self.assertEqual(BRITISH.findall(self.text), [])

    def test_required_rules_are_present(self):
        for phrase in (
            "never reword",
            "never skip the review",
            "< /dev/null",
            "held review",
            "by PID",
            "not authenticated with one of your authorized hardware keys",
            "findings section",
            "physical hardware security key",
            "confirmed by the maintainer",
            "re-login with the physical key fixed it",
            "managed scan",
            "Record the previous mode first",
            "Verify against your vendor's current docs",
            "Change one variable",
            "ONE real review",
        ):
            with self.subTest(phrase):
                self.assertIn(phrase, self.text)


class PrivacySweep(unittest.TestCase):
    FILES = (
        PACK_PATH,
        ROOT / "roles" / "adversarial-reviewer.md",
        ROOT / "templates" / "lead.md",
        ROOT / "docs" / "packs.md",
        ROOT / ".claude" / "skills" / f"pack-{PACK}" / "SKILL.md",
        ROOT / ".agents" / "skills" / f"pack-{PACK}" / "SKILL.md",
    )

    def test_no_maintainer_specific_value_ships(self):
        for f in self.FILES:
            with self.subTest(f=f.name):
                self.assertTrue(f.is_file(), f)
                self.assertEqual(sweep(f.read_text(encoding="utf-8")), [])

    def test_sweep_bites_on_each_planted_value(self):
        planted = {
            "account handle": "run it as ChiefGyk3D",
            "work account": "switch with ghsw first",
            "enrollment wording": "my cyber access is individually approved",
            "home path": "config in /ho" + "me/someone/.codex",
            "host or address": "ssh to 192" + ".168.1.20 or box" + ".lan",
        }
        self.assertEqual(sorted(planted), sorted(PRIVATE_PATTERNS))
        base = PACK_PATH.read_text(encoding="utf-8")
        for name, value in planted.items():
            with self.subTest(name):
                self.assertIn(name, sweep(base + "\n" + value))


if __name__ == "__main__":
    unittest.main()


class EvalCase(unittest.TestCase):
    """Issue #71: one mechanical behavioral case names the pack (the registry allows it; no model call is made)."""

    def cases(self):
        return em.load_registry(ROOT / "evals/cases.json")

    def named(self):
        return [c for c in self.cases() if PACK in c["packs"]]

    def test_exactly_one_mechanical_case_names_the_pack(self):
        picked = self.named()
        self.assertEqual([c["id"] for c in picked], [31])
        case = picked[0]
        self.assertEqual(case["grading"], "mechanical")
        self.assertEqual(case["roles"], ["adversarial-reviewer"])
        self.assertIn(PACK, case["subject"])
        self.assertEqual(case["cost_class"], "fixture")

    def test_case_covers_every_pack_behavior_with_a_negative_condition(self):
        case = self.named()[0]
        expect = case["expect"].lower()
        reason = case["grading_reason"].lower()
        for phrase in ("high-trust tier", "same prompt", "never reword", "never skip", "held", "placeholder"):
            self.assertIn(phrase, expect, phrase)
        for phrase in ("stub call log", "fails the case", "no call on the default", "byte", "placeholder"):
            self.assertIn(phrase, reason, phrase)
        self.assertIn("not set", case["input"].lower() + expect)

    def test_case_text_carries_no_tier_model_flag_value_or_identifier(self):
        case = self.named()[0]
        text = json.dumps(case).lower()
        for value in ("daybreak", "gpt-", "access_programs", "cyber-access-program"):
            self.assertNotIn(value, text, value)
        self.assertEqual(em.identifier_problems(case["input"] + case["expect"] + case["grading_reason"]), [])
        planted = json.dumps(dict(case, expect=case["expect"] + " use daybreak_blue")).lower()
        self.assertIn("daybreak", planted)  # a planted tier value is what the loop above rejects

    def test_the_guard_bites_when_the_case_is_missing(self):
        stripped = [dict(c, packs=[]) if c["id"] == 31 else c for c in self.cases()]
        self.assertEqual([c for c in stripped if PACK in c["packs"]], [])  # the selector finds nothing without it
        self.assertTrue(self.named())
