"""scripts/mutation_check.py, round 7: remove_tree pins the root once and deletes through descriptors, so a work
directory swapped for a sibling after the pin never costs the sibling its files; and a failed cleanup of the private
directory is reported the same way as a failed cleanup of the work directory. Each test goes red without its fix."""

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import Base, host_has_pid_namespace  # noqa: E402


class Round7Pinned(Base):
    def _scratch(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        return scratch

    def _tree(self, scratch):
        work = scratch / "work"
        (work / "a" / "b").mkdir(parents=True)
        (work / "a" / "b" / "mine").write_text("mine")
        (work / "top").write_text("mine")
        victim = scratch / "victim"
        (victim / "sub").mkdir(parents=True)
        (victim / "sub" / "precious").write_text("precious")
        (victim / "keep").write_text("precious")
        return work, victim

    def _swap_after_first_pin(self, scratch):
        """Wrap _enter: right after the root is pinned, rename work -> saved and victim -> work (no real race)."""
        real_enter = mc._enter
        calls = []

        def enter(*a, **k):
            r = real_enter(*a, **k)
            calls.append(1)
            if len(calls) == 1:
                os.rename(scratch / "work", scratch / "saved")
                os.rename(scratch / "victim", scratch / "work")
            return r

        return mock.patch.object(mc, "_enter", enter)

    def test_swapped_in_sibling_is_never_deleted_and_the_result_is_incomplete(self):
        scratch = self._scratch()
        work, _ = self._tree(scratch)
        with self._swap_after_first_pin(scratch):
            with self.assertRaises(mc.CleanupIncomplete):
                mc.remove_tree(str(work), str(scratch))
        self.assertEqual((scratch / "work" / "sub" / "precious").read_text(), "precious")
        self.assertEqual((scratch / "work" / "keep").read_text(), "precious")

    def test_the_pinned_original_is_the_thing_that_gets_deleted(self):
        scratch = self._scratch()
        work, _ = self._tree(scratch)
        with self._swap_after_first_pin(scratch):
            with self.assertRaises(mc.CleanupIncomplete) as cm:
                mc.remove_tree(str(work), str(scratch))
        self.assertFalse((scratch / "saved").exists(), "the pinned original is removed wherever it was moved to")
        self.assertTrue(any("different" in p for p in cm.exception.problems), cm.exception.problems)

    def test_plain_delete_still_succeeds_without_shutil_rmtree(self):
        scratch = self._scratch()
        work, _ = self._tree(scratch)
        (work / "link").symlink_to(scratch / "victim")
        with mock.patch.object(shutil, "rmtree", side_effect=AssertionError("rmtree must not be used")):
            mc.remove_tree(str(work), str(scratch))
        self.assertFalse(work.exists())
        self.assertEqual((scratch / "victim" / "keep").read_text(), "precious")

    def test_a_vanished_root_is_success(self):
        scratch = self._scratch()
        mc.remove_tree(str(scratch / "gone"), str(scratch))


class Round7Private(Base):
    def _run(self, remove):
        repo, _, c2 = self.make_repo()
        argv = ["--repo", str(repo), f"--head={c2}", f"--revert={c2}", "--test", "python3 tests/check.py"]
        scratch = self.tmp / "scratch"
        argv += ["--private-parent", str(self.tmp / "priv"), "--scratch", str(scratch), "--settle", "0.3"]
        if not host_has_pid_namespace():
            argv.append("--allow-degraded-isolation")
        real_remove = mc.remove_tree
        seen = []

        def wrapper(path, parent):
            seen.append(path)
            return remove(len(seen), path, parent, real_remove)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(mc, "remove_tree", wrapper), contextlib.redirect_stdout(out):
            with contextlib.redirect_stderr(err):
                code = mc.main(argv)
        report = json.loads(out.getvalue())
        for p in seen:
            self.addCleanup(shutil.rmtree, p, True)
        return code, report, err.getvalue(), seen

    def test_private_cleanup_failure_alone_is_kept_with_leftover_and_stderr(self):
        def remove(n, path, parent, real):
            if n == 1:
                raise mc.CleanupIncomplete(path, ["boom"])
            return real(path, parent)

        code, r, err, seen = self._run(remove)
        self.assertEqual(code, mc.EXIT_INCONCLUSIVE)
        self.assertIs(r["kept"], True)
        self.assertEqual(r["leftover"], seen[0])
        self.assertIn("CleanupIncomplete", r["cleanup_error"])
        self.assertEqual(r["private_leftover"], seen[0])
        self.assertIn("cleanup_error_private", r)
        self.assertIn("cleanup incomplete", err)
        self.assertTrue(r["reason"].startswith("cleanup incomplete"))

    def test_work_cleanup_failure_alone_keeps_private_keys_absent(self):
        def remove(n, path, parent, real):
            if n == 2:
                raise mc.CleanupIncomplete(path, ["boom"])
            return real(path, parent)

        code, r, err, seen = self._run(remove)
        self.assertEqual(code, mc.EXIT_INCONCLUSIVE)
        self.assertIs(r["kept"], True)
        self.assertEqual(r["leftover"], seen[1])
        self.assertNotIn("private_leftover", r)
        self.assertNotIn("cleanup_error_private", r)
        self.assertIn("cleanup incomplete", err)


if __name__ == "__main__":
    unittest.main()
