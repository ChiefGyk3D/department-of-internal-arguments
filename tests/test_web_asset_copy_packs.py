"""The web-quality, asset-production and copy-review packs (issue #109): each required rule is present, the shape is
right, the generated skills exist, the packs hold no private value, and the guard bites."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

MAX_BYTES = 15000
RULES = {
    "web-quality": {
        "lighthouse mobile": r"Lighthouse.{0,40}mobile",
        "performance 90": r"performance >= 90",
        "accessibility 100": r"accessibility = 100",
        "CLS": r"CLS <= 0\.1",
        "LCP": r"LCP <= 2\.5 s",
        "contrast text": r"4\.5:1",
        "contrast large and UI": r"3:1",
        "page weight budget": r"budget",
        "third-party script": r"render-blocking third-party",
        "measure": r"45 to 75",
        "line height": r"line height >= 1\.4",
        "font size": r"16 px",
        "tap target minimum": r">= 24 px",
        "tap target recommended": r"44 px",
        "alt text": r"alt text",
        "image dimensions": r"width and height",
        "keyboard pass": r"[Kk]eyboard pass",
        "200 percent zoom": r"200% zoom",
        "320 px": r"320 px",
        "hugo build": r"hugo --gc --minify",
        "headers file": r"_headers",
        "link check": r"link check",
        "pinned hugo": r"Hugo version is pinned",
        "calls accessibility skills": r"accessibility-scan",
        "calls web-design-guidelines": r"web-design-guidelines",
        "calls ui-ux-pro-max": r"ui-ux-pro-max",
        "calls frontend-design": r"frontend-design",
        "calls web-perf": r"cloudflare:web-perf",
        "calls impeccable": r"impeccable",
        "chooses no aesthetics": r"does not choose aesthetics",
    },
    "asset-production": {
        "provenance manifest": r"[Pp]rovenance",
        "provenance fields": r"source, license, author, date",
        "QR error correction": r"Q or H",
        "quiet zone": r"[Qq]uiet zone >= 4 modules",
        "zbarimg round trip": r"zbarimg",
        "canonical URL": r"canonical URL",
        "no shortener": r"[Nn]o third-party shortener",
        "svg no script": r"no `<script>`",
        "svg viewBox": r"viewBox",
        "svgo": r"svgo",
        "size 32": r"32 x 32",
        "size 180": r"180 x 180",
        "size 192": r"192 x 192",
        "size 512": r"512 x 512",
        "size 1200x630": r"1200 x 630",
        "mascot clear-space": r"clear-space",
        "exiftool": r"exiftool",
        "gps": r"GPS",
        "license allow list": r"allow list",
        "attribution": r"attribution",
        "300 dpi": r"300 DPI",
        "bleed": r"bleed",
        "transparent png or vector": r"transparent PNG or vector",
    },
    "copy-review": {
        "pilot no item measured": r"pilot pack.{0,80}No item here was measured",
        "claims traced": r"trace[s]? to a recorded source",
        "url redirects": r"at most 2 redirects",
        "disclosure before first link": r"before the first (?:such )?link",
        "disclosure checked live": r"ALWAYS checked against the live platform or regulator text, "
        r"never quoted from memory",
        "secret scan": r"secret scan",
        "authorized scope": r"authorized scope",
        "length table re-verified": r"re-verified before use",
        "banned phrase grep": r"banned-phrase list by grep",
        "filler flagged not judged": r"flagged, not judged",
        "voice skill called": r"call the private voice skill for first-person drafts",
        "voice never restated": r"Never restate, summarize or embed",
    },
}
# Built from pieces so this file does not trip the sweep it implements.
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|renegade|penguin|clouds.against|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)


def missing(pack, text):
    return [name for name, pat in RULES[pack].items() if not re.search(pat, text, re.S)]


class WebAssetCopyPacks(unittest.TestCase):
    def text(self, pack):
        return (ROOT / "packs" / pack / "PACK.md").read_text(encoding="utf-8")

    def test_shape(self):
        self.assertEqual(len(RULES), 3)
        for pack in RULES:
            with self.subTest(pack):
                text = self.text(pack)
                self.assertIn(pack, PACK_NAMES)
                self.assertTrue(text.startswith(f'---\nname: "{pack}"\n'))
                self.assertRegex(text, r'\nupdated: "\d{4}-\d{2}-\d{2}"\n')
                self.assertIn("## How to verify", text)
                self.assertLessEqual(len(text.encode("utf-8")), MAX_BYTES)

    def test_every_rule_is_present(self):
        for pack in RULES:
            with self.subTest(pack):
                self.assertEqual(missing(pack, self.text(pack)), [])

    def test_copy_review_is_marked_pilot_and_embeds_no_voice_rules(self):
        text = self.text("copy-review")
        self.assertIn("Pilot pack, no item measured", text.split("---", 2)[1])
        self.assertNotRegex(text, r"(?i)\bsample\b.{0,40}voice|voice rules?:")

    def test_generated_skills_exist(self):
        for pack in RULES:
            for rel in (f".claude/skills/pack-{pack}/SKILL.md", f".agents/skills/pack-{pack}/SKILL.md"):
                with self.subTest(rel):
                    self.assertIn("## How to verify", (ROOT / rel).read_text(encoding="utf-8"))

    def test_no_private_values(self):
        for pack in RULES:
            with self.subTest(pack):
                self.assertIsNone(PRIVATE.search(self.text(pack)), PRIVATE.search(self.text(pack)))

    def test_eval_rows_exist_for_each_pack(self):
        rows = (ROOT / "evals/behavioral-evals.md").read_text(encoding="utf-8")
        for needle in ("3.2:1", "no width and height", "shortener", "GPS", "token", "affiliate link"):
            with self.subTest(needle):
                self.assertIn(needle, rows)

    def test_guard_bites(self):
        for pack in RULES:
            self.assertEqual(len(missing(pack, "nothing here")), len(RULES[pack]))
        cut = self.text("asset-production").replace("Q or H", "x")
        self.assertEqual(missing("asset-production", cut), ["QR error correction"])
        for planted in ("/ho" + "me/someone/x", "host 10.1." + "2.3", "a" + "@example.com", "renegade" + "-x"):
            with self.subTest(planted):
                self.assertIsNotNone(PRIVATE.search(planted))


if __name__ == "__main__":
    unittest.main()
