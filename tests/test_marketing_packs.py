"""The marketing and advertising packs (issue #131): ad-disclosure, marketing-grc, audience-data-privacy,
platform-data-terms, media-kit-claims. Registry, shape, size, every required rule, labeled sources (every VERIFIED line
carries a URL and a date read), links to the existing packs, router rows, a hygiene sweep with a placeholder word list,
generated outputs and a negative case for each guard."""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate  # noqa: E402  (path set up above)
from lib import PACK_NAMES, load_packs  # noqa: E402

PACKS = ("ad-disclosure", "marketing-grc", "audience-data-privacy", "platform-data-terms", "media-kit-claims")
MAX_BODY_CHARS = 12000  # 3,000 tokens at four characters a token
MAX_DESC_CHARS = 200  # about 40 tokens asked for; the trigger comes first
MIN_BODY_CHARS = 3200
STANDING_LINE = (
    "These packs describe working practices and examples, not legal, audit, or compliance advice. "
    "No output produced with these packs is a claim of compliance."
)

PACK_RULES = {
    "ad-disclosure": {
        "register of connections": r"register of connections",
        "free access is a connection": r"free (?:product|access|plan).{0,160}connection",
        "register row fields": r"type.{0,200}start date.{0,200}end date.{0,200}surfaces.{0,200}owner",
        "surface checklist": r"surface checklist",
        "surfaces named": r"video.{0,400}live stream.{0,400}link page",
        "live repeat": r"live stream.{0,160}(?:repeat|periodic)",
        "in the video": r"in the video",
        "unclear terms": r"collab.{0,200}ambassador|ambassador.{0,200}collab",
        "ambassador qualifier": r"stand-alone.{0,40}ambassador.{0,200}brand-qualified",
        "platform label": r"platform label",
        "tool alone not assumed": r"platform tool.{0,120}(?:alone|not assumed)",
        "never from memory": r"never quoted from memory",
        "never compliant": r'never write "FTC compliant"',
        "owner wording held": r"HELD for the owner",
        "human review": r"[Hh]uman review",
        "no negative line": r'"not an affiliate"',
        "both parties": r"influencer and (?:the )?brand",
        "unfetched policy": r"UNVERIFIED",
    },
    "marketing-grc": {
        "extends grc": r"`grc-controls`.{0,200}rules 1 to 13",
        "claim register": r"claim register",
        "claim row fields": r"claim.{0,100}source.{0,100}date read.{0,100}approver",
        "connection register link": r"`ad-disclosure`",
        "exception expiry": r"exception.{0,160}expir",
        "approval log": r"approval log",
        "approval gate": r"approval gate",
        "quarterly review": r"quarterly",
        "self-assessment": r"SELF-ASSESSMENT",
        "never compliant": r'never write "compliant"',
        "unconditional prohibition": (
            r"never write that a (?:page|campaign|claim|practice|disclosure)[^.]{0,120}compliant, legal or approved"
        ),
        "evidence rows found": r"evidence rows found.{0,80}evidence rows missing",
        "verbatim": r"verbatim",
        "independent review": r"about half",
        "own issue": r"own issue",
        "reasonable programs": r"reasonable programs",
        "human approves claims": r"approval of each public claim",
    },
    "audience-data-privacy": {
        "do-not-collect list": r"do-not-collect list",
        "claim to code": r"claim-to-code",
        "retention constants": r"retention constants",
        "signals": r"GPC.{0,200}(?:DNT|Do Not Track)",
        "aggregate and floor": r"floor",
        "raw data short window": r"short (?:window|retention)",
        "unverified email": r"unverified.{0,80}email|email.{0,80}unverified",
        "no marketing mail": r"no marketing (?:mail|email)",
        "controller and contact": r"controller and contact",
        "notice beside field": r"notice.{0,60}(?:beside|next to).{0,40}field",
        "templates": r"records of processing.{0,300}impact.{0,300}breach",
        "lawyer reads": r"a lawyer reads this",
        "never names a basis": r"never names a (?:lawful )?basis",
        "processors": r"processor",
        "can-spam": r"CAN-SPAM",
        "storage exceptions": r"strictly necessary",
        "ico scope": r"UK storage and access",
        "repository policy": r"repository policy.{0,120}stricter",
        "gpc narrower": r"narrower signal.{0,200}(?:sale|sharing)",
        "dnt unverified": r"DNT.{0,200}UNVERIFIED",
        "separate policy test": r"separate test",
        "no regulator selected": r"does not select a regulator.{0,200}counsel",
        "threat-detection link": r"`threat-detection`",
        "data inventory": r"data inventory",
    },
    "platform-data-terms": {
        "terms table": r"terms table.{0,400}URL.{0,200}date read",
        "access class": r"access class",
        "expiry per source": r"expiry per source|per-source expiry",
        "owner-pasted": r"owner-pasted",
        "not from memory": r"never (?:filled|inferred|taken).{0,60}memory|not from memory",
        "append-only": r"append-only",
        "deletion duty": r"deletion",
        "hand-copied": r"hand-copied",
        "written permission": r"written permission",
        "sponsor page": r"sponsor page",
        "terms change": r"terms change",
        "inferred until re-read": r"INFERRED until",
        "cache window": r"cache.{0,80}(?:window|refresh)",
        "human reads ambiguity": r"ambiguous",
        "hand-copied still a row": r"hand-copied figure still gets a row",
        "unread rule not published": r"unread rule.{0,100}not published",
    },
    "media-kit-claims": {
        "metric table": r"metric table",
        "era mark": r"era mark",
        "floor": r"\bfloor",
        "suppression": r"suppress",
        "range with formula": r"range.{0,120}formula",
        "no market benchmark": r"no market benchmark",
        "fees stated": r"fees",
        "no invented premium": r"premium",
        "forbidden display": r"forbids? (?:it|showing|display)|display rule",
        "bought metrics": r"(?:bot|purchased|bought).{0,120}(?:followers|views)",
        "testimonial": r"testimonial",
        "partner names": r"partner (?:name|logo)",
        "access class link": r"access class",
        "fake review rule": r"fake",
        "disclosure check": r"`ad-disclosure`",
        "sentiment condition": r"conditioned on.{0,100}(?:positive|negative).{0,100}sentiment",
        "sentiment-neutral paid testimonial": r"sentiment-neutral",
        "refused and escalated": r"refused and escalated to the human",
    },
}
# Pack names each pack must link to (the design's "links to existing packs"); every one must also be a known pack.
REQUIRED_LINKS = {
    "ad-disclosure": {"copy-review", "front-door", "grc-controls", "marketing-grc"},
    "marketing-grc": {"grc-controls", "ad-disclosure", "delivery-business-mgmt", "org-dispatch"},
    "audience-data-privacy": {"grc-controls", "threat-detection", "delivery-business-mgmt", "platform-data-terms"},
    "platform-data-terms": {"grc-controls", "delivery-business-mgmt", "audience-data-privacy", "media-kit-claims"},
    "media-kit-claims": {"ad-disclosure", "platform-data-terms", "grc-controls", "front-door"},
}
# Regulators and platforms a pack may not name unless its Sources section has a line for them.
SOURCED_NAMES = (
    r"\bFTC\b",
    r"16 CFR",
    r"CAN-SPAM",
    r"\bICO\b",
    r"Global Privacy Control",
    r"\bGPC\b",
    r"\bASA\b",
    r"\bYouTube\b",
    r"\bTwitch\b",
    r"\bMeta\b",
    r"\bEDPB\b",
    r"\bGDPR\b",
    r"\bCCPA\b",
    r"\bDAA\b",
    r"\bNAD\b",
    r"\bTikTok\b",
    r"Google Ads",
)
# Words a public tree must not carry. Placeholders only: the real list (names, accounts, amounts) is private and
# is read from the file named by MKT_HYGIENE_EXTRA_WORDS (one word or phrase per line) when the lead runs this test.
PLACEHOLDER_WORDS = ("acme-sponsor", "example-partner", "example-vendor", "sample-employer", "example-handle")
GENERIC_PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|~/\.local|chiefgyk|renegade|\bghsw\b|\bghwho\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\$\s?\d|\b\d[\d,.]*\s?(?:USD|EUR|GBP)\b|(?:^|[\s(])#\d{2,}\b|\bissue \d+|\bPR \d+|—",
    re.I,
)
BRITISH = re.compile(r"\b(?:behaviour|colour|authoris\w*|defence|organis\w*|recognis\w*|analyse\w*|licence)\b", re.I)


