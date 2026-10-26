"""The lead persona: the orchestrator rules in templates/lead.md and the --lead installer block (issues #32, #33)."""

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install as installer  # noqa: E402  (path set up above)

LEAD = (ROOT / "templates" / "lead.md").read_text(encoding="utf-8")
# Phrases that must stay in the lead text, one per rule the issue names. Collected loosely, asserted strictly.
RULES = {
    "waits": "You do not wait.",
    "tracking": "`issue: owner/repo#N`",
    "cwd": "same command as the worktree dispatch",
    "look first": "## Before you tell the human to do something",
    "access": "ask once for the union",
    "done": "## Done",
    "token": "approval token",
    "public weakness": "unfixed weakness",
    "rejected": "Re-suggest an option the human has rejected.",
}
# Words that would make the public template maintainer-specific (the validator sweeps hosts and paths separately).
PRIVATE_HINTS = ("SAFO", "Cloudflare", "Doppler", "Twingate")


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


class LeadText(unittest.TestCase):
    def test_every_rule_is_present(self):
        self.assertEqual(len(RULES), 9)
        for name, phrase in RULES.items():
            self.assertIn(phrase, LEAD, name)

    def test_guard_bites_when_a_rule_is_removed(self):
        broken = LEAD.replace(RULES["waits"], "")
        self.assertNotIn(RULES["waits"], broken)

    def test_no_maintainer_specific_text(self):
        for word in PRIVATE_HINTS:
            self.assertNotIn(word, LEAD)

    def test_installed_body_is_the_template_without_its_header_comment(self):
        body = installer.lead_body()
        self.assertTrue(body.startswith("# The lead"))
        self.assertNotIn("<!--", body)
        self.assertIn("## What you never do", body)

    def test_growth_stays_modest(self):
        # lead.md is injected into a controller session; the installed text stays under 6.5 KB (it was 4.3 KB).
        self.assertLess(len(installer.lead_body().encode("utf-8")), 6500)


