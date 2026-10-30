"""The twingate-doppler pack (issue #83, under #78): each required rule is present, every rule names where it was
measured, the sources carry fetch dates, the pack says what it does not cover, and it holds no private identifier."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACK = "twingate-doppler"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"
MAX_BYTES = 15000  # the largest existing pack is under this; there is no enforced cap, so keep to the existing range

# One regex per rule; each must match inside the pack. Loose to collect, strict to assert.
RULES = {
    "secret value never read, printed, logged or passed to an agent": (
        r"never read, printed, logged or passed to an agent"
    ),
    "names only": r"names only",
    "access through the tool at run time": r"at run time",
    "no value on a command line": r"command line|argv",
    "least privilege": r"least privilege",
    "one request for the whole job": r"ask once for the union|one request",
    "never offer a broader grant as a fallback": r"never offer a broader grant",
    "CI uses OIDC identities": r"OIDC",
    "no long-lived token in CI": r"long-lived",
    "identity subject claims kept narrow": r"subject",
    "Twingate must be on for lab access": r"Twingate must be (?:on|connected)",
    "tell the maintainer before any workaround": r"tell the maintainer.{0,160}before.{0,60}workaround",
    "restart the client first": r"restart",
    "never search the secret stores for a credential to reuse": (
        r"never search (?:the|his|a) secret stores? for a credential"
    ),
    "rotated tokens and what rotation breaks": r"rotation breaks",
    "two-secret strategy": r"two.secret",
    "rotation interval versus restart cadence": r"rotation interval",
    "revoking the superseded token": r"revok",
    "token expiry calendared": r"expir",
    "service key shown once": r"shown once|only time",
    "one service key per system": r"unique service key|one service key per",
    "connector needs outbound only": r"outbound",
    "connector tokens unique per connector": r"unique pair of tokens",
    "update connectors one at a time": r"one connector at a time|one at a time",
    "API token permission levels": r"Read, Write (?:&|and) Provision",
    "service token is read-only to one config": r"read-only.{0,80}single config|single config.{0,80}read-only",
    "secret visibility restricted": r"Restricted",
    "secret names endpoint": r"secrets/names|list names",
    "audit and activity logs": r"activity log",
    "limits section": r"## Limits",
    "does-not-cover section": r"## What this pack does not cover",
    "sources section": r"## Sources",
    "benchmark pending": r"benchmark.{0,80}#73.{0,80}pending|#73.{0,120}pending",
    "VERIFIED label": r"\bVERIFIED\b",
    "INFERRED label": r"\bINFERRED\b",
    "Twingate services page cited with its fetch date": r"twingate\.com/docs/services\b.{0,80}2026-10-30",
    "Twingate headless page cited with its fetch date": r"twingate\.com/docs/linux-headless.{0,80}2026-10-30",
    "Twingate connector best practices cited with its fetch date": (
        r"twingate\.com/docs/connector-best-practices.{0,80}2026-10-30"
    ),
    "Twingate API page cited with its fetch date": r"twingate\.com/docs/api-overview.{0,80}2026-10-30",
    "Doppler service tokens page cited with its fetch date": r"docs\.doppler\.com/docs/service-tokens.{0,80}2026-10-30",
    "Doppler service accounts page cited with its fetch date": (
        r"docs\.doppler\.com/docs/service-accounts\b.{0,80}2026-10-30"
    ),
    "Doppler identities page cited with its fetch date": (
        r"docs\.doppler\.com/docs/service-account-identities.{0,80}2026-10-30"
    ),
    "Doppler rotation page cited with its fetch date": r"docs\.doppler\.com/docs/secrets-rotation.{0,80}2026-10-30",
}
# Built from pieces so this file does not trip the sweep it implements.
_TG = r"(?<![a-z0-9.-])(?!www\.|docs\.)[a-z0-9-]+\.(?:[a-z0-9]+\.)?twingate" + r"\.com"
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|chiefgyk3d|renegade|penguin|clouds.against|\bcah-\d|\btor1\b|\bnyc3\b|zsca" + r"ler"
    r"|" + _TG + r"|\bdp\.(?:st|pt|ct|sa)\.[a-z0-9]|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b[0-9A-F]{40}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\bgha-(?!security)[a-z]|dop" + r"pler (?:secrets get|run) .{0,40}(?:-p|--project) ",
    re.I,
)


def missing(text):
    return [name for name, pat in RULES.items() if not re.search(pat, text, re.S | re.I)]


class TwingateDopplerPack(unittest.TestCase):
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
        cut = re.sub(r"two.secret", "x", self.text, flags=re.I)
        self.assertEqual(missing(cut), ["two-secret strategy"])
        for planted in (
            "/ho" + "me/someone/x",
            "acme." + "twingate.com",
            "acme.us1." + "twingate.com",
            "host 10.1." + "2.3",
            "user" + "@" + "example.com",
            "ca" + "h-00-foundation",
            "chiefgyk" + "3d",
            "dp." + "st.prd.abc123",
            "0123abcd-" + "0123-4567-89ab-0123456789ab",
            "EC952DEE4701A407A82B0C505C363F09" + "A9174A9E",
            "gha" + "-somerepo",
            "dop" + "pler secrets get X -" + "p proj",
        ):
            with self.subTest(planted):
                self.assertIsNotNone(PRIVATE.search(planted))
        # The vendor documentation host is not a private value.
        self.assertIsNone(PRIVATE.search("https://www.twingate.com/docs/services"))


if __name__ == "__main__":
    unittest.main()
