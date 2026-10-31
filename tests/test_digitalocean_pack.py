"""The digitalocean pack (issue #84, under #78): each required rule is present, every rule names where it was
measured, the sources carry fetch dates, the pack says what it does not cover, and it holds no private identifier."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

PACK = "digitalocean"
PACK_PATH = ROOT / "packs" / PACK / "PACK.md"
MAX_BYTES = 15000  # the existing packs sit just under this; there is no enforced cap, so keep to the existing range

# One regex per rule; each must match inside the pack. Loose to collect, strict to assert.
RULES = {
    # Limits and tokens
    "plan before apply, never apply to a live system": r"never apply to a live system",
    "creating a billable resource is a paid action": r"paid action|billable",
    "least privilege": r"least privilege",
    "one request for the whole job": r"ask once for the union|one request",
    "never offer a broader grant as a fallback": r"never offer a broader grant",
    "custom scopes": r"custom scopes",
    "full access alias scope avoided": r"api:write",
    "read-only alias scope": r"api:read",
    "a scope cannot be edited after creation": r"cannot (?:edit|change) (?:the|a) token.s scopes?|cannot be edited",
    "token shown once": r"shown once|only shown once",
    "token expiry set and calendared": r"expir",
    "missing scope returns 403": r"\b403\b",
    "token comes from the environment, never text": r"DIGITALOCEAN_TOKEN",
    "names only, never print a token": r"names only",
    "credential scopes are separate": r"kubernetes:access_cluster",
    "database credentials scope": r"database:view_credentials",
    "spaces key secret scope": r"spaces_key:create_credentials",
    "never search the secret stores for a credential": r"never search (?:the|his|a) secret stores? for a credential",
    # Droplets
    "droplet size availability varies by region": r"sizes? (?:API|endpoint)|available sizes|lacked the chosen size",
    "user_data change forces a new droplet": r"user_data.{0,160}(?:new resource|replace|recreate)",
    "ssh key change recreates the droplet": r"ssh_keys.{0,200}(?:destroy and recreate|recreate)",
    "look up an existing ssh key by name": r"look(?:s|ed)? (?:it )?up.{0,60}by name|by name",
    "backups outlive the droplet": r"backups remain|automated backups",
    "destroy is irreversible": r"irreversibl",
    "smtp blocked": r"\b25\b.{0,40}\b465\b|SMTP",
    # VPC and firewalls
    "firewall denies by default": r"blocks? all traffic",
    "no outbound rules means no outbound traffic": r"no outbound rules.{0,80}no outbound",
    "firewall is stateful but new outbound connections need a rule": r"stateful",
    "firewall rule and droplet limits": r"50 (?:total )?(?:incoming and outgoing )?rules|50 total",
    "ten droplets per firewall, use tags": r"10 Droplets per firewall",
    "deny rules win": r"deny.{0,60}precedence|precedence.{0,60}deny",
    "doctl cannot set rule action": r"doctl.{0,80}action",
    "firewalls cover VPC traffic too": r"public and (?:VPC|private)",
    "no cloud firewall on load balancers": r"cannot apply cloud firewalls to load balancers|firewall.{0,40}load balancers?",
    "VPC ranges cannot overlap": r"cannot overlap|may not overlap",
    "region default VPC": r"default VPC",
    "VPC cannot span regions": r"span",
    "VPC destroy only when empty": r"no resources in it|only destroy a VPC",
    "load balancers and clusters cannot move between VPCs": r"cannot (?:be )?migrate|not supported",
    # DOKS
    "control plane firewall limit": r"500",
    "worker size slug is immutable": r"immutable",
    "never kubectl delete a node": r"kubectl delete",
    "automatic upgrades are patch only": r"patch",
    "forced upgrade at end of support": r"end.of.support|end of support",
    "surge upgrades need droplet headroom": r"surge",
    "cluster and kubernetes provider in separate configurations": r"separate (?:module|configuration|stack)",
    "kubeconfig is a credential": r"kubeconfig",
    # Spaces
    "spaces key limits": r"200 access keys|200 (?:Spaces )?keys",
    "bucket limits": r"100 (?:Spaces )?buckets",
    "limited key incompatible with bucket policy": r"PutBucketPolicy",
    "spaces has no built-in backups": r"no built-in backups|does not include built-in backups",
    "pages disagree about creating keys through the API": r"disagree|conflict",
    "force_destroy": r"force_destroy",
    # Databases
    "trusted sources replace the whole list": r"full (?:trusted sources )?list|omit",
    "trusted sources hold no IPv6": r"IPv6",
    "private hostname only inside the VPC": r"private hostname",
    "destroy removes automated backups": r"destroys? the cluster.{0,60}backups|backups.{0,60}destroy",
    "backups daily and kept seven days": r"seven days|7 days",
    "database firewall resource": r"digitalocean_database_firewall",
    # Load balancers
    "ten backend droplets by name, then a tag": r"10 backend Droplets",
    "network load balancer has no URL routing": r"URL",
    "ssl passthrough loses forwarded headers": r"X-Forwarded",
    # doctl and API
    "doctl auth contexts": r"auth init --context|authentication context",
    "API rate limit": r"5000 requests per hour|5,000 requests per hour",
    "429 or retry settings": r"requests_per_second|http_retry",
    # OpenTofu, Spacelift and Twingate
    "names the opentofu-spacelift pack": r"opentofu-spacelift",
    "names the twingate-doppler pack": r"twingate-doppler",
    "token comes through Doppler and a Spacelift context": r"Spacelift context|context",
    "read-only token for proposed runs": r"read-only.{0,120}proposed|proposed.{0,120}read-only",
    "twingate connectors need outbound only": r"outbound",
    "no public SSH, access through Twingate": r"Twingate",
    "state holds secrets": r"state",
    # Shape
    "limits section": r"## Limits",
    "does-not-cover section": r"## What this pack does not cover",
    "sources section": r"## Sources",
    "benchmark pending": r"benchmark.{0,80}#73.{0,80}pending|#73.{0,120}pending",
    "VERIFIED label": r"\bVERIFIED\b",
    "INFERRED label": r"\bINFERRED\b",
    "token page cited with its fetch date": r"create-personal-access-token.{0,80}2026-10-30",
    "scopes page cited with its fetch date": r"reference/api/scopes.{0,80}2026-10-30",
    "firewall limits page cited with its fetch date": r"firewalls/details/limits.{0,80}2026-10-30",
    "firewall rules page cited with its fetch date": r"firewalls/how-to/configure-rules.{0,80}2026-10-30",
    "VPC limits page cited with its fetch date": r"vpc/details/limits.{0,80}2026-10-30",
    "droplet limits page cited with its fetch date": r"droplets/details/limits.{0,80}2026-10-30",
    "kubernetes limits page cited with its fetch date": r"kubernetes/details/limits.{0,80}2026-10-30",
    "spaces limits page cited with its fetch date": r"spaces/details/limits.{0,80}2026-10-30",
    "database limits page cited with its fetch date": r"postgresql/details/limits.{0,80}2026-10-30",
    "load balancer limits page cited with its fetch date": r"load-balancers/details/limits.{0,80}2026-10-30",
    "doctl page cited with its fetch date": r"doctl/reference/auth/init.{0,80}2026-10-30",
    "provider docs cited with their fetch date": r"terraform-provider-digitalocean.{0,120}2026-10-30",
}
# Built from pieces so this file does not trip the sweep it implements.
_TG = r"(?<![a-z0-9.-])(?!www\.|docs\.)[a-z0-9-]+\.(?:[a-z0-9]+\.)?twingate" + r"\.com"
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|chiefgyk3d|renegade|penguin|clouds.against|\bcah-\d|\btor1\b|\bnyc3\b|zsca" + r"ler"
    r"|" + _TG + r"|\bdp\.(?:st|pt|ct|sa)\.[a-z0-9]|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b[0-9A-F]{40}\b|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\bgha-(?!security)[a-z]|dop" + r"pler (?:secrets get|run) .{0,40}(?:-p|--project) "
    r"|\bdo[opr]_v1_[0-9a-f]{6}|\bDO00[A-Z0-9]{8}|\bhammunition\b|\bfoundation-\w+-vpc\b",
    re.I,
)


def missing(text):
    return [name for name, pat in RULES.items() if not re.search(pat, text, re.S | re.I)]


class DigitalOceanPack(unittest.TestCase):
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
        self.assertGreaterEqual(len(re.findall(r"\(Source: ", self.text)), 15)

    def test_lessons_are_in_the_generated_skills(self):
        for rel in (f".claude/skills/pack-{PACK}/SKILL.md", f".agents/skills/pack-{PACK}/SKILL.md"):
            with self.subTest(rel):
                self.assertIn("## What this pack does not cover", (ROOT / rel).read_text(encoding="utf-8"))

    def test_every_vendor_claim_is_labeled(self):
        # Every bullet or numbered rule carries a Source line or an explicit INFERRED/VERIFIED label.
        body = self.text.split("## Limits", 1)[1].split("## What this pack does not cover", 1)[0]
        unlabeled = [
            line[:80]
            for line in body.splitlines()
            if re.match(r"(?:-|\d+\.) ", line) and not re.search(r"\(Source: |VERIFIED|INFERRED", line)
        ]
        self.assertEqual(unlabeled, [])

    def test_no_private_values(self):
        self.assertIsNone(PRIVATE.search(self.text), PRIVATE.search(self.text))

    def test_guard_bites(self):
        # Negative cases: an empty pack misses every rule, one removed phrase misses exactly its rule, and each kind
        # of private value is caught by the sweep.
        self.assertEqual(len(missing("nothing here")), len(RULES))
        cut = re.sub(r"immutable", "x", self.text, flags=re.I)
        self.assertEqual(missing(cut), ["worker size slug is immutable"])
        for planted in (
            "/ho" + "me/someone/x",
            "acme." + "twingate.com",
            "host 10.1." + "2.3",
            "user" + "@" + "example.com",
            "ca" + "h-00-foundation",
            "chiefgyk" + "3d",
            "to" + "r1",
            "ny" + "c3",
            "0123abcd-" + "0123-4567-89ab-0123456789ab",
            "gha" + "-somerepo",
            "do" + "p_v1_0123456789abcdef",
            "DO" + "00ABCDEFGH12345678",
            "foundation-" + "xyz-vpc",
        ):
            with self.subTest(planted):
                self.assertIsNotNone(PRIVATE.search(planted))
        # The vendor documentation hosts are not private values.
        self.assertIsNone(PRIVATE.search("https://docs.digitalocean.com/reference/api/scopes/"))
        self.assertIsNone(PRIVATE.search("https://www.twingate.com/docs/services"))
        # The unlabeled-claim check bites on a bare rule.
        bare = "## Limits\n\n1. A bare claim with no label.\n\n## What this pack does not cover\n"
        body = bare.split("## Limits", 1)[1].split("## What this pack does not cover", 1)[0]
        self.assertTrue(
            [ln for ln in body.splitlines() if re.match(r"(?:-|\d+\.) ", ln) and not re.search(r"Source|VERIFIED", ln)]
        )


if __name__ == "__main__":
    unittest.main()