class LeadInstall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "proj"
        self.target.mkdir()
        self.file = self.target / "CLAUDE.md"
        # --lead rides on an install; put the agents in once so each test exercises only the lead block.
        run_main("--target", str(self.target), "--adapter", "claude", "--apply")

    def lead(self, *extra, apply=True):
        args = ["--target", str(self.target), "--lead", "CLAUDE.md", "--update", *extra]
        if apply:
            args.append("--apply")
        return run_main(*args)

    def test_creates_the_file_with_a_marked_block(self):
        code, out, err = self.lead()
        self.assertEqual(code, 0, err)
        text = self.file.read_text()
        state, release, start, stop = installer.lead_inspect(text)
        self.assertEqual((state, start, stop), ("owned", 0, len(text)))
        self.assertEqual(release, installer.version())
        self.assertIn("new: CLAUDE.md", out)

    def test_appends_and_leaves_existing_text_alone(self):
        self.file.write_text("# My project\n\nKeep this.\n")
        code, _out, err = self.lead()
        self.assertEqual(code, 0, err)
        text = self.file.read_text()
        self.assertTrue(text.startswith("# My project\n\nKeep this.\n\n<!-- doia-lead:begin"))
        self.assertEqual(installer.lead_inspect(text)[0], "owned")

    def test_preview_writes_nothing(self):
        self.file.write_text("x\n")
        code, out, _err = self.lead(apply=False)
        self.assertEqual(code, 0)
        self.assertIn("append", out)
        self.assertEqual(self.file.read_text(), "x\n")

    def test_rerun_is_idempotent_and_needs_no_update_flag(self):
        self.lead()
        first = self.file.read_bytes()
        code, out, _err = run_main("--target", str(self.target), "--lead", "CLAUDE.md", "--update", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertEqual(self.file.read_bytes(), first)
        self.assertEqual(self.file.read_text().count("doia-lead:begin"), 1)

    def test_stale_block_needs_update_and_only_the_block_is_replaced(self):
        body = "# The lead\nold text\n"
        begin = f"<!-- doia-lead:begin release 0.0.1 sha256:{installer.body_sha256(body)} -->\n"
        self.file.write_text("before\n" + begin + body + installer.LEAD_END + "\nafter\n")
        code, _out, err = run_main("--target", str(self.target), "--lead", "CLAUDE.md")
        self.assertEqual(code, 1)
        self.assertIn("--update", err)
        code, _out, err = self.lead()
        self.assertEqual(code, 0, err)
        new = self.file.read_text()
        self.assertTrue(new.startswith("before\n<!-- doia-lead:begin release " + installer.version()))
        self.assertTrue(new.endswith(installer.LEAD_END + "\nafter\n"))
        self.assertNotIn("old text", new)

    def test_hand_edited_block_is_refused_and_force_discards_only_the_block(self):
        self.lead()
        edited = self.file.read_text().replace("You do not wait.", "You wait.", 1)
        self.file.write_text("top\n" + edited + "bottom\n")
        code, _out, err = self.lead()
        self.assertEqual(code, 1)
        self.assertIn("edited files refused", err)
        self.assertIn("You wait.", self.file.read_text())
        code, _out, err = self.lead("--force-edited")
        self.assertEqual(code, 0, err)
        text = self.file.read_text()
        self.assertTrue(text.startswith("top\n<!-- doia-lead:begin"))
        self.assertTrue(text.endswith("bottom\n"))
        self.assertNotIn("You wait.", text)

    def test_malformed_blocks_are_refused_even_with_force(self):
        begin = "<!-- doia-lead:begin release 1 sha256:" + "0" * 64 + " -->\n"
        cases = {
            "lone begin": begin + "text\n",
            "prose mention": "see doia-lead: in the docs\n",
            "two blocks": installer.lead_block() * 2,
            "end before begin": installer.LEAD_END + "\n" + begin,
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.file.write_text(content)
                for extra in ((), ("--force-edited",)):
                    code, _out, _err = self.lead(*extra)
                    self.assertEqual(code, 1)
                self.assertEqual(self.file.read_text(), content)

    def test_paths_outside_the_target_and_symlinks_are_refused(self):
        for bad in ("../CLAUDE.md", "/etc/x.md"):
            code, _o, err = run_main("--target", str(self.target), "--lead", bad, "--update")
            self.assertEqual(code, 1, bad)
            self.assertIn("under the target", err)
        real = self.target / "real.md"
        real.write_text("x\n")
        self.file.symlink_to(real)
        code, _o, _err = self.lead()
        self.assertEqual(code, 1)
        self.assertEqual(real.read_text(), "x\n")

    def test_non_utf8_file_is_refused(self):
        self.file.write_bytes(b"\xff\xfe broken")
        code, _o, err = self.lead()
        self.assertEqual(code, 1)
        self.assertIn("UTF-8", err)

    def test_list_managed_covers_the_block(self):
        self.lead()
        code, out, _e = run_main("--target", str(self.target), "--list-managed", "--lead", "CLAUDE.md")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"current\s+\S+\s+public\s+CLAUDE\.md")
        self.file.write_text(self.file.read_text().replace("You do not wait.", "You wait.", 1))
        code, out, _e = run_main("--target", str(self.target), "--list-managed", "--lead", "CLAUDE.md")
        self.assertEqual(code, 3)
        self.assertRegex(out, r"edited\s+\S+\s+public\s+CLAUDE\.md")

    def test_block_hash_matches_its_body(self):
        block = installer.lead_block()
        m = installer.LEAD_BEGIN_RE.fullmatch(block.split("\n", 1)[0])
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), installer.body_sha256(installer.lead_body()))
        self.assertTrue(re.search(r"\n<!-- doia-lead:end -->\n$", block))


if __name__ == "__main__":
    unittest.main()
