"""scripts/mutation_check.py, trusted steps: after the test has run the tool reads nothing the test could have
written. These tests plant hostile files where the test can reach and check the verdict and evidence do not move.
Each has a mutation that turns it red (see the report of round 2)."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import TEST_SRC, Base  # noqa: E402

# Run 1 plants configuration where the test's own HOME and XDG_CONFIG_HOME point; run 3 leaves an untracked file
# that the planted ignore file would hide if a trusted step read it. Run 2 fails before reaching the last branch.
HIDE_SRC = (
    TEST_SRC
    + r"""m = os.path.join(os.path.dirname(os.getcwd()), 'marker')
beacon = 'python3 @TMP@/beacon.py'
if not os.path.exists(m):
    open(m, 'w').close()
    xdg = os.environ['XDG_CONFIG_HOME'] + '/git'
    os.makedirs(xdg, exist_ok=True)
    open(xdg + '/ignore', 'w').write('*\n')
    open(xdg + '/attributes', 'w').write('calc.py filter=x\nREADME filter=x\n')
    cfg = '[filter "x"]\n\tclean = ' + beacon + '\n\tsmudge = ' + beacon + '\n'
    cfg += '[core]\n\tattributesFile = ' + xdg + '/attributes\n\texcludesFile = ' + xdg + '/ignore\n'
    open(xdg + '/config', 'w').write(cfg)
    open(os.environ['HOME'] + '/.gitconfig', 'w').write(cfg)
    open(os.environ['HOME'] + '/.gitattributes', 'w').write('calc.py filter=x\n')
    with open('.git/config', 'a') as c:
        c.write(cfg)
else:
    open('junk.txt', 'w').write('left behind and hidden by the planted ignore file')
"""
)
# Overwrite the loose object of the fixed calc.py, in the clone AND in the source repository, with the reverted text;
# plant a replace ref and a grafts file too.
TAMPER_SRC = (
    TEST_SRC
    + r"""import hashlib, zlib
good = b'def f():\n    return 2\n'
bad = b'def f():\n    return 1\n'
sha = hashlib.sha1(b'blob %d\0' % len(good) + good).hexdigest()
for base in ('.git', '@SRC@/.git'):
    obj = base + '/objects/' + sha[:2] + '/' + sha[2:]
    if os.path.exists(obj):
        os.chmod(obj, 0o644)
        open(obj, 'wb').write(zlib.compress(b'blob %d\0' % len(bad) + bad))
os.makedirs('.git/refs/replace', exist_ok=True)
open('.git/refs/replace/' + sha, 'w').write(hashlib.sha1(b'blob 1\0x').hexdigest() + '\n')
open('.git/info/grafts', 'w').write('0' * 40 + '\n')
"""
)
# Plant the predictable names an earlier version used for the trusted git directories and index files.
PLANT_SRC = (
    TEST_SRC
    + r"""w = os.path.dirname(os.getcwd())
os.makedirs(w + '/evil/hooks', exist_ok=True)
open(w + '/evil/config', 'w').write('[core]\n\tbare = true\n')
for n in range(1, 60):
    for name in ('trusted-%d.git' % n, 'trusted-%d' % n):
        if not os.path.lexists(w + '/' + name):
            os.symlink(w + '/evil', w + '/' + name)
    open(w + '/trusted-%d.index' % n, 'w').write('junk')
