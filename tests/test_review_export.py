"""scripts/review_export.py (issue #138): the clean review export and the next-round prompt, tested against real
temporary git repositories, with one case per refusal."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_export as rx  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(repo, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def write(repo, rel, text="x\n"):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        write(self.repo, "src/app.py", "print('one')\n")
        write(self.repo, "README.md", "readme\n")
        self.base = commit(self.repo, "base")
        write(self.repo, "src/app.py", "print('two')\n")
        write(self.repo, "src/other.py", "other = 1\n")
        write(self.repo, "AGENTS.md", "agent text\n")
        write(self.repo, ".claude/agents/x.md", "agent\n")
        write(self.repo, "sub/CLAUDE.md", "nested instructions\n")
        write(self.repo, ".codex/config.toml", "k = 1\n")
        write(self.repo, ".agents/skills/s/SKILL.md", "skill\n")
        write(self.repo, "GEMINI.md", "g\n")
        self.head = commit(self.repo, "head")
        self.out = self.tmp / "out"

    def files(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file() or p.is_symlink())


class Export(Base):
    def test_exports_the_ref_and_strips_agent_files(self):
        m = rx.export(self.repo, self.head, self.out, base=self.base)
        got = self.files(self.out)
        self.assertEqual(got, ["CHANGE.diff", "README.md", "src/app.py", "src/other.py"])
        self.assertEqual((self.out / "src/app.py").read_text(), "print('two')\n")
        self.assertEqual(m["files"], 4)
        self.assertEqual(m["bytes"], sum((self.out / f).stat().st_size for f in got))

    def test_export_is_from_the_ref_not_the_worktree(self):
        write(self.repo, "src/app.py", "print('dirty')\n")
        rx.export(self.repo, self.head, self.out)
        self.assertEqual((self.out / "src/app.py").read_text(), "print('two')\n")
        self.assertNotIn("CHANGE.diff", self.files(self.out))

    def test_change_diff_excludes_stripped_files_and_honors_paths(self):
        rx.export(self.repo, self.head, self.out, base=self.base)
        diff = (self.out / "CHANGE.diff").read_text()
        self.assertIn("src/app.py", diff)
        for leaked in ("AGENTS.md", "CLAUDE.md", ".claude", ".codex", "GEMINI.md", "SKILL.md"):
            self.assertNotIn(leaked, diff)
        out2 = self.tmp / "out2"
        rx.export(self.repo, self.head, out2, base=self.base, paths=["src/other.py"])
        diff2 = (out2 / "CHANGE.diff").read_text()
        self.assertIn("src/other.py", diff2)
        self.assertNotIn("src/app.py", diff2)

    def test_strip_list_is_configurable(self):
        rx.export(self.repo, self.head, self.out, strip=["src/other.py"])
        got = self.files(self.out)
        self.assertNotIn("src/other.py", got)
        self.assertIn("AGENTS.md", got)  # defaults replaced, not added to

    def test_empty_existing_output_directory_is_accepted(self):
        self.out.mkdir()
        rx.export(self.repo, self.head, self.out)
        self.assertIn("README.md", self.files(self.out))

    def test_safe_symlink_inside_the_tree_is_kept(self):
        os.symlink("app.py", self.repo / "src" / "link.py")
        head = commit(self.repo, "link")
        rx.export(self.repo, head, self.out)
        self.assertTrue((self.out / "src/link.py").is_symlink())


class Refusals(Base):
    def refused(self, *args, **kw):
        with self.assertRaises(rx.Refused) as cm:
            rx.export(*args, **kw)
        return str(cm.exception)

    def test_refuses_a_non_empty_output_directory(self):
        self.out.mkdir()
        (self.out / "keep.txt").write_text("mine")
        msg = self.refused(self.repo, self.head, self.out)
        self.assertIn("not empty", msg)
        self.assertEqual(self.files(self.out), ["keep.txt"])

    def test_refuses_an_output_path_that_is_a_file(self):
        self.out.write_text("a file")
        self.assertIn("not a directory", self.refused(self.repo, self.head, self.out))

    def test_refuses_an_output_path_inside_the_repo(self):
        msg = self.refused(self.repo, self.head, self.repo / "build" / "export")
        self.assertIn("inside the repository", msg)
        self.assertFalse((self.repo / "build").exists())

    def test_refuses_an_output_path_inside_the_repo_through_a_symlink(self):
        link = self.tmp / "alias"
        os.symlink(self.repo, link)
        self.assertIn("inside the repository", self.refused(self.repo, self.head, link / "x"))

    def test_refuses_a_symlink_that_escapes_with_dotdot(self):
        os.symlink("../../outside", self.repo / "src" / "bad")
        head = commit(self.repo, "bad link")
        self.assertIn("escapes", self.refused(self.repo, head, self.out))
        self.assertEqual(self.files(self.out) if self.out.exists() else [], [])

    def test_refuses_an_absolute_symlink(self):
        os.symlink("/etc/passwd", self.repo / "abs")
        head = commit(self.repo, "abs link")
        self.assertIn("escapes", self.refused(self.repo, head, self.out))

    def test_refuses_a_file_over_the_size_cap(self):
        write(self.repo, "big.bin", "y" * 5000)
        head = commit(self.repo, "big")
        msg = self.refused(self.repo, head, self.out, max_file_bytes=1000)
        self.assertIn("big.bin", msg)
        self.assertIn("size cap", msg)
        self.assertEqual(self.files(self.out) if self.out.exists() else [], [])

    def test_a_stripped_file_over_the_cap_does_not_count(self):
        write(self.repo, "AGENTS.md", "y" * 5000)
        head = commit(self.repo, "big agent file")
        rx.export(self.repo, head, self.out, max_file_bytes=1000)

    def test_refuses_an_oversized_change_diff(self):
        msg = self.refused(self.repo, self.head, self.out, base=self.base, max_file_bytes=20)
        self.assertIn("size cap", msg)

    def test_refuses_an_unknown_ref_and_an_option_shaped_ref(self):
        self.assertIn("unknown ref", self.refused(self.repo, "no-such-ref", self.out))
        self.assertIn("unknown ref", self.refused(self.repo, "--output=x", self.out))
        self.assertIn("unknown ref", self.refused(self.repo, self.head, self.out, base="--no-such"))

    def test_refuses_a_repo_file_named_change_diff(self):
        write(self.repo, "CHANGE.diff", "planted\n")
        head = commit(self.repo, "planted")
        self.assertIn("CHANGE.diff", self.refused(self.repo, head, self.out, base=self.base))


class NextPrompt(unittest.TestCase):
    BASE = "Review src/app.py at <head> against <base>.\n\n  Keep  my   spacing.\n"
    FIND = "F1 High: input x breaks y\nF2 Low: typo\n"

    def test_base_prompt_is_kept_byte_for_byte_then_header_then_findings(self):
        out = rx.next_prompt(self.BASE, self.FIND)
        self.assertTrue(out.startswith(self.BASE))
        self.assertTrue(out.endswith(self.FIND))
        self.assertIn(rx.PROMPT_HEADER, out)
        self.assertEqual(out, self.BASE + "\n" + rx.PROMPT_HEADER + "\n" + self.FIND)

    def test_base_without_trailing_newline_is_not_reworded(self):
        out = rx.next_prompt("no newline", self.FIND)
        self.assertTrue(out.startswith("no newline\n"))

    def test_header_carries_the_held_rule_and_no_model_or_tier_values(self):
        h = rx.PROMPT_HEADER.lower()
        self.assertIn("held", h)
        self.assertIn("failed scan tool", h)
        self.assertIn("not clean", h)
        for banned in ("gpt", "--model", " -m ", "cyber-access", "daybreak", "claude", "codex", "sandbox"):
            self.assertNotIn(banned, h)

    def test_refuses_empty_findings(self):
        with self.assertRaises(rx.Refused):
            rx.next_prompt(self.BASE, "  \n")

    def test_refuses_empty_base_prompt(self):
        with self.assertRaises(rx.Refused):
            rx.next_prompt("", self.FIND)


class Cli(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = rx.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_export_prints_a_manifest(self):
        rc, out, _ = self.run_cli(
            "export", "--repo", str(self.repo), "--ref", self.head, "--out", str(self.out), "--base", self.base
        )
        self.assertEqual(rc, 0)
        self.assertIn("files: 4", out)
        self.assertIn("bytes: ", out)

    def test_refusal_is_exit_1_with_a_message_and_writes_nothing(self):
        rc, out, err = self.run_cli(
            "export", "--repo", str(self.repo), "--ref", self.head, "--out", str(self.repo / "inside")
        )
        self.assertEqual(rc, 1)
        self.assertIn("refused", err.lower())
        self.assertEqual(out, "")

    def test_next_prompt_writes_a_new_file_and_never_overwrites(self):
        b, f, o = self.tmp / "base.txt", self.tmp / "find.txt", self.tmp / "next.txt"
        b.write_text("base prompt\n")
        f.write_text("finding\n")
        argv = ("next-prompt", "--base-prompt", str(b), "--findings", str(f), "--output", str(o))
        self.assertEqual(self.run_cli(*argv)[0], 0)
        self.assertTrue(o.read_text().startswith("base prompt\n"))
        rc, _, err = self.run_cli(*argv)
        self.assertEqual(rc, 1)
        self.assertIn("refused", err.lower())


if __name__ == "__main__":
    unittest.main()
