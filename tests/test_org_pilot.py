"""The organization pilot (issue #125): three roles (analyst, assessor, planner) and five packs (threat-detection,
grc-controls, delivery-business-mgmt, org-operating-model, org-dispatch). Registry, shape, size, every required rule,
source labels, router links, privacy, generated outputs and a negative case for each guard."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402  (path set up above)
from lib import PACK_NAMES, ROLE_NAMES, load_packs, load_roles  # noqa: E402

PACKS = ("threat-detection", "grc-controls", "delivery-business-mgmt", "org-operating-model", "org-dispatch")
ROLES = ("analyst", "assessor", "planner")
ROLE_TOOLS = {
    "analyst": ["read", "search", "shell", "skills"],
    "assessor": ["read", "search", "skills"],
    "planner": ["read", "search", "skills"],
}
MAX_BODY_CHARS = 12000  # 3,000 tokens at four characters a token
MAX_DESC_CHARS = 200  # about 40 tokens asked for; the trigger comes first
MIN_BODY_CHARS = 3200
MAX_ROLE_DESC_CHARS = 220  # roles list at about 40 tokens each

PACK_RULES = {
    "threat-detection": {
        "id format": r"T####(?:\.###)?",
        "version read": r"ATT&CK version",
        "id from memory": r"typed from memory.{0,80}UNVERIFIED",
        "two test events": r"fires.{0,120}must not fire",
        "false-positive budget": r"false-positive budget",
        "owner and runbook": r"owner.{0,60}runbook",
        "alert diet": r"alert diet",
        "incidents per shift": r"2 incidents per 12-hour",
        "rule test events coverage": r"rule, its test events and its coverage row",
        "never deploys": r"never deploys",
        "tier 1": r"Tier 1.{0,120}runbook.{0,120}escalat",
        "never contains": r"never contain",
        "classification": r"benign true positive",
        "copies of evidence": r"copies of the evidence",
        "log text is data": r"log text is data",
        "empty hunt": r"no match in",
        "coverage table": r"coverage table",
        "log source inventory": r"log source inventory",
        "shift handoff": r"shift handoff",
        "osint limits": r"never private individuals",
        "not covered": r"NOT COVERED",
    },
    "grc-controls": {
        "control table": r"requirement text.{0,300}evidence path.{0,300}owner",
        "never compliant": r'never write "compliant" without',
        "not assessed": r"not assessed",
        "publisher page": r"publisher's page",
        "unverified when unread": r"UNVERIFIED",
        "csf functions": r"Govern, Identify, Protect, Detect, Respond, Recover",
        "cis ig1": r"IG1",
        "crosswalk needs two": r"both requirement texts",
        "risk statement": r"cause, event, consequence",
        "owner sets scales": r"scale.{0,100}owner",
        "exceptions": r"compensating control.{0,100}expiry",
        "agent never accepts": r"never accepts",
        "self-assessment": r"SELF-ASSESSMENT",
        "sampling": r"population, sample size",
        "no legal advice": r"[Nn]o legal advice",
        "public claims held": r"HELD for the owner",
        "stale evidence": r"stale",
        "no personal data": r"no personal data",
    },
    "delivery-business-mgmt": {
        "owner date next action": r"owner, date and next action",
        "raid": r"RAID",
        "critical path by code": r"critical path.{0,160}code",
        "top three risks": r"top three risks",
        "capacity ceiling": r"capacity ceiling",
        "cost gates": r"cost gates.{0,100}up front",
        "scrum": r"Developers, Product Owner and Scrum Master",
        "dora": r"change lead time",
        "metric pairs": r"never a target on its own",
        "stale items": r"stale",
        "never approves": r"never approves",
        "lessons need action": r"indistinguishable from no postmortem",
        "estimates labeled": r"estimate",
        "decision log": r"templates/adr\.md",
    },
    "org-operating-model": {
        "three lines": r"first line.{0,600}second line.{0,600}third line",
        "both sides": r"not on both sides of one decision",
        "raci access": r"Access request and joiner-mover-leaver",
        "raci change": r"Change and release",
        "raci incident": r"Incident response",
        "raci vuln": r"Vulnerability management",
        "raci vendor": r"Vendor and third-party risk",
        "raci backup": r"Backup and DR test",
        "raci audit": r"Audit and evidence",
        "raci onboarding": r"Onboarding of a new service",
        "raci exception": r"Exception and risk acceptance",
        "exactly one A": r"exactly one A",
        "incident roles": r"Incident Commander.{0,400}Operations Lead.{0,400}Communications Lead.{0,400}Planning Lead",
        "ops only modifies": r"only group modifying the system",
        "on-call numbers": r"50%.{0,300}25%",
        "small team": r"[Ss]mall team",
        "sod rules": r"\bSD1\b.{0,3000}\bSD13\b",
        "approver of record": r"approver of record",
        "mode map": r"researcher.{0,800}analyst.{0,800}assessor.{0,800}planner",
        "not a policy": r"not (?:yet )?an adopted policy",
        "four to keep": r"separate admin from daily accounts",
    },
    "org-dispatch": {
        "at most three": r"at most three packs",
        "refuses more": r"[Rr]efuses? .{0,80}more than three",
        "title to mode": r"one mode",
        "planned not built": r"Planned, not built",
        "seniority": r"[Ss]eniority is a brief setting",
        "approver of record": r"approver of record",
        "reviewer on request": r"reviewer on request",
        "name the role": r"names? the role",
        "no pack for no match": r"no alias matches",
    },
}
ROLE_RULES = validate.ORG_ROLE_RULES
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|~/\.local|renegade|penguin|clouds.against|chiefgyk|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\bghsw\b|\bghwho\b|—",
    re.I,
)
BRITISH = re.compile(r"\b(?:behaviour|colour|authoris\w*|defence|organis\w*|recognis\w*|analyse\w*|licence)\b", re.I)
# Framework names a pack may not use unless its Sources section carries a line for them.
FRAMEWORKS = (
    r"ATT&CK",
    r"NIST CSF|CSF 2\.0",
    r"CIS Control",
    r"ISO 27001",
    r"SOC 2",
    r"COBIT",
    r"ITIL",
    r"PMBOK",
    r"PRINCE2",
    r"DORA",
    r"Scrum Guide",
    r"SRE book|Google SRE",
    r"Three Lines",
)


def shape_problems(pack):
    """Size and description problems of one loaded pack (a dict from lib.load_packs)."""
    out = []
    desc, body = pack["description"], pack["body"]
    if not desc.startswith("Load "):
        out.append("description must start with Load")
    if len(desc) > MAX_DESC_CHARS:
        out.append("description too long")
    if len(body) > MAX_BODY_CHARS:
        out.append("body too long")
    if len(body) <= MIN_BODY_CHARS:
        out.append("body near empty")
    for h in ("## How to verify", "## Limits", "## Deliverable", "## Sources"):
        if h not in body:
            out.append(f"missing {h}")
    return out


def style_problems(text):
    out = []
    if PRIVATE.search(text):
        out.append("private value or em dash")
    if BRITISH.search(text):
        out.append("British spelling")
    return out


def mutated_tree(case, pack, edit):
    """A temporary tree holding one pack, edited by `edit(text)`; cleaned up with the test case."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    d = Path(tmp.name) / "packs" / pack
    d.mkdir(parents=True)
    (d / "PACK.md").write_text(edit(pack_text(pack)), encoding="utf-8")
    return Path(tmp.name)


