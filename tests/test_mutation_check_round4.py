"""scripts/mutation_check.py, round 4: the source repository's whole gitdir is part of the before/after check, a
disposable directory with an unreadable subdirectory is still removed, and the private directory of a run that
found damage is cleaned up. Each test is meant to go red when its guard is removed."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import TEST_SRC, Base, unchanged  # noqa: E402
from test_mutation_check_round3 import ATTACK_BODIES, PrivateRepository  # noqa: E402

# The test writes into the source repository's gitdir where `git status` never looks.
GITDIR_WRITES = {
    "hook": "os.makedirs('@SRC@/.git/hooks', exist_ok=True)\nopen('@SRC@/.git/hooks/post-checkout', 'w').write('x')\n",
    "reflog": "open('@SRC@/.git/logs/HEAD', 'a').write('forged\\n')\n",
    "object": "os.makedirs('@SRC@/.git/objects/zz', exist_ok=True)\nopen('@SRC@/.git/objects/zz/yy', 'w').write('x')\n",
    "emptydir": "os.makedirs('@SRC@/.git/planted-dir', exist_ok=True)\n",
}


class GitdirState(Base):
    def test_a_write_anywhere_in_the_source_gitdir_is_detected(self):
        for name, body in GITDIR_WRITES.items():
            repo, _, c2 = self.make_repo(name=f"src-{name}", check_src=TEST_SRC + body)
            code, r = self.run_tool(repo, c2, c2)
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), (name, r["reason"]))
            self.assertIs(unchanged(r), False, name)

    def test_an_untouched_gitdir_still_bites(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertIn("gitdir_sha256", r["source_repo_state"]["before"])


class RemoveTree(Base):
    def test_a_subdirectory_with_mode_zero_is_removed(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        d = scratch / "doia-mc-x"
        (d / "sub" / "deeper").mkdir(parents=True)
        (d / "sub" / "deeper" / "f").write_text("x")
        (d / "sub").chmod(0)
        mc.remove_tree(str(d), str(scratch))
        self.assertFalse(d.exists())

    def test_a_path_outside_the_scratch_directory_is_still_refused(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        other = self.tmp / "other"
        other.mkdir()
        with self.assertRaises(RuntimeError):
            mc.remove_tree(str(other), str(scratch))
        self.assertTrue(other.exists())


class PrivateCleanup(Base):
    attack = PrivateRepository.attack

    def test_a_private_directory_the_test_made_unreadable_is_removed_anyway(self):
        priv = self.tmp / "priv"
        for name in ATTACK_BODIES:
            code, r = self.attack(name, extra=["--hide-private", "off"])
            self.assertEqual(code, 3, (name, r["reason"]))
            self.assertNotIn("cleanup_error_private", r, name)
            self.assertEqual([p.name for p in priv.iterdir() if p.is_dir()], [], name)


if __name__ == "__main__":
    unittest.main()
