import re
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


SAFO_INSTALL_CLAIM = re.compile(r"SAFO\s+(will\s+install|installs)\b", re.IGNORECASE)
INSTALL_SENTENCE = "SAFO does not install personas"


def doc_files(root):
    files = sorted(Path(root, "docs").glob("*.md")) + [Path(root, "README.md")]
    if len(files) < len(PAGES) + 1:
        raise AssertionError("docs tree not collected")
    return files


def install_claims(root):
    """Pages that still say SAFO installs personas. Unreadable pages raise (fail closed)."""
    return [f.name for f in doc_files(root) if SAFO_INSTALL_CLAIM.search(f.read_text(encoding="utf-8"))]


def routine_block(root):
    text = Path(root, "docs/install.md").read_text(encoding="utf-8")
    m = re.search(r"The routine, from a checkout pinned [^\n]*:\n\n```\n(.*?)\n```", text, re.DOTALL)
    if not m:
        raise AssertionError("install routine block not found")
    return m.group(1)


def routine_problems(root):
    lines = [ln for ln in routine_block(root).splitlines() if re.search(r"\bcheckout\b", ln)]
    problems = []
    if not lines:
        problems.append("no checkout line")
    for ln in lines:
        if re.search(r"checkout\s+<tag>", ln):
            problems.append("checkout <tag> form")
        if not re.search(r"checkout\s+<commit>\s+#\s+\S", ln):
            problems.append("no `checkout <commit>   # <tag>` form")
    return problems


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

    def test_relationship_page_says_safo_does_not_install(self):
        text = (ROOT / "docs/relationship-to-safo-and-gyst.md").read_text(encoding="utf-8")
        self.assertIn(INSTALL_SENTENCE, text)

    def test_no_page_says_safo_installs(self):
        self.assertEqual(install_claims(ROOT), [])

    def test_install_claim_guard_bites(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            page = fx / "docs/relationship-to-safo-and-gyst.md"
            page.write_text(
                page.read_text(encoding="utf-8").replace(INSTALL_SENTENCE, "SAFO will install personas"),
                encoding="utf-8",
            )
            self.assertEqual(install_claims(fx), ["relationship-to-safo-and-gyst.md"])
            readme = fx / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8") + "\nsafo INSTALLS them.\n", encoding="utf-8")
            self.assertIn("README.md", install_claims(fx))

    def test_install_guard_fails_closed_on_missing_page(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            (fx / "docs/roles.md").unlink()
            with self.assertRaises(AssertionError):
                install_claims(fx)

    def test_install_routine_pins_by_commit_with_tag_comment(self):
        self.assertEqual(routine_problems(ROOT), [])

    def test_install_routine_guard_bites_on_old_form(self):
        with tempfile.TemporaryDirectory() as t:
            fx = fixture(t)
            page = fx / "docs/install.md"
            page.write_text(
                page.read_text(encoding="utf-8").replace("checkout <commit>", "checkout <tag>"), encoding="utf-8"
            )
            self.assertTrue(routine_problems(fx))


if __name__ == "__main__":
    unittest.main()