# The owner-approved wording (owner answer 2026-11-25, "Approve as written"), applied verbatim.
APPROVED = {
    "README.md": [
        "This repository defines thirteen roles and thirty-one packs for AI coding assistants. "
        "The five newest packs are marketing and advertising pilots with predicted effects and measurement targets."
    ],
    "docs/what-and-why.md": [
        "Organizations need more than coding work. Marketing and advertising also require records, evidence, and "
        "review. You cannot disclose what nobody recorded, and every privacy claim must be tested against the code. "
        "These pilot packs define working practices and measurement targets, not proven results or legal advice. "
        "A human or counsel decides which rules apply."
    ],
    "docs/packs.md": [
        "- [ad-disclosure](../packs/ad-disclosure/PACK.md): Records commercial connections and places clear "
        "disclosures close to the related claim.",
        "- [marketing-grc](../packs/marketing-grc/PACK.md): Reports evidence found or missing without stating that "
        "the work is compliant.",
        "- [audience-data-privacy](../packs/audience-data-privacy/PACK.md): Records audience data, purpose, "
        "retention, and whether privacy claims match the code.",
        "- [platform-data-terms](../packs/platform-data-terms/PACK.md): Tracks platform caching and display rules, "
        "including who reviewed each rule and when.",
        "- [media-kit-claims](../packs/media-kit-claims/PACK.md): Requires a source, date, floor, and era mark for "
        "every public number, with no bought or inflated metrics.",
    ],
    "packs/ad-disclosure/PACK.md": [
        "Links and sponsorships on [site name] are disclosed next to the related claim, and [owner name] keeps a "
        "register of those commercial connections."
    ],
    "packs/audience-data-privacy/PACK.md": [
        "Sample only. [Owner name] must check this statement against what [site name] actually does. We count "
        "[page views, downloads, or other events] using [analytics service]. We do not track individuals. We keep "
        "this data for [retention period] to [purpose]. Ask privacy or analytics questions at [contact method].",
        "We do not send marketing mail unless you provide a separate opt-in.",
    ],
    "packs/media-kit-claims/PACK.md": [
        "Price shown without a market benchmark.",
        "Fees: [owner name] receives [amount or percentage] when [purchase or other event] occurs.",
    ],
    "CHANGELOG.md": [
        "Added five marketing pilot packs and router rows for marketing work: ad-disclosure, marketing-grc, "
        "audience-data-privacy, platform-data-terms, and media-kit-claims."
    ],
}
STALE = {"README.md": ("Five new packs cover", "All eight additions")}
TEMPLATE_PACKS = ("ad-disclosure", "audience-data-privacy", "media-kit-claims")


