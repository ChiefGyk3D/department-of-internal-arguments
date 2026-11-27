"""CI tests the Python versions the GYST python-ci workflow supports (issue #81): the ci.yml caller must not pin a
narrower list than the shared default, so 3.14 and 3.15 are covered. Loose to collect, strict to assert."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
REQUIRED = ("3.11", "3.12", "3.13", "3.14", "3.15")


def pinned_versions(text):
    """Versions the caller passes as python-versions, or None when it takes the shared default."""
    m = re.search(r"^\s*python-versions:\s*'?(\[.*?\])'?\s*(?:#.*)?$", text, re.M)
    return json.loads(m.group(1)) if m else None


class CiPythonMatrix(unittest.TestCase):
    def test_ci_covers_every_supported_python(self):
        text = CI.read_text()
        self.assertIn("python-ci.yml@", text, "ci.yml must call the shared python-ci workflow")
        pinned = pinned_versions(text)
        if pinned is not None:
            for v in REQUIRED:
                self.assertIn(v, pinned, f"ci.yml python-versions omits {v}")

    def test_guard_bites_on_the_old_list(self):
        self.assertEqual(
            pinned_versions('      python-versions: \'["3.11", "3.12", "3.13"]\'\n'), ["3.11", "3.12", "3.13"]
        )
        self.assertIsNone(pinned_versions("    with:\n      docker-build: false\n"))

    def test_drop_dates_are_recorded(self):
        text = CI.read_text()
        for line in ("3.11 after 2027-10-31", "3.12 after 2028-10-31", "3.13 after 2029-10-31"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
