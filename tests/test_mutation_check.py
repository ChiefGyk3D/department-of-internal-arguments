"""scripts/mutation_check.py: the verdicts, the exit codes, and every confinement promise, each with a case that
proves the guard bites. Every repository is built in a temporary directory under ~/.local/share (never /tmp)."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mutation_check.py"
sys.path.insert(0, str(ROOT / "scripts"))

import mutation_check as mc  # noqa: E402  (path set up above)

BASE = Path.home() / ".local" / "share" / "doia-mutation-tests"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
TEST_SRC = "import os, sys\nsys.path.insert(0, os.getcwd())\nfrom calc import f\nassert f() == 2, f()\n"
TEST_CMD = "python3 tests/check.py"


def git(cwd, *args, check=True):
    p = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True, text=True, check=check
    )
    return p.stdout.strip()


def tree_hash(path):
    """Hash of every file (name, mode, bytes) and symlink under path, .git included."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            h.update(os.path.relpath(p, path).encode() + b"\0" + str(st.st_mode).encode())
            h.update(os.readlink(p).encode() if os.path.islink(p) else Path(p).read_bytes())
    return h.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="t-", dir=BASE))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scratch = self.tmp / "scratch"

    def make_repo(self, name="src", check_src=TEST_SRC, setup=None):
        """c1: calc.f() == 1 and README. c2: the fix (f() == 2) plus tests/check.py. Returns (repo, c1, c2)."""
        repo = self.tmp / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "calc.py").write_text("def f():\n    return 1\n")
        (repo / "README").write_text("readme\n")
        if setup:
            setup(repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        c1 = git(repo, "rev-parse", "HEAD")
        (repo / "calc.py").write_text("def f():\n    return 2\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "check.py").write_text(check_src.replace("@SRC@", str(repo)))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fix")
        return repo, c1, git(repo, "rev-parse", "HEAD")

    def run_tool(self, repo, head, revert, test=TEST_CMD, extra=(), env=None, scratch=True):
        argv = [sys.executable, str(SCRIPT), "--repo", str(repo), f"--head={head}", f"--revert={revert}"]
        argv += ["--test", test]
        if scratch:
            argv += ["--scratch", str(self.scratch)]
        argv += list(extra)
        p = subprocess.run(argv, capture_output=True, text=True, env={**os.environ, **(env or {})}, timeout=300)
        report = json.loads(p.stdout)
        self.assertEqual(p.returncode, report["exit_code"])
        if report.get("kept") and report.get("work_dir"):
            self.addCleanup(shutil.rmtree, report["work_dir"], ignore_errors=True)
        return p.returncode, report

    def phases(self, report):
        return [c["phase"] for c in report["commands"] if c["phase"].startswith("test-")]


class Verdicts(Base):
    def test_bites_exit_0_and_source_byte_identical(self):
        repo, _, c2 = self.make_repo()
        before = tree_hash(repo)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertTrue(r["green_before"] and r["green_after_restore"])
        self.assertIs(r["red_after_revert"], True)
        self.assertTrue(r["tree_clean"]["clean"])
        self.assertIs(r["source_repo_unchanged"], True)
        self.assertEqual(tree_hash(repo), before)
        self.assertEqual(r["reverted"]["files"], ["calc.py"])
        self.assertEqual(r["reverted"]["excluded_test_files"], ["tests/check.py"])
        self.assertEqual(self.phases(r), ["test-at-head", "test-after-revert", "test-after-restore"])
        self.assertFalse(os.path.exists(r["work_dir"]), "the copy is deleted on bites")

    def test_keep_flag_keeps_the_copy_on_bites(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--keep"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isdir(r["work_dir"]))
        self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)

    def test_vacuous_exit_2_and_copy_kept(self):
        repo, _, c2 = self.make_repo()
        before = tree_hash(repo)
        code, r = self.run_tool(repo, c2, c2, test="python3 -c pass")
        self.assertEqual((code, r["verdict"]), (2, "vacuous"), r["reason"])
        self.assertEqual(self.phases(r), ["test-at-head", "test-after-revert"])
        self.assertTrue(os.path.isdir(r["work_dir"]), "kept on a non-bites result")
        self.assertEqual(tree_hash(repo), before)

    def test_already_red_at_head_is_inconclusive_and_nothing_else_runs(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, test='python3 -c "raise SystemExit(1)"')
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIs(r["green_before"], False)
        self.assertEqual(self.phases(r), ["test-at-head"])
        self.assertNotIn("revert", [c["phase"] for c in r["commands"]])

    def test_failure_outside_red_exit_is_inconclusive_not_bites(self):
        """pytest exit 5 (nothing collected) after a revert is not a bite; exit 1 is, by default."""
        src = TEST_SRC + "if f() != 2:\n    sys.exit(5)\n"
        repo, _, c2 = self.make_repo(check_src=src.replace("assert f() == 2, f()\n", ""))
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        code, r = self.run_tool(repo, c2, c2, extra=["--red-exit", "5"])
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_revert_that_applies_to_nothing_is_inconclusive(self):
        repo, c1, c2 = self.make_repo()
        # c1 is the root commit: no parent, so it cannot be reverted.
        code, r = self.run_tool(repo, c2, c1)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertNotIn("test-at-head", self.phases(r))

    def test_commit_with_only_test_files_is_inconclusive(self):
        repo, _, c2 = self.make_repo()
        (repo / "tests" / "extra_test.py").write_text("pass\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "tests only")
        c3 = git(repo, "rev-parse", "HEAD")
        code, r = self.run_tool(repo, c3, c3)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("no non-test file", r["reason"])

    def test_patch_file_revert(self):
        repo, _, c2 = self.make_repo()
        patch = self.tmp / "fix.patch"
        patch.write_text(git(repo, "diff", "HEAD~1", "HEAD", "--", "calc.py") + "\n")
        code, r = self.run_tool(repo, c2, patch)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertEqual(r["reverted"]["kind"], "patch")

    def test_base_is_recorded(self):
        repo, c1, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--base", c1])
        self.assertEqual((code, r["base"]), (0, c1))

    def test_usage_error_is_json_exit_3_never_2(self):
        p = subprocess.run([sys.executable, str(SCRIPT), "--repo"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(json.loads(p.stdout)["verdict"], "inconclusive")

    def test_timeout_and_output_cap_are_inconclusive(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, test='python3 -c "import time; time.sleep(60)"', extra=["--timeout", "1"])
        self.assertEqual(code, 3)
        self.assertEqual(r["commands"][-1]["status"], "timeout")
        spam = "python3 -c \"import sys\nwhile True: sys.stdout.write('x' * 4096)\""
        code, r = self.run_tool(repo, c2, c2, test=spam, extra=["--max-output", "10000"])
        self.assertEqual(code, 3)
        self.assertEqual(r["commands"][-1]["status"], "output-cap")
        self.assertLessEqual(len(r["commands"][-1]["output_tail"]), mc.TAIL_BYTES)

    def test_missing_test_program_is_inconclusive(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, test="no-such-program-here")
        self.assertEqual((code, r["commands"][-1]["result"]), (3, "error"))
        self.assertFalse(r["green_before"])


class Confinement(Base):
    def test_dirty_tree_and_stash_untouched(self):
        repo, _, c2 = self.make_repo()
        (repo / "README").write_text("dirty tracked\n")
        git(repo, "stash", "push", "-q", "-m", "unrelated")
        (repo / "README").write_text("dirty again\n")
        (repo / "untracked.txt").write_text("u\n")
        before = (tree_hash(repo), git(repo, "status", "--porcelain"), git(repo, "stash", "list"))
        refs = git(repo, "for-each-ref")
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertEqual((tree_hash(repo), git(repo, "status", "--porcelain"), git(repo, "stash", "list")), before)
        self.assertEqual(git(repo, "for-each-ref"), refs)
        self.assertEqual((repo / "README").read_text(), "dirty again\n")

    def test_relative_path_write_does_not_touch_the_source(self):
        repo, _, c2 = self.make_repo(check_src=TEST_SRC + "open('../../../escape-rel', 'w').write('x')\n")
        before = tree_hash(repo)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual(tree_hash(repo), before)
        self.assertIs(r["source_repo_unchanged"], True)

    def test_absolute_path_write_to_the_source_is_detected_not_prevented(self):
        src = TEST_SRC + "open('@SRC@/leak.txt', 'w').write('x')\n"
        repo, _, c2 = self.make_repo(check_src=src)
        code, r = self.run_tool(repo, c2, c2)
        self.assertTrue((repo / "leak.txt").exists(), "documented limit: the tool cannot prevent this")
        self.assertIs(r["source_repo_unchanged"], False)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))

    def test_absolute_path_edit_of_a_tracked_source_file_is_detected(self):
        src = TEST_SRC + "open('@SRC@/README', 'w').write('changed')\n"
        repo, _, c2 = self.make_repo(check_src=src)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["source_repo_unchanged"]), (3, False))

    def test_edit_of_an_already_dirty_source_file_is_detected(self):
        """The status text is identical (still ' M'), so only the content hash catches it."""
        src = TEST_SRC + "open('@SRC@/README', 'w').write('second edit')\n"
        repo, _, c2 = self.make_repo(check_src=src)
        (repo / "README").write_text("first edit\n")
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["source_repo_unchanged"]), (3, False))

    def test_untracked_leftover_after_restore_is_detected(self):
        src = (
            TEST_SRC
            + "m = os.path.join(os.path.dirname(os.getcwd()), 'marker')\n"
            + "if os.path.exists(m):\n    open('junk.txt', 'w').write('left behind')\n"
            + "else:\n    open(m, 'w').close()\n"
        )
        repo, _, c2 = self.make_repo(check_src=src)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIs(r["tree_clean"]["final"]["clean"], False)
        self.assertIn("junk.txt", r["tree_clean"]["final"]["status_porcelain"])

    def test_restore_slip_is_detected(self):
        src = (
            TEST_SRC
            + "m = os.path.join(os.path.dirname(os.getcwd()), 'marker')\n"
            + "if os.path.exists(m):\n    open('README', 'a').write('slip')\n"
            + "else:\n    open(m, 'w').close()\n"
        )
        repo, _, c2 = self.make_repo(check_src=src)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIs(r["green_after_restore"], True)
        self.assertIs(r["tree_clean"]["clean"], False)
        self.assertIs(r["tree_clean"]["after_restore"]["clean"], True)
        self.assertIs(r["tree_clean"]["final"]["clean"], False)
        self.assertIn("README", r["tree_clean"]["final"]["status_porcelain"])

    def test_hostile_hook_and_filter_do_not_run(self):
        sentinel = self.tmp / "SENTINEL"

        def hostile(repo):
            (repo / ".gitattributes").write_text("* filter=evil\n")

        repo, _, c2 = self.make_repo(setup=hostile)
        script = f"#!/bin/sh\necho ran >> '{sentinel}'\n"
        for hook in ("post-checkout", "post-merge", "pre-commit", "post-commit", "reference-transaction"):
            p = repo / ".git" / "hooks" / hook
            p.write_text(script)
            p.chmod(0o755)
        for key in ("clean", "smudge", "process"):
            git(repo, "config", f"filter.evil.{key}", f"sh -c 'echo {key} >> {sentinel}; cat'")
        git(repo, "config", "core.fsmonitor", f"sh -c 'echo fsm >> {sentinel}'")
        code, r = self.run_tool(repo, c2, c2)
        self.assertFalse(
            sentinel.exists(),
            sentinel.read_text() if sentinel.exists() else "a hook, filter or fsmonitor from the source ran",
        )
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_user_global_git_config_hooks_and_templates_do_not_run(self):
        """A hooksPath or template directory in the caller's own ~/.gitconfig must not reach the clone either."""
        sentinel = self.tmp / "SENTINEL3"
        hooks = self.tmp / "hooks"
        template = self.tmp / "template" / "hooks"
        for d in (hooks, template):
            d.mkdir(parents=True)
            h = d / "post-checkout"
            h.write_text(f"#!/bin/sh\necho ran >> '{sentinel}'\n")
            h.chmod(0o755)
        for variant in (f"[core]\n\thooksPath = {hooks}\n", f"[init]\n\ttemplateDir = {template.parent}\n"):
            home = self.tmp / "home"
            home.mkdir(exist_ok=True)
            (home / ".gitconfig").write_text(variant)
            repo, _, c2 = self.make_repo(name=f"src{abs(hash(variant))}")
            code, r = self.run_tool(
                repo, c2, c2, env={"HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
            )
            self.assertFalse(sentinel.exists(), variant)
            self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_hostile_sentinel_would_be_seen_by_plain_git(self):
        """Negative case: the same setup does run a hook under a plain `git clone`, so the test above can fail."""
        sentinel = self.tmp / "SENTINEL2"
        repo, _, _ = self.make_repo()
        hook = repo / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\necho ran >> '{sentinel}'\n")
        hook.chmod(0o755)
        git(repo, "checkout", "-q", "HEAD~1")
        self.assertTrue(sentinel.exists())

    def test_arguments_are_data(self):
        weird = self.tmp / "my repo $(touch PWNED) ; -x `touch PWNED2` & 'q'"
        weird.mkdir()
        repo, _, c2 = self.make_repo(name="plain")
        shutil.move(str(repo), str(weird / "r"))
        repo = weird / "r"
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        for bad in ("-x", "--output=" + str(self.tmp / "PWNED3"), "HEAD; touch PWNED4", "$(touch PWNED5)"):
            code, r = self.run_tool(repo, bad, c2)
            self.assertEqual(code, 3, bad)
            code, r = self.run_tool(repo, c2, bad)
            self.assertEqual(code, 3, bad)
        for name in ("PWNED", "PWNED2", "PWNED3", "PWNED4", "PWNED5"):
            self.assertFalse((weird / name).exists() or (self.tmp / name).exists() or Path(name).exists(), name)
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_leading_dash_repo_path(self):
        repo, _, c2 = self.make_repo(name="-dashrepo")
        p = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo=-dashrepo", f"--head={c2}", f"--revert={c2}", "--test", TEST_CMD]
            + ["--scratch", str(self.scratch)],
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_scratch_that_equals_contains_or_lies_inside_the_repo_is_refused(self):
        repo, _, c2 = self.make_repo()
        before = tree_hash(repo)
        for scratch in (repo, repo.parent, repo / "sub", repo / ".git"):
            argv = [sys.executable, str(SCRIPT), "--repo", str(repo), f"--head={c2}", f"--revert={c2}"]
            p = subprocess.run(argv + ["--test", TEST_CMD, "--scratch", str(scratch)], capture_output=True, text=True)
            r = json.loads(p.stdout)
            self.assertEqual((p.returncode, r["verdict"]), (3, "inconclusive"), scratch)
            self.assertIn("scratch", r["reason"])
            self.assertIsNone(r.get("work_dir"))
        self.assertEqual(tree_hash(repo), before)
        self.assertFalse((repo / "sub").exists(), "the refusal came before anything was created")

    def test_default_scratch_is_never_under_tmp_but_a_named_one_is_honored(self):
        args = mock.Mock(scratch=None)
        with mock.patch.dict(os.environ, {"HOME": "/tmp/fake-home-for-test"}):
            with self.assertRaises(mc.Inconclusive):
                mc.resolve_scratch(args, str(self.tmp / "elsewhere"))
        shutil.rmtree("/tmp/fake-home-for-test", ignore_errors=True)
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp / "h")}):
            got = mc.resolve_scratch(args, str(self.tmp / "elsewhere"))
        self.assertTrue(got.endswith("doia-mutation"))
        named = mc.resolve_scratch(mock.Mock(scratch=str(self.tmp / "named")), str(self.tmp / "elsewhere"))
        self.assertEqual(named, os.path.realpath(self.tmp / "named"))

    def test_work_dir_is_0700_and_environment_is_sanitized(self):
        repo, _, c2 = self.make_repo()
        dump = 'python3 -c "import os, json; print(json.dumps(dict(os.environ)))"'
        secrets = {"GH_TOKEN": "tok-abc", "DOPPLER_TOKEN": "dp-xyz", "AWS_SECRET_ACCESS_KEY": "aws-123"}
        code, r = self.run_tool(repo, c2, c2, test=dump, env=secrets)
        self.assertEqual(code, 2)
        env = json.loads([c for c in r["commands"] if c["phase"] == "test-at-head"][0]["output_tail"])
        self.assertFalse(set(secrets) & set(env), env.keys())
        self.assertNotIn("tok-abc", json.dumps(r))
        work = r["work_dir"]
        self.assertEqual(oct(os.stat(work).st_mode & 0o777), "0o700")
        for key in ("HOME", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "npm_config_cache", "TMPDIR"):
            self.assertTrue(env[key].startswith(work + os.sep), (key, env[key]))
            self.assertFalse(env[key].startswith(os.path.join(work, "repo") + os.sep), "caches stay out of the tree")
        self.assertIn("PATH", env)
        self.assertIn("LANG", env)

    def test_relative_path_entry_cannot_plant_a_program(self):
        with mock.patch.dict(os.environ, {"PATH": f".{os.pathsep}{os.pathsep}rel/bin{os.pathsep}/usr/bin"}):
            self.assertEqual(mc.clean_path(), "/usr/bin")

    def test_network_is_removed_when_the_host_allows_it(self):
        repo, _, c2 = self.make_repo()
        srv = socket.socket()
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        cmd = f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}), 2)\""
        code, r = self.run_tool(repo, c2, c2, test=cmd)
        probe = (shutil.which("unshare"), "--net", "--map-current-user", "--", "true")  # independent of the tool's own
        if probe[0] and subprocess.run(probe, capture_output=True).returncode == 0:
            self.assertEqual(r["network_isolation"], "unshare", "the host can isolate the network; the tool must")
        if r["network_isolation"] == "unshare":
            self.assertEqual((code, r["green_before"]), (3, False))
        else:
            self.assertEqual(r["network_isolation"], "none")
            self.assertEqual(code, 2)  # connection works; a vacuous test
            code, r = self.run_tool(repo, c2, c2, extra=["--require-no-network"])
            self.assertEqual(code, 3)