def pack_text(pack):
    return (ROOT / "packs" / pack / "PACK.md").read_text(encoding="utf-8")


def role_text(role):
    return (ROOT / "roles" / f"{role}.md").read_text(encoding="utf-8")


def split(text):
    m = re.match(r'---\nname: "[^"]+"\ndescription: "(.*)"\nupdated: "[^"]+"\n---\n(.*)', text, re.S)
    return (m.group(1), m.group(2)) if m else (None, None)


def missing(pack, text):
    return sorted(n for n, pat in PACK_RULES[pack].items() if not re.search(pat, text, re.S | re.I))


def sources_section(body):
    m = re.search(r"\n## Sources\n(.*?)(?=\n## |\Z)", body, re.S)
    return m.group(1) if m else None


def source_problems(body):
    """Every framework named outside Sources needs a Sources line that is VERIFIED (URL, date read) or UNVERIFIED."""
    src = sources_section(body)
    if src is None:
        return ["no Sources section"]
    problems = []
    lines = [ln for ln in src.splitlines() if ln.startswith("- ")]
    for ln in lines:
        if ln.startswith("- VERIFIED"):
            if not re.search(r"https://\S+", ln) or not re.search(r"read 20\d\d-\d\d-\d\d", ln):
                problems.append(f"VERIFIED line without URL and date read: {ln[:60]}")
        elif not (ln.startswith("- UNVERIFIED") or ln.startswith("- INFERRED")):
            problems.append(f"source line not labeled: {ln[:60]}")
    outside = body.replace(src, "")
    for fw in FRAMEWORKS:
        if re.search(fw, outside) and not any(re.search(fw, ln) for ln in lines):
            problems.append(f"framework {fw!r} named with no Sources line")
    return problems