"""
)


class TrustedBoundary(Base):
    def beacon(self):
        (self.tmp / "beacon.py").write_text(
            f"import pathlib\npathlib.Path('{self.tmp / 'BEACON_RAN'}').write_text('ran')\n"
        )

    def test_files_the_test_plants_in_its_xdg_and_home_do_not_reach_the_trusted_steps(self):
        self.beacon()
        repo, _, c2 = self.make_repo(check_src=HIDE_SRC)
        code, r = self.run_tool(repo, c2, c2)
        self.assertFalse((self.tmp / "BEACON_RAN").exists(), "a planted filter ran in a trusted step")
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        self.assertIn("junk.txt", r["tree_clean"]["final"]["status_porcelain"], "the planted ignore file hid it")

    def test_trusted_steps_have_their_own_environment_outside_the_work_dir(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--keep"])
        self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)
        self.assertEqual(code, 0, r["reason"])
        t = r["trusted"]
        self.assertEqual(
            sorted(v for v in t["env_keys"] if v.startswith("GIT_")),
            ["GIT_ATTR_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM"],
        )
        for key in ("HOME", "XDG_CONFIG_HOME", "PATH", "LANG", "TMPDIR"):
            self.assertIn(key, t["env_keys"])
        private = os.path.realpath(t["private_dir"])
        self.assertFalse(mc.under(private, os.path.realpath(r["work_dir"])))
        self.assertFalse(mc.under(os.path.realpath(r["work_dir"]), private))
        self.assertFalse(os.path.exists(private), "the private directory is removed at the end")
        self.assertNotIn(os.path.basename(private), json.dumps(r["environment"]))

    def test_attributes_and_excludes_files_named_by_config_are_overridden(self):
        """The two overrides alone: a hostile global config names an attributes file and an excludes file."""
        repo, _, _ = self.make_repo()
        attrs, ignore, cfg = self.tmp / "attrs", self.tmp / "ignore", self.tmp / "gitconfig"
        attrs.write_text("* hostile=yes\n")
        ignore.write_text("*\n")
        cfg.write_text(f"[core]\n\tattributesFile = {attrs}\n\texcludesFile = {ignore}\n")
        env = {**mc.sanitized_env(self.tmp / "h", self.tmp / "w"), "GIT_CONFIG_GLOBAL": str(cfg)}
        (repo / "new.txt").write_text("x\n")
        plain = subprocess.run(
            ["git", "check-attr", "hostile", "new.txt"], cwd=repo, env={**os.environ, **env}, capture_output=True
        )
        self.assertIn(b"yes", plain.stdout, "negative case: plain git reads the attributes file")
        tool_git = mc.Git(shutil.which("git"), env)
        _, out = tool_git.out(str(repo), "check-attr", "hostile", "new.txt")
        self.assertNotIn(b"yes", out)
        _, out = tool_git.out(str(repo), "status", "--porcelain")
        self.assertIn(b"new.txt", out, "the excludes file hid a file")

    def test_objects_the_test_can_write_are_never_trusted(self):
        repo, _, c2 = self.make_repo(check_src=TAMPER_SRC)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertTrue(r["tree_clean"]["clean"])

    def test_predictable_trusted_names_planted_by_the_test_are_never_used(self):
        repo, _, c2 = self.make_repo(check_src=PLANT_SRC)
        code, r = self.run_tool(repo, c2, c2, extra=["--keep"])
        self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        evil = Path(r["work_dir"]) / "evil"
        self.assertEqual(sorted(p.name for p in evil.iterdir()), ["config", "hooks"], "the planted directory was used")
        self.assertEqual((evil / "config").read_text(), "[core]\n\tbare = true\n")

    def test_a_foreign_or_non_directory_trusted_path_is_refused(self):
        private = self.tmp / "private"
        private.mkdir()
        (private / "g-x").mkdir()
        git = mc.Git(shutil.which("git"), mc.sanitized_env(self.tmp / "h"))
        t = mc.Trusted(git, str(private), str(self.tmp), str(self.tmp / "o"), "0" * 40)
        real = os.lstat(private / "g-x")
        other_uid = os.stat_result((real.st_mode, 0, 0, 1, real.st_uid + 1, 0, 0, 0, 0, 0))
        symlink = os.stat_result((0o120777, 0, 0, 1, real.st_uid, 0, 0, 0, 0, 0))
        for fake in (other_uid, symlink):
            with mock.patch.object(mc.tempfile, "mkdtemp", return_value=str(private / "g-x")):
                with mock.patch.object(mc.os, "lstat", return_value=fake):
                    with self.assertRaises(mc.Inconclusive) as cm:
                        t.prepare()
            self.assertIn("fresh empty directory", str(cm.exception))

    def test_an_existing_alternates_file_is_never_overwritten(self):
        private = self.tmp / "private"
        private.mkdir()
        target = self.tmp / "target"
        target.write_text("precious")

        class Planting(mc.Git):
            def run(self, cwd, *args, phase=None, **kw):
                if phase == "trusted-init":  # the moment between init and the alternates file
                    gd = Path(args[-1])
                    (gd / "objects" / "info").mkdir(parents=True, exist_ok=True)
                    (gd / "objects" / "info" / "alternates").symlink_to(target)
                    return None
                return super().run(cwd, *args, phase=phase, **kw)

        t = mc.Trusted(
            Planting(shutil.which("git"), mc.sanitized_env(self.tmp / "h")), str(private), str(self.tmp), "/x", "0" * 40
        )
        with self.assertRaises(OSError):
            t.prepare()
        self.assertEqual(target.read_text(), "precious", "the alternates file was written through a planted symlink")
        # a planted regular file at the same place is not truncated either
        precious = self.tmp / "precious-alternates"
        precious.write_text("keep me")

        class PlantingFile(Planting):
            def run(self, cwd, *args, phase=None, **kw):
                if phase == "trusted-init":
                    gd = Path(args[-1])
                    (gd / "objects" / "info").mkdir(parents=True, exist_ok=True)
                    shutil.copy(precious, gd / "objects" / "info" / "alternates")
                    self.planted = gd / "objects" / "info" / "alternates"
                    return None
                return mc.Git.run(self, cwd, *args, phase=phase, **kw)

        pf = PlantingFile(shutil.which("git"), mc.sanitized_env(self.tmp / "h"))
        t2 = mc.Trusted(pf, str(private), str(self.tmp), "/x", "0" * 40)
        with self.assertRaises(OSError):
            t2.prepare()
        self.assertEqual(pf.planted.read_text(), "keep me")

    def test_private_directory_inside_the_repository_is_refused(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, env={"HOME": str(repo)})
        self.assertEqual(code, 3)
        self.assertIn("private directory", r["reason"])
        self.assertFalse((repo / ".local").exists(), "something was created inside the repository")

    def test_a_nonempty_trusted_directory_is_refused(self):
        private = self.tmp / "private"
        private.mkdir()
        seeded = private / "g-seeded"
        seeded.mkdir()
        (seeded / "planted").write_text("x")
        git = mc.Git(shutil.which("git"), mc.sanitized_env(self.tmp / "h"))
        t = mc.Trusted(git, str(private), str(self.tmp), str(self.tmp / "o"), "0" * 40)
        with mock.patch.object(mc.tempfile, "mkdtemp", return_value=str(seeded)):
            with self.assertRaises(mc.Inconclusive) as cm:
                t.prepare()
        self.assertIn("fresh empty directory", str(cm.exception))
        # negative case: a fresh empty directory passes that check (and fails later, on the made-up commit)
        fresh = private / "g-fresh"
        fresh.mkdir()
        with mock.patch.object(mc.tempfile, "mkdtemp", return_value=str(fresh)):
            with self.assertRaises(mc.Inconclusive) as cm:
                t.prepare()
        self.assertNotIn("fresh empty directory", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
