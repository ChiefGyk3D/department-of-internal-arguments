"""scripts/mutation_check.py, round 5: the source gitdir digest covers metadata (mode, owner, identity of special
files, link targets, link counts, times), an unreadable file in the snapshot is inconclusive, and open_up works
through descriptors so a symlink swapped in mid-walk cannot redirect a chmod. Each test goes red without its guard."""

import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import TEST_SRC, Base, unchanged  # noqa: E402

H = "@SRC@/.git/hooks/"


def _hook(name, mode=0o644, data="x"):
    def setup(repo):
        p = repo / ".git" / "hooks" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text(data)
        p.chmod(mode)

    return setup


def _fifo(repo):
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    os.mkfifo(repo / ".git" / "hooks" / "f")


def _sock(repo):
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    cwd = os.getcwd()
    try:
        os.chdir(repo / ".git" / "hooks")  # a relative name keeps the path under the AF_UNIX length limit
        s.bind("s")
    finally:
        os.chdir(cwd)
        s.close()


def _link(repo):
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    os.symlink("target-a", repo / ".git" / "hooks" / "l")


MUTATIONS = {
    "chmod-only": (
        _hook("pre-commit"),
        f"os.chmod('{H}pre-commit', 0o700)\n",
    ),
    "fifo-replaced": (_fifo, f"os.unlink('{H}f')\nos.mkfifo('{H}f')\n"),
    "socket-replaced": (
        _sock,
        f"import socket\nos.unlink('{H}s')\nos.chdir('{H}')\nsocket.socket(socket.AF_UNIX).bind('s')\n",
    ),
    "symlink-retargeted": (_link, f"os.unlink('{H}l')\nos.symlink('target-b', '{H}l')\n"),
    "hardlink-count": (_hook("x"), f"os.link('{H}x', '@TMP@/other-link')\n"),
}


class GitdirMetadata(Base):
    def test_a_metadata_only_change_in_the_source_gitdir_is_detected(self):
        for name, (setup, body) in MUTATIONS.items():
            repo, _, c2 = self.make_repo(name=f"src-{name}", check_src=TEST_SRC + body, setup=setup)
            code, r = self.run_tool(repo, c2, c2)
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), (name, r["reason"]))
            self.assertIs(unchanged(r), False, name)

    def test_an_untouched_source_still_bites_with_the_same_digest_twice(self):
        for name, (setup, _) in MUTATIONS.items():
            repo, _, c2 = self.make_repo(name=f"quiet-{name}", setup=setup)
            code, r = self.run_tool(repo, c2, c2)
            self.assertEqual((code, r["verdict"]), (0, "bites"), (name, r["reason"]))
            state = r["source_repo_state"]
            self.assertEqual(state["before"]["gitdir_sha256"], state["after"]["gitdir_sha256"], name)


class UnreadableFile(Base):
    SEQUENCE = f"p = '{H}opaque'\nos.chmod(p, 0o600)\nopen(p, 'w').write('changed')\nos.chmod(p, 0)\n"

    def test_an_unreadable_file_makes_the_snapshot_inconclusive(self):
        repo, _, c2 = self.make_repo(check_src=TEST_SRC + self.SEQUENCE, setup=_hook("opaque", 0))
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        self.assertIn("unreadable", r["reason"])

    def test_hash_file_raises_on_a_read_error_and_counts_bytes(self):
        d = self.tmp / "hf"
        d.mkdir()
        f = d / "opaque"
        f.write_text("secret")
        f.chmod(0)
        with self.assertRaises(mc.Inconclusive):
            mc.hash_file(str(f), mc.Budget(1 << 20))
        f.chmod(0o600)
        budget = mc.Budget(1 << 20)
        mc.hash_file(str(f), budget)
        self.assertEqual(budget.left, (1 << 20) - 6)

    def test_the_digest_of_an_unreadable_file_changes_with_its_ctime(self):
        d = self.tmp / "gd"
        d.mkdir()
        f = d / "opaque"
        f.write_text("a")
        before = mc.entry_meta(os.lstat(f))
        f.chmod(0o600)
        f.write_text("b")
        f.chmod(0o644)
        self.assertNotEqual(before, mc.entry_meta(os.lstat(f)))


class OpenUpDescriptors(Base):
    def test_a_directory_swapped_for_a_symlink_mid_walk_does_not_redirect_the_chmod(self):
        top = self.tmp / "top"
        outside = self.tmp / "outside"
        (outside / "inner").mkdir(parents=True)
        outside.chmod(0o755)
        (outside / "inner").chmod(0o755)
        (top / "victim" / "deep").mkdir(parents=True)
        done = []

        def swap():
            if done:
                return
            done.append(1)
            os.rename(top / "victim", self.tmp / "moved")
            os.symlink(outside, top / "victim")

        real_open, real_chmod = os.open, os.chmod

        def fake_open(path, *a, **k):
            if os.fsdecode(path).rsplit("/", 1)[-1] == "victim":
                swap()
            return real_open(path, *a, **k)

        def fake_chmod(path, *a, **k):
            if isinstance(path, (str, bytes, os.PathLike)) and os.fsdecode(path).rsplit("/", 1)[-1] == "victim":
                swap()
            return real_chmod(path, *a, **k)

        with mock.patch.object(os, "open", fake_open), mock.patch.object(os, "chmod", fake_chmod):
            mc.open_up(str(top))
        self.assertTrue(done, "the simulated swap never ran, so the test proves nothing")
        self.assertEqual(oct(outside.stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((outside / "inner").stat().st_mode & 0o777), "0o755")

    def test_open_up_still_opens_nested_mode_zero_directories(self):
        top = self.tmp / "t2"
        (top / "a" / "b").mkdir(parents=True)
        (top / "a" / "b").chmod(0)
        (top / "a").chmod(0)
        mc.open_up(str(top))
        self.assertEqual(oct((top / "a").stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct((top / "a" / "b").stat().st_mode & 0o777), "0o700")

    def test_open_up_does_not_follow_a_symlink_given_as_the_path(self):
        outside = self.tmp / "o2"
        outside.mkdir()
        outside.chmod(0o755)
        link = self.tmp / "lnk"
        os.symlink(outside, link)
        mc.open_up(str(link))
        self.assertEqual(oct(outside.stat().st_mode & 0o777), "0o755")


if __name__ == "__main__":
    unittest.main()
