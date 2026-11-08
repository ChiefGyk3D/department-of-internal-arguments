"""The five media packs (issue #110): each required rule is present, the shape is right, the generated skills exist,
safe-live keeps its block authority, front-door keeps the partner parity rule, the packs hold no private value, and the
guard bites."""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import PACK_NAMES  # noqa: E402  (path set up above)

MAX_BYTES = 15000
RULES = {
    "media-dispatch": {
        "pilot no item measured": r"[Pp]ilot pack.{0,80}No item here was measured",
        "routing table": r"Classify the request, then load packs",
        "loads safe-live always": r"`safe-live` \(always, before release\)",
        "safe-live outranks": r"`safe-live` outranks everything",
        "never overrides": r"never overrides",
        "owner lifts block": r"Only the owner lifts one, in the owner's own message",
        "one decision": r"one decision sentence|ONE decision|One decision",
        "hours": r"estimated hours",
        "displaces": r"what it displaces",
        "ceiling": r"capacity ceiling",
        "code adds hours": r"Code adds the hour estimates",
        "estimates only": r"estimates only",
        "parity before distribution": r"parity table",
        "facts from overlay": r"Facts come from the overlay",
        "platform rules fetched": r"never relays a platform or regulator rule from memory",
        "local first": r"[Dd]rafting is local-first",
        "no approval inferred": r"No approval is inferred",
    },
    "safe-live": {
        "can block": r"can BLOCK any release",
        "not overridable": r"cannot be overridden",
        "owner lifts": r"Only the owner can lift a BLOCK, in the owner's own message",
        "window only capture": r"[Ww]indow-only capture or a dedicated capture display",
        "canary": r"CANARY-N0TST",
        "stream delay": r"[Ss]tream delay covers the takedown time",
        "delay measured": r"worst of three stopwatch trials",
        "notifications": r"[Nn]otifications are off",
        "redaction scene": r"redaction scene",
        "redaction one second": r"at most 1 s",
        "checklist dated": r"dated, pass or fail per line",
        "frame sampling": r"fps=1/5",
        "frame count rule": r"floor\(duration_seconds / 5\)",
        "ocr": r"tesseract",
        "secret shapes": r"BEGIN \[A-Z \]\*PRIVATE KEY",
        "location and kind": r"file:line: kind` only, never the matched text",
        "sampling is prefilter": r"Sampling is a prefilter only",
        "full duration review": r"whole duration",
        "scene change": r"scene,0\.05",
        "thumbnail own path": r"Thumbnail path",
        "thumbnail no duration": r"no duration",
        "exiftool strip": r"exiftool -all= -overwrite_original",
        "scope line": r"[Ll]ab-only scope line",
        "disclosure always verified": r"ALWAYS verified against the live platform or regulator text, "
        r"never taken from memory",
        "sponsor from owner": r"comes from the owner, never assumed",
        "rotation to human": r"Rotation is handed to the human",
        "take down": r"Take down, first",
        "rotate": r"\*\*Rotate\.\*\*",
        "notify": r"\*\*Notify\.\*\*",
        "record": r"\*\*Record\.\*\*",
    },
    "clip-desk": {
        "whole recording": r"Search the whole recording",
        "broad then focused": r"Pass 1 \(broad\).{0,400}Pass 2 \(focused\)",
        "hook context payoff": r"hook, context, payoff",
        "length": r"60 to 120 s.{0,20}up to 180 s for a technical story",
        "ffprobe": r"ffprobe",
        "never stretch": r"Never stretch a weak clip",
        "three a week": r"about 3 finished clips",
        "dedupe": r"Dedupe",
        "caption qa": r"Caption QA",
        "technical names": r"technical-name list",
        "compare to transcript": r"comm -23",
        "missing and mismatched": r"missing and mismatched",
        "vertical framing": r"Vertical framing check",
        "nine sixteen": r"9:16",
        "thumbnail contrast": r"4\.5:1 body text, 3:1 large text",
        "export presets": r"Per-platform export presets",
        "presets sourced": r"live help page with URL and date read",
        "safe-live gate": r"No clip is released without a `safe-live` PASS",
        "pilot": r"treat them as pilot",
    },
    "stage-crew": {
        "audio preset": r"One audio preset, saved and loadable",
        "alignment": r"-20 dBFS.{0,60}-9 dBFS",
        "ebur128": r"ebur128",
        "alerts under voice": r"Alerts sit under the voice",
        "six scenes": r"Six scenes, one collection, hotkeys",
        "redaction scene": r"Redaction scene matches `safe-live`",
        "alert diet": r"Alert diet",
        "contrast aa": r">= 4\.5:1.{0,200}>= 3:1",
        "captions owner decision": r"Live captions policy is an OWNER decision",
        "captions undecided": r"\"undecided\"",
        "safe areas": r"Safe-area crops per destination",
        "vertical live": r"Vertical live layout",
        "alert paths": r"Alert paths per platform, separate",
        "live assets copy": r"change a copy named as work in progress",
    },
    "front-door": {
        "parity rule": r"PARTNER PARITY RULE",
        "parity none diminished": r"none may be diminished against another in any surface",
        "parity dimensions": r"Same size, prominence, stable order, same wording|same prominence",
        "parity table": r"parity table",
        "parity contract not read": r"contract terms themselves are never read",
        "schedule source": r"One canonical schedule source",
        "iana timezone": r"IANA timezone",
        "link hub one file": r"link hub is generated from one data file",
        "bios agree": r"Bios agree",
        "discord matrix": r"Discord and Matrix rules",
        "priority list": r"Platform priority list",
        "hand and auto": r"tended by hand.{0,200}automated",
        "capacity ceiling": r"capacity ceiling",
        "total by code": r"computed by code",
        "text alternative": r"alt text",
    },
}
# Built from pieces so this file does not trip the sweep it implements.
PRIVATE = re.compile(
    r"/ho"
    r"me/\w+|~/s"
    r"rc/|renegade|penguin|clouds.against|chiefgyk|\b\d{1,3}(?:\.\d{1,3}){3}\b|@[a-z0-9-]+\.[a-z]{2,}"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|streamer\.bot|streamelements|tikfinity|aitum|elgato|zv-e10|stream deck",
    re.I,
)


