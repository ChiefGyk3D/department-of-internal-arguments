import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_wiki  # noqa: E402  (path set up above)
from validate import privacy_hits, validate  # noqa: E402

PAGES = {
    "home.md",
    "what-and-why.md",
    "install.md",
    "roles.md",
    "packs.md",
    "policies.md",
    "writing-a-brief.md",
    "overlay-and-ownership.md",
    "evals-and-measurements.md",
    "relationship-to-safo-and-gyst.md",
}


def fixture(base):
    out = Path(base) / "fx"
    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return out


class DocsTests(unittest.TestCase):
    def test_all_pages_exist(self):
        found = {p.name for p in (ROOT / "docs").glob("*.md")}
        self.assertEqual(found, PAGES)

    def test_wiki_renders_every_page(self):
        pages = gen_wiki.render(ROOT / "docs", ROOT, None, gen_wiki.REPO_URL)
        self.assertIn("Home.md", pages)
        self.assertIn("_Sidebar.md", pages)
        self.assertEqual(len(pages), len(PAGES) + 1)
        self.assertIn("ChiefGyk3D/department-of-internal-arguments", gen_wiki.REPO_URL)

    def test_check_passes_on_repo(self):
        r = subprocess.run([sys.executable, "scripts/gen_wiki.py", "--check"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_check_fails_on_broken_link(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            with (fx / "docs/roles.md").open("a", encoding="utf-8") as fh:
                fh.write("\n[gone](nope.md)\n")
            r = subprocess.run(
                [sys.executable, "scripts/gen_wiki.py", "--check"], cwd=fx, capture_output=True, text=True
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("nope.md", r.stderr)

    def test_privacy_sweep_covers_docs(self):
        self.assertEqual([h for h in privacy_hits(ROOT) if h[0].startswith("docs/")], [])
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            with (fx / "docs/home.md").open("a", encoding="utf-8") as fh:
                fh.write("\nserver at " + ".".join(["10", "1", "2", "3"]) + "\n")
            self.assertIn("docs/home.md", {h[0] for h in privacy_hits(fx)})
            self.assertTrue(any("docs/home.md" in e for e in validate(fx)))

    def test_link_check_covers_docs(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            with (fx / "docs/packs.md").open("a", encoding="utf-8") as fh:
                fh.write("\n[bad](../roles/missing.md)\n")
            self.assertTrue(any("docs/packs.md" in e and "broken link" in e for e in validate(fx)))

    def test_validate_requires_docs_home(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            (fx / "docs/home.md").unlink()
            readme = fx / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8").replace("docs/home.md", "docs/install.md"))
            errors = validate(fx)
            self.assertIn("docs/home.md is missing: it becomes the wiki Home page", errors)
            self.assertEqual([e for e in errors if "broken link" in e], [])


if __name__ == "__main__":
    unittest.main()
