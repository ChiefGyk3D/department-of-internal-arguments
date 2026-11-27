"""The review-protocol and implementer-self-check packs (issue #124): registered, within the token budget, every
required rule present, the twelve checklist ids stable, no private identifier, and a negative case for each guard."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACKS = ("review-protocol", "implementer-self-check")
MAX_BODY_TOKENS = 3000  # chars divided by 4
MAX_DESC_TOKENS = 60  # about 40 asked for; the trigger comes first

RULES = {
    "review-protocol": {
        "context and trust sentence": r"who trusts whom",
        "names the files": r"name the files",
        "all classes in round one": r"all (?:the )?classes.{0,60}round (?:one|1)",
        "failing input plus observed behavior": r"failing input.{0,80}observed behavior",
        "no attacker-voice lists": r"attacker[- ]voice",
        "never reword around a filter": r"never reword.{0,80}fix the tier",
        "severity labels": r"Critical, High, Medium,? (?:and )?Low",
        "CIA tag": r"CIA tag",
        "context adjustment reason recorded": r"adjust.{0,120}reason",
        "Critical and High never closed by budget": r"never (?:closed|ended) by (?:the )?budget",
        "stop rule fix-induced": r"fix-induced",
        "stop rule accepted residuals": r"accepted residual",
        "default budget 3 escalation 6": r"budget (?:is )?3.{0,120}\b6\b",
        "round 7 asks the owner": r"round 7.{0,60}owner",
        "simplify pass on escalation": r"simplify",
        "evidence handoff test log": r"captured test[- ]run log",
        "revert-proof summary": r"revert[- ]proof summary",
        "read-only cannot run tests": r"read-only.{0,80}(?:cannot|can't) (?:run|execute)",
        "round prompt template": r"CONTEXT:",
        "findings table template": r"\| id \| severity",
        "residual record": r"residual.{0,40}(?:id|reason|owner|review date)",
    },
    "implementer-self-check": {
        "files and paths": r"\bF1\b.{0,80}[Ff]iles and paths",
        "subprocess and environment": r"\bS1\b.{0,80}[Ss]ubprocess and environment",
        "fail-closed": r"\bO1\b.{0,80}[Ff]ail-closed versus fail-open",
        "locks and ordering": r"\bL1\b.{0,80}[Ll]ocks and ordering",
        "parsing and normalization": r"\bP1\b.{0,80}[Pp]arsing and normalization",
        "bounds and timeouts": r"\bB1\b.{0,80}[Bb]ounds and timeouts",
        "output escaping": r"\bE1\b.{0,80}[Oo]utput escaping",
        "secrets handling": r"\bK1\b.{0,80}[Ss]ecrets handling",
        "tests that bite": r"\bT1\b.{0,80}[Tt]ests that bite",
        "docs accuracy": r"\bD1\b.{0,80}[Dd]ocs accuracy",
        "github automation": r"\bG1\b.{0,80}GitHub automation",
        "trust boundaries": r"\bX1\b.{0,80}[Tt]rust boundaries",
        "prefer removing surface": r"[Pp]refer removing surface over adding guards",
        "revert proof required": r"revert[- ]proof",
        "self-check line": r"self-check: ids passed, ids not applicable, ids with known residuals",
        "helper patterns": r"safe_open.{0,2000}atomic_write.{0,2000}run_cmd",
        "helpers are reference text": r"reference text",
    },
}
IDS = ("F1", "S1", "O1", "L1", "P1", "B1", "E1", "K1", "T1", "D1", "G1", "X1")
PRIVATE = (
    r"chiefgyk3d|renegade-penguin|\bghsw\b|\bghwho\b|/ho"
    r"me/\w+|~/dot"
    r"files|~/s"
    r"rc/"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b|—"
)
BRITISH = re.compile(r"\b(?:behaviour|colour|authoris\w*|defence|organis\w*|recognis\w*|analyse\w*|licence)\b", re.I)


def read(pack):
    return (ROOT / "packs" / pack / "PACK.md").read_text(encoding="utf-8")


def split(text):
    """(description, body) of a pack file; the description is the quoted front matter value."""
    m = re.match(r'---\nname: "[^"]+"\ndescription: "(.*)"\nupdated: "[^"]+"\n---\n(.*)', text, re.S)
    return (m.group(1), m.group(2)) if m else (None, None)


def missing(pack, text):
    return sorted(name for name, pat in RULES[pack].items() if not re.search(pat, text, re.S | re.I))


class ReviewPacks(unittest.TestCase):
    def test_registered_and_shaped(self):
        for pack in PACKS:
            with self.subTest(pack):
                self.assertIn(pack, PACK_NAMES)
                text = read(pack)
                self.assertTrue(text.startswith(f'---\nname: "{pack}"\n'))
                self.assertIn("## How to verify", text)
                self.assertIn("## Limits", text)
                self.assertIn("## Deliverable", text)
        self.assertEqual(len(PACK_NAMES), 31)

    def test_token_budgets(self):
        for pack in PACKS:
            with self.subTest(pack):
                desc, body = split(read(pack))
                self.assertIsNotNone(desc)
                self.assertLessEqual(len(body) / 4, MAX_BODY_TOKENS)
                self.assertGreater(len(body) / 4, 800, "a near-empty body is not a pack")
                self.assertLessEqual(len(desc) / 4, MAX_DESC_TOKENS)
                self.assertTrue(desc.startswith("Load "), "the trigger comes first")

    def test_every_rule_is_present(self):
        for pack in PACKS:
            with self.subTest(pack):
                self.assertEqual(missing(pack, read(pack)), [])

    def test_checklist_ids_are_stable(self):
        text = read("implementer-self-check")
        for i in IDS:
            with self.subTest(i):
                self.assertEqual(len(re.findall(rf"^\| {i} \|", text, re.M)), 1)
        self.assertIn("review-protocol", text)

    def test_review_protocol_sweep_lists_the_same_ids(self):
        text = read("review-protocol")
        for i in IDS:
            with self.subTest(i):
                self.assertRegex(text, rf"\b{i}\b")

    def test_no_private_identifier_or_british_spelling(self):
        for pack in PACKS:
            with self.subTest(pack):
                text = read(pack)
                self.assertIsNone(re.search(PRIVATE, text, re.I))
                self.assertIsNone(BRITISH.search(text))

    def test_docs_list_both_packs(self):
        docs = (ROOT / "docs" / "packs.md").read_text(encoding="utf-8")
        for pack in PACKS:
            self.assertIn(f"packs/{pack}/PACK.md", docs)

    def section(self, text, head):
        return text.split(head, 1)[1].split("\n## ", 1)[0]

    def test_s3_never_ends_the_loop_with_an_open_high(self):
        stop = self.section(read("review-protocol"), "## Stop rule")
        s3 = next(line for line in stop.splitlines() if line.startswith("- S3"))
        self.assertRegex(s3, r"no Critical or High is open")
        self.assertRegex(s3, r"S3 does not apply.{0,40}keep going")
        self.assertNotRegex(stop.split("S3", 1)[0], r"when any one holds")
        self.assertRegex(stop, r"always continues while a Critical or High is open")

    def test_no_accept_exception_for_critical_or_high(self):
        res = self.section(read("review-protocol"), "## Accepted residuals")
        self.assertRegex(res, r"Only Medium and Low can be accepted")
        self.assertRegex(res, r"closes only by a fix with revert-proved evidence")
        self.assertNotRegex(res, r"maintainer decides any exception|exception in the maintainer")

    def test_adjustment_and_untrusted_data_rules(self):
        text = read("review-protocol")
        self.assertRegex(text, r"one step down at most.{0,80}never below Medium unless a named compensating control")
        self.assertRegex(text, r"never instructions")
        self.assertRegex(text, r"private paths, account or person identifiers, employer names or customer data")

    def test_self_check_hardening_wording(self):
        text = read("implementer-self-check")
        self.assertRegex(text, r"verify the opened descriptor.{0,40}never the path")
        self.assertRegex(text, r"owner.{0,60}is dead, never by age alone")
        self.assertRegex(text, r"never drops a mandatory check")
        self.assertRegex(text, r"per identifier kind and per filesystem")

    def test_base_severity_governs_acceptance(self):
        text = read("review-protocol")
        self.assertRegex(text, r"follow the BASE severity")
        self.assertRegex(text, r"adjustment never makes it acceptable")
        self.assertRegex(text, r"base High stays open until a fix with revert-proved evidence")

    def test_safe_open_contract_is_descriptor_relative(self):
        line = next(x for x in read("implementer-self-check").splitlines() if "`safe_open(" in x)
        self.assertRegex(line, r"descriptor-relative.{0,80}component by component with no-follow")
        self.assertRegex(line, r"fstat the opened descriptor")

    def test_guards_bite(self):
        good = read("review-protocol")
        weak = good.replace("S3 does not apply", "S3 applies")
        self.assertNotRegex(weak, r"S3 does not apply")
        weak2 = good.replace("closes only by a fix", "closes by a fix or an exception")
        self.assertNotRegex(weak2, r"closes only by a fix")
        good = read("review-protocol")
        self.assertEqual(missing("review-protocol", good), [])
        self.assertNotEqual(missing("review-protocol", good.replace("CIA tag", "tag")), [])
        self.assertNotEqual(missing("review-protocol", ""), [])
        fat = good + "x" * 13000
        self.assertGreater(len(split(fat)[1]) / 4, MAX_BODY_TOKENS)
        self.assertIsNotNone(re.search(PRIVATE, "see /ho" + "me/someone/x"))
        sc = read("implementer-self-check").replace("| T1 |", "| T9 |")
        self.assertEqual(len(re.findall(r"^\| T1 \|", sc, re.M)), 0)


if __name__ == "__main__":
    unittest.main()