def pack_text(pack):
    return (ROOT / "packs" / pack / "PACK.md").read_text(encoding="utf-8")


def split(text):
    m = re.match(r'---\nname: "[^"]+"\ndescription: "(.*)"\nupdated: "[^"]+"\n---\n(.*)', text, re.S)
    return (m.group(1), m.group(2)) if m else (None, None)


def mutated_tree(case, pack, edit):
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    d = Path(tmp.name) / "packs" / pack
    d.mkdir(parents=True)
    (d / "PACK.md").write_text(edit(pack_text(pack)), encoding="utf-8")
    return Path(tmp.name)


def missing(pack, text):
    return sorted(n for n, pat in PACK_RULES[pack].items() if not re.search(pat, text, re.S | re.I))


def shape_problems(pack):
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
    for h in ("## Rules", "## How to verify", "## Limits", "## Deliverable", "## Related packs", "## Sources"):
        if h not in body:
            out.append(f"missing {h}")
    if STANDING_LINE not in body:
        out.append("missing the standing not-legal-advice line")
    return out


def section(body, name):
    m = re.search(rf"\n## {re.escape(name)}[^\n]*\n(.*?)(?=\n## |\Z)", body, re.S)
    return m.group(1) if m else None


def source_problems(body):
    """Every Sources bullet is labeled; every VERIFIED bullet has a URL and a date read; every regulator or platform
    named outside Sources has a Sources bullet that names it."""
    src = section(body, "Sources")
    if src is None:
        return ["no Sources section"]
    problems = []
    lines = [ln for ln in src.splitlines() if ln.startswith("- ")]
    if not lines:
        problems.append("Sources has no bullets")
    for ln in lines:
        if ln.startswith("- VERIFIED"):
            if not re.search(r"https://\S+", ln) or not re.search(r"read 20\d\d-\d\d-\d\d", ln):
                problems.append(f"VERIFIED line without URL and date read: {ln[:60]}")
        elif not (ln.startswith("- UNVERIFIED") or ln.startswith("- INFERRED")):
            problems.append(f"source line not labeled: {ln[:60]}")
    outside = body.replace(src, "")
    for name in SOURCED_NAMES:
        if re.search(name, outside) and not any(re.search(name, ln) for ln in lines):
            problems.append(f"{name!r} named with no Sources line")
    return problems


