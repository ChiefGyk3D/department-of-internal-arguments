"""Round-4 hardening: terminal-safe installer output and the filesystem-root identity check. Stdlib only."""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install as installer  # noqa: E402  (path set up above)

FORGED = "new: forged.toml  [public, sha256 " + "0" * 64 + "]"
# Every class the review named: LF, CR, ESC, C1 CSI, bidi controls, line/paragraph separators, zero-width.
DANGEROUS = (
    ["\n", "\r", "\x1b", "\x9b", "\x00", "\x7f", "\x85", " ", " "]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2066, 0x206A)]
    + ["​", "‌", "‍", "⁠", "﻿", "‎", "‏"]
)


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


def escape_of(ch):
    o = ord(ch)
    return f"\\x{o:02x}" if o <= 0xFF else f"\\u{o:04x}"


class Sanitizer(unittest.TestCase):
    def test_collected_the_full_set(self):
        self.assertGreaterEqual(len(DANGEROUS), 25)

    def test_every_dangerous_character_becomes_a_visible_escape(self):
        for ch in DANGEROUS:
            with self.subTest(code=hex(ord(ch))):
                out = installer._safe(f"a{ch}b")
                if ch != "\n":  # the line terminator itself is legitimate; line counts cover LF
                    self.assertNotIn(ch, out)
                self.assertEqual(out, f"a{escape_of(ch)}b")
                self.assertTrue(out.isascii() and out.isprintable())

    def test_ordinary_text_is_unchanged_and_backslash_is_not_ambiguous(self):
        self.assertEqual(installer._safe(".codex/agents/reviewer.toml"), ".codex/agents/reviewer.toml")
        self.assertEqual(installer._safe("a\\x1bb"), "a\\\\x1bb")  # literal backslash text differs from a real ESC

    def test_astral_characters_are_escaped_too(self):
        self.assertEqual(installer._safe("\U000e0001"), "\\U000e0001")


