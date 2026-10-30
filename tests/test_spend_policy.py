"""Spend and data-class policy: the policy lines, the brief `spend:` line, the lead rules, the evals (#66)."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import evals_matrix as em  # noqa: E402  (path set up above)
import validate as validate_mod  # noqa: E402
from test_user_layout import copy_fixture  # noqa: E402

PAID_OK = "spend: paid - approval in the human message at 2026-10-29 14:05 ET, cap 5.00 USD"


def fixture_errors(rel, mutate, needle):
    with tempfile.TemporaryDirectory() as d:
        fx = copy_fixture(d)
        f = fx / rel
        f.write_text(mutate(f.read_text(encoding="utf-8")), encoding="utf-8")
        return [e for e in validate_mod.validate(fx) if needle in e]


class SpendLineParser(unittest.TestCase):
    def errs(self, text):
        return validate_mod.spend_line_errors(text)

    def test_accepts_both_forms(self):
        self.assertEqual(self.errs("# Work brief\n\nspend: none - free-only\n- x:\n"), [])
        self.assertEqual(self.errs(f"# Work brief\n\n{PAID_OK}\n"), [])

    def test_rejects_each_bad_shape(self):
        bad = {
            "missing": "# Work brief\n- x:\n",
            "duplicate": "spend: none - free-only\nspend: none - free-only\n",
            "two values": "spend: none - free-only\nspend: " + PAID_OK[7:] + "\n",
            "alternatives": "spend: none - free-only | paid - approval in the human message at T, cap 5 USD\n",
            "empty": "spend:\n",
            "paid without approval": "spend: paid - cap 5.00 USD\n",
            "paid without cap": "spend: paid - approval in the human message at 2026-10-29 14:05 ET\n",
            "paid without time": "spend: paid - approval in the human message at , cap 5.00 USD\n",
            "paid unbounded cap": "spend: paid - approval in the human message at 14:05, cap unlimited\n",
            "leading spaces": "  spend: none - free-only\n",
            "inside a code fence": "```\nspend: none - free-only\n```\n",
            "crlf": "spend: none - free-only\r\n",
            "zero width": "spend:\u200b none - free-only\n",
            "free with cap": "spend: none - free-only, cap 5.00 USD\n",
        }
        for name, text in bad.items():
            with self.subTest(name):
                self.assertTrue(self.errs(text), name)


class BriefTemplate(unittest.TestCase):
    def test_template_carries_exactly_one_free_only_line(self):
        text = (ROOT / "templates/brief.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^spend:.*$", text, re.M), ["spend: none - free-only"])

    def test_validator_fails_when_the_template_line_is_broken(self):
        for name, mutate in {
            "removed": lambda s: s.replace("spend: none - free-only\n", ""),
            "duplicated": lambda s: s.replace("spend: none - free-only\n", "spend: none - free-only\n" * 2),
            "alternatives": lambda s: s.replace("none - free-only\n", "none - free-only | paid\n", 1),
        }.items():
            with self.subTest(name):
                self.assertTrue(fixture_errors("templates/brief.md", mutate, "spend"), name)

    def test_the_brief_doc_explains_both_forms(self):
        doc = (ROOT / "docs/writing-a-brief.md").read_text(encoding="utf-8")
        for tok in ("`spend:`", "none - free-only", "paid - approval in the human message at"):
            self.assertIn(tok, doc)


class PolicyLines(unittest.TestCase):
    def test_every_rule_is_present_and_removal_fails(self):
        self.assertGreaterEqual(len(validate_mod.POLICY_RULES), 10)
        for (pol, rule), phrase in validate_mod.POLICY_RULES.items():
            with self.subTest(rule):
                self.assertIn(phrase, (ROOT / "policies" / f"{pol}.md").read_text(encoding="utf-8"))
                errs = fixture_errors(f"policies/{pol}.md", lambda s, p=phrase: s.replace(p, "REMOVED"), "policies/")
                self.assertTrue(any(rule in e for e in errs), errs)

    def test_no_vendor_named_policy(self):
        for pol in validate_mod.POLICIES:
            text = (ROOT / "policies" / f"{pol}.md").read_text(encoding="utf-8").lower()
            self.assertNotIn("gemini", text)

    def test_stale_model_routing_pointer_is_fixed(self):
        text = (ROOT / "policies/model-routing.md").read_text(encoding="utf-8")
        self.assertNotIn("(see the README)", text)
        self.assertIn("docs/relationship-to-safo-and-gyst.md", text)


class LeadRules(unittest.TestCase):
    def test_new_rules_are_enforced(self):
        for rule in ("merge", "paid action", "data class", "researcher planned"):
            self.assertIn(rule, validate_mod.LEAD_RULES)
            phrase = validate_mod.LEAD_RULES[rule]
            errs = fixture_errors("templates/lead.md", lambda s, p=phrase: s.replace(p, "REMOVED"), "lead.md")
            self.assertTrue(any(rule in e for e in errs), (rule, errs))


class StalePointers(unittest.TestCase):
    def test_researcher_no_longer_waits_on_issue_20(self):
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        self.assertNotIn("waits for the Codex research input in issue 20", roles)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertNotIn("(waits for issue #20)", changelog)


class EvalCases(unittest.TestCase):
    def test_two_spend_cases(self):
        cases = {c["id"]: c for c in em.load_registry(ROOT / "evals/cases.json")}
        self.assertEqual(sorted(cases), list(range(1, 29)))
        for n in (27, 28):
            self.assertEqual(cases[n]["grading"], "mechanical")
            self.assertIn("spend", cases[n]["input"] + cases[n]["expect"])
        self.assertIn("spend:", cases[27]["expect"])
        self.assertIn("zero paid", cases[28]["expect"])
        self.assertIn("no paid call", cases[28]["grading_reason"])


if __name__ == "__main__":
    unittest.main()
