"""scripts/review_export.py (issue #138): the clean review export and the next-round prompt, tested against real
temporary git repositories, with one case per refusal."""

import inspect
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_export as rx  # noqa: E402

NFC = "café"  # one code point
NFD = "café"  # base letter plus combining accent: the same name to a normalizing file system
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
        rx.export(self.repo, self.head, self.out, strip=["src/other.py"], dot_allow=(".claude", ".codex", ".agents"))
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


class Hardening(Base):
    """Issue #138 security review: link composition, case, denylist breadth, attributes."""

    def refused(self, head):
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out)
        return str(cm.exception)

    def test_link_through_an_earlier_link_that_leaves_the_export_is_refused(self):
        # a/b/c -> ../.. is the root; x -> a/b/c/.. looks inside lexically but is the parent of the root
        (self.repo / "a" / "b").mkdir(parents=True)
        os.symlink("../..", self.repo / "a" / "b" / "c")
        os.symlink("a/b/c/..", self.repo / "x")
        head = commit(self.repo, "composed")
        self.assertIn("symlink", self.refused(head))
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_link_that_passes_through_another_link_is_refused_even_when_it_stays_inside(self):
        os.symlink("src", self.repo / "d")
        os.symlink("d/app.py", self.repo / "e")
        head = commit(self.repo, "through")
        self.assertIn("passes through", self.refused(head))

    def test_link_to_a_link_target_is_allowed(self):
        os.symlink("app.py", self.repo / "src" / "l1")
        os.symlink("l1", self.repo / "src" / "l2")
        head = commit(self.repo, "chain")
        rx.export(self.repo, head, self.out)
        self.assertTrue((self.out / "src" / "l2").is_symlink())

    def test_final_link_check_failure_removes_everything_written(self):
        real = rx._verify_links

        def boom(out, links):
            raise rx.Refused("simulated escape")

        rx._verify_links = boom
        self.addCleanup(setattr, rx, "_verify_links", real)
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, self.head, self.out)
        self.assertFalse(self.out.exists())
        self.out.mkdir()
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, self.head, self.out)
        self.assertTrue(self.out.is_dir())
        self.assertEqual(list(self.out.iterdir()), [])

    def test_verify_links_catches_a_link_that_resolves_outside(self):
        out = self.tmp / "v"
        out.mkdir()
        os.symlink("../repo", out / "bad")
        with self.assertRaises(rx.Refused):
            rx._verify_links(out, ["bad"])
        os.unlink(out / "bad")
        (out / "f").write_text("x")
        os.symlink("f", out / "ok")
        rx._verify_links(out, ["ok"])

    def test_strip_matching_ignores_case(self):
        write(self.repo, "Agents.md", "a\n")
        write(self.repo, "docs/CLAUDE.MD", "a\n")
        write(self.repo, ".Claude/x.md", "a\n")
        write(self.repo, ".GitHub/Agents/y.md", "a\n")
        head = commit(self.repo, "cases")
        rx.export(self.repo, head, self.out, base=self.base)
        got = self.files(self.out)
        self.assertEqual(got, ["CHANGE.diff", "README.md", "src/app.py", "src/other.py"])
        diff = (self.out / "CHANGE.diff").read_text().lower()
        for leaked in ("agents", "claude"):
            self.assertNotIn(leaked, diff)

    def test_default_denylist_covers_the_known_agent_files(self):
        names = [
            ".mcp.json",
            "CLAUDE.local.md",
            ".vscode/mcp.json",
            ".github/instructions/a.md",
            ".github/prompts/a.md",
            ".github/chatmodes/a.md",
            ".clinerules",
            ".roo/rules.md",
            ".aider.conf.yml",
            ".aiderignore",
            ".continue/config.json",
            ".zed/settings.json",
            ".junie/guidelines.md",
            ".kiro/steering.md",
            ".amazonq/rules.md",
            "AGENT.md",
            "AGENTS.override.md",
            "deep/er/GEMINI.md",
        ]
        for n in names:
            write(self.repo, n)
        write(self.repo, ".vscode/settings.json", "{}\n")  # editor settings, not agent files: kept
        head = commit(self.repo, "denylist")
        rx.export(self.repo, head, self.out)
        got = self.files(self.out)
        self.assertEqual(got, [".vscode/settings.json", "README.md", "src/app.py", "src/other.py"])

    def test_export_ignores_gitattributes_and_agrees_with_the_diff(self):
        write(self.repo, ".gitattributes", "src/other.py export-ignore\nsrc/sub.py export-subst\nsrc/bin.dat -diff\n")
        write(self.repo, "src/sub.py", "v = '$Format:%H$'\n")
        write(self.repo, "src/bin.dat", "line\n")
        head = commit(self.repo, "attrs")
        rx.export(self.repo, head, self.out, base=self.base)
        self.assertTrue((self.out / "src/other.py").is_file())  # export-ignore not applied
        self.assertEqual((self.out / "src/sub.py").read_text(), "v = '$Format:%H$'\n")  # no substitution
        diff = (self.out / "CHANGE.diff").read_text()
        for f in ("src/other.py", "src/sub.py"):
            self.assertIn(f, diff)
        self.assertIn("+line", diff)  # the -diff attribute does not hide the content
        self.assertIn("$Format:%H$", diff)

    def _tree_with(self, entries):
        """Commit a tree holding exactly these (path, kind, text) entries, built with plumbing so names that
        differ only by case or normalization can coexist."""
        env = {**os.environ, **GIT_ENV}
        lines = []
        for path, kind, payload in entries:
            blob = (
                subprocess.run(
                    ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                    input=payload if isinstance(payload, bytes) else payload.encode(),
                    capture_output=True,
                    check=True,
                    env=env,
                )
                .stdout.decode()
                .strip()
            )
            mode = "120000" if kind == "link" else "100644"
            lines.append(f"{mode} {blob}\t{path}\0")
        index = self.tmp / "plumbing-index"
        index.unlink(missing_ok=True)  # one fresh index per tree, so trees never accumulate entries
        env = {**env, "GIT_INDEX_FILE": str(index)}
        for argv, data in (
            (["update-index", "--add", "-z", "--index-info"], "".join(lines).encode()),
            (["write-tree"], b""),
        ):
            res = subprocess.run(
                ["git", "-C", str(self.repo), *argv], input=data, capture_output=True, check=True, env=env
            )
        tree = res.stdout.decode().strip()
        return git(self.repo, "commit-tree", tree, "-m", "plumbing").strip()

    def test_normalization_twins_are_refused_by_default_and_collide_when_checked(self):
        head = self._tree_with([(NFC + ".txt", "file", "a"), (NFD + ".txt", "file", "b")])
        self.assertIn("non-ASCII", self.refused(head))
        self.assertFalse(self.out.exists())
        with self.assertRaises(rx.Refused) as cm:
            rx._check_names([("100644", "a", NFC + ".txt"), ("100644", "b", NFD + ".txt")])
        self.assertIn("collide", str(cm.exception))

    def test_names_that_differ_only_by_case_collide(self):
        head = self._tree_with([("Readme.md", "file", "a"), ("README.MD", "file", "b")])
        self.assertIn("collide", self.refused(head))

    def test_path_under_a_link_by_case_is_refused(self):
        head = self._tree_with([("Dir", "link", "src"), ("dir/x.txt", "file", "x")])
        self.assertIn("lies under", self.refused(head))
        self.assertFalse(self.out.exists())

    def test_path_under_a_link_by_normalization_is_refused_by_the_name_check(self):
        with self.assertRaises(rx.Refused) as cm:
            rx._check_names([("120000", "a", NFC), ("100644", "b", NFD + "/x.txt")])
        self.assertIn("lies under", str(cm.exception))

    def test_link_target_through_a_link_by_normalization_is_refused(self):
        self.assertEqual(rx._link_problem("e", NFD + "/app.py", {rx._key(NFC)}), "passes through another symlink")

    def test_strip_matches_across_normalization(self):
        head = self._tree_with([(NFD + ".md", "file", "x"), ("keep.md", "file", "y")])
        rx.export(self.repo, head, self.out, strip=[NFC + ".md"])
        self.assertEqual(self.files(self.out), ["keep.md"])

    def test_symlinks_are_created_after_every_regular_file(self):
        calls = []
        real_link, real_chmod = os.symlink, os.fchmod

        def link(*a, **k):
            calls.append("link")
            return real_link(*a, **k)

        def chmod(*a, **k):
            calls.append("file")
            return real_chmod(*a, **k)

        os.symlink, os.fchmod = link, chmod
        self.addCleanup(setattr, os, "symlink", real_link)
        self.addCleanup(setattr, os, "fchmod", real_chmod)
        real_link("app.py", self.repo / "aaa_link")
        head = commit(self.repo, "link first alphabetically")
        rx.export(self.repo, head, self.out)
        self.assertIn("link", calls)
        self.assertNotIn("file", calls[calls.index("link") :])

    def test_modes_are_kept_from_the_tree(self):
        write(self.repo, "run.sh", "#!/bin/sh\n")
        os.chmod(self.repo / "run.sh", 0o755)
        head = commit(self.repo, "exec")
        rx.export(self.repo, head, self.out)
        self.assertTrue(os.access(self.out / "run.sh", os.X_OK))
        self.assertFalse(os.access(self.out / "README.md", os.X_OK))

    def test_odd_names_round_trip(self):
        name = NFC + "/" + NFC + ".txt"
        write(self.repo, name, "n\n")
        head = commit(self.repo, "names")
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, head, self.out)
        rx.export(self.repo, head, self.out, allow_nonascii=True)
        self.assertIn(name, self.files(self.out))


