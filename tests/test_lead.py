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
import state_fixture  # noqa: E402  (path set up above)
import validate as validate_mod  # noqa: E402
from test_user_layout import copy_fixture  # noqa: E402

# Every test in this module runs against a private state directory (issue #40): no write reaches the home.
setUpModule = state_fixture.set_up_module
tearDownModule = state_fixture.tear_down_module

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
        self.assertEqual(len(validate_mod.LEAD_RULES), 13)

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
        state_fixture.record_lead(self.target, "CLAUDE.md", body)  # an older install of this block
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


class LeadOwnership(unittest.TestCase):
    """Issue #41: the lead block is owned through the state file, never through its own begin-line hash."""

    OLD_BODY = "# The lead\nold text\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = Path(self._tmp.name) / "proj"
        self.target.mkdir()
        self.file = self.target / "CLAUDE.md"

    def forged(self, body=None, release="0.0.1", before="before\n", after="\nafter\n"):
        """A block whose begin line hashes its body, in a file the installer never wrote."""
        body = self.OLD_BODY if body is None else body
        begin = f"<!-- doia-lead:begin release {release} sha256:{installer.body_sha256(body)} -->\n"
        return before + begin + body + installer.LEAD_END + after

    def lead(self, *extra, apply=True, layout="project"):
        args = ["--target", str(self.target), "--adapter", "none", "--lead", "CLAUDE.md", "--layout", layout, *extra]
        if apply:
            args.append("--apply")
        return run_main(*args)

    def listed(self, layout="project"):
        return run_main("--target", str(self.target), "--list-managed", "--lead", "CLAUDE.md", "--layout", layout)

    def entries(self, layout="project"):
        return state_fixture.entries_of(self.target, layout, roots=None)

    def test_a_forged_block_is_refused_by_update_and_the_file_is_byte_identical(self):
        forged = self.forged()
        self.file.write_text(forged)
        for extra in (("--update",), ()):
            with self.subTest(extra):
                code, _out, err = self.lead(*extra)
                self.assertEqual(code, 1)
                self.assertIn("unverified files refused", err)
                self.assertEqual(self.file.read_bytes(), forged.encode())
        self.assertIsNone(self.entries())  # a refused run records nothing

    def test_a_forged_block_lists_as_unverified_and_exits_3(self):
        self.file.write_text(self.forged())
        code, out, _e = self.listed()
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r"unverified\s+0\.0\.1\s+public\s+CLAUDE\.md")

    def test_a_forged_copy_of_this_releases_block_lists_as_unverified_until_applied(self):
        self.file.write_text("top\n" + installer.lead_block())
        code, out, _e = self.listed()
        self.assertEqual(code, 3, out)
        self.assertRegex(out, r"unverified\s+\S+\s+public\s+CLAUDE\.md")
        self.assertIn("state: ", out)

    def test_force_edited_replaces_only_the_forged_block_and_records_it(self):
        self.file.write_text(self.forged())
        code, out, err = self.lead("--update", "--force-edited")
        self.assertEqual(code, 0, err)
        self.assertIn("forced", out)
        text = self.file.read_text()
        self.assertTrue(text.startswith("before\n<!-- doia-lead:begin release " + installer.version()))
        self.assertTrue(text.endswith(installer.LEAD_END + "\nafter\n"))
        self.assertNotIn("old text", text)
        self.assertEqual(self.listed()[0], 0)
        entry = self.entries()["CLAUDE.md"]
        self.assertEqual(entry["kind"], "lead")
        self.assertEqual(set(entry), {"kind", "release", "source", "body_sha256"})

    def test_a_recorded_block_is_updated_and_the_entry_follows(self):
        self.file.write_text(self.forged())
        state_fixture.record_lead(self.target, "CLAUDE.md", self.OLD_BODY)
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"stale\s+0\.0\.1\s+public\s+CLAUDE\.md")
        code, _out, err = self.lead("--update")
        self.assertEqual(code, 0, err)
        digest = installer.statestore.body_sha256(installer.lead_body().encode())
        self.assertEqual(self.entries()["CLAUDE.md"]["body_sha256"], digest)
        self.assertEqual(self.entries()["CLAUDE.md"]["release"], installer.version())
        self.assertEqual(self.listed()[0], 0)

    def test_a_block_swapped_for_another_self_consistent_one_is_edited_not_owned(self):
        self.lead()
        swapped = self.forged(body="# The lead\nsomeone else's text\n", release=installer.version())
        self.file.write_text(swapped)  # the begin-line hash matches this body; the recorded digest does not
        code, _out, err = self.lead("--update")
        self.assertEqual(code, 1)
        self.assertIn("edited files refused", err)
        self.assertEqual(self.file.read_text(), swapped)
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"edited\s+\S+\s+public\s+CLAUDE\.md")
        code, _out, err = self.lead("--update", "--force-edited")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.listed()[0], 0)

    def test_rerun_of_this_releases_block_changes_nothing_and_is_recorded_on_apply(self):
        original = "# Mine\n\n" + installer.lead_block() + "tail\n"
        self.file.write_text(original)
        code, out, err = self.lead(apply=False)  # no --update anywhere
        self.assertEqual(code, 0, err)
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertIsNone(self.entries())  # a preview records nothing
        code, out, err = self.lead()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.file.read_text(), original)
        self.assertEqual(self.entries()["CLAUDE.md"]["kind"], "lead")
        self.assertEqual(self.listed()[0], 0)

    def test_the_entry_is_the_digest_of_the_lf_body_whatever_the_line_endings(self):
        self.file.write_bytes(b"# Mine\r\n")
        self.lead()
        crlf = self.entries()["CLAUDE.md"]["body_sha256"]
        self.assertEqual(crlf, installer.statestore.body_sha256(installer.lead_body().encode()))
        self.assertEqual(self.listed()[0], 0)
        raw = self.file.read_bytes()
        code, out, _e = self.lead()
        self.assertIn("unchanged: CLAUDE.md", out)
        self.assertEqual(self.file.read_bytes(), raw)
        # An editor that converts the whole file to LF keeps the block current.
        self.file.write_bytes(raw.replace(b"\r\n", b"\n"))
        self.assertEqual(self.listed()[0], 0)

    def test_the_same_file_in_two_layouts_keeps_separate_entries(self):
        self.assertEqual(self.lead(layout="project")[0], 0)
        self.assertIsNotNone(self.entries("project"))
        self.assertIsNone(self.entries("user"))
        # Only the project layout recorded it, so the user layout does not own an older block in the same file.
        self.file.write_text(self.forged())
        state_fixture.record_lead(self.target, "CLAUDE.md", self.OLD_BODY, layout="project")
        forged = self.file.read_bytes()
        code, _out, err = self.lead("--update", layout="user")
        self.assertEqual(code, 1)
        self.assertIn("unverified files refused", err)
        self.assertEqual(self.file.read_bytes(), forged)
        self.assertRegex(self.listed("user")[1], r"unverified\s+0\.0\.1")
        self.assertRegex(self.listed("project")[1], r"stale\s+0\.0\.1")
        self.assertEqual(self.lead("--update", layout="project")[0], 0)
        self.assertEqual(self.entries("project")["CLAUDE.md"]["release"], installer.version())
        self.assertIsNone(self.entries("user"))

    def test_the_text_around_the_block_is_never_touched_on_any_path(self):
        before, after = "# Mine\r\nkeep\n\n", "trailing text\r\nno final newline"

        def check(label, content, args, record_old=False, want=0):
            self.file.write_bytes(content.encode())
            if record_old:
                state_fixture.record_lead(self.target, "CLAUDE.md", self.OLD_BODY)
            code, _o, err = self.lead(*args)
            self.assertEqual(code, want, (label, err))
            return self.file.read_bytes()

        forged = self.forged(before=before, after="\n" + after)
        for label, args in (("unverified", ()), ("unverified update", ("--update",))):
            with self.subTest(label):
                self.assertEqual(check(label, forged, args, want=1), forged.encode())
        for label, args, old in (("forced", ("--update", "--force-edited"), False), ("update", ("--update",), True)):
            with self.subTest(label):
                out = check(label, forged, args, record_old=old)
                self.assertTrue(out.startswith(before.encode()), out)
                self.assertTrue(out.endswith(installer.LEAD_END.encode() + b"\r\n" + after.encode()), out)  # CRLF file
                self.assertNotIn(b"old text", out)
        with self.subTest("unchanged"):
            same = before + installer.lead_block().replace("\n", "\r\n") + after  # the block ends its own line
            self.assertEqual(check("unchanged", same, ()), same.encode())
        for tail in (b"", b"\n", b"x"):
            with self.subTest(append=tail):
                self.file.write_bytes(b"# Mine\n" + tail)
                self.assertEqual(self.lead()[0], 0)
                self.assertTrue(self.file.read_bytes().startswith(b"# Mine\n" + tail))

    def test_an_entry_of_another_kind_at_the_path_is_not_a_lead_record(self):
        self.file.write_text(self.forged())
        state_fixture.record(self.target, "CLAUDE.md", layout="project", roots=None)  # kind file
        raw = self.file.read_bytes()
        code, _out, err = self.lead("--update", "--force-edited")
        self.assertEqual(code, 1)
        self.assertIn("different kind", err)
        self.assertEqual(self.file.read_bytes(), raw)

    def test_a_lead_entry_does_not_vouch_for_a_role_file_at_the_same_path(self):
        rel = ".claude/agents/reviewer.md"
        code, _o, err = run_main("--target", str(self.target), "--adapter", "claude", "--lead", rel)
        self.assertEqual(code, 1)
        self.assertIn("also a role file", err)

    def test_a_file_entry_at_the_path_does_not_make_a_block_owned_in_the_listing(self):
        self.file.write_text(self.forged())
        entry = {
            "kind": "file",
            "release": installer.version(),
            "source": "public",
            "body_sha256": installer.statestore.body_sha256(self.OLD_BODY.encode()),
        }
        with installer.statestore.Store(self.target, "project") as store:
            store.save(installer.statestore.with_entry({}, "CLAUDE.md", entry))
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"unverified\s+0\.0\.1\s+public\s+CLAUDE\.md")

    def test_a_lead_entry_holding_a_role_files_digest_does_not_make_that_file_owned(self):
        rel = ".claude/agents/reviewer.md"
        base = ["--target", str(self.target), "--adapter", "claude"]
        self.assertEqual(run_main(*base, "--apply")[0], 0)
        path = self.target / rel
        path.write_text(path.read_text().replace(f"(release {installer.version()})", "(release 0.0.1)", 1))
        state_fixture.record(self.target, rel, layout="project", roots=None)  # recorded as an older install
        raw = path.read_bytes()
        self.assertEqual(run_main(*base, "--update")[0], 0)  # owned: a file entry vouches for it
        path.write_bytes(raw)
        entries = state_fixture.entries_of(self.target, "project")
        entries[rel]["kind"] = "lead"  # same digest, wrong kind
        with installer.statestore.Store(self.target, "project") as store:
            store.save(entries)
        code, _o, err = run_main(*base, "--update", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("unverified files refused", err)
        self.assertEqual(path.read_bytes(), raw)
        self.assertRegex(run_main("--target", str(self.target), "--list-managed")[1], r"unverified\s+0\.0\.1")

    def test_a_pending_only_lead_entry_is_not_owned(self):
        self.file.write_text(self.forged())
        digest = installer.statestore.body_sha256(self.OLD_BODY.encode())
        entry = {"kind": "lead", "release": installer.version(), "source": "public", "pending_sha256": digest}
        with installer.statestore.Store(self.target, "project") as store:
            store.save(installer.statestore.with_entry({}, "CLAUDE.md", entry))
        self.assertEqual(self.lead("--update")[0], 1)
        self.assertRegex(self.listed()[1], r"unverified\s+0\.0\.1")

    def test_a_failed_apply_leaves_no_pending_digest_and_a_vanished_file_is_pruned(self):
        self.assertEqual(self.lead()[0], 0)
        self.assertIsNotNone(self.entries()["CLAUDE.md"])
        self.file.unlink()
        other = self.target / "AGENTS.md"
        run_main("--target", str(self.target), "--adapter", "none", "--lead", "AGENTS.md", "--apply")
        self.assertTrue(other.exists())
        self.assertEqual(sorted(self.entries()), ["AGENTS.md"])
        for entry in self.entries().values():
            self.assertNotIn("pending_sha256", entry)

    def test_an_unusable_state_makes_a_marked_block_unverified_and_refuses_writes(self):
        self.assertEqual(self.lead()[0], 0)
        path = installer.statestore.state_path(self.target, "project")
        saved = Path(path).read_bytes()
        self.addCleanup(Path(path).write_bytes, saved)
        Path(path).write_text("not json")
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"unverified\s+\S+\s+public\s+CLAUDE\.md")
        raw = self.file.read_bytes()
        code, _o, err = self.lead("--update", "--force-edited")
        self.assertEqual(code, 1)
        self.assertIn("ownership state refused", err)
        self.assertEqual(self.file.read_bytes(), raw)

    def test_hand_edit_of_a_recorded_block_is_edited(self):
        self.lead()
        self.file.write_text(self.file.read_text().replace("You do not wait.", "You wait.", 1))
        code, out, _e = self.listed()
        self.assertEqual(code, 3)
        self.assertRegex(out, r"edited\s+\S+\s+public\s+CLAUDE\.md")


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
            if str(a[0]).endswith(".doia-tmp"):  # the state store renames its own temporary files too
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