def link_problems(pack, body):
    """The Related packs section names every required pack, and every backticked name in it is a known pack."""
    rel = section(body, "Related packs")
    if rel is None:
        return ["no Related packs section"]
    names = set(re.findall(r"`([a-z][a-z0-9-]+)`", rel))
    out = [f"does not link {n}" for n in sorted(REQUIRED_LINKS[pack] - names)]
    out += [f"unknown pack {n}" for n in sorted(names - set(PACK_NAMES))]
    return out


RESULT_WORDS = re.compile(
    r"(?i)\bcompliant\b|regulator-approved|platform-approved|\b(?:is|are|as|be)\s+legal\b|\blegal(?:,|\s+or)\b"
)
PROHIBITION = r"never write that .{0,80}compliant, legal, regulator-approved or platform-approved"


def compliant_problems(line):
    """Any line that names a compliance, legality or approval conclusion must be an unconditional prohibition: a
    negation, and no condition that would let an agent write it once some evidence exists."""
    if not RESULT_WORDS.search(line):
        return []
    out = []
    if not re.search(r"(?i)\b(never|not|no)\b", line):
        out.append("unqualified compliant")
    if re.search(r"(?i)\b(without|unless|except|only when|only if|if)\b", line):
        out.append("conditional compliant")
    return out


def extra_words():
    path = os.environ.get("MKT_HYGIENE_EXTRA_WORDS")
    if not path:
        return []
    return [w.strip() for w in Path(path).read_text(encoding="utf-8").splitlines() if w.strip()]


def hygiene_problems(text, words=PLACEHOLDER_WORDS):
    out = []
    if GENERIC_PRIVATE.search(text):
        out.append("private-looking value, amount, issue number or em dash")
    if BRITISH.search(re.sub(r"https://\S+", "", text)):  # a cited URL is spelled as its publisher spells it
        out.append("British spelling")
    low = text.lower()
    out += [f"forbidden word {w!r}" for w in words if w.lower() in low]
    return out


