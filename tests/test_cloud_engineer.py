"""The cloud-engineer role (issue #86, under #78): plans before it applies, read-only on live systems, names only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import evals_matrix as em  # noqa: E402  (path set up above)
import generate  # noqa: E402
import validate as validate_mod  # noqa: E402
from lib import ROLE_NAMES, TIER_MODEL, frontmatter, load_roles  # noqa: E402
from test_user_layout import copy_fixture  # noqa: E402

REPORT_SECTIONS = validate_mod.CLOUD_ENGINEER_SECTIONS
WRITE_TOOLS = {"edit", "web"}


def cloud_engineer():
    rows = [r for r in load_roles(ROOT) if r["name"] == "cloud-engineer"]
    assert len(rows) == 1, "exactly one cloud-engineer role must be collected"
    return rows[0]


class RoleFile(unittest.TestCase):
    def test_role_is_registered_and_shaped(self):
        self.assertIn("cloud-engineer", ROLE_NAMES)
        r = cloud_engineer()
        self.assertEqual(r["tier"], "standard")
        self.assertEqual(r["tools"], ["read", "search", "shell", "skills"])
        self.assertEqual(validate_mod.role_errors(r), [])

    def test_tier_is_a_pinned_tier_never_the_most_capable(self):
        self.assertIn(cloud_engineer()["tier"], TIER_MODEL)
        self.assertEqual(sorted(TIER_MODEL), ["cheap", "standard"])

    def test_no_edit_or_web_tool_is_granted(self):
        self.assertFalse(WRITE_TOOLS & set(cloud_engineer()["tools"]))

    def test_report_format_has_every_section_after_the_shared_header(self):
        body = cloud_engineer()["body"]
        last = -1
        for field in validate_mod.REPORT_FIELDS:
            self.assertIn(field, body)
        for section in REPORT_SECTIONS:
            pos = body.index(section, body.index("DISCREPANCIES:"))
            self.assertGreater(pos, last, section)
            last = pos

    def test_process_rules_present(self):
        body = cloud_engineer()["body"]
        self.assertTrue(validate_mod.CLOUD_ENGINEER_RULES, "the validator must pin rules")
        for rule, phrase in validate_mod.CLOUD_ENGINEER_RULES.items():
            self.assertIn(phrase, body, rule)

    def test_hands_off_to_the_other_roles_by_name(self):
        body = cloud_engineer()["body"]
        for name in ("implementer", "reviewer", "adversarial-reviewer", "researcher"):
            self.assertIn(f"`{name}`", body, name)

    def test_names_the_pack_and_does_not_copy_its_content(self):
        body = cloud_engineer()["body"]
        self.assertIn("`opentofu-spacelift`", body)
        # Phrases that are the pack's own content; the role must point to the pack, not restate it.
        for pack_only in (
            "mcp:write",
            "mcp:read",
            "for_each",
            "prevent_destroy",
            "stackDelete",
            "tofu show",
            "-refresh-only",
        ):
            self.assertNotIn(pack_only, body, pack_only)
        pack = ROOT / "packs/opentofu-spacelift/PACK.md"
        if pack.is_file():  # the pack lands in its own change; when present the name must match its frontmatter
            self.assertEqual(frontmatter(pack)[0]["name"], "opentofu-spacelift")

    def test_no_real_identifier_in_the_source(self):
        text = (ROOT / "roles/cloud-engineer.md").read_text(encoding="utf-8")
        self.assertEqual(em.identifier_problems(text), [])
        planted = text + "\nsee " + ".".join(["10", "1", "2", "3"]) + " now\n"
        self.assertTrue(em.identifier_problems(planted))  # the sweep bites on a planted address


class HostMappings(unittest.TestCase):
    def test_generated_tool_lists(self):
        claude, _ = frontmatter(ROOT / ".claude/agents/cloud-engineer.md")
        self.assertEqual(claude["tools"], "Read, Grep, Glob, Bash, Skill")
        self.assertEqual(claude["model"], TIER_MODEL["standard"])
        copilot, _ = frontmatter(ROOT / ".github/agents/cloud-engineer.agent.md")
        self.assertEqual(copilot["tools"], ["read", "search", "execute"])
        codex = (ROOT / ".codex/agents/cloud-engineer.toml").read_text(encoding="utf-8")
        self.assertIn("no edit capability", codex)
        self.assertNotIn("- web:", codex)

    def test_edit_on_the_role_would_be_refused(self):
        r = dict(cloud_engineer(), tools=["read", "search", "shell", "skills", "edit"])
        self.assertTrue(any("edit capability" in e for e in validate_mod.role_errors(r)))

    def test_generator_renders_the_role_for_every_host(self):
        out = generate.render_all(ROOT)
        for rel in (
            ".claude/agents/cloud-engineer.md",
            ".github/agents/cloud-engineer.agent.md",
            ".codex/agents/cloud-engineer.toml",
        ):
            self.assertIn(rel, out)


class ValidatorBites(unittest.TestCase):
    def test_web_or_edit_fails(self):
        for extra in ("web", "edit"):
            r = dict(cloud_engineer(), tools=[*cloud_engineer()["tools"], extra])
            self.assertTrue(any("cloud-engineer" in e or "capability" in e for e in validate_mod.role_errors(r)), extra)

    def test_without_shell_fails(self):
        r = dict(cloud_engineer(), tools=["read", "search", "skills"])
        self.assertTrue(any("shell" in e for e in validate_mod.role_errors(r)))

    def test_must_be_standard(self):
        for tier in ("cheap", "opus", "inherit"):
            r = dict(cloud_engineer(), tier=tier)
            self.assertTrue(validate_mod.role_errors(r), tier)
        r = dict(cloud_engineer(), tier="cheap")
        self.assertTrue(any("cloud-engineer" in e and "standard" in e for e in validate_mod.role_errors(r)))

    def test_each_rule_is_enforced(self):
        for rule, phrase in validate_mod.CLOUD_ENGINEER_RULES.items():
            r = cloud_engineer()
            r = dict(r, body=r["body"].replace(phrase, "REMOVED"))
            self.assertTrue(any(rule in e for e in validate_mod.role_errors(r)), rule)

    def test_each_report_section_is_enforced(self):
        for section in REPORT_SECTIONS:
            r = cloud_engineer()
            r = dict(r, body=r["body"].replace(section, "X:"))
            self.assertTrue(any(section in e for e in validate_mod.role_errors(r)), section)

    def test_removing_the_role_file_fails_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            (fx / "roles/cloud-engineer.md").unlink()
            self.assertTrue(any("exactly the" in e for e in validate_mod.validate(fx)))


class EvalCases(unittest.TestCase):
    def cases(self):
        return {c["id"]: c for c in em.load_registry(ROOT / "evals/cases.json")}

    def test_two_cloud_engineer_cases(self):
        picked = [c for c in self.cases().values() if c["roles"] == ["cloud-engineer"]]
        self.assertEqual([c["id"] for c in picked], [32, 33])
        for c in picked:
            self.assertEqual(c["grading"], "mechanical")
            json.dumps(c)  # serializable
            self.assertEqual(em.identifier_problems(c["input"] + c["expect"] + c["grading_reason"]), [], c["id"])
        apply_case, secret_case = picked
        self.assertIn("apply", apply_case["expect"])
        self.assertIn("fails the case", apply_case["grading_reason"])
        self.assertIn("names only", secret_case["expect"])
        self.assertIn("fails the case", secret_case["grading_reason"])


class Docs(unittest.TestCase):
    def test_docs_list_the_role(self):
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        self.assertIn("`cloud-engineer`", roles)
        self.assertIn("roles/cloud-engineer.md", roles)

    def test_changelog_says_the_benchmark_is_pending(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        head = text.split("## [0.", 1)[0]
        self.assertIn("cloud-engineer", head)
        self.assertIn("#73", head)
        self.assertIn("pending", head)


if __name__ == "__main__":
    unittest.main()