class PreviewForgery(unittest.TestCase):
    def fake_plan(self, target, name):
        dest = Path(target) / name
        return [("src", dest, "new", b"data", None, False)]

    def test_forged_record_in_a_filename_prints_as_one_escaped_line(self):
        for ch in DANGEROUS:
            with self.subTest(code=hex(ord(ch))), tempfile.TemporaryDirectory() as d:
                name = f"evil{ch}{FORGED}"
                with mock.patch.object(installer, "_plan", return_value=self.fake_plan(d, name)):
                    code, out, _ = run_main("--target", d, "--adapter", "codex")
                self.assertEqual(code, 0)
                lines = out.splitlines()
                self.assertEqual(len(lines), 2, out)  # one preview record, one summary line
                self.assertTrue(lines[0].startswith("new: evil"))
                if ch != "\n":  # the line terminator itself is legitimate; line counts cover LF
                    self.assertNotIn(ch, out)
                self.assertFalse(any(ln.startswith("new: forged.toml") for ln in lines))
                self.assertTrue(lines[0].isascii())

    def test_the_review_reproduction_with_a_newline(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(installer, "_plan", return_value=self.fake_plan(d, "x\n" + FORGED)):
                _, out, _ = run_main("--target", d, "--adapter", "codex")
            self.assertEqual(sum(ln.startswith("new:") for ln in out.splitlines()), 1)

    def test_a_refusal_message_naming_a_hostile_path_is_one_escaped_line(self):
        for ch in DANGEROUS:
            with self.subTest(code=hex(ord(ch))), tempfile.TemporaryDirectory() as d:
                err_in = ValueError(f"collision; merge manually (or use --update): a{ch}b\n{FORGED}")
                with mock.patch.object(installer, "_plan", side_effect=err_in):
                    code, _, err = run_main("--target", d, "--adapter", "codex")
                self.assertEqual(code, 1)
                self.assertEqual(len(err.splitlines()), 1, err)
                if ch != "\n":
                    self.assertNotIn(ch, err)

    def test_list_managed_prints_a_hostile_filename_as_one_row(self):
        with tempfile.TemporaryDirectory() as d:
            agents = Path(d) / ".codex/agents"
            agents.mkdir(parents=True)
            shutil.copy(ROOT / ".codex/agents/reviewer.toml", agents / "reviewer.toml")
            shutil.copy(ROOT / ".codex/agents/reviewer.toml", agents / f"x\n{FORGED}.toml")
            code, out, _ = run_main("--target", d, "--list-managed")
            self.assertEqual(code, 3)  # the extra file is an orphan
            self.assertEqual(len(out.splitlines()), 3, out)  # two rows and the count line
            self.assertFalse(any(ln.startswith("new:") for ln in out.splitlines()))


class UserTextInMessages(unittest.TestCase):
    def test_a_pack_name_with_a_newline_prints_one_escaped_stderr_line(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = run_main(
                "--target", d, "--adapter", "codex", "--pack", "threat-model\nnew: fake  [public, sha256 0]"
            )
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertEqual(len(err.splitlines()), 1, err)
            self.assertIn("unknown pack", err)
            self.assertIn("\\x0anew: fake", err)

    def test_a_target_directory_name_with_a_newline_or_esc_prints_one_escaped_line(self):
        for ch in ("\n", "\x1b[31m"):
            with self.subTest(ch=repr(ch)), tempfile.TemporaryDirectory() as d:
                real = Path(d) / "real"
                real.mkdir()
                link = Path(d) / f"a{ch}b"
                os.symlink(real, link)  # the symlink refusal echoes the path
                code, out, err = run_main("--target", str(link), "--adapter", "codex")
                self.assertEqual(code, 1)
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertNotIn("\x1b", err)
                self.assertIn("symlink", err)

    def argparse_error(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            installer.main(list(args))
        self.assertEqual(cm.exception.code, 2)
        return err.getvalue()

    def check_one_escaped_error_line(self, err, ch):
        lines = err.splitlines()
        self.assertTrue(lines and lines[0].startswith("usage:"), err)  # argparse's usage is kept
        self.assertEqual(sum(": error:" in ln for ln in lines), 1, err)
        self.assertFalse(any(ln.startswith("FORGED") for ln in lines), err)
        if ch != "\n":
            self.assertNotIn(ch, err)
        self.assertTrue(err.isascii())

    def test_argparse_errors_are_one_escaped_line_for_every_dangerous_character(self):
        for ch in DANGEROUS:
            with self.subTest(code=hex(ord(ch))), tempfile.TemporaryDirectory() as d:
                cases = {
                    "unknown option": ("--target", d, f"--bad{ch}FORGED"),
                    "unknown positional": ("--target", d, f"pos{ch}FORGED"),
                    "bad choice": ("--target", d, "--adapter", f"bad{ch}FORGED"),
                    "bad layout choice": ("--target", d, "--layout", f"bad{ch}FORGED"),
                }
                for label, args in cases.items():
                    err = self.argparse_error(*args)
                    self.check_one_escaped_error_line(err, ch)
                    if "choice" not in label:  # argparse already repr()s a bad choice: \n and \r arrive as \\n and \\r
                        self.assertIn(escape_of(ch), err, label)

    def test_missing_required_and_missing_value_errors_keep_usage_and_exit_2(self):
        err = self.argparse_error()
        self.assertIn("are required: --target", err)
        self.assertEqual(sum(": error:" in ln for ln in err.splitlines()), 1)
        self.assertIn("expected one argument", self.argparse_error("--target"))

    def test_a_plain_bad_choice_message_keeps_its_content(self):
        # Only version-independent properties: argparse quotes the choice list differently across Python versions.
        err = self.argparse_error("--target", "/tmp", "--adapter", "nope")
        msg = next(ln for ln in err.splitlines() if ": error:" in ln).split(": error:", 1)[1]
        self.assertIn("invalid choice", msg)
        self.assertIn("nope", msg)
        for name in installer.ADAPTERS:
            self.assertIn(name, msg)


class FilesystemIdentity(unittest.TestCase):
    """A bind mount of / would keep its own spelling; its (st_dev, st_ino) is the same as /'s. A real bind-mount test
    needs privileges and is NOT run here; os.stat is mocked instead so the identity comparison itself is exercised."""

    def test_a_directory_with_the_roots_device_and_inode_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.stat
            root_stat = real("/")

            def fake(path, *a, **k):
                return root_stat if os.fspath(path) == str(Path(d).absolute()) else real(path, *a, **k)

            self.assertNotEqual(real(d).st_ino, root_stat.st_ino)  # the mock really changes the answer
            with mock.patch.object(os, "stat", side_effect=fake):
                with self.assertRaisesRegex(ValueError, "filesystem root"):
                    installer._check_target(d, ROOT)
                with self.assertRaisesRegex(ValueError, "filesystem root"):
                    installer.plan(d, "codex")

    def test_a_normal_directory_with_its_real_stat_still_passes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(installer._check_target(d, ROOT), Path(d).absolute())

    def test_same_inode_on_a_different_device_is_not_the_root(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.stat
            root_stat = real("/")
            other = os.stat_result((root_stat.st_mode, root_stat.st_ino, root_stat.st_dev + 1) + tuple(root_stat)[3:])

            def fake(path, *a, **k):
                return other if os.fspath(path) == str(Path(d).absolute()) else real(path, *a, **k)

            with mock.patch.object(os, "stat", side_effect=fake):
                self.assertEqual(installer._check_target(d, ROOT), Path(d).absolute())



class HostileProgName(unittest.TestCase):
    """A prog name taken from sys.argv[0] reaches argparse's error prefix, usage line and -h output; none may carry
    a raw control character. Only version-independent properties are asserted (no argparse wording)."""

    PROGS = ("tool\x1b[31m", "tool\nFORGED: error: x", "tool\r\u202e", "tool%(prog)s%s")

    def run_main(self, prog, *args):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sys, "argv", [prog, *args]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as cm,
        ):
            installer.main(list(args))
        return cm.exception.code, out.getvalue(), err.getvalue()

    def assert_clean(self, text):
        self.assertTrue(text.isascii(), repr(text))
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertFalse(any(ln.startswith("FORGED") for ln in text.splitlines()), repr(text))

    def test_error_prefix_and_usage_line_are_escaped(self):
        for prog in self.PROGS:
            with self.subTest(prog=prog):
                code, _, err = self.run_main(prog, "--bogus")
                self.assertEqual(code, 2)
                self.assert_clean(err)
                self.assertTrue(err.startswith("usage:"), repr(err))
                prefix = installer._safe(prog) + ": error:"
                self.assertEqual(sum(ln.startswith(prefix) for ln in err.splitlines()), 1, repr(err))

    def test_help_output_is_escaped(self):
        for prog in self.PROGS:
            with self.subTest(prog=prog):
                code, out, _ = self.run_main(prog, "-h")
                self.assertEqual(code, 0)
                self.assert_clean(out)

    def test_a_plain_prog_name_is_unchanged(self):
        code, _, err = self.run_main("install.py", "--bogus")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("usage: install.py"), repr(err))
        self.assertIn("install.py: error:", err)

    def test_a_lone_surrogate_argument_prints_as_safe_ascii(self):
        # argparse repr()-escapes a surrogate in a bad choice and _safe() escapes that backslash again, so the
        # text is doubly escaped. That is accepted: readable, ASCII, one line. Only these properties are asserted.
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_main("install.py", "--target", d, "--adapter", "\ud800")
        self.assertEqual(code, 2)
        self.assertTrue(err.isascii(), repr(err))
        self.assertEqual(sum(": error:" in ln for ln in err.splitlines()), 1, repr(err))


if __name__ == "__main__":
    unittest.main()