def missing(pack, text):
    return [name for name, pat in RULES[pack].items() if not re.search(pat, text, re.S)]


class MediaPacks(unittest.TestCase):
    def text(self, pack):
        return (ROOT / "packs" / pack / "PACK.md").read_text(encoding="utf-8")

    def test_shape(self):
        self.assertEqual(len(RULES), 5)
        for pack in RULES:
            with self.subTest(pack):
                text = self.text(pack)
                self.assertIn(pack, PACK_NAMES)
                self.assertTrue(text.startswith(f'---\nname: "{pack}"\n'))
                self.assertRegex(text, r'\nupdated: "\d{4}-\d{2}-\d{2}"\n')
                self.assertIn("## How to verify", text)
                self.assertLessEqual(len(text.encode("utf-8")), MAX_BYTES)
        self.assertEqual(len(PACK_NAMES), 18)

    def test_every_rule_is_present(self):
        for pack in RULES:
            with self.subTest(pack):
                self.assertEqual(missing(pack, self.text(pack)), [])

    def test_dispatch_is_a_pack_and_no_media_role_exists(self):
        self.assertFalse(list((ROOT / "roles").glob("*dispatch*")))
        self.assertFalse(list((ROOT / "roles").glob("*media*")))

    def test_dispatch_routes_to_every_specialist_pack(self):
        text = self.text("media-dispatch")
        for pack in ("safe-live", "clip-desk", "stage-crew", "front-door"):
            with self.subTest(pack):
                self.assertIn(f"`{pack}`", text)

    def test_dispatch_is_marked_pilot(self):
        front = self.text("media-dispatch").split("---\n", 2)[1]
        self.assertIn("pilot pack", front.lower() + self.text("media-dispatch").lower())
        self.assertRegex(self.text("media-dispatch"), r"Status: pilot pack.{0,80}No item here was measured")

    def test_no_pack_overrides_safe_live(self):
        for pack in ("clip-desk", "stage-crew", "front-door"):
            with self.subTest(pack):
                self.assertRegex(self.text(pack), r"BLOCKS? stands?")

    def test_platform_rules_are_never_stated_from_memory(self):
        # The disclosure kit must name the live source and forbid memory; no platform's wording is quoted.
        text = self.text("safe-live")
        self.assertNotRegex(text, r"(?i)\"(ad|advertisement|sponsored)\"")
        self.assertIn("record its URL and the date read", text)

    def test_scan_block_prints_location_and_kind_only(self):
        text = self.text("safe-live")
        script = re.search(r"```bash\n(.*?)```", text, re.S).group(1)
        planted = "api_key=" + "PLANTEDVALUE123\nhost 10.1." + "2.3 up\nclean line\n"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ocr.txt"
            f.write_text(planted, encoding="utf-8")
            out = subprocess.run(["bash", "-c", script, "scan", str(f)], capture_output=True, text=True).stdout
        self.assertIn(":1: label", out)
        self.assertIn(":2: ipv4", out)
        self.assertNotIn("PLANTEDVALUE123", out)
        self.assertNotIn("10.1.", out)
        self.assertNotIn("clean line", out)

    def test_caption_compare_finds_misspelling_and_omission(self):
        text = self.text("clip-desk")
        cmd = re.search(r"```bash\n(.*?)```", text, re.S).group(1)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "terms.txt").write_text("kubectl\nnmap\n", encoding="utf-8")
            (Path(d) / "transcript.txt").write_text("run kubectl then nmap\n", encoding="utf-8")
            (Path(d) / "captions.txt").write_text("run kubctl then\n", encoding="utf-8")
            out = subprocess.run(
                ["bash", "-c", cmd, "cmp", "terms.txt", "transcript.txt", "captions.txt"],
                capture_output=True,
                text=True,
                cwd=d,
            ).stdout
        self.assertIn("kubectl", out)
        self.assertIn("nmap", out)

    def test_dispatch_eval_requires_the_cut(self):
        import json

        cases = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))["cases"]
        case = next(c for c in cases if c["id"] == 44)
        self.assertRegex(case["expect"], r"bottom-ranked")
        self.assertRegex(case["grading_reason"], r"cut")

    def test_generated_skills_exist(self):
        for pack in RULES:
            for rel in (f".claude/skills/pack-{pack}/SKILL.md", f".agents/skills/pack-{pack}/SKILL.md"):
                with self.subTest(rel):
                    self.assertIn("## How to verify", (ROOT / rel).read_text(encoding="utf-8"))

    def test_no_private_values(self):
        for pack in RULES:
            with self.subTest(pack):
                self.assertIsNone(PRIVATE.search(self.text(pack)), PRIVATE.search(self.text(pack)))

    def test_facts_file_shape_is_documented(self):
        doc = (ROOT / "docs/overlay-and-ownership.md").read_text(encoding="utf-8")
        self.assertIn("## Media team facts file", doc)
        for needle in ("hours_per_week", "live_captions", "stream_delay_seconds", "partner yes|no"):
            with self.subTest(needle):
                self.assertIn(needle, doc)

    def test_eval_rows_exist(self):
        rows = (ROOT / "evals/behavioral-evals.md").read_text(encoding="utf-8")
        for needle in ("canary", "floor(duration/5)", "10 hour weekly ceiling", "partnered", "3.4:1"):
            with self.subTest(needle):
                self.assertIn(needle, rows)

    def test_guard_bites(self):
        for pack in RULES:
            self.assertEqual(len(missing(pack, "nothing here")), len(RULES[pack]))
        cut = self.text("safe-live").replace("can BLOCK any release", "x")
        self.assertEqual(missing("safe-live", cut), ["can block"])
        cut = self.text("front-door").replace("none may be diminished against another in any surface", "x")
        self.assertEqual(missing("front-door", cut), ["parity none diminished"])
        for planted in ("/ho" + "me/someone/x", "host 10.1." + "2.3", "a" + "@example.com", "Stream" + "Elements"):
            with self.subTest(planted):
                self.assertIsNotNone(PRIVATE.search(planted))


if __name__ == "__main__":
    unittest.main()