def router_rows(body):
    rows = []
    for ln in body.splitlines():
        m = re.fullmatch(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", ln)
        if m and not set(m.group(1)) <= set("-: ") and m.group(1) != "Job title":
            rows.append(m.groups())
    return rows


def router_problems(body, known):
    """Every alias row names at most three packs, and every pack named is built or listed as planned."""
    problems = []
    planned = set(re.findall(r"`([a-z-]+)`", (re.search(r"Planned, not built[^\n]*", body) or [""])[0]))
    for title, _mode, packs, _note in router_rows(body):
        names = re.findall(r"`([a-z0-9-]+)`", packs)
        if not names:
            problems.append(f"{title}: no pack named")
        if len(names) > 3:
            problems.append(f"{title}: {len(names)} packs, the limit is three")
        for n in names:
            if n not in known and n not in planned:
                problems.append(f"{title}: unknown pack {n}")
    return problems


class Registry(unittest.TestCase):
    def test_registered(self):
        for r in ROLES:
            self.assertIn(r, ROLE_NAMES)
        for p in PACKS:
            self.assertIn(p, PACK_NAMES)
        self.assertEqual(len(ROLE_NAMES), 12)
        self.assertEqual(len(PACK_NAMES), 30)

    def test_generated_outputs_exist(self):
        for r in ROLES:
            for rel in (f".claude/agents/{r}.md", f".github/agents/{r}.agent.md", f".codex/agents/{r}.toml"):
                with self.subTest(rel):
                    self.assertTrue((ROOT / rel).is_file())
        for p in PACKS:
            for rel in (f".claude/skills/pack-{p}/SKILL.md", f".agents/skills/pack-{p}/SKILL.md"):
                with self.subTest(rel):
                    self.assertIn("## How to verify", (ROOT / rel).read_text(encoding="utf-8"))


class Roles(unittest.TestCase):
    def test_shape(self):
        by = {r["name"]: r for r in load_roles(ROOT)}
        self.assertEqual(sorted(by), sorted(ROLE_NAMES))
        for r in ROLES:
            with self.subTest(r):
                self.assertEqual(by[r]["tools"], ROLE_TOOLS[r])
                self.assertEqual(by[r]["tier"], "standard")
                self.assertLessEqual(len(by[r]["description"]), MAX_ROLE_DESC_CHARS)
                self.assertNotIn("edit", by[r]["tools"])
                self.assertNotIn("web", by[r]["tools"])
                self.assertEqual(validate.role_errors(by[r]), [])

    def test_every_role_rule_is_present(self):
        for r in ROLES:
            with self.subTest(r):
                text = role_text(r)
                self.assertEqual([k for k, ph in ROLE_RULES[r].items() if ph not in text], [])

    def test_pilot_marking_and_targets(self):
        for r in ROLES:
            with self.subTest(r):
                m = re.search(r"## Pilot status\n(.*?)(?=\n## |\n\{\{|\Z)", role_text(r), re.S)
                self.assertIsNotNone(m)
                section = m.group(1)
                self.assertRegex(section, r"prediction, not a finding")
                self.assertRegex(section, r"[Mm]easurement targets?")
                self.assertRegex(section, r"\d")

    def test_report_sections(self):
        sections = {
            "analyst": (
                "EVIDENCE:",
                "TIMELINE:",
                "HYPOTHESES:",
                "OPTIONS FOR THE HUMAN:",
                "NOT COVERED:",
                "FINDINGS:",
                "ESCALATIONS:",
            ),
            "assessor": ("CONTROL TABLE:", "EXCEPTIONS:", "INDEPENDENCE:", "NOT COVERED:", "FINDINGS:", "ESCALATIONS:"),
            "planner": (
                "PLAN:",
                "CRITICAL PATH:",
                "TOP THREE RISKS:",
                "RAID:",
                "DECISIONS NEEDED:",
                "FINDINGS:",
                "ESCALATIONS:",
            ),
        }
        for r, wanted in sections.items():
            for s in wanted:
                with self.subTest((r, s)):
                    self.assertIn(s, role_text(r))

    def test_validator_bites_when_a_role_rule_is_cut(self):
        by = {r["name"]: r for r in load_roles(ROOT)}
        for r in ROLES:
            for key, phrase in ROLE_RULES[r].items():
                with self.subTest((r, key)):
                    cut = dict(by[r])
                    cut["body"] = cut["body"].replace(phrase, "x")
                    self.assertTrue(any(key in e for e in validate.role_errors(cut)), key)

    def test_validator_bites_on_a_forbidden_capability(self):
        by = {r["name"]: r for r in load_roles(ROOT)}
        for r in ROLES:
            for cap in ("edit", "web"):
                with self.subTest((r, cap)):
                    bad = dict(by[r])
                    bad["tools"] = [*by[r]["tools"], cap]
                    self.assertTrue(validate.role_errors(bad))
        self.assertEqual(validate.ORG_ROLE_NO_SHELL, {"assessor", "planner"})
        for r in sorted(validate.ORG_ROLE_NO_SHELL):
            with self.subTest((r, "shell")):
                bad = dict(by[r])
                bad["tools"] = [*by[r]["tools"], "shell"]
                self.assertTrue(any(r in e and "capability" in e for e in validate.role_errors(bad)))

    def test_sandbox_requirement_is_in_every_role_and_adapter(self):
        ro = "Dispatch only under an enforced read-only sandbox of the parent session."
        marks = {
            "assessor": ro,
            "planner": ro,
            "analyst": "Dispatch only with the repository and live systems read-only to you",
        }
        for r, mark in marks.items():
            for rel in (f"roles/{r}.md", f".claude/agents/{r}.md", f".github/agents/{r}.agent.md"):
                with self.subTest(rel):
                    text = (ROOT / rel).read_text(encoding="utf-8")
                    self.assertIn(mark, text)
                    self.assertIn("the dispatch is held", text)
                    self.assertIn("instruction only", text)
            with self.subTest((r, "codex")):
                codex = (ROOT / f".codex/agents/{r}.toml").read_text(encoding="utf-8")
                self.assertIn("Required sandbox:", codex)
                self.assertIn("the dispatch is held, never run on trust", codex)

    def adapters(self, role):
        import generate

        policies = generate.policies_block(ROOT)
        return {
            "claude": generate.render_claude_agent(role, policies, "0.0.0"),
            "copilot": generate.render_copilot_agent(role, policies, "0.0.0"),
            "codex": generate.render_codex_agent(role, policies, "0.0.0"),
        }

    def test_no_shell_gates_section_has_no_command_imperative(self):
        import generate

        seen = 0
        for role in load_roles(ROOT):
            if "shell" in role["tools"]:
                continue
            for label, text in self.adapters(role).items():
                with self.subTest((role["name"], label)):
                    section = generate.gates_section(text)
                    self.assertIn("This role has no shell", section)  # something was collected
                    self.assertEqual(generate.command_imperatives(section), [])
                    self.assertEqual(validate.gate_wording_errors(role["name"], text), [])
                    seen += 1
        self.assertEqual(seen, 9)  # three no-shell roles (researcher, assessor, planner), three adapters each

    def test_shell_roles_keep_the_gate_wording_byte_identical(self):
        import generate

        policies = generate.policies_block(ROOT)
        seen = 0
        for role in load_roles(ROOT):
            if "shell" not in role["tools"]:
                continue
            self.assertEqual(generate.role_policies(role, policies), policies)
            for label, text in self.adapters(role).items():
                with self.subTest((role["name"], label)):
                    self.assertIn(policies, text)
                    seen += 1
        self.assertGreaterEqual(seen, 18)

    def test_the_imperative_check_bites(self):
        import generate

        raw = generate.gates_section(generate.policies_block(ROOT))
        self.assertGreaterEqual(len(generate.command_imperatives(raw)), 3)
        text = (ROOT / ".claude/agents/assessor.md").read_text(encoding="utf-8")
        for planted in (
            "Run the tests and lint before you report.",
            "Execute the script on the table.",
            "Invoke the linter, then capture the output.",
            "Revert the fix and confirm the test goes red.",
        ):
            with self.subTest(planted):
                bad = text.replace("- Say what you did not run", planted + "\n- Say what you did not run", 1)
                self.assertNotEqual(bad, text)
                self.assertTrue(validate.gate_wording_errors("assessor", bad))
        self.assertEqual(generate.command_imperatives("This role runs no command. Never run a test."), [])
        implementer = (ROOT / ".claude/agents/implementer.md").read_text(encoding="utf-8")
        self.assertTrue(validate.gate_wording_errors("implementer", implementer))

    def test_no_shell_roles_are_not_told_to_run_commands(self):
        for r in sorted(validate.ORG_ROLE_NO_SHELL):
            for rel in (f".claude/agents/{r}.md", f".github/agents/{r}.agent.md", f".codex/agents/{r}.toml"):
                with self.subTest(rel):
                    text = (ROOT / rel).read_text(encoding="utf-8")
                    self.assertNotIn("capture the exit code", text)
                    self.assertNotIn("gate exit=", text)
                    self.assertIn("This role has no shell and runs no command, test or gate.", text)
                    self.assertIn("`not-run`", text)
        for rel in (".claude/agents/analyst.md", ".claude/agents/implementer.md"):
            self.assertIn("capture the exit code", (ROOT / rel).read_text(encoding="utf-8"))

    def test_planner_hands_computation_to_a_role_with_a_shell(self):
        text = role_text("planner")
        self.assertIn("you have no shell", text)
        self.assertIn("`analyst` or the `implementer` runs", text)
        self.assertNotIn("produced by code", text)
        self.assertRegex(pack_text("delivery-business-mgmt"), r"the `analyst` or the `implementer` runs the script")

    def test_generator_output_follows_the_shell_capability_and_the_sandbox_table(self):
        import generate

        policies = generate.policies_block(ROOT)
        for role in load_roles(ROOT):
            claude = generate.render_claude_agent(role, policies, "0.0.0")
            codex = generate.render_codex_agent(role, policies, "0.0.0")
            has_shell = "shell" in role["tools"]
            for label, text in (("claude", claude), ("codex", codex)):
                with self.subTest((role["name"], label)):
                    self.assertEqual("capture the exit code" in text, has_shell)
            with self.subTest((role["name"], "sandbox")):
                if role["name"] in ROLES:
                    self.assertIn(generate.CODEX_ROLE_SANDBOX[role["name"]], codex)
                else:
                    self.assertNotIn("the dispatch is held, never run on trust", codex)

    def test_the_codex_sandbox_table_covers_the_pilot_roles(self):
        import generate

        for r in ROLES:
            self.assertIn(r, generate.CODEX_ROLE_SANDBOX)
        self.assertNotIn("researcher", generate.CODEX_ROLE_SANDBOX)


class Packs(unittest.TestCase):
    def test_shape_and_size(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertTrue(pack_text(p).startswith(f'---\nname: "{p}"\n'))
                loaded = {x["dir"]: x for x in load_packs(ROOT)}[p]
                self.assertEqual(shape_problems(loaded), [])

    def test_every_rule_is_present(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(missing(p, pack_text(p)), [])

    def test_pilot_marking_and_measurement_targets(self):
        for p in PACKS:
            with self.subTest(p):
                body = split(pack_text(p))[1]
                self.assertRegex(body, r"Status: pilot pack")
                m = re.search(r"## How to verify[^\n]*\n(.*?)(?=\n## |\Z)", body, re.S)
                self.assertIsNotNone(m)
                section = m.group(1)
                self.assertRegex(section, r"prediction")
                self.assertRegex(section, r"[Mm]easurement target")
                self.assertRegex(section, r"20 percentage points|9 of 10|\+300 tokens|\bzero\b|\b0\b")

    def test_sources_are_labeled(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(source_problems(split(pack_text(p))[1]), [])

    def test_no_private_value_no_british_no_em_dash(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(style_problems(pack_text(p)), [])
        for r in ROLES:
            with self.subTest(r):
                self.assertEqual(style_problems(role_text(r)), [])
        self.assertEqual(validate.privacy_hits(ROOT), [])

    def test_docs_list_every_new_pack_and_role(self):
        packs_doc = (ROOT / "docs/packs.md").read_text(encoding="utf-8")
        roles_doc = (ROOT / "docs/roles.md").read_text(encoding="utf-8")
        for p in PACKS:
            self.assertIn(f"(../packs/{p}/PACK.md)", packs_doc)
        for r in ROLES:
            self.assertIn(f"`{r}`", roles_doc)


class Router(unittest.TestCase):
    def body(self):
        return split(pack_text("org-dispatch"))[1]

    def test_every_row_names_at_most_three_known_packs(self):
        body = self.body()
        self.assertGreaterEqual(len(router_rows(body)), 12, "something was collected")
        self.assertEqual(router_problems(body, set(PACK_NAMES)), [])

    def test_pilot_titles_are_covered(self):
        titles = " ".join(t for t, *_ in router_rows(self.body())).lower()
        for needle in (
            "soc analyst tier 1",
            "soc analyst tier 2",
            "detection engineer",
            "grc analyst",
            "risk analyst",
            "internal auditor",
            "project manager",
            "scrum master",
        ):
            with self.subTest(needle):
                self.assertIn(needle, titles)

    def test_the_pilot_packs_are_routed_to(self):
        body = self.body()
        for p in ("threat-detection", "grc-controls", "delivery-business-mgmt", "org-operating-model"):
            self.assertIn(f"`{p}`", body)


class Tools(unittest.TestCase):
    def test_critical_path_script_runs(self):
        text = pack_text("delivery-business-mgmt")
        script = re.search(r"```python\n(.*?)```", text, re.S).group(1)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "cp.py"
            f.write_text(script, encoding="utf-8")
            plan = "a 2\nb 3 a\nc 1 a\nd 4 b c\n"
            out = subprocess.run([sys.executable, "-I", str(f)], input=plan, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("a b d", out.stdout)
            self.assertIn("9", out.stdout)
            cyc = subprocess.run([sys.executable, "-I", str(f)], input="a 1 b\nb 1 a\n", capture_output=True, text=True)
            self.assertNotEqual(cyc.returncode, 0)
            self.assertIn("cycle", cyc.stderr)


class GuardsBite(unittest.TestCase):
    def test_missing_rules_are_found(self):
        for p in PACKS:
            self.assertEqual(len(missing(p, "nothing here")), len(PACK_RULES[p]))
        cut = pack_text("threat-detection").replace("must not fire", "x")
        self.assertEqual(missing("threat-detection", cut), ["two test events"])
        cut = pack_text("org-dispatch").replace("at most three packs", "x")
        self.assertEqual(missing("org-dispatch", cut), ["at most three"])

    def test_source_guard_bites(self):
        body = split(pack_text("grc-controls"))[1]
        self.assertEqual(source_problems(body), [])
        planted = body.replace("\n## Sources\n", "\nThe PMBOK process applies.\n\n## Sources\n", 1)
        self.assertTrue(any("PMBOK" in e for e in source_problems(planted)))
        unlabeled = body.replace("\n## Sources\n", "\n## Sources\n- A bare line with no label.\n", 1)
        self.assertTrue(any("not labeled" in e for e in source_problems(unlabeled)))
        nodate = re.sub(r"read 20\d\d-\d\d-\d\d", "read", body, count=1)
        self.assertTrue(any("without URL and date" in e for e in source_problems(nodate)))
        self.assertEqual(source_problems("no sources here"), ["no Sources section"])

    def test_router_guard_bites(self):
        body = split(pack_text("org-dispatch"))[1]
        four = "| Test title | AN | `aws`, `threat-model`, `linux-platform`, `gha-security-dast` | note |\n"
        self.assertTrue(any("limit is three" in e for e in router_problems(body + four, set(PACK_NAMES))))
        ghost = "| Test title | AN | `no-such-pack` | note |\n"
        self.assertTrue(any("unknown pack" in e for e in router_problems(body + ghost, set(PACK_NAMES))))

    def test_size_and_description_guards_bite(self):
        def loaded(edit):
            return load_packs(mutated_tree(self, "org-dispatch", edit))[0]

        self.assertEqual(shape_problems(loaded(lambda t: t)), [])
        long_desc = loaded(lambda t: t.replace('description: "Load', 'description: "Load ' + "x" * 250, 1))
        self.assertIn("description too long", shape_problems(long_desc))
        not_load = loaded(lambda t: t.replace('description: "Load', 'description: "Use', 1))
        self.assertIn("description must start with Load", shape_problems(not_load))
        big = loaded(lambda t: t + "\n" + "word " * 3000)
        self.assertIn("body too long", shape_problems(big))
        no_sources = loaded(lambda t: t.replace("## Sources", "## Notes"))
        self.assertIn("missing ## Sources", shape_problems(no_sources))

    def test_privacy_guard_bites(self):
        planted = {"home path": "/ho" + "me/someone/x", "address": "host 10.1." + "2.3 is up"}
        for label, text in planted.items():
            with self.subTest(label):
                tree = mutated_tree(self, "org-dispatch", lambda t, x=text: t + "\n" + x + "\n")
                self.assertTrue(validate.privacy_hits(tree), label)
        clean = mutated_tree(self, "org-dispatch", lambda t: t)
        self.assertEqual(validate.privacy_hits(clean), [])
        for label, text in {"em dash": "dash " + chr(0x2014) + " here", "british": "organis" + "ation"}.items():
            with self.subTest(label):
                tree = mutated_tree(self, "org-dispatch", lambda t, x=text: t + "\n" + x + "\n")
                self.assertTrue(style_problems((tree / "packs/org-dispatch/PACK.md").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