class PatchGuards(Base):
    def patch(self, a, b=None):
        b = b or a
        return f"diff --git a/{a} b/{b}\n--- a/{a}\n+++ b/{b}\n@@ -1 +1 @@\n-x\n+y\n".encode()

    def test_paths_are_extracted(self):
        self.assertEqual(mc.patch_paths(self.patch("src/a b.py")), ["src/a b.py"])
        binary = b"diff --git a/x.bin b/x.bin\nindex 1..2 100644\nGIT binary patch\nliteral 1\n"
        self.assertEqual(mc.patch_paths(binary), ["x.bin"])

    def refused(self, paths, clone):
        with self.assertRaises(mc.Inconclusive):
            mc.check_patch_paths(paths, clone)

    def test_absolute_dotdot_and_dotgit_refused_and_normal_accepted(self):
        with tempfile.TemporaryDirectory(dir=self.tmp) as clone:
            mc.check_patch_paths(["src/ok.py", "a/b/c.txt"], clone)  # negative case: the guard lets good paths by
            for bad in ("/etc/passwd", "../x", "a/../../x", ".git/hooks/post-checkout", "a/.GIT/config", "a//b", ""):
                self.refused([bad], clone)

    def test_symlinked_parent_is_refused(self):
        with tempfile.TemporaryDirectory(dir=self.tmp) as clone:
            os.symlink("/", os.path.join(clone, "link"))
            self.refused(["link/etc/x"], clone)
            mc.check_patch_paths(["other/x"], clone)

    def test_quoted_and_headerless_paths_are_refused(self):
        for bad in (b'--- "a/we\\tird"\n+++ "b/we\\tird"\n', b"--- x\n+++ y\n", b"nothing here\n"):
            with self.assertRaises(mc.Inconclusive):
                mc.patch_paths(bad)

    def test_absolute_path_inside_a_patch_file_is_refused_by_the_tool(self):
        base = self
        repo, _, c2 = base.make_repo()
        evil = base.tmp / "evil.patch"
        evil.write_bytes(b"--- /etc/hostname\n+++ /etc/hostname\n@@ -1 +1 @@\n-x\n+y\n")
        code, r = base.run_tool(repo, c2, evil)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertNotIn("test-at-head", self.phases(r))
        sneaky = base.tmp / "sneaky.patch"
        sneaky.write_bytes(self.patch(".git/hooks/post-checkout"))
        code, r = base.run_tool(repo, c2, sneaky)
        self.assertEqual(code, 3)
        self.assertIn(".git", r["reason"])
        link = base.tmp / "link.patch"
        real = base.tmp / "real.patch"
        real.write_bytes(self.patch("calc.py"))
        link.symlink_to(real)
        code, r = base.run_tool(repo, c2, link)
        self.assertEqual(code, 3)
        self.assertIn("symlink", r["reason"])

    def test_test_file_classifier(self):
        for p in ("tests/a.py", "pkg/test/x.py", "test_a.py", "a_test.py", "x.spec.ts", "conftest.py"):
            self.assertTrue(mc.is_test_path(p), p)
        for p in ("calc.py", "src/contest.py", "latest.py", "docs/tests.md"):
            self.assertFalse(mc.is_test_path(p), p)


class ProcessGuards(unittest.TestCase):
    def test_no_shell_true_anywhere_in_the_script(self):
        src = SCRIPT.read_text()
        self.assertNotIn("shell=True", src)
        self.assertNotIn("os.system", src)
        self.assertNotIn("os.popen", src)

    def test_runner_kills_the_process_group(self):
        BASE.mkdir(parents=True, exist_ok=True)
        marker = Path(tempfile.mkdtemp(dir=BASE)) / "alive"
        self.addCleanup(shutil.rmtree, marker.parent, ignore_errors=True)
        code = f"import subprocess,time; subprocess.Popen(['sh','-c','sleep 3; touch {marker}']); time.sleep(30)"
        res = mc.run([sys.executable, "-c", code], cwd=None, env=mc.sanitized_env("/nonexistent"), timeout=1, cap=1000)
        self.assertEqual(res.status, "timeout")
        import time

        time.sleep(4)
        self.assertFalse(marker.exists(), "a child of the test survived the timeout")


if __name__ == "__main__":
    unittest.main()
