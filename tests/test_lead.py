"""The lead persona: the orchestrator rules in templates/lead.md and the --lead installer block (issues #32, #33)."""

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import install as installer  # noqa: E402  (path set up above)
import validate as validate_mod  # noqa: E402
from test_user_layout import copy_fixture  # noqa: E402

LEAD = (ROOT / "templates" / "lead.md").read_text(encoding="utf-8")
# Words that would make the public template maintainer-specific (the validator sweeps hosts and paths separately).
PRIVATE_HINTS = ("SAFO", "Cloudflare", "Doppler", "Twingate")


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


class LeadText(unittest.TestCase):
    def fixture_errors(self, mutate):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            lead = fx / "templates" / "lead.md"
            lead.write_text(mutate(lead.read_text(encoding="utf-8")), encoding="utf-8")
            return [e for e in validate_mod.validate(fx) if "lead.md" in e]

    def test_the_real_validator_accepts_the_template(self):
        self.assertEqual(self.fixture_errors(lambda s: s), [])
        self.assertEqual(len(validate_mod.LEAD_RULES), 9)

    def test_the_real_validator_fails_when_any_rule_is_removed(self):
        for rule, phrase in validate_mod.LEAD_RULES.items():
            with self.subTest(rule):
                errors = self.fixture_errors(lambda s, p=phrase: s.replace(p, "REMOVED"))
                self.assertTrue(any(rule in e for e in errors), errors)

    def test_no_maintainer_specific_text(self):
        for word in PRIVATE_HINTS:
            self.assertNotIn(word, LEAD)

    def test_installed_body_is_the_template_without_its_header_comment(self):
        body = installer.lead_body()
        self.assertTrue(body.startswith("# The lead"))
        self.assertTrue(body.endswith("\n") and not body.endswith("\n\n"))
        self.assertNotIn("<!--", body)
        self.assertIn("## What you never do", body)
        # No blank lines or spaces between the begin marker and the text.
        self.assertIn("-->\n# The lead", installer.lead_block())

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

    def lead(self, *extra, apply=True, adapter="none"):
        args = ["--target", str(self.target), "--adapter", adapter, "--lead", "CLAUDE.md", *extra]
        if apply:
            args.append("--apply")
        return run_main(*args)

    def listed(self):
        return run_main("--target", str(self.target), "--list-managed", "--lead", "CLAUDE.md")

    def test_creates_the_file_with_a_marked_block(self):
        code, out, err = self.lead()
        self.assertEqual(code, 0, err)
        text = self.file.read_text()
        state, release, start, stop = installer.lead_inspect(text)
        self.assertEqual((state, start, stop), ("owned", 0, len(text)))
        self.assertEqual(release, installer.version())
        self.assertIn("new: CLAUDE.md", out)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["CLAUDE.md"])

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
        code, out, err = self.lead()  # no --update anywhere
        self.assertEqual(code, 0, err)
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertEqual(self.file.read_bytes(), first)
        self.assertEqual(self.file.read_text().count("doia-lead:begin"), 1)

    def test_stale_block_is_refused_without_update_with_the_lead_message(self):
        body = "# The lead\nold text\n"
        begin = f"<!-- doia-lead:begin release 0.0.1 sha256:{installer.body_sha256(body)} -->\n"
        stale = "before\n" + begin + body + installer.LEAD_END + "\nafter\n"
        self.file.write_text(stale)
        code, _out, err = self.lead()
        self.assertEqual(code, 1)
        self.assertIn("lead block differs from this release; use --update", err)
        self.assertEqual(self.file.read_text(), stale)
        self.assertEqual(self.listed()[0], 3)
        code, _out, err = self.lead("--update")
        self.assertEqual(code, 0, err)
        new = self.file.read_text()
        self.assertTrue(new.startswith("before\n<!-- doia-lead:begin release " + installer.version()))
        self.assertTrue(new.endswith(installer.LEAD_END + "\nafter\n"))
        self.assertNotIn("old text", new)

    def test_hand_edited_block_is_refused_and_force_discards_only_the_block(self):
        self.lead()
        edited = self.file.read_text().replace("You do not wait.", "You wait.", 1)
        self.file.write_text("top\n" + edited + "bottom\n")
        code, _out, err = self.lead("--update")
        self.assertEqual(code, 1)
        self.assertIn("edited files refused", err)
        self.assertIn("You wait.", self.file.read_text())
        code, _out, err = self.lead("--update", "--force-edited")
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
                code, _out, err = self.lead("--update")
                self.assertEqual(code, 1)
                self.assertIn("edited files refused", err)
                code, _out, err = self.lead("--update", "--force-edited")
                self.assertEqual(code, 1)
                self.assertIn("malformed lead block; fix it by hand", err)
                self.assertEqual(self.file.read_text(), content)

    def test_paths_outside_the_target_and_symlinks_are_refused(self):
        for bad in ("../CLAUDE.md", "/etc/x.md"):
            code, _o, err = run_main("--target", str(self.target), "--adapter", "none", "--lead", bad)
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

    def test_file_over_the_size_cap_is_refused_untouched(self):
        big = b"x" * (installer.MAX_MANAGED_BYTES + 1)
        self.file.write_bytes(big)
        code, _o, err = self.lead()
        self.assertEqual(code, 1)
        self.assertIn("too large", err)
        self.assertEqual(self.file.read_bytes(), big)

    def test_adapter_none_needs_lead_and_other_adapters_warn(self):
        code, _o, err = run_main("--target", str(self.target), "--adapter", "none")
        self.assertEqual(code, 1)
        self.assertIn("--lead", err)
        _code, out, _e = self.lead(apply=False)
        self.assertNotIn("note:", out)
        _code, out, _e = self.lead(apply=False, adapter="claude")
        self.assertIn("note: the claude role agents are planned with --lead", out)
        self.assertIn(".claude/agents/reviewer.md", out)

    def test_crlf_file_gets_a_crlf_block_that_stays_current(self):
        self.file.write_bytes(b"# Mine\r\n\r\nKeep.\r\n")
        code, _o, err = self.lead()
        self.assertEqual(code, 0, err)
        raw = self.file.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertEqual(self.listed()[0], 0)
        _code, out, _e = self.lead()
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertEqual(self.file.read_bytes(), raw)

    def test_block_converted_to_crlf_or_stripped_of_its_final_newline_is_still_current(self):
        self.lead()
        lf = self.file.read_bytes()
        self.file.write_bytes(lf.replace(b"\n", b"\r\n"))
        self.assertEqual(self.listed()[0], 0)
        self.file.write_bytes(lf.rstrip(b"\n"))
        code, out, _e = self.listed()
        self.assertEqual(code, 0, out)
        _code, out, _e = self.lead()
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertEqual(self.file.read_bytes(), lf.rstrip(b"\n"))

    def test_list_managed_current_and_edited(self):
        self.lead()
        code, out, _e = self.listed()
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"current\s+\S+\s+public\s+CLAUDE\.md")
        self.file.write_text(self.file.read_text().replace("You do not wait.", "You wait.", 1))
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"edited\s+\S+\s+public\s+CLAUDE\.md")

    def test_list_managed_never_reads_a_named_lead_file_as_clean(self):
        def expect(status):
            code, out, _e = self.listed()
            self.assertEqual(code, 3, out)
            self.assertRegex(out, status + r"\s+-\s+public\s+CLAUDE\.md")

        expect("missing")  # no file
        self.file.write_text("# nothing here\n")
        expect("missing")  # a file with no block
        self.file.write_bytes(b"\xff\xfe")
        expect("unsafe")  # not UTF-8
        self.file.write_bytes(b"x" * (installer.MAX_MANAGED_BYTES + 1))
        expect("unsafe")  # over the cap
        self.file.unlink()
        real = self.target / "real.md"
        real.write_text("x\n")
        self.file.symlink_to(real)
        expect("unsafe")  # a symlink
        self.file.unlink()
        self.file.mkdir()
        expect("unsafe")  # not a regular file
        # Without --lead the file is simply not looked at.
        code, out, _e = run_main("--target", str(self.target), "--list-managed")
        self.assertEqual(code, 0)
        self.assertNotIn("CLAUDE.md", out)

    def test_a_filesystem_error_on_the_named_file_is_a_row_not_a_refusal(self):
        self.file.write_text("x\n")  # exists, so the read is reached
        for exc, status in (
            (PermissionError("denied"), "unsafe"),
            (IsADirectoryError("dir"), "unsafe"),
            (OSError(5, "I/O error"), "unsafe"),
            (FileNotFoundError("gone"), "missing"),  # vanished between the existence check and the read
        ):
            with self.subTest(type(exc).__name__), mock.patch.object(installer, "_read_capped", side_effect=exc):
                code, out, err = self.listed()
                self.assertEqual(code, 3, (out, err))
                self.assertRegex(out, status + r"\s+-\s+public\s+CLAUDE\.md")
                self.assertNotIn("denied", out + err)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_really_unreadable_file_is_unsafe_and_exits_3(self):
        self.file.write_text("x\n")
        self.file.chmod(0)
        self.addCleanup(self.file.chmod, 0o600)
        code, out, _e = self.listed()
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r"unsafe\s+-\s+public\s+CLAUDE\.md")

    def test_block_hash_matches_its_body(self):
        block = installer.lead_block()
        m = installer.LEAD_BEGIN_RE.fullmatch(block.split("\n", 1)[0])
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), installer.body_sha256(installer.lead_body()))
        self.assertTrue(re.search(r"\n<!-- doia-lead:end -->\n$", block))