class Registry(unittest.TestCase):
    def test_registered(self):
        for p in PACKS:
            self.assertIn(p, PACK_NAMES)
        self.assertEqual(len(PACK_NAMES), 31)
        self.assertEqual(len(set(PACK_NAMES)), 31)

    def test_no_new_role(self):
        from lib import ROLE_NAMES

        thirteen = {
            "reviewer", "adversarial-reviewer", "implementer", "transcriber", "researcher",
            "cloud-engineer", "devops-engineer", "cybersecurity-blue", "grc-analyst", "linux-sysadmin",
            "analyst", "assessor", "planner",
        }  # fmt: skip
        self.assertEqual(set(ROLE_NAMES), thirteen)
        self.assertEqual({p.stem for p in (ROOT / "roles").glob("*.md")} - {"README"}, thirteen)

    def test_generated_outputs_exist(self):
        for p in PACKS:
            for rel in (f".claude/skills/pack-{p}/SKILL.md", f".agents/skills/pack-{p}/SKILL.md"):
                with self.subTest(rel):
                    self.assertIn("## How to verify", (ROOT / rel).read_text(encoding="utf-8"))

    def test_validator_accepts_the_tree(self):
        self.assertEqual(validate.validate(ROOT), [])
        self.assertEqual(validate.privacy_hits(ROOT), [])


