"""scripts/mutation_check.py, round 2: escaped processes, git metadata the test controls, interrupts, namespace
wrapping, help and usage exits, pruning, and one failing-on-revert test for each remaining guard. Same rule as
test_mutation_check.py: a guard test is only worth having if it goes red when the guard is removed."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import SCRIPT, TEST_SRC, Base, git, host_has_pid_namespace  # noqa: E402

DAEMON_SRC = (
    TEST_SRC
    + """import subprocess
subprocess.Popen(
    [sys.executable, '-c', "import time; time.sleep(3); open('@SRC@/PWNED', 'w').write('x')"],
    start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    close_fds=True,
)
"""
)
# A daemon that is started only by the third run (the marker exists by then) and writes 1.5 s later.
LATE_DAEMON_SRC = (
    TEST_SRC
    + """import subprocess
m = os.path.join(os.path.dirname(os.getcwd()), 'marker')
if os.path.exists(m):
    subprocess.Popen(
        [sys.executable, '-c', "import time; time.sleep(1.5); open('@SRC@/PWNED', 'w').write('x')"],
        start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
else:
    open(m, 'w').close()
"""
)
# The first run plants hostile git configuration in the clone's own .git (the test controls it).
HOSTILE_GIT_SRC = (
    TEST_SRC
    + """m = os.path.join(os.path.dirname(os.getcwd()), 'marker')
if not os.path.exists(m):
    open(m, 'w').close()
    beacon = 'python3 @TMP@/beacon.py'
    with open('.git/config', 'a') as c:
        c.write('[filter "x"]\\n\\tclean = ' + beacon + '\\n\\tsmudge = ' + beacon + '\\n\\trequired = false\\n')
        c.write('[core]\\n\\tfsmonitor = ' + beacon + '\\n\\thooksPath = @TMP@/evilhooks\\n')
        c.write('\\tpager = ' + beacon + '\\n\\tsshCommand = ' + beacon + '\\n')
        c.write('[diff]\\n\\texternal = ' + beacon + '\\n')
        c.write('[include]\\n\\tpath = @TMP@/evil.inc\\n')
    with open('.git/info/attributes', 'a') as a:
        a.write('calc.py filter=x\\nREADME filter=x\\n')
"""
)
# After the revert (run 2) leave an untracked directory and an ignored file; the third run must find neither.
CLEAN_SRC = """import os, sys
sys.path.insert(0, os.getcwd())
m = os.path.join(os.path.dirname(os.getcwd()), 'marker')
from calc import f
if os.path.exists(m):
    if f() != 2:
        os.makedirs('dd/sub', exist_ok=True)
        open('dd/sub/z', 'w').write('z')
        open('x.log', 'w').write('z')
        sys.exit(1)
    assert not os.path.exists('dd') and not os.path.exists('x.log'), 'left over from the reverted run'
else:
    assert f() == 2
    open(m, 'w').close()
"""
BINARY_SRC = "assert open('data.bin', 'rb').read() == b'\\x00\\x02'\n"  # independent of calc.py on purpose


class Escapes(Base):
    def wait_for(self, path, seconds=90):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if path.exists():
                return
            time.sleep(0.1)
        self.fail(f"{path} never appeared")

    @unittest.skipUnless(host_has_pid_namespace(), "this host cannot run a PID namespace")
    def test_a_daemon_that_escaped_the_process_group_dies_with_the_run(self):
        repo, _, c2 = self.make_repo(check_src=DAEMON_SRC)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertEqual(r["process_isolation"], "pid-namespace")
        self.assertIs(r["source_repo_unchanged"], True)
        time.sleep(5)  # the daemons would have written at 3 s
        self.assertFalse((repo / "PWNED").exists(), "a process started by the test outlived it and wrote later")
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_without_a_pid_namespace_a_late_write_is_caught_after_the_settle_time(self):
        repo, _, c2 = self.make_repo(check_src=LATE_DAEMON_SRC)
        code, r = self.run_tool(repo, c2, c2, extra=["--isolation", "net", "--settle", "4"])
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        self.assertEqual(r["process_isolation"], "none")
        self.assertIs(r["source_unchanged_at_check"], False)
        self.assertNotIn("source_repo_unchanged", r, "no PID namespace: the claim is only 'unchanged at check'")
        self.assertTrue((repo / "PWNED").exists())

    def test_field_name_follows_what_is_proved(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--isolation", "net"])
        self.assertEqual(code, 0, r["reason"])
        self.assertIs(r["source_unchanged_at_check"], True)
        self.assertNotIn("source_repo_unchanged", r)
        if host_has_pid_namespace():
            code, r = self.run_tool(repo, c2, c2)
            self.assertIs(r["source_repo_unchanged"], True)
            self.assertNotIn("source_unchanged_at_check", r)

    def test_a_backgrounded_child_in_the_group_is_killed_when_the_run_ends(self):
        mark = self.tmp / "straggler"
        repo, _, c2 = self.make_repo()
        test = f"sh -c '(sleep 2; touch {mark}) & exit 0'"
        code, r = self.run_tool(repo, c2, c2, test=test, extra=["--isolation", "net"])
        self.assertEqual(code, 2, r["reason"])
        time.sleep(4)
        self.assertFalse(mark.exists(), "a straggler in the process group survived the run")

    def test_signal_kills_the_whole_run_and_exits_3(self):
        for iso, sig in (("none", signal.SIGINT), ("auto", signal.SIGTERM)):
            repo, _, c2 = self.make_repo(name=f"sig-{iso}")
            started, mark = self.tmp / f"started-{iso}", self.tmp / f"mark-{iso}"
            test = (
                f"python3 -c \"import pathlib, time; pathlib.Path('{started}').write_text('x'); "
                f"time.sleep(4); pathlib.Path('{mark}').write_text('y')\""
            )
            argv = [sys.executable, str(SCRIPT), "--repo", str(repo), f"--head={c2}", f"--revert={c2}", "--test", test]
            argv += ["--scratch", str(self.scratch), "--isolation", iso, "--settle", "0.2"]
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.wait_for(started)
            proc.send_signal(sig)
            out, _ = proc.communicate(timeout=60)
            r = json.loads(out)
            self.assertEqual((proc.returncode, r["exit_code"], r["verdict"]), (3, 3, "inconclusive"), r["reason"])
            self.assertIn("interrupted", r["reason"])
            if r.get("work_dir"):
                self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)
            time.sleep(6)
            self.assertFalse(mark.exists(), f"{iso}: the test survived {sig.name}")


class CloneControlledGit(Base):
    def hostile_files(self):
        beacon = self.tmp / "beacon.py"
        beacon.write_text(f"import pathlib\npathlib.Path('{self.tmp / 'BEACON_RAN'}').write_text('ran')\n")
        hooks = self.tmp / "evilhooks"
        hooks.mkdir()
        hook = hooks / "post-checkout"
        hook.write_text(f"#!/bin/sh\necho hook >> '{self.tmp / 'BEACON_RAN'}'\n")
        hook.chmod(0o755)
        (self.tmp / "evil.inc").write_text(f"[core]\n\tfsmonitor = python3 {beacon}\n")

    def test_filter_and_config_planted_in_the_clone_never_run(self):
        self.hostile_files()
        repo, _, c2 = self.make_repo(check_src=HOSTILE_GIT_SRC)
        code, r = self.run_tool(repo, c2, c2)
        beacon = self.tmp / "BEACON_RAN"
        self.assertFalse(beacon.exists(), "a driver planted in the clone's .git ran in a tool git step")
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_planted_config_is_live_for_plain_git(self):
        """Negative case: the same planted filter runs for plain git in the clone, so the test above can fail."""
        self.hostile_files()
        repo = self.tmp / "plain"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "calc.py").write_text("x\n")
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("calc.py filter=x\n")
        git(repo, "config", "filter.x.clean", f"python3 {self.tmp / 'beacon.py'}")
        git(repo, "add", "calc.py")
        self.assertTrue((self.tmp / "BEACON_RAN").exists())

    def test_every_git_step_runs_inside_the_namespaces_like_the_test(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual(code, 0, r["reason"])
        steps = [c for c in r["commands"] if not c["phase"].startswith("test-")]
        phases = {c["phase"] for c in steps}
        self.assertTrue(
            {"clone", "checkout-head", "trusted-init", "trusted-read-tree", "revert", "restore-checkout"} <= phases,
            phases,
        )
        if r["network_isolation"] != "none":
            for c in steps:
                self.assertTrue(c["argv"][0].endswith("unshare") and "--net" in c["argv"], c["argv"])
        if r["process_isolation"] != "none":
            for c in steps:
                self.assertIn("--pid", c["argv"], c["argv"])
        for c in steps:
            if c["phase"] != "trusted-base" and c["phase"].startswith(("trusted", "revert", "restore")):
                self.assertIn("protocol.allow=never", c["argv"])
            for needle in (
                "core.hooksPath=/dev/null",
                "core.fsmonitor=false",
                "core.pager=cat",
                "core.sshCommand=",
                "core.askPass=",
                "credential.helper=",
                "diff.external=",
            ):
                self.assertIn(needle, c["argv"], c["phase"])

    def test_hooks_path_override_alone_blocks_a_hook_in_the_repository(self):
        repo, _, _ = self.make_repo()
        sentinel = self.tmp / "HOOKRAN"
        hook = repo / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\necho ran >> '{sentinel}'\n")
        hook.chmod(0o755)
        tool_git = mc.Git(shutil.which("git"), mc.sanitized_env(self.tmp / "h", self.tmp / "w"))
        tool_git.run(str(repo), "checkout", "-q", "--detach", "HEAD~1")
        self.assertFalse(sentinel.exists(), "a repository hook ran through the tool's git")
        git(repo, "checkout", "-q", "main")
        self.assertTrue(sentinel.exists(), "negative case: plain git runs the hook")

    def test_user_and_system_git_config_are_not_read(self):
        home = self.tmp / "hostile-home"
        home.mkdir()
        (home / ".gitconfig").write_text("[hostile]\n\tkey = yes\n")
        env = mc.sanitized_env(home, self.tmp / "w")  # HOME is hostile; XDG_CONFIG_HOME is not
        self.assertEqual(env["GIT_CONFIG_SYSTEM"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        repo, _, _ = self.make_repo()
        tool_git = mc.Git(shutil.which("git"), env)
        _, out = tool_git.out(str(repo), "config", "--list", "--show-origin")
        self.assertNotIn(b"hostile.key", out)
        plain = subprocess.run(
            ["git", "config", "--list"],
            cwd=repo,
            env={**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")},
            capture_output=True,
        )
        self.assertIn(b"hostile.key", plain.stdout, "negative case: plain git does read it")

    def test_clone_does_not_hardlink_the_source_objects(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--keep"])
        self.assertEqual(code, 0, r["reason"])
        seen = 0
        for dirpath, _, names in os.walk(repo / ".git" / "objects"):
            if dirpath.endswith(("info", "pack")):
                continue
            for n in names:
                seen += 1
                self.assertEqual(os.stat(os.path.join(dirpath, n)).st_nlink, 1, "a source object is hard-linked")
        self.assertGreater(seen, 0)


class Honesty(Base):
    def test_isolation_labels_say_what_is_and_is_not_isolated(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2)
        iso = r["isolation"]
        self.assertIn(iso["network"], ("net-namespace", "none"))
        self.assertIn(iso["process"], ("pid-namespace", "none"))
        for key in ("filesystem", "identity"):
            self.assertTrue(iso[key].startswith("none"), iso)
        self.assertNotIn("unshare", (r["network_isolation"], r["process_isolation"]))

    def test_docstring_states_the_limits(self):
        doc = mc.__doc__
        for phrase in (
            "throwaway user, container or VM",
            "source_unchanged_at_check",
            "does not prove that the test is honest",
            "network namespace only",
            "unix sockets",
        ):
            self.assertIn(phrase, doc)
        self.assertNotIn("No token or other inherited variable is passed", doc)


class UsageExits(Base):
    def call(self, *argv):
        return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=60)

    def test_help_exits_3_with_json_and_usage_on_stderr(self):
        for flag in ("-h", "--help"):
            p = self.call(flag)
            self.assertEqual(p.returncode, 3, flag)
            self.assertEqual(json.loads(p.stdout)["verdict"], "inconclusive")
            self.assertIn("usage:", p.stderr)

    def test_usage_errors_exit_3(self):
        for argv in (["--repo"], [], ["--bogus"], ["--repo", "x", "--head", "h", "--revert", "r"]):
            p = self.call(*argv)
            self.assertEqual(p.returncode, 3, argv)
            self.assertEqual(json.loads(p.stdout)["verdict"], "inconclusive")

    def test_red_exit_must_be_nonempty_and_exclude_zero(self):
        base = ["--repo", "x", "--head", "h", "--revert", "r", "--test", "t"]
        for bad in ("0,1", "0", "", " , "):
            with self.assertRaises(mc.Inconclusive, msg=bad):
                mc.parse([*base, "--red-exit", bad])
        self.assertEqual(mc.parse([*base, "--red-exit", "1,5"]).red_exit_codes, {1, 5})

    def test_require_no_network_is_inconclusive_when_isolation_is_off(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--isolation", "none", "--require-no-network"])
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("--require-no-network", r["reason"])
        self.assertNotIn("test-at-head", self.phases(r))
        code, r = self.run_tool(repo, c2, c2, extra=["--isolation", "none"])
        self.assertEqual((code, r["network_isolation"], r["process_isolation"]), (0, "none", "none"))


class Pruning(Base):
    def entry(self, name, marker=True, age_days=10, target=None):
        path = self.scratch / name
        if target is not None:
            path.symlink_to(target)
            return path
        path.mkdir()
        (path / "payload.txt").write_text("evidence")
        if marker:
            m = path / mc.MARKER
            m.write_text("x")
            old = time.time() - age_days * 86400
            os.utime(m, (old, old))
        return path

    def test_old_copies_made_by_the_tool_are_pruned_and_nothing_else(self):
        self.scratch.mkdir(mode=0o700)
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / mc.MARKER).write_text("x")
        os.utime(victim / mc.MARKER, (1, 1))
        old = self.entry("doia-mc-old")
        new = self.entry("doia-mc-new", age_days=1)
        nomark = self.entry("doia-mc-nomarker", marker=False)
        other = self.entry("keepme")
        link = self.entry("doia-mc-link", target=victim)
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual(code, 0, r["reason"])
        self.assertEqual(r["pruned"], ["doia-mc-old"])
        self.assertFalse(old.exists())
        for kept in (new, nomark, other):
            self.assertTrue((kept / "payload.txt").exists(), kept)
        self.assertTrue(link.is_symlink())
        self.assertTrue((victim / mc.MARKER).exists(), "pruning followed a symlink")

    def test_clean_old_zero_disables_pruning(self):
        self.scratch.mkdir(mode=0o700)
        old = self.entry("doia-mc-old")
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--clean-old", "0"])
        self.assertEqual((code, r["pruned"]), (0, []))
        self.assertTrue(old.exists())

    def test_remove_tree_refuses_anything_but_a_real_direct_child(self):
        self.scratch.mkdir(mode=0o700)
        child = self.scratch / "child"
        (child / "deep").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            mc.remove_tree(str(child / "deep"), str(self.scratch))  # not a direct child
        sibling = self.scratch / "sibling"
        sibling.mkdir()
        (sibling / "keep.txt").write_text("evidence")
        link = self.scratch / "link"
        link.symlink_to(sibling)  # a symlink whose target is itself a direct child of the scratch directory
        with self.assertRaises(RuntimeError):
            mc.remove_tree(str(link), str(self.scratch))
        self.assertTrue((sibling / "keep.txt").exists())
        mc.remove_tree(str(child), str(self.scratch))  # negative case: a real child goes
        self.assertFalse(child.exists())


class Guards(Base):
    def test_revert_that_changes_nothing_is_inconclusive(self):
        repo, _, c2 = self.make_repo()
        noop = self.tmp / "noop.patch"
        noop.write_text("diff --git a/README b/README\n--- a/README\n+++ b/README\n@@ -1 +1 @@\n-readme\n+readme\n")
        code, r = self.run_tool(repo, c2, noop)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("changed nothing", r["reason"])
        self.assertEqual(self.phases(r), ["test-at-head"])

    def test_path_checks_run_on_the_final_patch_before_any_test(self):
        def link(repo):
            (repo / "sub").mkdir()
            (repo / "sub" / "f.txt").write_text("x\n")
            os.symlink("sub", repo / "lnk")

        repo, _, c2 = self.make_repo(setup=link)
        cases = {
            "through-symlink": ("a/lnk/f.txt", "goes through a symlink"),
            "absolute": ("a//etc/hostname", "absolute, empty, dot component or .git"),
        }
        for name, (path, why) in cases.items():
            patch = self.tmp / f"{name}.patch"
            patch.write_text(f"diff --git {path} b/{path[2:]}\n--- {path}\n+++ b/{path[2:]}\n@@ -1 +1 @@\n-x\n+y\n")
            code, r = self.run_tool(repo, c2, patch)
            self.assertEqual(code, 3, name)
            self.assertIn(why, r["reason"], name)
            self.assertNotIn("test-at-head", self.phases(r), name)

    def test_revisions_that_start_with_a_dash_are_refused_by_name(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, "-x", c2)
        self.assertEqual(code, 3)
        self.assertIn("must not start with '-'", r["reason"])
        code, r = self.run_tool(repo, c2, "-x")
        self.assertEqual(code, 3)
        self.assertIn("--revert must not start with '-'", r["reason"])
        code, r = self.run_tool(repo, c2, c2, extra=["--base=-x"])
        self.assertEqual(code, 3)
        self.assertIn("must not start with '-'", r["reason"])

    def test_revert_commit_must_be_an_ancestor_of_head(self):
        repo, c1, c2 = self.make_repo()
        git(repo, "checkout", "-q", "-b", "side", c1)
        (repo / "other.py").write_text("x\n")
        git(repo, "add", "other.py")
        git(repo, "commit", "-q", "-m", "side")
        side = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "main")
        code, r = self.run_tool(repo, c2, side)
        self.assertEqual(code, 3)
        self.assertIn("not an ancestor", r["reason"])
        self.assertEqual(self.phases(r), [])

    def test_revert_commit_must_have_exactly_one_parent(self):
        repo, c1, _ = self.make_repo()
        git(repo, "checkout", "-q", "-b", "side2", c1)
        (repo / "side2.py").write_text("x\n")
        git(repo, "add", "side2.py")
        git(repo, "commit", "-q", "-m", "side2")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "side2", "-m", "merge")
        merge = git(repo, "rev-parse", "HEAD")
        code, r = self.run_tool(repo, merge, merge)
        self.assertEqual(code, 3)
        self.assertIn("exactly one parent", r["reason"])
        self.assertEqual(self.phases(r), [])

    def test_clean_evidence_needs_status_and_diff_both_empty(self):
        class Fake:
            sha = "0" * 40

            def __init__(self, status_code, status, diff_code):
                self.codes = {"status": (status_code, status), "diff": (diff_code, b"")}

            def prepare(self):
                return {}

            def out(self, *args, envx=None):
                return self.codes[args[0]]

        self.assertTrue(mc.clean_evidence(Fake(0, b"", 0))["clean"])
        self.assertFalse(mc.clean_evidence(Fake(0, b"", 1))["clean"], "status empty but diff not")
        self.assertFalse(mc.clean_evidence(Fake(0, b" M x\n", 0))["clean"])
        self.assertFalse(mc.clean_evidence(Fake(128, b"", 0))["clean"])

    def test_restore_removes_untracked_directories_and_ignored_files(self):
        repo, _, c2 = self.make_repo(check_src=CLEAN_SRC, base_files={".gitignore": b"*.log\n"})
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_binary_changes_are_reverted(self):
        repo, _, c2 = self.make_repo(
            check_src=BINARY_SRC, base_files={"data.bin": b"\x00\x01"}, fix_files={"data.bin": b"\x00\x02"}
        )
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertEqual(r["reverted"]["files"], ["calc.py", "data.bin"])
        self.assertIs(r["red_after_revert"], True)

    def test_hostile_diff_drivers_in_the_source_do_not_change_the_patch(self):
        sentinel = self.tmp / "DIFFRAN"
        drv = self.tmp / "drv.sh"
        drv.write_text(f"#!/bin/sh\necho ran >> '{sentinel}'\ncat \"$1\"\n")
        drv.chmod(0o755)

        def attrs(repo):
            (repo / ".gitattributes").write_text("*.py diff=evil\n")

        repo, _, c2 = self.make_repo(setup=attrs)
        git(repo, "config", "diff.evil.textconv", str(drv))
        git(repo, "config", "diff.external", str(drv))
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