class LeadRace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "proj"
        self.target.mkdir()
        self.file = self.target / "CLAUDE.md"

    def run_with_edit_at(self, stage, edit):
        def seam(where, dest):
            if where == stage and dest.name == "CLAUDE.md":
                edit()

        with mock.patch.object(installer, "_race_seam", side_effect=seam):
            return run_main("--target", str(self.target), "--adapter", "none", "--lead", "CLAUDE.md", "--apply")

    def test_an_in_place_edit_between_read_and_rename_is_refused_and_kept(self):
        self.file.write_text("mine\n")
        inode = self.file.stat().st_ino

        def edit():  # same inode, same mode: only the bytes change, which is what an editor's save does
            with open(self.file, "r+") as f:
                f.write("mine\nplus a late edit\n")

        code, _out, err = self.run_with_edit_at("checked", edit)
        self.assertEqual(code, 1)
        self.assertIn("file changed while installing; re-run", err)
        self.assertEqual(self.file.read_text(), "mine\nplus a late edit\n")
        self.assertEqual(self.file.stat().st_ino, inode)
        self.assertEqual([p.name for p in self.target.iterdir()], ["CLAUDE.md"])  # no temp file left behind

    def test_without_a_concurrent_edit_the_same_path_succeeds(self):
        self.file.write_text("mine\n")
        code, _out, err = self.run_with_edit_at("checked", lambda: None)
        self.assertEqual(code, 0, err)
        self.assertTrue(self.file.read_text().startswith("mine\n"))

    def test_a_permission_only_change_before_the_rename_is_refused(self):
        self.file.write_text("mine\n")
        self.file.chmod(0o644)
        before = self.file.read_bytes()

        code, _out, err = self.run_with_edit_at("checked", lambda: self.file.chmod(0o600))
        self.assertEqual(code, 1)
        self.assertIn("destination changed during install", err)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.target.iterdir()], ["CLAUDE.md"])

    def test_the_recheck_runs_immediately_before_the_rename(self):
        self.file.write_text("mine\n")
        events = []
        real_recheck, real_rename = installer._recheck, os.rename

        def recheck(*a, **k):
            events.append("recheck")
            return real_recheck(*a, **k)

        def rename(*a, **k):
            events.append("rename")
            return real_rename(*a, **k)

        def seam(where, dest):
            events.append(where)

        with (
            mock.patch.object(installer, "_race_seam", side_effect=seam),
            mock.patch.object(installer, "_recheck", side_effect=recheck),
            mock.patch.object(installer.os, "rename", side_effect=rename),
        ):
            code, _out, err = run_main(
                "--target", str(self.target), "--adapter", "none", "--lead", "CLAUDE.md", "--apply"
            )
        self.assertEqual(code, 0, err)
        # Nothing, seam or otherwise, runs between the recheck and the rename; the 'checked' seam comes before both.
        self.assertEqual(events, ["checked", "recheck", "rename", "written"])


if __name__ == "__main__":
    unittest.main()
