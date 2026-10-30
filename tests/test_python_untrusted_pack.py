"""The python-untrusted-input pack (issue #69): the measured-lessons, subprocess and guard sections exist, the pack
stays short, and nothing maintainer-specific ships in it."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PACK_PATH = ROOT / "packs" / "python-untrusted-input" / "PACK.md"
MAX_BYTES = 14000  # the pack is read whole into a brief's context; trim before raising this

REQUIRED_HEADINGS = (
    "## Checklist: types, text and regexes",
    "## Checklist: archives and output caps",
    "## Checklist: subprocesses",
    "## Checklist: guards, parsers and logs",
)
# One phrase per lesson the issue asks for; a deleted lesson turns the matching test red.
REQUIRED_PHRASES = {
    "exact type": r"type\(x\) is",
    "no str on foreign": r"never call `str\(\)`",
    "process group": r"process group",
    "output cap": r"bounded output|cap the output",
    "argv not shell": r"argv list, never `shell=True`",
    "own CPU limit": r"RLIMIT_CPU",
    "dies with parent": r"PR_SET_PDEATHSIG|dies with its parent",
    "no inherited secrets": r"no inherited secrets|allowlist the environment",
    "isolated interpreter": r"python3 -I",
    "one definition": r"one definition",
    "fail closed": r"fail closed",
    "regex shape": r"shape limit",
    "tamper evident": r"tamper-evident",
    "test seam": r"test-only seam",
    "sealed descriptor": r"sealed|immutable descriptor",
    "casefold": r"casefold",
}
PRIVATE = re.compile(r"/ho" r"me/\w+|\b\d{1,3}(?:\.\d{1,3}){3}\b|chiefgyk3d|renegade-penguin", re.I)
BRITISH = re.compile(r"\b(?:behaviour|colour|authoris\w*|defence|organis\w*|recognis\w*|analyse\w*|licence)\b", re.I)


def missing(text):
    return sorted(
        [h for h in REQUIRED_HEADINGS if h not in text]
        + [k for k, pat in REQUIRED_PHRASES.items() if not re.search(pat, text, re.I)]
    )


class PythonUntrustedPack(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")

    def test_sections_and_lessons_present(self):
        self.assertEqual(missing(self.text), [])

    def test_missing_check_bites(self):
        self.assertTrue(missing(re.sub("(?i)fail closed", "fail open", self.text)))
        self.assertTrue(missing(self.text.replace("## Checklist: subprocesses", "## Other")))

    def test_size(self):
        self.assertLessEqual(len(self.text.encode("utf-8")), MAX_BYTES)

    def test_no_private_values_or_british_spelling(self):
        self.assertIsNone(PRIVATE.search(self.text))
        self.assertIsNone(BRITISH.search(self.text))
        self.assertIsNotNone(PRIVATE.search("see " + ".".join(["10", "0", "0", "1"])))

    def test_skill_files_match(self):
        for d in (".claude", ".agents"):
            skill = (ROOT / d / "skills" / "pack-python-untrusted-input" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("## Checklist: subprocesses", skill)


if __name__ == "__main__":
    unittest.main()
