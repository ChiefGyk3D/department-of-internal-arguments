"""scripts/mutation_check.py, round 6: a delete that leaves files behind is never reported as a deletion. open_up
walks iteratively (no recursion limit), falls back to the parent descriptor when /proc is unavailable, and
remove_tree verifies the root is gone; main turns a leftover into an inconclusive run with kept true. Each test goes
red without its guard."""

import errno
import shutil
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import Base, host_has_pid_namespace  # noqa: E402

real_chmod = os.chmod


def _no_proc_chmod(path, *a, **k):
    if isinstance(path, str) and path.startswith("/proc/self/fd/"):
        raise OSError(errno.ENOENT, "no procfs")
    return real_chmod(path, *a, **k)


def _no_chmod_at_all(path, *a, **k):
    if isinstance(path, str) and path.startswith("/proc/self/fd/"):
        raise OSError(errno.ENOENT, "no procfs")
    if k.get("dir_fd") is not None:
        raise OSError(errno.ENOTSUP, "no fchmodat")
    return real_chmod(path, *a, **k)


class Round6(Base):
    def _scratch(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        return scratch

    def _locked(self, scratch):
        work = scratch / "work"
        (work / "repo" / "locked").mkdir(parents=True)
        (work / "repo" / "locked" / "payload").write_text("x")
        (work / "repo" / "locked").chmod(0)
        return work

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), key=lambda q: -len(str(q))):
            try:
                if p.is_dir() and not p.is_symlink():
                    real_chmod(p, 0o700)
            except OSError:
                pass
        super().tearDown()

    def test_normal_tree_is_removed_and_no_problems(self):
        scratch = self._scratch()
        work = scratch / "work"
        (work / "a" / "b").mkdir(parents=True)
        (work / "a" / "b" / "f").write_text("x")
        mc.remove_tree(str(work), str(scratch))
        self.assertFalse(work.exists())

    def test_without_procfs_the_parent_descriptor_still_opens_a_mode_zero_directory(self):
        scratch = self._scratch()
        work = self._locked(scratch)
        with mock.patch.object(os, "chmod", _no_proc_chmod):
            mc.remove_tree(str(work), str(scratch))
        self.assertFalse(work.exists())

    def test_leftover_is_raised_when_no_chmod_route_works(self):
        scratch = self._scratch()
        work = self._locked(scratch)
        with mock.patch.object(os, "chmod", _no_chmod_at_all):
            with self.assertRaises(mc.CleanupIncomplete) as cm:
                mc.remove_tree(str(work), str(scratch))
        self.assertEqual(cm.exception.path, str(work))
        self.assertTrue(work.exists())

    def test_tree_deeper_than_the_recursion_limit_is_removed(self):
        scratch = self._scratch()
        work = scratch / "work"
        work.mkdir()
        depth = sys.getrecursionlimit() + 200
        fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for _ in range(depth):
                os.mkdir("d", dir_fd=fd)
                nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = nxt
        finally:
            os.close(fd)
        mc.remove_tree(str(work), str(scratch))
        self.assertFalse(work.exists())

    def test_open_up_opens_a_deep_mode_zero_directory_with_few_descriptors(self):
        scratch = self._scratch()
        work = scratch / "work"
        work.mkdir()
        depth = sys.getrecursionlimit() + 200
        fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        for _ in range(depth):
            os.mkdir("d", dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
        os.mkdir("locked", dir_fd=fd)
        real_chmod("locked", 0, dir_fd=fd)
        os.close(fd)
        opened = []
        real_open = os.open

        def counting(*a, **k):
            r = real_open(*a, **k)
            opened.append(r)
            return r

        with mock.patch.object(os, "open", counting):
            problems = mc.open_up(str(work))
        self.assertEqual(problems, [])
        self.assertLess(max(opened), 30, "descriptors must not grow with depth")
        mc.remove_tree(str(work), str(scratch))
        self.assertFalse(work.exists())

    def test_descriptor_exhaustion_is_reported_not_silent(self):
        scratch = self._scratch()
        work = self._locked(scratch)
        real_open = os.open

        def exhausted(path, flags, *a, **k):
            if path == "locked" and flags & os.O_PATH:
                raise OSError(errno.EMFILE, "too many open files")
            return real_open(path, flags, *a, **k)

        with mock.patch.object(os, "open", exhausted):
            problems = mc.open_up(str(work))
            self.assertTrue(problems and "locked" in problems[0])
            with self.assertRaises(mc.CleanupIncomplete):
                mc.remove_tree(str(work), str(scratch))
        self.assertTrue(work.exists())

    def _main(self, locked):
        import contextlib
        import io

        repo, _, c2 = self.make_repo()
        argv = ["--repo", str(repo), f"--head={c2}", f"--revert={c2}", "--test", "python3 tests/check.py"]
        argv += ["--private-parent", str(self.tmp / "priv"), "--scratch", str(self.scratch), "--settle", "0.3"]
        if not host_has_pid_namespace():
            argv.append("--allow-degraded-isolation")
        real_remove = mc.remove_tree

        def remove(path, scratch):
            if locked and os.path.basename(path).startswith("doia-mc") and "priv" not in path:
                d = Path(path) / "locked"
                d.mkdir()
                (d / "payload").write_text("x")
                d.chmod(0)
                with mock.patch.object(os, "chmod", _no_chmod_at_all):
                    return real_remove(path, scratch)
            return real_remove(path, scratch)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(mc, "remove_tree", remove), contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(err):
                code = mc.main(argv)
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_main_leftover_is_inconclusive_kept_true_with_path(self):
        code, r, err = self._main(locked=True)
        self.addCleanup(shutil.rmtree, r.get("work_dir", "/nonexistent"), True)
        self.assertEqual(code, mc.EXIT_INCONCLUSIVE)
        self.assertEqual(r["exit_code"], mc.EXIT_INCONCLUSIVE)
        self.assertEqual(r["verdict"], "inconclusive")
        self.assertIs(r["kept"], True)
        self.assertTrue(r["reason"].startswith("cleanup incomplete"))
        self.assertEqual(r["leftover"], r["work_dir"])
        self.assertTrue(os.path.exists(r["leftover"]))
        self.assertIn("cleanup incomplete", err)

    def test_main_normal_bites_removes_the_copy_and_exits_zero(self):
        code, r, _ = self._main(locked=False)
        self.assertEqual((code, r["verdict"], r["kept"]), (0, "bites", False))
        self.assertFalse(os.path.exists(r["work_dir"]))
        self.assertNotIn("leftover", r)


if __name__ == "__main__":
    unittest.main()
