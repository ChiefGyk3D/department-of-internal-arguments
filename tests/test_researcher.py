"""The researcher role and the provider-neutral `web` capability (issue #68)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import evals_matrix as em  # noqa: E402  (path set up above)
import export_prompt  # noqa: E402
import generate  # noqa: E402
import validate as validate_mod  # noqa: E402
from lib import CAPABILITY_ORDER, CLAUDE_TOOLS, COPILOT_TOOLS, ROLE_NAMES, frontmatter, load_roles  # noqa: E402
from test_spend_policy import fixture_errors  # noqa: E402
from test_user_layout import copy_fixture  # noqa: E402

REPORT_SECTIONS = ("QUESTION:", "ANSWER:", "CLAIMS:", "CONFLICTS:", "DATA SENT:", "NOT FOUND:", "OPEN:")


def researcher():
    rows = [r for r in load_roles(ROOT) if r["name"] == "researcher"]
    assert len(rows) == 1, "exactly one researcher role must be collected"
    return rows[0]


class RoleFile(unittest.TestCase):
    def test_role_is_registered_and_shaped(self):
        self.assertIn("researcher", ROLE_NAMES)
        r = researcher()
        self.assertEqual(r["tier"], "standard")
        self.assertEqual(r["tools"], ["read", "search", "web"])
        self.assertEqual(validate_mod.role_errors(r), [])

    def test_report_format_has_every_section_after_the_shared_header(self):
        body = researcher()["body"]
        last = -1
        for field in validate_mod.REPORT_FIELDS:
            self.assertIn(field, body)
        for section in REPORT_SECTIONS:
            pos = body.index(section, body.index("DISCREPANCIES:"))
            self.assertGreater(pos, last, section)
            last = pos

    def test_process_rules_present(self):
        body = researcher()["body"]
        for rule, phrase in validate_mod.RESEARCHER_RULES.items():
            self.assertIn(phrase, body, rule)

    def test_no_vendor_named_role_or_text(self):
        self.assertFalse([n for n in ROLE_NAMES if "gemini" in n.lower()])
        self.assertNotIn("gemini", researcher()["source"].lower())

    def test_api_only_provider_has_no_tools(self):
        body = researcher()["body"]
        self.assertIn("no tools", body)
        text = export_prompt.build("researcher", root=ROOT)
        self.assertIn("grants no tool access", text)
        self.assertIn("no tools", text)


class HostMappings(unittest.TestCase):
    def test_capability_is_known_and_mapped_everywhere(self):
        self.assertIn("web", CAPABILITY_ORDER)
        self.assertEqual(CLAUDE_TOOLS["web"], ("WebFetch", "WebSearch"))
        self.assertEqual(COPILOT_TOOLS["web"], "web")

    def test_generated_tool_lists(self):
        claude, _ = frontmatter(ROOT / ".claude/agents/researcher.md")
        self.assertEqual(claude["tools"], "Read, Grep, Glob, WebFetch, WebSearch")
        self.assertEqual(claude["model"], "claude-sonnet-5-5")
        copilot, _ = frontmatter(ROOT / ".github/agents/researcher.agent.md")
        self.assertEqual(copilot["tools"], ["read", "search", "web"])
        codex = (ROOT / ".codex/agents/researcher.toml").read_text(encoding="utf-8")
        self.assertIn("- web:", codex)
        self.assertIn("no edit capability", codex)

    def test_other_roles_do_not_gain_web(self):
        for name in ROLE_NAMES:
            if name == "researcher":
                continue
            claude, _ = frontmatter(ROOT / f".claude/agents/{name}.md")
            self.assertNotIn("Web", claude["tools"], name)
            codex = (ROOT / f".codex/agents/{name}.toml").read_text(encoding="utf-8")
            self.assertNotIn("- web:", codex, name)

    def test_unknown_capability_still_refused(self):
        role = dict(researcher(), tools=["read", "browse"])
        with self.assertRaises(ValueError):
            generate.render_claude_agent(role, "", "0.0.0")


class ValidatorBites(unittest.TestCase):
    def test_web_on_another_role_fails(self):
        r = dict(next(x for x in load_roles(ROOT) if x["name"] == "reviewer"))
        r["tools"] = [*r["tools"], "web"]
        self.assertTrue(any("web capability" in e for e in validate_mod.role_errors(r)))

    def test_researcher_with_edit_shell_or_skills_fails(self):
        for extra in ("edit", "shell", "skills"):
            r = dict(researcher(), tools=["read", "search", "web", extra])
            self.assertTrue(any("researcher" in e for e in validate_mod.role_errors(r)), extra)

    def test_researcher_without_web_fails(self):
        r = dict(researcher(), tools=["read", "search"])
        self.assertTrue(any("web capability" in e for e in validate_mod.role_errors(r)))

    def test_researcher_must_be_standard(self):
        r = dict(researcher(), tier="cheap")
        self.assertTrue(any("researcher" in e and "standard" in e for e in validate_mod.role_errors(r)))

    def test_each_process_rule_is_enforced(self):
        for rule, phrase in validate_mod.RESEARCHER_RULES.items():
            r = researcher()
            r = dict(r, body=r["body"].replace(phrase, "REMOVED"))
            self.assertTrue(any(rule in e for e in validate_mod.role_errors(r)), rule)

    def test_each_report_section_is_enforced(self):
        for section in REPORT_SECTIONS:
            r = researcher()
            r = dict(r, body=r["body"].replace(section, "X:"))
            self.assertTrue(any(section in e for e in validate_mod.role_errors(r)), section)

    def test_removing_the_role_file_fails_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            (fx / "roles/researcher.md").unlink()
            self.assertTrue(any("exactly the" in e for e in validate_mod.validate(fx)))


class LeadAndDocs(unittest.TestCase):
    def test_lead_dispatches_the_researcher(self):
        lead = (ROOT / "templates/lead.md").read_text(encoding="utf-8")
        self.assertNotIn("planned, not built", lead)
        self.assertIn(validate_mod.LEAD_RULES["researcher dispatchable"], lead)
        errs = fixture_errors(
            "templates/lead.md",
            lambda s: s.replace(validate_mod.LEAD_RULES["researcher dispatchable"], "REMOVED"),
            "researcher dispatchable",
        )
        self.assertTrue(errs)

    def test_docs_list_the_researcher(self):
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        self.assertIn("`researcher`", roles)
        self.assertIn("roles/researcher.md", roles)
        self.assertNotIn("planned but not built", roles)
        self.assertIn("web", roles)


class EvalCases(unittest.TestCase):
    def cases(self):
        return {c["id"]: c for c in em.load_registry(ROOT / "evals/cases.json")}

    def test_two_researcher_cases(self):
        cases = self.cases()
        picked = [c for c in cases.values() if c["roles"] == ["researcher"]]
        self.assertEqual([c["id"] for c in picked], [29, 30])
        for c in picked:
            self.assertEqual(c["grading"], "mechanical")
        unlabeled, injected = picked
        self.assertIn("without a source label", unlabeled["input"] + unlabeled["expect"])
        self.assertIn("fails", unlabeled["grading_reason"])
        self.assertIn("quote", injected["expect"].lower())
        self.assertIn("not obey", injected["expect"].lower())

    def test_registry_text_carries_no_real_identifier(self):
        for c in (self.cases()[29], self.cases()[30]):
            json.dumps(c)  # serializable
            self.assertEqual(em.identifier_problems(c["input"] + c["expect"]), [], c["id"])
        self.assertTrue(em.identifier_problems("see " + ".".join(["10", "1", "2", "3"]) + " now"))  # the sweep bites on a planted address


if __name__ == "__main__":
    unittest.main()
