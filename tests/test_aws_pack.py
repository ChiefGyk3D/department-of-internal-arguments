"""The aws pack (issue #85, under #78): each required rule is present, every rule names where it was verified, the
sources carry fetch dates, the pack says AWS is planned and not in use, and it holds no private identifier."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACK = "aws"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"
MAX_BYTES = 15000  # the largest existing pack is under this; there is no enforced cap, so keep to the existing range

# One regex per rule; each must match inside the pack. Loose to collect, strict to assert.
RULES = {
    "AWS is planned, not in use": r"planned.{0,120}not (?:yet )?in use|not (?:yet )?in use.{0,120}planned",
    "no long-lived keys in text": r"no long-lived (?:access )?keys?",
    "no key in a prompt, brief, log or commit": r"prompt, brief.{0,40}commit",
    "temporary credentials through roles": r"temporary credentials",
    "root user locked down": r"root user.{0,200}MFA|MFA.{0,200}root user",
    "no access keys for the root user": r"no access keys? for the root user|never create access keys? for the root",
    "least privilege": r"least privilege",
    "no wildcard action and resource": r"Action.{0,6}\*.{0,80}Resource.{0,6}\*|wildcard",
    "generate policies from CloudTrail activity": (
        r"Access Analyzer.{0,160}CloudTrail|CloudTrail.{0,160}Access Analyzer"
    ),
    "explicit deny overrides allow": r"explicit deny",
    "OIDC federation": r"OIDC",
    "trust policy sub condition": r":sub\b",
    "audience condition": r":aud\b|sts\.amazonaws\.com",
    "no bare wildcard subject": (
        r"never.{0,80}(?:bare|lone|only).{0,40}wildcard|wildcard.{0,120}(?:whole|every) (?:repo|stack)"
    ),
    "GitHub immutable subject form": r"immutable",
    "workflow needs id-token write": r"id-token: write",
    "Spacelift OIDC subject shape": r"run_type",
    "Spacelift OIDC is a paid feature": r"paid",
    "Spacelift AWS integration and external ID": r"ExternalId|external ID",
    "Spacelift example role is too broad": r"PowerUserAccess",
    "session duration and ExpiredToken": r"ExpiredToken|session duration",
    "RoleSessionName run trail": r"RoleSessionName",
    "security groups allow rules only": r"allow rules?,? (?:but )?not deny|no deny rules?|cannot deny",
    "security groups are stateful": r"stateful",
    "default security group": r"default security group",
    "nothing open to the world on admin ports": r"open to the world",
    "no inbound for the Twingate connector": r"Twingate.{0,200}(?:no inbound|nothing inbound|outbound only)",
    "public IPv4 is charged": r"public IPv4.{0,120}charge|charge.{0,120}public IPv4",
    "NAT gateway cost": r"NAT gateway",
    "IMDSv2 required": r"IMDSv2",
    "http_tokens required": r"http_tokens",
    "instance role, not keys on the instance": r"instance (?:profile|role)",
    "S3 Block Public Access all four": r"all four",
    "provider defaults for public access block are false": r"default.{0,60}false|false.{0,60}default",
    "ACLs disabled": r"ACLs",
    "bucket policy deny non-TLS": r"SecureTransport",
    "bucket policy lockout": r"lock (?:everyone|you) out|lock(?:ed)? (?:yourself )?out",
    "versioning for state": r"[Vv]ersioning",
    "state backend locking": r"use_lockfile",
    "state is sensitive": r"state.{0,60}sensitive|sensitive.{0,60}state",
    "KMS key policy is the root of access": r"key policy",
    "KMS deletion waiting period": r"waiting period",
    "disable before deleting a key": r"disabl\w+.{0,60}(?:instead|before).{0,60}delet|delet\w+.{0,60}irreversible",
    "KMS key rotation": r"rotation",
    "CloudTrail multi-Region trail": r"multi-Region",
    "CloudTrail log file validation": r"log file (?:integrity )?validation|enable_log_file_validation",
    "event history is only 90 days": r"90 days",
    "CloudTrail data events are off by default": r"data events",
    "Config records configuration history": r"AWS Config",
    "budgets alert, they do not cap": r"not a hard cap|does not (?:stop|cap)|no hard cap|cannot cap",
    "budget data lags": r"three times a day|up to three",
    "budget actions apply IAM or SCP": r"budget action",
    "Organizations SCPs": r"\bSCPs?\b",
    "SCPs do not grant": r"(?:do|does) not grant|never grants?",
    "SCPs skip the management account": r"management account",
    "test SCPs on an OU first": r"\bOU\b",
    "FullAWSAccess": r"FullAWSAccess",
    "separate accounts": r"separate accounts?|member accounts?",
    "OpenTofu with Spacelift": r"opentofu-spacelift",
    "Doppler holds names, not AWS keys": r"twingate-doppler",
    "plan before apply": r"plan before apply|Plan before apply|plan, not|read the plan",
    "blast radius and rollback": r"blast radius",
    "never apply to a live system without the maintainer": r"without the maintainer",
    "IAM changes are the most dangerous": r"IAM.{0,160}(?:lock|escalat|self-)",
    "limits section": r"## Limits",
    "does-not-cover section": r"## What this pack does not cover",
    "sources section": r"## Sources",
    "benchmark pending": r"benchmark.{0,80}#73.{0,80}pending|#73.{0,120}pending",
    "VERIFIED label": r"\bVERIFIED\b",
    "INFERRED label": r"\bINFERRED\b",
    "IAM best practices page cited with its fetch date": r"IAM/latest/UserGuide/best-practices\.html.{0,80}2026-10-30",
    "OIDC provider page cited with its fetch date": r"id_roles_providers_create_oidc\.html.{0,80}2026-10-30",
    "Organizations SCP page cited with its fetch date": r"orgs_manage_policies_scps\.html.{0,80}2026-10-30",
    "security group page cited with its fetch date": r"vpc-security-groups\.html.{0,80}2026-10-30",
    "KMS key policy page cited with its fetch date": r"key-policies\.html.{0,80}2026-10-30",
    "S3 Block Public Access page cited with its fetch date": (
        r"access-control-block-public-access\.html.{0,80}2026-10-30"
    ),
    "Budgets page cited with its fetch date": r"budgets-managing-costs\.html.{0,80}2026-10-30",
    "Spacelift AWS page cited with its fetch date": r"cloud-providers/aws\.html.{0,80}2026-10-30",
    "GitHub OIDC for AWS page cited with its fetch date": r"oidc-in-aws.{0,80}2026-10-30",
    "OpenTofu S3 backend page cited with its fetch date": r"backends/s3/.{0,80}2026-10-30",
}
# Built from pieces so this file does not trip the sweep it implements. An AWS account ID is twelve digits; an access
# key ID is AKIA or ASIA plus sixteen characters; a secret access key is forty base64 characters.
_AK = "AK" + "IA"
_AS = "AS" + "IA"
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|chiefgyk3d|renegade|penguin|clouds.against|\bcah-\d|\btor1\b|\bnyc3\b|zsca" + r"ler"
    r"|\barn:aws:[a-z0-9-]*:[a-z0-9-]*:(?!\d{12}\b)\d{6,}"
    r"|\barn:aws:[a-z0-9-]*:[a-z0-9-]*:(?!(?:111122223333|123456789012|<|\$)\S)\d{12}\b"
    r"|\b(?<![0-9])(?!111122223333\b|123456789012\b)\d{12}(?![0-9])\b"
    r"|\b(?:" + _AK + "|" + _AS + r")[A-Z0-9]{16}\b|\b[A-Za-z0-9/+=]{40}\b(?=.{0,0}$)"
    r"|\b[a-z0-9-]+\.app\.spacelift\.io\b|\b[a-z0-9-]+\.twingate\.com\b|\bdp\.(?:st|pt|ct|sa)\.[a-z0-9]"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}|\bgha-(?!security)[a-z]",
    re.I,
)
SAFE_HOSTS = ("docs.spacelift.io", "docs.aws.amazon.com", "docs.github.com", "opentofu.org", "www.twingate.com")


def missing(text):
    return [name for name, pat in RULES.items() if not re.search(pat, text, re.S | re.I)]


def private_hits(text):
    return [m.group(0) for m in PRIVATE.finditer(text) if not any(h in m.group(0) for h in SAFE_HOSTS)]


class AwsPack(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")

    def test_pack_is_registered_and_keeps_its_shape(self):
        self.assertIn(PACK, PACK_NAMES)
        self.assertTrue(self.text.startswith(f'---\nname: "{PACK}"\n'))
        self.assertIn("## How to verify", self.text)
        self.assertLessEqual(len(self.text.encode("utf-8")), MAX_BYTES)

    def test_every_rule_is_present(self):
        self.assertEqual(missing(self.text), [])

    def test_lessons_name_where_they_were_verified(self):
        self.assertGreaterEqual(len(re.findall(r"\(Source: ", self.text)), 20)

    def test_lessons_are_in_the_generated_skills(self):
        for rel in (f".claude/skills/pack-{PACK}/SKILL.md", f".agents/skills/pack-{PACK}/SKILL.md"):
            with self.subTest(rel):
                self.assertIn("## What this pack does not cover", (ROOT / rel).read_text(encoding="utf-8"))

    def test_no_private_values(self):
        self.assertEqual(private_hits(self.text), [])

    def test_no_long_lived_key_is_shown_as_an_example(self):
        # The pack teaches "no keys in text"; it must not carry a key-shaped example of its own.
        self.assertNotRegex(self.text, r"AWS_SECRET_ACCESS_KEY\s*=\s*\S")
        self.assertNotRegex(self.text, r"aws_secret_access_key\s*=\s*\S")

    def test_guard_bites(self):
        # Negative cases: an empty pack misses every rule, one removed phrase misses exactly its rule, and each kind
        # of private value is caught by the sweep.
        self.assertEqual(len(missing("nothing here")), len(RULES))
        cut = re.sub(r"IMDSv2", "x", self.text)
        self.assertEqual(
            sorted(missing(cut)),
            sorted(n for n in ("IMDSv2 required",)),
        )
        for planted in (
            "/ho" + "me/someone/x",
            "arn:aws:iam::" + "210987654321:role/x",
            "account " + "210987654321",
            "key " + _AK + "ABCDEFGHIJKLMNOP",
            "session " + _AS + "ABCDEFGHIJKLMNOP",
            "acme." + "app.spacelift.io",
            "acme." + "twingate.com",
            "host 10.1." + "2.3",
            "user" + "@" + "example.com",
            "ca" + "h-00-foundation",
            "chiefgyk" + "3d",
            "dp." + "st.prd.abc123",
            "gha" + "-somerepo",
        ):
            with self.subTest(planted):
                self.assertNotEqual(private_hits(planted), [])
        # The documented placeholder account IDs and the vendor documentation hosts are not private values.
        self.assertEqual(private_hits("arn:aws:iam::" + "123456789012:role/x"), [])
        self.assertEqual(private_hits("https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html"), [])


if __name__ == "__main__":
    unittest.main()
