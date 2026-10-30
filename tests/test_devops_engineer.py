"""The devops-engineer role (issue #87, under #78): GYST callers, head-pinned reads, no foreground waits, no loosening."""

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

REPORT_SECTIONS = validate_mod.DEVOPS_ENGINEER_SECTIONS
WRITE_TOOLS = {"edit", "web"}


def devops_engineer():
    rows = [r for r in load_roles(ROOT) if r["name"] == "devops-engineer"]
    assert len(rows) == 1, "exactly one devops-engineer role must be collected"
    return rows[0]


class RoleFile(unittest.TestCase):
    def test_role_is_registered_and_shaped(self):
        self.assertIn("devops-engineer", ROLE_NAMES)
        r = devops_engineer()
        self.assertEqual(r["tier"], "standard")
        self.assertEqual(r["tools"], ["read", "search", "shell", "skills"])
        self.assertEqual(validate_mod.role_errors(r), [])

    def test_tier_is_a_pinned_tier_never_the_most_capable(self):
        self.assertIn(devops_engineer()["tier"], TIER_MODEL)
        self.assertEqual(sorted(TIER_MODEL), ["cheap", "standard"])

    def test_no_edit_or_web_tool_is_granted(self):
        self.assertFalse(WRITE_TOOLS & set(devops_engineer()["tools"]))

    def test_report_format_has_every_section_after_the_shared_header(self):
        body = devops_engineer()["body"]
        last = -1
        for field in validate_mod.REPORT_FIELDS:
            self.assertIn(field, body)
        for section in REPORT_SECTIONS:
            pos = body.index(section, body.index("DISCREPANCIES:"))
            self.assertGreater(pos, last, section)
            last = pos

    def test_process_rules_present(self):
        body = devops_engineer()["body"]
        self.assertTrue(validate_mod.DEVOPS_ENGINEER_RULES, "the validator must pin rules")
        for rule, phrase in validate_mod.DEVOPS_ENGINEER_RULES.items():
            self.assertIn(phrase, body, rule)

    def test_names_every_gyst_workflow(self):
        body = devops_engineer()["body"]
        for wf in (
            "python-ci",
            "bash-ci",
            "tofu-ci",
            "arduino-ci",
            "security",
            "artifact-release",
            "python-package-release",
            "container-release",
            "verify-published",
        ):
            self.assertIn(f"`{wf}`", body, wf)

    def test_hands_off_to_the_other_roles_by_name(self):
        body = devops_engineer()["body"]
        for name in ("implementer", "reviewer", "adversarial-reviewer", "researcher"):
            self.assertIn(f"`{name}`", body, name)

    def test_names_the_pack_and_does_not_copy_its_content(self):
        body = devops_engineer()["body"]
        self.assertIn("`gha-security-dast`", body)
        # Phrases that are the pack's own content; the role must point to the pack, not restate it.
        for pack_only in (
            "pull_request_target",
            "bypassForcePushAllowances",
            "startup_failure",
            "ZapXmlConfiguration",
            "analysis_key",
            "retry-after",
        ):
            self.assertNotIn(pack_only, body, pack_only)
        pack = ROOT / "packs/gha-security-dast/PACK.md"
        self.assertEqual(frontmatter(pack)[0]["name"], "gha-security-dast")

    def test_no_real_identifier_in_the_source(self):
        text = (ROOT / "roles/devops-engineer.md").read_text(encoding="utf-8")
        self.assertEqual(em.identifier_problems(text), [])
        planted = text + "\nsee " + ".".join(["10", "1", "2", "3"]) + " now\n"
        self.assertTrue(em.identifier_problems(planted))  # the sweep bites on a planted address

    def test_no_private_name_in_the_source(self):
        text = (ROOT / "roles/devops-engineer.md").read_text(encoding="utf-8").lower()
        # Private notes are input only; names of the maintainer's own repositories, accounts and exceptions stay out.
        for private in ("hammunition", "chiefgyk3d", "dopp" + "ler", "ex-0001", "reach around", "stream-daemon"):
            self.assertNotIn(private, text, private)