class Allowlist(Base):
    """Issue #138 design change: an allowlist of path shapes, an on-disk backstop and a diff built from the same
    decisions. Names git allows are put in the tree with plumbing (hash-object, update-index, write-tree)."""

    _tree_with = Hardening._tree_with

    def refuse(self, entries, **kw):
        head = self._tree_with([(n, "file", "x") for n in entries])
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, **kw)
        self.assertFalse(self.out.exists(), "a refusal leaves no output")
        return str(cm.exception)

    def test_zero_width_character_inside_an_agent_name_is_refused(self):
        name = "AGEN​TS.md"
        self.assertIn("non-ASCII", self.refuse([name]))
        self.assertIn("not a letter", self.refuse([name], allow_nonascii=True))

    def test_trailing_dot_and_trailing_space_are_refused(self):
        for name in ("AGENTS.md.", "AGENTS.md ", "CLAUDE.md.", "sub/GEMINI.md. "):
            with self.subTest(name=name):
                self.assertTrue(self.refuse([name]))

    def test_short_name_alias_is_refused(self):
        self.assertIn("~", self.refuse(["CLAUDE~1.MD"]))
        self.assertIn("~", self.refuse(["docs/AGENTS~2.md"]))

    def test_alternate_data_stream_name_is_refused(self):
        self.assertIn(":", self.refuse(["AGENTS.md:stream"]))
        self.assertIn(":", self.refuse(["CLAUDE.md::$DATA"]))

    def test_windows_device_names_are_refused(self):
        for name in ("CON", "nul.txt", "docs/COM1.md", "Lpt9", "aux"):
            with self.subTest(name=name):
                self.assertIn("device", self.refuse([name]))
        self.assertTrue(self.refuse(["ＣＯＮ"], allow_nonascii=True))  # fullwidth CON: script not allowed

    def test_cyrillic_lookalike_is_refused_by_default_and_when_mixed_with_latin(self):
        name = "АGENTS.md"  # Cyrillic A, then Latin
        self.assertIn("non-ASCII", self.refuse([name]))
        self.assertIn("outside the allowed", self.refuse([name], allow_nonascii=True))

    def test_fullwidth_agent_name_is_refused_and_with_opt_in_caught_on_disk(self):
        name = "ＡＧＥＮＴＳ.md"  # fullwidth AGENTS.md
        self.assertIn("non-ASCII", self.refuse([name]))
        self.assertIn("outside the allowed", self.refuse([name], allow_nonascii=True))

    def test_opt_in_keeps_nfc_and_refuses_nfd(self):
        self.assertIn("NFC", self.refuse([NFD + ".txt"], allow_nonascii=True))

    def test_leading_dot_names_need_the_allow_list(self):
        self.assertIn("dot-name", self.refuse([".env"]))
        self.assertIn("dot-name", self.refuse(["pkg/.hidden/x"]))
        head = self._tree_with([(".env", "file", "x"), ("a.txt", "file", "x")])
        rx.export(self.repo, head, self.out, dot_allow=(".env",))
        self.assertIn(".env", self.files(self.out))

    def test_size_and_shape_caps(self):
        self.assertIn("long", self.refuse(["a" * 101]))
        self.assertIn("long", self.refuse(["/".join(["d"] * 17) + "/f"]))
        self.assertIn("long", self.refuse(["/".join(["d" * 90] * 3)]))
        self.assertIn("allowlist", self.refuse(["sp ace.txt"]))
        self.assertIn("allowlist", self.refuse(["a=b.txt"]))

    def test_ordinary_names_pass(self):
        names = ["c++/main.cc", "@types/node/index.d.ts", "docs/a-b_c.1.md", ".github/workflows/ci.yml", ".gitignore"]
        head = self._tree_with([(n, "file", "x") for n in names])
        rx.export(self.repo, head, self.out)
        self.assertEqual(self.files(self.out), sorted(names))

    def test_a_plain_directory_named_like_a_stripped_dot_directory_is_kept(self):
        head = self._tree_with([("agents/a.txt", "file", "x"), ("claude/b.txt", "file", "x")])
        rx.export(self.repo, head, self.out)
        self.assertEqual(self.files(self.out), ["agents/a.txt", "claude/b.txt"])

    def test_backstop_rolls_back_a_disguise_the_allowlist_permits(self):
        # every character here is on the allowlist, so only the on-disk check can see the agent name
        for name in ("A-G-E-N-T-S.md", "agents_md", "docs/Claude-.md", "x/GEMINI-.md"):
            with self.subTest(name=name):
                msg = self.refuse([name, "keep.txt"])
                self.assertTrue("reduces to" in msg or "stripped" in msg, msg)

    def test_backstop_scans_a_descriptor_that_was_already_read(self):
        # on some file systems a directory descriptor that was listed once scans from its end; the export lists
        # the empty output directory first. Use a directory beside the tests (a real disk, not tmpfs) too.
        for base in (self.tmp, ROOT / "tests"):
            with self.subTest(base=str(base)), tempfile.TemporaryDirectory(dir=base) as d:
                fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    self.assertEqual(os.listdir(fd), [])
                    (Path(d) / "sub").mkdir()
                    (Path(d) / "sub" / "a.txt").write_text("x")
                    (Path(d) / "top.txt").write_text("x")
                    rx._backstop(fd, {"sub/a.txt", "top.txt"}, rx.DEFAULT_STRIP)
                    with self.assertRaises(rx.Refused):
                        rx._backstop(fd, {"top.txt"}, rx.DEFAULT_STRIP)  # sub/a.txt is seen, so it differs
                finally:
                    os.close(fd)

    def test_backstop_unit_cases(self):
        out = self.tmp / "b"
        (out / "agents").mkdir(parents=True)
        (out / "agents" / "a.txt").write_text("x")
        rx._backstop(out, {"agents/a.txt"}, rx.DEFAULT_STRIP)
        with self.assertRaises(rx.Refused):
            rx._backstop(out, {"agents/a.txt", "gone.txt"}, rx.DEFAULT_STRIP)  # file set differs from the plan
        (out / "Claude.").write_text("x")
        with self.assertRaises(rx.Refused):
            rx._backstop(out, {"agents/a.txt", "Claude."}, rx.DEFAULT_STRIP)  # reduces to a stripped dir name
        (out / "Claude.").unlink()
        (out / ".vscode").mkdir()
        (out / ".vscode" / "mcp.json").write_text("x")
        with self.assertRaises(rx.Refused):
            rx._backstop(out, {"agents/a.txt", ".vscode/mcp.json"}, rx.DEFAULT_STRIP)  # exact strip match on disk

    def _two_commits(self, base_entries, head_entries):
        return (
            self._tree_with([(n, "file", "b") for n in base_entries]),
            self._tree_with([(n, "file", "h") for n in head_entries]),
        )

    def test_nfd_named_agent_file_is_kept_out_of_the_diff(self):
        base, head = self._two_commits(["keep.txt"], ["keep.txt", NFD + ".md", "new.txt"])
        rx.export(self.repo, head, self.out, base=base, strip=[NFC + ".md"])
        diff = (self.out / "CHANGE.diff").read_bytes()
        self.assertIn(b"new.txt", diff)
        self.assertNotIn(NFD.encode(), diff)
        self.assertNotIn(NFC.encode(), diff)
        self.assertEqual(self.files(self.out), ["CHANGE.diff", "keep.txt", "new.txt"])

    def test_diff_excludes_every_stripped_path_literally(self):
        base, head = self._two_commits(["AGENTS.md", ".claude/a.md"], ["AGENTS.md", ".claude/a.md", "k.txt"])
        rx.export(self.repo, head, self.out, base=base)
        self.assertEqual(self.files(self.out), ["CHANGE.diff", "k.txt"])
        base2 = self._tree_with([("Agents.md", "file", "b"), ("src/CLAUDE.MD", "file", "b")])
        head2 = self._tree_with([("Agents.md", "file", "h"), ("src/CLAUDE.MD", "file", "h"), ("k.txt", "file", "h")])
        out2 = self.tmp / "out2"
        rx.export(self.repo, head2, out2, base=base2)
        text = (out2 / "CHANGE.diff").read_text().lower()
        self.assertNotIn("agents", text)
        self.assertNotIn("claude", text)

    def test_diff_check_refuses_a_path_that_is_not_kept(self):
        base, head = self._two_commits(["a.txt"], ["a.txt", "AGENTS.md"])
        with self.assertRaises(rx.Refused) as cm:
            rx._diff(self.repo, base, head, [], [], {"a.txt"}, 10**6)  # AGENTS.md is not excluded and not kept
        self.assertIn("not a kept path", str(cm.exception))

    def test_a_deleted_odd_name_in_the_base_is_refused_in_the_diff(self):
        base = self._tree_with([("bad name.txt", "file", "b"), ("a.txt", "file", "b")])
        head = self._tree_with([("a.txt", "file", "b")])
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, base=base)
        self.assertIn("not a kept path", str(cm.exception))
        self.assertFalse(self.out.exists())

    def test_cli_flags(self):
        head = self._tree_with([(NFC + ".txt", "file", "x")])
        argv = ["export", "--repo", str(self.repo), "--ref", head, "--out", str(self.out)]
        with redirect_stderr(io.StringIO()):
            self.assertEqual(rx.main(argv), 1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(rx.main([*argv, "--allow-nonascii-paths"]), 0)
        self.assertEqual(self.files(self.out), [NFC + ".txt"])


class Round2(Base):
    """Daybreak round 1 findings (issue #138): destination race, disguised non-ASCII names, aggregate caps, raw
    symlink text in messages; plus --exclude-tree."""

    _tree_with = Hardening._tree_with

    def setUp(self):
        super().setUp()
        self.victim = self.tmp / "victim.txt"
        self.victim.write_text("precious")
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def raced(self, plant):
        """Export into a pre-existing empty directory; `plant(dir_fd, leaf)` runs after each parent directory is
        opened and before the file is created, as an attacker with write access to the output directory would."""
        self.out.mkdir()
        real = rx._open_parent

        def hook(root_fd, parts, journal=None):
            pfd, fds = real(root_fd, parts, journal)
            for leaf in ("README.md", "app.py", "other.py"):
                plant(pfd, leaf)
            return pfd, fds

        rx._open_parent = hook
        self.addCleanup(setattr, rx, "_open_parent", real)
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, self.head, self.out)
        self.assertEqual(self.victim.read_text(), "precious", "nothing was written through the planted entry")
        # only the planted entries remain: rollback removes what this export created, never what it did not
        for leaf in os.listdir(self.out):
            self.assertEqual(os.stat(self.out / leaf).st_ino, self.victim.stat().st_ino)

    def test_final_component_symlink_swap_is_not_followed(self):
        def plant(pfd, leaf):
            try:
                os.symlink(self.victim, leaf, dir_fd=pfd)
            except FileExistsError:
                pass

        self.raced(plant)

    def test_final_component_hard_link_swap_is_not_truncated(self):
        def plant(pfd, leaf):
            try:
                os.link(self.victim, leaf, dst_dir_fd=pfd)
            except FileExistsError:
                pass

        self.raced(plant)

    def test_directory_component_swapped_for_a_symlink_is_not_followed(self):
        self.out.mkdir()
        real = os.mkdir

        def mkdir(path, mode=0o777, *, dir_fd=None):
            real(path, mode, dir_fd=dir_fd)
            if path == "src":
                os.rmdir(path, dir_fd=dir_fd)
                os.symlink(self.outside, path, dir_fd=dir_fd)

        os.mkdir = mkdir
        self.addCleanup(setattr, os, "mkdir", real)
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, self.head, self.out)
        self.assertEqual(os.listdir(self.outside), [])
        self.assertEqual(os.listdir(self.out), [])

    def test_output_directory_that_fills_after_the_check_is_refused_and_left_alone(self):
        real = rx._check_out

        def late(repo, out):
            path = real(repo, out)
            path.mkdir()
            (path / "mine.txt").write_text("keep me")
            return path

        rx._check_out = late
        self.addCleanup(setattr, rx, "_check_out", real)
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, self.head, self.out)
        self.assertIn("not empty", str(cm.exception))
        self.assertEqual(os.listdir(self.out), ["mine.txt"])

    def test_output_directory_swapped_for_a_symlink_is_refused_and_not_walked(self):
        (self.outside / "theirs.txt").write_text("theirs")
        real = rx._check_out

        def late(repo, out):
            path = real(repo, out)
            os.symlink(self.outside, path)
            return path

        rx._check_out = late
        self.addCleanup(setattr, rx, "_check_out", real)
        with self.assertRaises(rx.Refused):
            rx.export(self.repo, self.head, self.out)
        self.assertEqual(os.listdir(self.outside), ["theirs.txt"])

    def fail_after_swap(self, replace):
        """Make the on-disk check fail after the output path was renamed away and `replace()` put something else
        under its name, as an attacker who can rename entries in the parent directory would."""

        def swap_then_fail(fd, expected, strip):
            os.rename(self.out, str(self.out) + ".moved")
            replace()
            raise rx.Refused("simulated late failure")

        real = rx._backstop
        rx._backstop = swap_then_fail
        self.addCleanup(setattr, rx, "_backstop", real)
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, self.head, self.out)
        return str(cm.exception)

    def test_rollback_never_deletes_in_a_substituted_ordinary_directory(self):
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "precious.txt").write_text("precious")
        for pre in (True, False):
            with self.subTest(preexisting=pre):
                if self.out.exists():
                    shutil.rmtree(self.out)
                if pre:
                    self.out.mkdir()
                msg = self.fail_after_swap(lambda: os.rename(victim, self.out))
                self.assertEqual((self.out / "precious.txt").read_text(), "precious")
                self.assertEqual([p.name for p in self.out.iterdir()], ["precious.txt"])
                self.assertEqual(msg, "simulated late failure")
                os.rename(self.out, victim)
                shutil.rmtree(str(self.out) + ".moved")

    def test_rollback_never_walks_an_output_path_swapped_for_a_symlink(self):
        (self.outside / "theirs.txt").write_text("theirs")
        self.out.mkdir()
        self.fail_after_swap(lambda: os.symlink(self.outside, self.out))
        self.assertEqual(os.listdir(self.outside), ["theirs.txt"])

    def test_rollback_removes_what_was_created_through_the_pinned_directory(self):
        self.fail_after_swap(lambda: None)
        self.assertEqual(os.listdir(str(self.out) + ".moved"), [], "the pinned directory was emptied, not the path")

    def test_rollback_leaves_replaced_and_planted_entries_and_says_so(self):
        def tamper():
            moved = Path(str(self.out) + ".moved")
            (moved / "planted.txt").write_text("not ours")
            os.unlink(moved / "README.md")
            (moved / "README.md").write_text("a different file")

        msg = self.fail_after_swap(tamper)
        self.assertIn("left in place", msg)
        self.assertEqual(sorted(os.listdir(str(self.out) + ".moved")), ["README.md", "planted.txt"])

    def test_rollback_leaves_entries_under_a_substituted_subdirectory(self):
        def tamper():
            moved = Path(str(self.out) + ".moved")
            os.rename(moved / "src", moved / "src.orig")
            (moved / "src").mkdir()
            os.link(moved / "src.orig" / "app.py", moved / "src" / "app.py")  # same inode, wrong directory

        msg = self.fail_after_swap(tamper)
        self.assertIn("left in place", msg)
        self.assertTrue((Path(str(self.out) + ".moved") / "src" / "app.py").exists())

    def test_a_final_path_swap_refuses_a_completed_export(self):
        def swap(fd, expected, strip):
            os.rename(self.out, str(self.out) + ".moved")
            self.out.mkdir()

        real = rx._backstop
        rx._backstop = swap
        self.addCleanup(setattr, rx, "_backstop", real)
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, self.head, self.out)
        self.assertIn("no longer names", str(cm.exception))
        self.assertEqual(os.listdir(self.out), [])

    def refuse_opt_in(self, name):
        head = self._tree_with([(name, "file", "x"), ("keep.txt", "file", "x")])
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, allow_nonascii=True)
        self.assertFalse(self.out.exists())
        return str(cm.exception)

    def test_opt_in_refuses_replacement_disguises(self):
        for name, why in (
            ("GEMıNı.md", "not an accented ASCII letter"),  # dotless i, Latin script
            ("ꓮGENTS.md", "outside the allowed"),  # Lisu letter A
            ("ÀGENTS.md", "disguised"),  # accented A: the skeleton is agents.md
            ("À-GENTS.md", "disguised"),  # skeleton plus punctuation reduces to agentsmd
        ):
            with self.subTest(name=name):
                self.assertIn(why, self.refuse_opt_in(name))

    def test_opt_in_keeps_an_ordinary_accented_name(self):
        head = self._tree_with([(NFC + ".txt", "file", "x")])
        rx.export(self.repo, head, self.out, allow_nonascii=True)
        self.assertEqual(self.files(self.out), [NFC + ".txt"])

    def test_skeleton_maps_cyrillic_and_greek_lookalikes(self):
        self.assertEqual(rx._fold("аgents.md"), "agentsmd")  # Cyrillic a
        self.assertEqual(rx._fold("clαude.md"), "claudemd")  # Greek alpha
        self.assertTrue(rx._reduced_hit(["ÀGENTS.md"], rx.DEFAULT_STRIP))

    def test_script_allowlist_refuses_single_script_lookalikes(self):
        for label, name in (
            ("lisu", "ꓮꓖꓰꓠꓔꓢ.ꓟꓓ"),
            ("cherokee", "ᎪᏀᎬᏁᎢᏚ.ᎷᎠ"),
            ("greek", "ΑΓΕΝΤΣ.ΜΔ"),
            ("cyrillic", "АГЕНТС.МД"),
            ("cyrillic text", "документ"),
            ("fullwidth", "ＡＧＥＮＴＳ.ＭＤ"),
            ("math bold", "𝐀𝐆𝐄𝐍𝐓𝐒.𝐦𝐝"),
            ("circled", "ⒶⒼⒺⓃⓉⓈ"),
            ("coptic", "ⲀⲄⲈ"),
            ("armenian", "ԱԳԵՆՏՍ"),
            ("halfwidth katakana", "ｱｲｳ"),
        ):
            with self.subTest(label=label):
                self.assertRegex(self.refuse_opt_in(name), "outside the allowed|not a letter")

    def test_script_allowlist_keeps_latin_japanese_and_korean(self):
        names = [
            "café.txt",
            "日本語/ひらがな/カタカナ",
            "日本のカタカナ.md",
            "ラーメン.txt",
            "한국어/문서.txt",
            "naïve/ÅÉ.md",
        ]
        head = self._tree_with([(n, "file", "x") for n in names])
        rx.export(self.repo, head, self.out, allow_nonascii=True)
        self.assertEqual(self.files(self.out), sorted(names))

    def test_script_mixing_is_refused_except_japanese(self):
        for label, name in (
            ("han and latin", "日本abc"),
            ("hangul and han", "한국語"),
            ("hangul and kana", "한국あ"),
            ("latin and kana", "caféあ"),
        ):
            with self.subTest(label=label):
                self.assertIn("mixes", self.refuse_opt_in(name))

    def test_file_count_and_total_byte_caps_refuse_before_writing(self):
        head = self._tree_with([(f"f{i}.txt", "file", "y" * 100) for i in range(10)])
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, max_files=5)
        self.assertIn("file-count", str(cm.exception))
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, max_total_bytes=500)
        self.assertIn("in total", str(cm.exception))
        self.assertFalse(self.out.exists())
        rx.export(self.repo, head, self.out, max_files=10, max_total_bytes=1000)

    def test_total_cap_counts_the_change_diff(self):
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, self.head, self.out, base=self.base, max_total_bytes=60)
        self.assertIn("in total", str(cm.exception))

    def test_blobs_are_not_held_together(self):
        head = self._tree_with([(f"f{i}.txt", "file", "y" * 50) for i in range(6)])
        held, real = [], rx._write_entry

        def watch(root_fd, name, data, mode, journal=None):
            held.append(len(data))
            return real(root_fd, name, data, mode, journal)

        rx._write_entry = watch
        self.addCleanup(setattr, rx, "_write_entry", real)
        keep_alive = []
        real_git = rx._git

        def git_spy(repo, *args, **k):
            out = real_git(repo, *args, **k)
            if args[:2] == ("cat-file", "blob"):
                keep_alive.append(len(out))
            return out

        rx._git = git_spy
        self.addCleanup(setattr, rx, "_git", real_git)
        rx.export(self.repo, head, self.out)
        self.assertEqual(len(held), 6)
        self.assertNotIn("blobs = {}", inspect.getsource(rx.export))

    def test_a_big_diff_is_stopped_while_streaming(self):
        write(self.repo, "big.txt", "\n" * 99_000)  # 99 KB blob, a 198 KB diff: more than the pipe buffer past the cap
        head = commit(self.repo, "big")
        procs, real = [], subprocess.Popen

        def spy(*a, **k):
            p = real(*a, **k)
            procs.append(p)
            return p

        subprocess.Popen = spy
        self.addCleanup(setattr, subprocess, "Popen", real)
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out, base=self.base, max_file_bytes=100_000)
        self.assertIn("size cap", str(cm.exception))
        self.assertEqual(procs[-1].returncode, -9, "git was killed at the cap, not read to the end")

    def test_symlink_target_is_escaped_in_the_refusal(self):
        target = b"../../outside\n\x1b[2Jforged diagnostic \xff\xfe"
        head = self._tree_with([("ln", "link", target)])
        err = io.StringIO()
        with redirect_stderr(err):
            rc = rx.main(["export", "--repo", str(self.repo), "--ref", head, "--out", str(self.out)])
        text = err.getvalue()
        self.assertEqual(rc, 1)
        self.assertEqual(text.count("\n"), 1, "one line, no injected newline")
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b", text)
        self.assertTrue(text.isascii())

    def test_hostile_path_is_escaped_in_the_refusal(self):
        head = self._tree_with([("a\x1b[2Jb\nc.txt", "file", "x")])
        with self.assertRaises(rx.Refused) as cm:
            rx.export(self.repo, head, self.out)
        self.assertNotIn("\x1b", str(cm.exception))
        self.assertNotIn("\n", str(cm.exception))

    def test_exclude_tree_drops_a_subtree_from_export_and_diff_unchecked(self):
        base = self._tree_with([("keep.txt", "file", "b")])
        head = self._tree_with(
            [
                ("keep.txt", "file", "h"),
                (".githooks/pre commit", "file", "h"),  # would fail the allowlist
                ("vendor/sp ace/x.txt", "file", "h"),
                ("vendor2/ok.txt", "file", "h"),
            ]
        )
        rx.export(self.repo, head, self.out, base=base, exclude_trees=[".githooks", "vendor/"])
        self.assertEqual(self.files(self.out), ["CHANGE.diff", "keep.txt", "vendor2/ok.txt"])
        diff = (self.out / "CHANGE.diff").read_text()
        self.assertNotIn("githooks", diff)
        self.assertNotIn("sp ace", diff)
        self.assertIn("vendor2/ok.txt", diff)

    def test_exclude_tree_is_validated(self):
        for bad in ("", "/abs", "../x", "a//b", "a/./b"):
            with self.subTest(bad=bad), self.assertRaises(rx.Refused):
                rx.export(self.repo, self.head, self.out, exclude_trees=[bad])

    def test_cli_exclude_tree_and_allow_dot_examples(self):
        head = self._tree_with(
            [(".githooks/pre-commit", "file", "x"), (".gitleaksignore", "file", "x"), ("vendor/a b", "file", "x")]
        )
        argv = ["export", "--repo", str(self.repo), "--ref", head, "--out", str(self.out)]
        with redirect_stderr(io.StringIO()):
            self.assertEqual(rx.main(argv), 1)
        with redirect_stdout(io.StringIO()):
            rc = rx.main(
                [*argv, "--allow-dot", ".githooks", "--allow-dot", ".gitleaksignore", "--exclude-tree", "vendor"]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(self.files(self.out), [".githooks/pre-commit", ".gitleaksignore"])


class NextPrompt(unittest.TestCase):
    BASE = "Review src/app.py at <head> against <base>.\n\n  Keep  my   spacing.\n"
    FIND = "F1 High: input x breaks y\nF2 Low: typo\n"

    def test_base_prompt_is_kept_byte_for_byte_then_header_then_findings(self):
        out = rx.next_prompt(self.BASE, self.FIND)
        self.assertTrue(out.startswith(self.BASE))
        self.assertTrue(out.endswith(self.FIND))
        self.assertIn(rx.PROMPT_HEADER, out)
        self.assertEqual(out, self.BASE + "\n" + rx.PROMPT_HEADER + "\n" + self.FIND)

    def test_surrounding_whitespace_in_the_base_prompt_is_kept(self):
        base = "\n  indented first line\n\n\n"
        out = rx.next_prompt(base, self.FIND)
        self.assertTrue(out.startswith(base))

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