class Packs(unittest.TestCase):
    def test_shape_and_size(self):
        loaded = {x["dir"]: x for x in load_packs(ROOT)}
        for p in PACKS:
            with self.subTest(p):
                self.assertTrue(pack_text(p).startswith(f'---\nname: "{p}"\n'))
                self.assertEqual(shape_problems(loaded[p]), [])
                self.assertEqual(validate.pack_errors(loaded[p]), [])

    def test_every_rule_is_present(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(missing(p, pack_text(p)), [])

    def test_pilot_marking_and_measurement_targets(self):
        for p in PACKS:
            with self.subTest(p):
                body = split(pack_text(p))[1]
                self.assertRegex(body, r"Status: pilot pack")
                self.assertRegex(body, r"prediction")
                section_text = section(body, "How to verify")
                self.assertIsNotNone(section_text)
                self.assertRegex(section_text, r"[Mm]easurement target")
                self.assertRegex(section_text, r"20 percentage points")
                self.assertRegex(section_text, r"\bzero\b")
                self.assertRegex(section_text, r"rewritten once, then retired")

    def test_sources_are_labeled_and_dated(self):
        for p in PACKS:
            with self.subTest(p):
                body = split(pack_text(p))[1]
                self.assertEqual(source_problems(body), [])
                src = section(body, "Sources")
                self.assertGreaterEqual(len([ln for ln in src.splitlines() if ln.startswith("- ")]), 2)

    def test_every_pack_that_names_a_regulator_or_platform_has_a_verified_or_unverified_line(self):
        checked = 0
        for p in PACKS:
            body = split(pack_text(p))[1]
            src = section(body, "Sources")
            outside = body.replace(src, "")
            lines = [ln for ln in src.splitlines() if ln.startswith("- ")]
            for name in SOURCED_NAMES:
                if re.search(name, outside):
                    checked += 1
                    with self.subTest((p, name)):
                        hit = [ln for ln in lines if re.search(name, ln)]
                        self.assertTrue(hit)
                        self.assertTrue(
                            all(not ln.startswith("- INFERRED") for ln in hit)
                            or any(ln.startswith(("- VERIFIED", "- UNVERIFIED")) for ln in hit)
                        )
        self.assertGreaterEqual(checked, 4, "something was collected")
        for p in ("ad-disclosure", "marketing-grc", "audience-data-privacy", "media-kit-claims"):
            with self.subTest(p):
                self.assertRegex(section(split(pack_text(p))[1], "Sources"), r"(?m)^- VERIFIED")

    def test_links_to_existing_packs(self):
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(link_problems(p, split(pack_text(p))[1]), [])

    def test_hygiene(self):
        words = [*PLACEHOLDER_WORDS, *extra_words()]
        for p in PACKS:
            with self.subTest(p):
                self.assertEqual(hygiene_problems(pack_text(p), words), [])

    def test_media_kit_rule_text_is_general_and_marked_verified(self):
        text = pack_text("media-kit-claims")
        self.assertNotRegex(text, r"465\.")
        self.assertNotRegex(text, r"\$\s?\d")
        self.assertNotIn("to be confirmed by the lead", text)
        self.assertNotIn("read by the reviewer", text)
        src = section(split(text)[1], "Sources")
        line = [ln for ln in src.splitlines() if ln.startswith("- VERIFIED") and "sentiment" in ln]
        self.assertEqual(len(line), 1)
        self.assertIn("read 2026-11-25", line[0])

    def test_citation_guard_bites(self):
        base = pack_text("media-kit-claims")
        for planted in ("section 465." + "5", "a penalty of $" + "50"):
            self.assertTrue(re.search(r"465\.|\$\s?\d", base + planted), planted)

    def test_no_bypass_phrases(self):
        for phrase in ("no platform rule applies", "low-risk path"):
            with self.subTest(phrase):
                self.assertNotIn(phrase, pack_text("platform-data-terms"))

    def test_every_pack_prohibits_conclusions(self):
        for p in PACKS:
            with self.subTest(p):
                lines = [ln for ln in pack_text(p).splitlines() if re.search(PROHIBITION, ln, re.I)]
                self.assertTrue(lines, "something was collected")
                for ln in lines:
                    self.assertEqual(compliant_problems(ln), [], ln[:80])

    def test_untrusted_data_line(self):
        for p in PACKS:
            with self.subTest(p):
                lines = [ln for ln in pack_text(p).splitlines() if re.search(r"data, never instructions", ln)]
                self.assertEqual(len(lines), 1)
                for kind in ("ad copy", "platform pages", "partner messages", "tool output", "model output"):
                    self.assertIn(kind, lines[0].lower())

    def test_generated_adapters_match_their_sources(self):
        import hashlib

        import lib

        for p in PACKS:
            body = split(pack_text(p))[1].strip()
            for rel in (f".claude/skills/pack-{p}/SKILL.md", f".agents/skills/pack-{p}/SKILL.md"):
                with self.subTest(rel):
                    m, adapter = lib.split_marker((ROOT / rel).read_text(encoding="utf-8"))
                    self.assertEqual(m.group(3), hashlib.sha256(adapter.encode()).hexdigest())
                    self.assertIn(body, adapter)

    def test_no_pack_says_compliant_as_a_result(self):
        for p in PACKS:
            with self.subTest(p):
                for ln in pack_text(p).splitlines():
                    self.assertEqual(compliant_problems(ln), [], f"{p}: {ln[:80]}")

    def test_docs_list_every_pack(self):
        doc = (ROOT / "docs/packs.md").read_text(encoding="utf-8")
        for p in PACKS:
            self.assertIn(f"(../packs/{p}/PACK.md)", doc)

    def test_counts_in_docs(self):
        for rel in ("README.md", "docs/home.md", "docs/what-and-why.md"):
            with self.subTest(rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("thirty", text)
                self.assertNotIn("twenty-five", text)

    def test_copy_review_points_to_ad_disclosure(self):
        self.assertIn("`ad-disclosure`", pack_text("copy-review"))


def planned_packs():
    body = split(pack_text("org-dispatch"))[1]
    return set(re.findall(r"`([a-z-]+)`", re.search(r"Planned, not built[^\n]*", body).group(0)))


def row_problems(rows):
    """Every alias row names one to three known packs."""
    out = []
    for title, _mode, packs, _note in rows:
        names = re.findall(r"`([a-z0-9-]+)`", packs)
        if not names:
            out.append(f"{title}: no pack named")
        if len(names) > 3:
            out.append(f"{title}: {len(names)} packs, the limit is three")
        out += [f"{title}: unknown pack {n}" for n in names if n not in PACK_NAMES and n not in planned_packs()]
    return out


class ApprovedWording(unittest.TestCase):
    def test_every_approved_text_is_in_its_file_verbatim(self):
        for rel, texts in APPROVED.items():
            body = (ROOT / rel).read_text(encoding="utf-8")
            for text in texts:
                with self.subTest((rel, text[:40])):
                    self.assertIn(text, body)

    def test_no_stale_announcement(self):
        for rel, needles in STALE.items():
            body = (ROOT / rel).read_text(encoding="utf-8")
            for n in needles:
                self.assertNotIn(n, body)

    def test_the_held_sentence_names_the_approved_template(self):
        for p in TEMPLATE_PACKS:
            with self.subTest(p):
                self.assertRegex(
                    pack_text(p), r"owner-approved template.{0,200}checked against what the site really does"
                )

    def test_samples_are_marked_and_keep_placeholders(self):
        text = pack_text("audience-data-privacy")
        self.assertIn("Sample only.", text)
        for ph in ("[Owner name]", "[site name]", "[retention period]", "[contact method]"):
            self.assertIn(ph, text)


class Router(unittest.TestCase):
    def rows(self):
        body = split(pack_text("org-dispatch"))[1]
        rows = []
        for ln in body.splitlines():
            m = re.fullmatch(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", ln)
            if m and not set(m.group(1)) <= set("-: ") and m.group(1) != "Job title":
                rows.append(m.groups())
        return rows

    def test_marketing_alias_rows_exist_and_stay_within_three(self):
        rows = self.rows()
        marketing = [r for r in rows if set(re.findall(r"`([a-z0-9-]+)`", r[2])) & set(PACKS)]
        self.assertGreaterEqual(len(marketing), 3, "something was collected")
        self.assertEqual(row_problems(rows), [])
        routed = {n for r in marketing for n in re.findall(r"`([a-z0-9-]+)`", r[2])}
        self.assertEqual(set(PACKS) - routed, set(), "every marketing pack is routed to")
        self.assertIn("marketing", " ".join(r[0] for r in marketing).lower())

    def test_limits_count_matches_rows(self):
        body = split(pack_text("org-dispatch"))[1]
        m = re.search(r"The table lists (\d+) titles", body)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), len(self.rows()))

    def test_marketing_not_in_planned_list(self):
        body = split(pack_text("org-dispatch"))[1]
        planned = re.search(r"Planned, not built[^\n]*", body).group(0)
        for p in PACKS:
            self.assertNotIn(p, planned)


class GuardsBite(unittest.TestCase):
    def test_missing_rules_are_found(self):
        for p in PACKS:
            self.assertEqual(len(missing(p, "nothing here")), len(PACK_RULES[p]))
        cut = pack_text("marketing-grc").replace("approval gate", "x")
        self.assertIn("approval gate", missing("marketing-grc", cut))

    def test_source_guard_bites(self):
        body = split(pack_text("ad-disclosure"))[1]
        self.assertEqual(source_problems(body), [])
        nodate = re.sub(r"read 20\d\d-\d\d-\d\d", "read", body)
        self.assertTrue(any("URL and date" in e for e in source_problems(nodate)))
        nourl = body.replace("https://", "")
        self.assertTrue(any("URL and date" in e for e in source_problems(nourl)))
        unlabeled = body.replace("\n## Sources\n", "\n## Sources\n- A bare line with no label.\n", 1)
        self.assertTrue(any("not labeled" in e for e in source_problems(unlabeled)))
        planted = body.replace("\n## Sources\n", "\nThe EDPB view applies.\n\n## Sources\n", 1)
        self.assertTrue(any("EDPB" in e for e in source_problems(planted)))
        self.assertEqual(source_problems("no sources here"), ["no Sources section"])

    def test_link_guard_bites(self):
        body = split(pack_text("marketing-grc"))[1]
        self.assertEqual(link_problems("marketing-grc", body), [])
        cut = body.replace("`org-dispatch`", "`org-xx`")
        problems = link_problems("marketing-grc", cut)
        self.assertTrue(any("does not link org-dispatch" in e for e in problems))
        self.assertTrue(any("unknown pack org-xx" in e for e in problems))

    def test_shape_guards_bite(self):
        def loaded(edit):
            return load_packs(mutated_tree(self, "ad-disclosure", edit))[0]

        self.assertEqual(shape_problems(loaded(lambda t: t)), [])
        self.assertIn(
            "description too long", shape_problems(loaded(lambda t: t.replace('"Load', '"Load ' + "x" * 400, 1)))
        )
        self.assertIn(
            "description must start with Load", shape_problems(loaded(lambda t: t.replace('"Load', '"Use', 1)))
        )
        self.assertIn("body too long", shape_problems(loaded(lambda t: t + "\n" + "word " * 3000)))
        self.assertIn("missing ## Sources", shape_problems(loaded(lambda t: t.replace("## Sources", "## Notes"))))
        self.assertIn(
            "missing the standing not-legal-advice line",
            shape_problems(loaded(lambda t: t.replace(STANDING_LINE, "x"))),
        )

    def test_hygiene_guard_bites(self):
        base = pack_text("media-kit-claims")
        self.assertEqual(hygiene_problems(base), [])
        planted = {
            "word": "see acme-sponsor for terms",
            "amount": "a fee of $" + "500 a month",
            "issue": "tracked in issue " + "12",
            "path": "/ho" + "me/someone/x",
            "address": "host 10.1." + "2.3 is up",
            "em dash": "dash " + chr(0x2014) + " here",
            "british": "organis" + "ation",
        }
        for label, text in planted.items():
            with self.subTest(label):
                self.assertTrue(hygiene_problems(base + "\n" + text + "\n"), label)
        self.assertTrue(hygiene_problems("clean text", ("clean",)), "extra list words are applied")

    def test_extra_word_file_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "words.txt"
            f.write_text("zzz-private-name\n\n", encoding="utf-8")
            old = os.environ.get("MKT_HYGIENE_EXTRA_WORDS")
            os.environ["MKT_HYGIENE_EXTRA_WORDS"] = str(f)
            try:
                self.assertEqual(extra_words(), ["zzz-private-name"])
            finally:
                if old is None:
                    del os.environ["MKT_HYGIENE_EXTRA_WORDS"]
                else:
                    os.environ["MKT_HYGIENE_EXTRA_WORDS"] = old
        self.assertEqual(extra_words() if "MKT_HYGIENE_EXTRA_WORDS" not in os.environ else [], [])

    def test_router_guard_bites(self):
        real = Router().rows()
        self.assertEqual(row_problems(real), [])
        four = ("Test", "AN", "`ad-disclosure`, `marketing-grc`, `media-kit-claims`, `platform-data-terms`", "n")
        self.assertTrue(any("limit is three" in e for e in row_problems([*real, four])))
        ghost = ("Test", "AN", "`no-such-pack`", "n")
        self.assertTrue(any("unknown pack" in e for e in row_problems([*real, ghost])))

    def test_compliant_guard_bites(self):
        self.assertEqual(
            compliant_problems('Never write "compliant" without an evidence row.'), ["conditional compliant"]
        )
        self.assertEqual(compliant_problems("The campaign is compliant."), ["unqualified compliant"])
        self.assertEqual(compliant_problems("Never write that a page is compliant, legal or approved."), [])
        self.assertEqual(compliant_problems("no mention"), [])
        for bad in (
            "The page is regulator-approved.",
            "The disclosure is platform-approved.",
            "The practice is legal.",
        ):
            self.assertEqual(compliant_problems(bad), ["unqualified compliant"], bad)
        self.assertEqual(
            compliant_problems("Never write that it is platform-approved unless cleared."), ["conditional compliant"]
        )


if __name__ == "__main__":
    unittest.main()