class HostMappings(unittest.TestCase):
    def test_generated_tool_lists(self):
        claude, _ = frontmatter(ROOT / ".claude/agents/devops-engineer.md")
        self.assertEqual(claude["tools"], "Read, Grep, Glob, Bash, Skill")
        self.assertEqual(claude["model"], TIER_MODEL["standard"])
        copilot, _ = frontmatter(ROOT / ".github/agents/devops-engineer.agent.md")
        self.assertEqual(copilot["tools"], ["read", "search", "execute"])
        codex = (ROOT / ".codex/agents/devops-engineer.toml").read_text(encoding="utf-8")
        self.assertIn("no edit capability", codex)
        self.assertNotIn("- web:", codex)

    def test_edit_on_the_role_would_be_refused(self):
        r = dict(devops_engineer(), tools=["read", "search", "shell", "skills", "edit"])
        self.assertTrue(any("edit capability" in e for e in validate_mod.role_errors(r)))

    def test_generator_renders_the_role_for_every_host(self):
        out = generate.render_all(ROOT)
        for rel in (
            ".claude/agents/devops-engineer.md",
            ".github/agents/devops-engineer.agent.md",
            ".codex/agents/devops-engineer.toml",
        ):
            self.assertIn(rel, out)


class ValidatorBites(unittest.TestCase):
    def test_web_or_edit_fails(self):
        for extra in ("web", "edit"):
            r = dict(devops_engineer(), tools=[*devops_engineer()["tools"], extra])
            self.assertTrue(any("devops-engineer" in e or "capability" in e for e in validate_mod.role_errors(r)), extra)

    def test_without_shell_fails(self):
        r = dict(devops_engineer(), tools=["read", "search", "skills"])
        self.assertTrue(any("run commands" in e for e in validate_mod.role_errors(r)))

    def test_must_be_standard(self):
        for tier in ("cheap", "opus", "inherit"):
            r = dict(devops_engineer(), tier=tier)
            self.assertTrue(validate_mod.role_errors(r), tier)
        r = dict(devops_engineer(), tier="cheap")
        self.assertTrue(any("devops-engineer" in e and "standard" in e for e in validate_mod.role_errors(r)))

    def test_each_rule_is_enforced(self):
        for rule, phrase in validate_mod.DEVOPS_ENGINEER_RULES.items():
            r = devops_engineer()
            r = dict(r, body=r["body"].replace(phrase, "REMOVED"))
            self.assertTrue(any(rule in e for e in validate_mod.role_errors(r)), rule)

    def test_each_report_section_is_enforced(self):
        for section in REPORT_SECTIONS:
            r = devops_engineer()
            r = dict(r, body=r["body"].replace(section, "X:"))
            self.assertTrue(any(section in e for e in validate_mod.role_errors(r)), section)

    def test_removing_the_role_file_fails_the_tree(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            (fx / "roles/devops-engineer.md").unlink()
            self.assertTrue(any("exactly the" in e for e in validate_mod.validate(fx)))


class EvalCases(unittest.TestCase):
    def cases(self):
        return {c["id"]: c for c in em.load_registry(ROOT / "evals/cases.json")}

    def test_two_devops_engineer_cases(self):
        picked = [c for c in self.cases().values() if c["roles"] == ["devops-engineer"]]
        self.assertEqual([c["id"] for c in picked], [34, 35])
        for c in picked:
            self.assertEqual(c["grading"], "mechanical")
            json.dumps(c)  # serializable
            self.assertEqual(em.identifier_problems(c["input"] + c["expect"] + c["grading_reason"]), [], c["id"])
        merge_case, loosen_case = picked
        self.assertIn("merge", merge_case["expect"])
        self.assertIn("fails the case", merge_case["grading_reason"])
        self.assertIn("time bound", loosen_case["input"])
        self.assertIn("fails the case", loosen_case["grading_reason"])


class Docs(unittest.TestCase):
    def test_docs_list_the_role(self):
        roles = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        self.assertIn("`devops-engineer`", roles)
        self.assertIn("roles/devops-engineer.md", roles)
        brief = (ROOT / "docs/writing-a-brief.md").read_text(encoding="utf-8")
        self.assertIn("`devops-engineer`", brief)
        self.assertIn("devops-engineer", (ROOT / "templates/brief.md").read_text(encoding="utf-8"))

    def test_changelog_says_the_benchmark_is_pending(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        head = text.split("## [0.", 1)[0]
        self.assertIn("devops-engineer", head)
        self.assertIn("#73", head)
        self.assertIn("pending", head)


if __name__ == "__main__":
    unittest.main()
