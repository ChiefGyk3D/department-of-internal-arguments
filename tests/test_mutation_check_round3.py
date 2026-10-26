"""scripts/mutation_check.py, round 3: degraded isolation never yields `bites`, the private trusted repository cannot
be used to suppress or forge a verdict, `--clean-old` deletes only what the tool made, big source files are hashed
whole, crash and launcher exit codes are never red, storage is checked before copying, and the patch file is read
through one descriptor. Each test is meant to go red when its guard is removed."""

import contextlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_check as mc  # noqa: E402  (path set up above)
from test_mutation_check import SCRIPT, TEST_SRC, Base, host_has_pid_namespace  # noqa: E402

# Run 1 attacks the private trusted repository: it lists the fixed parent and damages what it finds.
ATTACK_HEAD = (
    TEST_SRC
    + r"""m = os.path.join(os.path.dirname(os.getcwd()), 'marker')
if not os.path.exists(m):
    open(m, 'w').close()
    priv = '@PRIV@'
    import ctypes
    ctypes.CDLL(None).umount2(priv.encode(), 2)  # try to uncover it
    for d in os.listdir(priv):
        p = os.path.join(priv, d)
        if not os.path.isdir(p):
            continue
        base = os.path.join(p, 'base.git')
"""
)
ATTACK_BODIES = {
    "chmod": "        if os.path.isdir(base):\n            os.chmod(base, 0)\n",
    "rewrite": (
        "        for dp, dn, fn in os.walk(os.path.join(base, 'objects')):\n"
        "            for f in fn:\n"
        "                fp = os.path.join(dp, f)\n"
        "                os.chmod(fp, 0o644)\n"
        "                open(fp, 'wb').write(b'garbage')\n"
    ),
    "delete": "        import shutil\n        shutil.rmtree(base, ignore_errors=True)\n",
    # git never notices these, only the guard's digest does
    "plant": (
        "        os.makedirs(os.path.join(base, 'objects', 'info'), exist_ok=True)\n"
        "        open(os.path.join(base, 'objects', 'info', 'extra'), 'w').write('x')\n"
    ),
    "dirlink": "        os.symlink('/', os.path.join(base, 'objects', 'evil'))\n",
    "fifo": "        os.mkfifo(os.path.join(base, 'objects', 'pipe'))\n",
    "homefile": "        open(os.path.join(p, 'home', 'evil'), 'w').write('x')\n",
    "tmplink": "        os.symlink('/', os.path.join(p, 'tmp', 'evil'))\n",
}
# The write of the large file happens in runs 1 and 3: one byte flipped, same length, timestamps restored.
BIGWRITE_SRC = (
    TEST_SRC
    + r"""p = '@SRC@/big.bin'
st = os.stat(p)
data = bytearray(open(p, 'rb').read())
data[-1] = 1
open(p, 'wb').write(data)
os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
"""
)
# Run 2 (after the revert, marker present, f() != 2) dies by a signal instead of failing.
CRASH_RUN2_SRC = (
    "import os, sys, signal\n"
    "sys.path.insert(0, os.getcwd())\n"
    "m = os.path.join(os.path.dirname(os.getcwd()), 'marker')\n"
    "from calc import f\n"
    "if os.path.exists(m):\n"
    "    if f() != 2:\n"
    "        os.kill(os.getpid(), signal.SIGSEGV)\n"
    "else:\n"
    "    assert f() == 2\n"
    "    open(m, 'w').close()\n"
)


def probe_hide():
    """Independent of the tool: can this host bind-mount over a directory inside a user namespace and drop the
    capabilities afterwards?"""
    exe = shutil.which("unshare")
    if not exe or not host_has_pid_namespace():
        return False
    code = (
        "import ctypes, sys\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "sys.exit(0 if libc.mount(sys.argv[1].encode(), sys.argv[1].encode(), None, 4096, None) == 0 else 1)\n"
    )
    argv = [exe, "--user", "--map-current-user", "--keep-caps", "--net", "--pid", "--fork", "--kill-child"]
    argv += ["--mount-proc", "--", sys.executable, "-I", "-c", code, str(ROOT)]
    return subprocess.run(argv, capture_output=True).returncode == 0


class Degraded(Base):
    def bin_without_unshare(self):
        b = self.tmp / "bin"
        b.mkdir(exist_ok=True)
        for n in ("git", "python3"):
            if not (b / n).exists():
                (b / n).symlink_to(shutil.which(n))
        return str(b)

    def test_missing_unshare_is_inconclusive_by_default(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, env={"PATH": self.bin_without_unshare()}, strict=True)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        self.assertIn("isolation", r["reason"])
        self.assertIs(r["isolation_degraded"], True)
        self.assertEqual(self.phases(r), [], "no test may run when the isolation cannot be had")
        self.assertNotIn("clone", [c["phase"] for c in r["commands"]])

    def test_missing_unshare_bites_only_with_the_opt_in_and_says_so(self):
        repo, _, c2 = self.make_repo()
        env = {"PATH": self.bin_without_unshare()}
        code, r = self.run_tool(repo, c2, c2, env=env, strict=True, extra=["--allow-degraded-isolation"])
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertIs(r["isolation_degraded"], True)
        self.assertEqual((r["network_isolation"], r["process_isolation"]), ("none", "none"))
        self.assertIn("source_unchanged_at_check", r)

    def test_each_requested_degraded_mode_needs_the_opt_in(self):
        repo, _, c2 = self.make_repo()
        for mode in ("net", "none"):
            code, r = self.run_tool(repo, c2, c2, strict=True, extra=["--isolation", mode])
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), mode)
            self.assertIn("--allow-degraded-isolation", r["reason"], mode)
            self.assertEqual(self.phases(r), [], mode)
            code, r = self.run_tool(
                repo, c2, c2, strict=True, extra=["--isolation", mode, "--allow-degraded-isolation"]
            )
            self.assertEqual((code, r["verdict"]), (0, "bites"), (mode, r["reason"]))
            self.assertIs(r["isolation_degraded"], True, mode)

    @unittest.skipUnless(host_has_pid_namespace(), "this host cannot run a PID namespace")
    def test_full_isolation_is_not_degraded(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, strict=True)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertIs(r["isolation_degraded"], False)

    def test_require_no_network_is_still_inconclusive_with_the_opt_in(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(
            repo,
            c2,
            c2,
            strict=True,
            extra=["--isolation", "none", "--allow-degraded-isolation", "--require-no-network"],
        )
        self.assertEqual(code, 3)
        self.assertIn("--require-no-network", r["reason"])


class PrivateRepository(Base):
    def attack(self, name, extra=()):
        src = ATTACK_HEAD + ATTACK_BODIES[name]
        repo, _, c2 = self.make_repo(name=f"src-{name}-{len(extra)}", check_src=src)
        return self.run_tool(repo, c2, c2, extra=list(extra))

    @unittest.skipUnless(probe_hide(), "this host cannot bind-mount inside a user namespace")
    def test_the_private_parent_is_invisible_to_the_test(self):
        for name in ATTACK_BODIES:
            code, r = self.attack(name)
            self.assertEqual((code, r["verdict"]), (0, "bites"), (name, r["reason"]))
            self.assertIs(r["isolation"]["private_dir_hidden"], True)

    def test_damage_to_the_private_repository_is_detected_when_it_is_not_hidden(self):
        """With hiding off the test can reach the repository; it can suppress a verdict but never forge a bite."""
        for name in ATTACK_BODIES:
            code, r = self.attack(name, extra=["--hide-private", "off"])
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), (name, r["reason"]))
            self.assertIs(r["isolation"]["private_dir_hidden"], False)
            self.assertIn("private", r["reason"], name)

    def test_untouched_private_repository_passes_the_check_with_hiding_off(self):
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--hide-private", "off"])
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])

    def test_links_and_special_files_are_refused_and_never_opened(self):
        base = self.tmp / "s.git"
        (base / "objects").mkdir(parents=True)
        (base / "objects" / "link").symlink_to("/")
        with self.assertRaises(mc.Inconclusive):
            mc.StoreGuard(str(base))
        (base / "objects" / "link").unlink()
        os.mkfifo(base / "objects" / "pipe")
        out = []

        def build():
            try:
                mc.StoreGuard(str(base))
            except mc.Inconclusive as e:
                out.append(e)

        t = threading.Thread(target=build, daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive(), "the guard opened a FIFO")
        self.assertEqual(len(out), 1)

    def test_every_trusted_step_asks_the_guard_first(self):
        calls = []

        class Guard:
            def quick(self):
                calls.append("quick")
                raise mc.Inconclusive("guard says no")

        class Git:
            def run(self, *a, **k):
                calls.append("git")

        t = mc.Trusted(Git(), str(self.tmp), str(self.tmp), "/x", "0" * 40, Guard())
        with self.assertRaises(mc.Inconclusive) as cm:
            t.prepare()
        self.assertEqual((calls, str(cm.exception)), (["quick"], "guard says no"))

    def test_quick_check_sees_mode_and_identity_changes(self):
        base = self.tmp / "q.git"
        (base / "objects").mkdir(parents=True)
        guard = mc.StoreGuard(str(base))
        guard.quick()
        os.chmod(base, 0o500)
        self.addCleanup(os.chmod, base, 0o700)
        with self.assertRaises(mc.Inconclusive):
            guard.quick()

    def test_the_guard_reports_a_changed_store_and_mode(self):
        base = self.tmp / "base.git"
        (base / "objects").mkdir(parents=True)
        (base / "objects" / "obj").write_text("one")
        guard = mc.StoreGuard(str(base))
        guard.verify()  # negative case: nothing changed
        (base / "objects" / "obj").write_text("two")
        with self.assertRaises(mc.Inconclusive):
            guard.verify()
        (base / "objects" / "obj").write_text("one")
        guard.verify()
        os.chmod(base, 0)
        self.addCleanup(os.chmod, base, 0o700)
        with self.assertRaises(mc.Inconclusive):
            guard.verify()


class InProc(Base):
    def inproc(self, repo, c2, *extra):
        argv = ["--repo", str(repo), f"--head={c2}", f"--revert={c2}", "--test", "python3 tests/check.py"]
        argv += ["--scratch", str(self.scratch), "--private-parent", str(self.tmp / "priv"), "--settle", "0.2"]
        argv += list(extra)
        if not host_has_pid_namespace():
            argv.append("--allow-degraded-isolation")
        out = io.StringIO()
        saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = mc.main(argv)
        finally:
            restored = {s: signal.getsignal(s) for s in saved}
            for s, h in saved.items():
                signal.signal(s, h)
        self.assertEqual(restored, saved, "main() must restore the caller's signal handlers")
        return code, json.loads(out.getvalue())

    def no_copy_made(self, r):
        self.assertNotIn("clone", [c["phase"] for c in r["commands"]])
        self.assertIsNone(r.get("work_dir"))
        priv = self.tmp / "priv"
        self.assertEqual([p.name for p in priv.glob("doia-mc-private-*")] if priv.exists() else [], [])


class HiddenChecks(InProc):
    @unittest.skipUnless(probe_hide(), "this host cannot bind-mount inside a user namespace")
    def test_the_hiding_is_rechecked_before_every_run(self):
        repo, _, c2 = self.make_repo()
        code, r = self.inproc(repo, c2)
        self.assertEqual((code, r["verdict"]), (0, "bites"), r["reason"])
        self.assertEqual([c["phase"] for c in r["commands"] if c["phase"] == "hide-check"], ["hide-check"] * 3)

    @unittest.skipUnless(probe_hide(), "this host cannot bind-mount inside a user namespace")
    def test_a_failed_recheck_is_inconclusive(self):
        repo, _, c2 = self.make_repo()
        real = mc.run
        seen = []

        def flaky(argv, **kw):
            if any("HIDECHECK" in str(a) for a in argv):
                seen.append(1)
                if len(seen) == 2:
                    return mc.Result("exited", 1, 0.0, "listing the private parent found entries", 0)
            return real(argv, **kw)

        with mock.patch.object(mc, "run", side_effect=flaky):
            code, r = self.inproc(repo, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("private", r["reason"])

    def test_the_mount_source_is_made_in_the_private_root_not_in_the_work_dir(self):
        repo, _, c2 = self.make_repo()
        code, r = self.inproc(repo, c2, "--keep")
        self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)
        src = r["trusted"]["hide_source"]
        self.assertEqual(os.path.dirname(src), r["trusted"]["private_dir"])
        self.assertTrue(os.path.basename(src).startswith("empty-"))
        self.assertFalse(mc.under(os.path.realpath(src), os.path.realpath(r["work_dir"])))


def run_helper(*argv, prelude=""):
    return subprocess.run(
        [sys.executable, "-I", "-c", prelude + mc.INIT_HELPER, *argv], capture_output=True, text=True, timeout=60
    )


STUB = (
    "import ctypes\n"
    "class F:\n"
    "    def mount(self, *a): return -1 if 'mount' in FAIL else 0\n"
    "    def prctl(self, op, *a): return -1 if ('bound' in FAIL and op == 24) else 0\n"
    "    def capset(self, *a): return -1 if 'capset' in FAIL else 0\n"
    "ctypes.CDLL = lambda *a, **k: F()\n"
    "import builtins, io\n"
    "_open = builtins.open\n"
    "def _fake_open(path, *a, **k):\n"
    "    if path == '/proc/self/status':\n"
    "        return io.StringIO(CAPS)\n"
    "    return _open(path, *a, **k)\n"
    "builtins.open = _fake_open\n"
)
ZERO = "'CapInh:\\t0\\nCapPrm:\\t0\\nCapEff:\\t0\\nCapBnd:\\t0\\nCapAmb:\\t0\\n'"


def stub(fail=(), caps=ZERO):
    return STUB.replace("FAIL", repr(set(fail))).replace("CAPS", caps)


class Helper(Base):
    """The PID 1 shim's mount-and-drop-capabilities step, run outside a namespace with a stubbed libc."""

    def setUp(self):
        super().setUp()
        self.empty = self.tmp / "empty-src"
        self.empty.mkdir(mode=0o700)
        self.target = self.tmp / "target"
        self.target.mkdir()

    def test_a_real_mount_failure_exits_126_never_a_red_code(self):
        p = run_helper(str(self.empty), str(self.target), "--", "true")
        self.assertEqual(p.returncode, 126, p.stderr)
        p = run_helper(str(self.empty), str(self.target), "--", "true", prelude=stub(["mount"]))
        self.assertEqual(p.returncode, 126, p.stderr)

    def test_each_failing_libc_call_is_fatal_and_all_succeeding_goes_on(self):
        for call in ("bound", "capset"):
            p = run_helper(str(self.empty), str(self.target), "--", "true", prelude=stub([call]))
            self.assertEqual(p.returncode, 126, (call, p.stderr))
        p = run_helper(str(self.empty), str(self.target), "--", "true", prelude=stub())
        self.assertEqual(p.returncode, 0, p.stderr)  # negative case: every call succeeds, all sets zero

    def test_capabilities_that_are_not_zero_are_fatal_even_when_every_call_succeeds(self):
        for line in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            caps = (
                "'"
                + "".join(
                    f"{k}:\\t{'1' if k == line else '0'}\\n" for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
                )
                + "'"
            )
            p = run_helper(str(self.empty), str(self.target), "--", "true", prelude=stub(caps=caps))
            self.assertEqual(p.returncode, 126, (line, p.stderr))

    def test_a_non_empty_or_symlinked_mount_source_is_refused(self):
        (self.empty / "planted").write_text("x")
        p = run_helper(str(self.empty), str(self.target), "--", "true", prelude=stub())
        self.assertEqual(p.returncode, 126, p.stderr)
        link = self.tmp / "linked-src"
        link.symlink_to(self.tmp / "target")
        p = run_helper(str(link), str(self.target), "--", "true", prelude=stub())
        self.assertEqual(p.returncode, 126, p.stderr)

    def test_without_a_target_the_shim_only_forwards_the_exit_status(self):
        p = run_helper("-", "-", "--", sys.executable, "-c", "raise SystemExit(7)")
        self.assertEqual(p.returncode, 7)


class Pruning(Base):
    def setUp(self):
        super().setUp()
        self.priv = self.tmp / "priv"
        self.priv.mkdir(mode=0o700)
        self.scratch.mkdir(mode=0o700)
        self.token = mc.get_token(str(self.priv))

    def made(self, name, age_days=10, content=None, mode=0o600, kind="file"):
        d = self.scratch / name
        d.mkdir()
        (d / "payload.txt").write_text("evidence")
        m = d / mc.MARKER
        text = f"{self.token}\n{name}\n" if content is None else content
        if kind == "file":
            m.write_text(text)
            m.chmod(mode)
        elif kind == "fifo":
            os.mkfifo(m)
        elif kind == "dir":
            m.mkdir()
        elif kind == "symlink":
            real = self.tmp / f"real-marker-{name}"
            real.write_text(text)
            real.chmod(0o600)
            m.symlink_to(real)
            old = time.time() - age_days * 86400
            os.utime(real, (old, old))
        if kind != "symlink" and kind != "fifo" and kind != "dir":
            old = time.time() - age_days * 86400
            os.utime(m, (old, old))
        return d

    def test_only_directories_with_an_authenticated_marker_are_pruned(self):
        old = self.made("doia-mc-old")
        recent = self.made("doia-mc-recent", age_days=1)
        spoofs = {
            "touch": self.made("doia-mc-touch", content=""),
            "wrongtoken": self.made("doia-mc-wrongtoken", content=f"{'0' * 64}\ndoia-mc-wrongtoken\n"),
            "copied": self.made("doia-mc-copied", content=f"{self.token}\ndoia-mc-old\n"),
            "mode": self.made("doia-mc-mode", mode=0o644),
            "fifo": self.made("doia-mc-fifo", kind="fifo"),
            "dir": self.made("doia-mc-dirmarker", kind="dir"),
            "symlink": self.made("doia-mc-linkmarker", kind="symlink"),
            "noprefix": self.made("keepme"),
        }
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "precious.txt").write_text("x")
        marker = victim / mc.MARKER  # a perfectly valid, old marker for the name the symlink has
        marker.write_text(f"{self.token}\ndoia-mc-linkdir\n")
        marker.chmod(0o600)
        os.utime(marker, (1, 1))
        (self.scratch / "doia-mc-linkdir").symlink_to(victim)
        pruned = mc.prune_old(str(self.scratch), 7, self.token)
        self.assertEqual(pruned, ["doia-mc-old"])
        self.assertFalse(old.exists())
        self.assertTrue((recent / "payload.txt").exists())
        for kind, d in spoofs.items():
            self.assertTrue((d / "payload.txt").exists(), f"{kind}: a spoofed marker got a directory deleted")
        self.assertTrue((victim / "precious.txt").exists())
        self.assertTrue((self.scratch / "doia-mc-linkdir").is_symlink())

    def test_marker_validation_by_fields(self):
        name, token = "doia-mc-x", "t" * 64
        good = os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, os.getuid(), 0, 10, 0, 100, 0))
        content = f"{token}\n{name}\n".encode()
        cutoff = 1000.0
        self.assertTrue(mc.validate_marker(good, content, name, token, cutoff))
        bad = {
            "owner": os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, os.getuid() + 1, 0, 10, 0, 100, 0)),
            "mode": os.stat_result((stat.S_IFREG | 0o644, 1, 1, 1, os.getuid(), 0, 10, 0, 100, 0)),
            "fifo": os.stat_result((stat.S_IFIFO | 0o600, 1, 1, 1, os.getuid(), 0, 10, 0, 100, 0)),
            "dir": os.stat_result((stat.S_IFDIR | 0o600, 1, 1, 1, os.getuid(), 0, 10, 0, 100, 0)),
            "recent": os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, os.getuid(), 0, 10, 0, 2000, 0)),
        }
        for label, st in bad.items():
            self.assertFalse(mc.validate_marker(st, content, name, token, cutoff), label)
        self.assertFalse(mc.validate_marker(good, content, "doia-mc-y", token, cutoff), "name")
        self.assertFalse(mc.validate_marker(good, b"nope", name, token, cutoff), "content")

    def test_candidate_swapped_between_validation_and_deletion_is_not_followed(self):
        victim = self.made("doia-mc-old")
        other = self.tmp / "other"
        other.mkdir()
        (other / "innocent.txt").write_text("keep me")
        moved = self.tmp / "moved"

        def swap(name):
            os.rename(self.scratch / name, moved)
            os.rename(other, self.scratch / name)

        self.addCleanup(setattr, mc, "_PRUNE_HOOK", None)
        mc._PRUNE_HOOK = swap
        mc.prune_old(str(self.scratch), 7, self.token)
        self.assertEqual((victim / "innocent.txt").read_text(), "keep me", "the swapped-in directory was deleted")

    def test_a_swapped_in_empty_directory_is_not_removed_either(self):
        self.made("doia-mc-old")
        other = self.tmp / "other-empty"
        other.mkdir()
        moved = self.tmp / "moved2"

        def swap(name):
            os.rename(self.scratch / name, moved)
            os.rename(other, self.scratch / name)

        self.addCleanup(setattr, mc, "_PRUNE_HOOK", None)
        mc._PRUNE_HOOK = swap
        self.assertEqual(mc.prune_old(str(self.scratch), 7, self.token), [])
        self.assertTrue((self.scratch / "doia-mc-old").is_dir(), "the swapped-in directory was rmdir-ed")

    @unittest.skipUnless(os.path.isdir("/dev/shm"), "no /dev/shm to act as a foreign device")
    def test_a_mount_point_inside_a_candidate_is_refused(self):
        home_dev = os.stat(self.tmp).st_dev
        shm = os.open("/dev/shm", os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, shm)
        if os.fstat(shm).st_dev == home_dev:
            self.skipTest("/dev/shm is on the same device")
        d = self.made("doia-mc-nested")
        cfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, cfd)
        with mock.patch.object(mc.os, "fwalk", return_value=iter([(".", [], [], shm)])):
            with self.assertRaises(OSError):
                mc.refuse_foreign_device(cfd, home_dev)
        mc.refuse_foreign_device(cfd, os.fstat(cfd).st_dev)  # negative case: same device passes

    def test_the_device_check_gates_every_deletion(self):
        d = self.made("doia-mc-mnt")
        with mock.patch.object(mc, "refuse_foreign_device", side_effect=OSError("mount point")) as check:
            pruned = mc.prune_old(str(self.scratch), 7, self.token)
        self.assertEqual((pruned, check.call_count), ([], 1))
        self.assertTrue((d / "payload.txt").exists())
        self.assertTrue(mc.is_mount_boundary(os.stat_result((0,) * 10), os.stat_result((0, 0, 1) + (0,) * 7)))
        self.assertFalse(mc.is_mount_boundary(os.stat_result((0,) * 10), os.stat_result((0,) * 10)))

    def test_a_fifo_named_like_a_candidate_does_not_hang_the_pruner(self):
        os.mkfifo(self.scratch / "doia-mc-fifo-top")
        out = []
        t = threading.Thread(target=lambda: out.append(mc.prune_old(str(self.scratch), 7, self.token)), daemon=True)
        t.start()
        t.join(10)
        self.assertFalse(t.is_alive(), "opening a FIFO candidate blocked")
        self.assertEqual(out, [[]])

    def test_a_candidate_swapped_between_the_lstat_and_the_open_is_not_followed(self):
        self.made("doia-mc-old")
        other = self.made("doia-mc-other-real")
        moved = self.tmp / "moved3"
        # `other` carries a perfectly valid marker for the name it will be renamed to
        for f in (other / mc.MARKER,):
            f.write_text(f"{self.token}\ndoia-mc-old\n")
            f.chmod(0o600)
            os.utime(f, (1, 1))

        def swap(name):
            if name == "doia-mc-old":
                os.rename(self.scratch / name, moved)
                os.rename(other, self.scratch / name)

        self.addCleanup(setattr, mc, "_PRE_OPEN_HOOK", None)
        mc._PRE_OPEN_HOOK = swap
        mc.prune_old(str(self.scratch), 7, self.token)
        self.assertTrue((self.scratch / "doia-mc-old" / "payload.txt").exists(), "the swapped-in directory was deleted")

    def test_deletion_never_resolves_a_multi_component_path(self):
        d = self.made("doia-mc-old")
        (d / "a" / "b" / "c").mkdir(parents=True)
        (d / "a" / "b" / "c" / "f").write_text("x")
        (d / "a" / "link").symlink_to(self.tmp)
        (d / "a" / "b" / "c").chmod(0o500)  # read-only directory: removal needs the descriptor's chmod first
        self.addCleanup(lambda: [p.chmod(0o700) for p in d.rglob("*") if p.is_dir() and not p.is_symlink()])
        calls = []
        real_rmdir, real_unlink = os.rmdir, os.unlink

        def rmdir(p, *a, **k):
            calls.append(("rmdir", str(p), k.get("dir_fd")))
            return real_rmdir(p, *a, **k)

        def unlink(p, *a, **k):
            calls.append(("unlink", str(p), k.get("dir_fd")))
            return real_unlink(p, *a, **k)

        with (
            mock.patch.object(mc.os, "rmdir", side_effect=rmdir),
            mock.patch.object(mc.os, "unlink", side_effect=unlink),
        ):
            pruned = mc.prune_old(str(self.scratch), 7, self.token)
        self.assertEqual(pruned, ["doia-mc-old"])
        self.assertGreaterEqual(len(calls), 6)
        for kind, p, dir_fd in calls:
            self.assertNotIn("/", p, f"{kind} resolved a multi-component path")
            self.assertIsNotNone(dir_fd, f"{kind} {p} was not descriptor-relative")
        self.assertTrue(self.tmp.exists(), "the symlink target was followed")

    def test_write_marker_never_overwrites_or_follows(self):
        d = self.tmp / "doia-mc-w"
        d.mkdir()
        (d / mc.MARKER).write_text("precious")
        with self.assertRaises(FileExistsError):
            mc.write_marker(str(d), self.token)
        self.assertEqual((d / mc.MARKER).read_text(), "precious")
        (d / mc.MARKER).unlink()
        target = self.tmp / "elsewhere-marker"
        target.write_text("precious")
        (d / mc.MARKER).symlink_to(target)
        with self.assertRaises(FileExistsError):
            mc.write_marker(str(d), self.token)
        self.assertEqual(target.read_text(), "precious")

    def test_token_file_must_be_ours_and_look_like_a_token(self):
        real = os.fstat

        def foreign(fd):
            r = real(fd)
            f = list(r)
            f[4] = r.st_uid + 1
            return os.stat_result(f)

        with mock.patch.object(mc.os, "fstat", side_effect=foreign):
            with self.assertRaises(mc.Inconclusive):
                mc.get_token(str(self.priv))
        (self.priv / ".token").write_text("not a token")
        with self.assertRaises(mc.Inconclusive):
            mc.get_token(str(self.priv))

    def test_token_file_is_private_and_not_followed(self):
        tok = self.priv / ".token"
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        self.assertEqual(mc.get_token(str(self.priv)), self.token, "stable across calls")
        tok.chmod(0o644)
        with self.assertRaises(mc.Inconclusive):
            mc.get_token(str(self.priv))
        tok.unlink()
        real = self.tmp / "elsewhere"
        real.write_text("a" * 64)
        real.chmod(0o600)
        tok.symlink_to(real)
        with self.assertRaises(mc.Inconclusive):
            mc.get_token(str(self.priv))

    def test_clean_old_zero_disables_pruning(self):
        old = self.made("doia-mc-old")
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2, extra=["--clean-old", "0"])
        self.assertEqual((code, r["pruned"]), (0, []))
        self.assertTrue((old / "payload.txt").exists())

    def test_the_tool_prunes_with_the_authenticated_marker_it_wrote(self):
        old = self.made("doia-mc-old")
        spoof = self.made("doia-mc-spoof", content="")
        repo, _, c2 = self.make_repo()
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual(code, 0, r["reason"])
        self.assertEqual(r["pruned"], ["doia-mc-old"])
        self.assertFalse(old.exists())
        self.assertTrue((spoof / "payload.txt").exists())
        code, r = self.run_tool(repo, c2, c2, extra=["--keep"])
        self.addCleanup(shutil.rmtree, r["work_dir"], ignore_errors=True)
        marker = Path(r["work_dir"]) / mc.MARKER
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        self.assertEqual(marker.read_text(), f"{self.token}\n{os.path.basename(r['work_dir'])}\n")


class SourceHashing(Base):
    def test_a_65_mib_equal_length_rewrite_with_restored_times_is_detected(self):
        repo, _, c2 = self.make_repo(check_src=BIGWRITE_SRC)
        (repo / "big.bin").write_bytes(b"\0" * (65 * 1024 * 1024))
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        unchanged = r.get("source_repo_unchanged", r.get("source_unchanged_at_check"))
        self.assertIs(unchanged, False)

    def test_the_hashing_budget_is_enforced(self):
        repo, _, c2 = self.make_repo()
        (repo / "dirty.bin").write_bytes(b"x" * 4096)
        code, r = self.run_tool(repo, c2, c2, extra=["--max-source-bytes", "1000"])
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("budget", r["reason"])
        code, r = self.run_tool(repo, c2, c2, extra=["--max-source-bytes", "100000"])
        self.assertEqual(code, 0, r["reason"])  # negative case: inside the budget

    def test_hash_file_reads_every_byte_of_a_large_file(self):
        p = self.tmp / "large"
        with open(p, "wb") as f:
            f.truncate(mc.MAX_HASH_FILE_REMOVED if hasattr(mc, "MAX_HASH_FILE_REMOVED") else 70 * 1024 * 1024)
        a = mc.hash_file(str(p), mc.Budget(10**12))
        st = os.stat(p)
        with open(p, "r+b") as f:
            f.seek(-1, 2)
            f.write(b"\1")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotEqual(a, mc.hash_file(str(p), mc.Budget(10**12)))


class ExitCodes(Base):
    def test_signal_deaths_are_crashed_never_red(self):
        repo, _, c2 = self.make_repo()
        for sig in ("SIGSEGV", "SIGABRT", "SIGKILL"):
            test = f'python3 -c "import os, signal; os.kill(os.getpid(), signal.{sig})"'
            code, r = self.run_tool(repo, c2, c2, test=test, extra=["--red-exit", "1"])
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), sig)
            self.assertEqual(r["commands"][-1]["result"], "crashed", sig)
            self.assertIn("crash", r["reason"], sig)

    def test_a_crash_after_the_revert_is_inconclusive(self):
        repo, _, c2 = self.make_repo(check_src=CRASH_RUN2_SRC)
        code, r = self.run_tool(repo, c2, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"), r["reason"])
        last = [c for c in r["commands"] if c["phase"] == "test-after-revert"][0]
        self.assertEqual(last["result"], "crashed")
        self.assertIsNone(r["red_after_revert"])

    def test_launcher_and_signal_codes_cannot_be_named_red(self):
        base = ["--repo", "x", "--head", "h", "--revert", "r", "--test", "t"]
        for bad in ("126", "127", "128", "134", "137", "139", "159", "255", "-11", "-1", "1,139", "256"):
            with self.assertRaises(mc.Inconclusive, msg=bad):
                mc.parse([*base, f"--red-exit={bad}"])
        self.assertEqual(mc.parse([*base, "--red-exit=1,2,125"]).red_exit_codes, {1, 2, 125})


class Storage(InProc):
    def test_too_little_free_space_is_inconclusive_before_any_copy(self):
        repo, _, c2 = self.make_repo()
        with mock.patch.object(mc, "free_bytes", return_value=10 * 1024 * 1024):
            code, r = self.inproc(repo, c2)
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("free", r["reason"])
        self.assertRegex(r["reason"], r"\d+")
        self.no_copy_made(r)

    def test_estimate_above_max_copy_bytes_is_inconclusive_before_any_copy(self):
        repo, _, c2 = self.make_repo()
        with mock.patch.object(mc, "estimate_copy_bytes", return_value=10**15):
            code, r = self.inproc(repo, c2, "--max-copy-bytes", "1000000")
        self.assertEqual((code, r["verdict"]), (3, "inconclusive"))
        self.assertIn("--max-copy-bytes", r["reason"])
        self.no_copy_made(r)

    def test_free_space_requirement_is_two_and_a_half_times_the_estimate_plus_a_margin(self):
        need = mc.required_free(1000)
        self.assertEqual(need, int(2.5 * 1000) + mc.COPY_MARGIN)
        repo, _, c2 = self.make_repo()
        est = 100 * 1024 * 1024
        with mock.patch.object(mc, "estimate_copy_bytes", return_value=est):
            with mock.patch.object(mc, "free_bytes", return_value=mc.required_free(est) - 1):
                code, r = self.inproc(repo, c2)
            self.assertEqual(code, 3, "one byte short must be refused")
            self.assertIn(str(est), r["reason"])
            with mock.patch.object(mc, "free_bytes", return_value=mc.required_free(est)):
                code, r = self.inproc(repo, c2)
            self.assertEqual(code, 0, r["reason"])  # negative case: exactly enough

    def test_enospc_during_the_copy_cleans_up_and_is_inconclusive(self):
        repo, _, c2 = self.make_repo()
        real = mc.run
        for needle in ("--no-checkout", "--bare"):

            def failing(argv, needle=needle, **kw):
                if "clone" in argv and needle in argv:
                    return mc.Result("exited", 128, 0.0, "fatal: write error: No space left on device", 0)
                return real(argv, **kw)

            with mock.patch.object(mc, "run", side_effect=failing):
                code, r = self.inproc(repo, c2)
            self.assertEqual((code, r["verdict"]), (3, "inconclusive"), needle)
            self.assertIn("No space left", r["reason"], needle)
            self.assertEqual(list(self.scratch.glob("doia-mc-*")) if self.scratch.exists() else [], [], needle)
            self.assertEqual(list((self.tmp / "priv").glob("doia-mc-private-*")), [], needle)


class PatchFile(Base):
    def test_exactly_the_limit_is_read_and_one_more_byte_is_refused(self):
        p = self.tmp / "p.patch"
        p.write_bytes(b"x" * mc.MAX_PATCH)
        self.assertEqual(len(mc.read_patch_file(str(p))), mc.MAX_PATCH)
        p.write_bytes(b"x" * (mc.MAX_PATCH + 1))
        with self.assertRaises(mc.Inconclusive):
            mc.read_patch_file(str(p))

    def test_a_file_that_grows_after_the_open_is_still_bounded(self):
        p = self.tmp / "grow.patch"
        p.write_bytes(b"small")

        def grow():
            with open(p, "ab") as f:
                f.write(b"y" * (mc.MAX_PATCH + 10))

        self.addCleanup(setattr, mc, "_AFTER_OPEN_HOOK", None)
        mc._AFTER_OPEN_HOOK = grow
        with self.assertRaises(mc.Inconclusive):
            mc.read_patch_file(str(p))

    def test_stat_size_is_not_trusted(self):
        p = self.tmp / "lie.patch"
        p.write_bytes(b"z" * (mc.MAX_PATCH + 5))
        real = os.fstat

        def lying(fd):
            r = real(fd)
            f = list(r)
            f[6] = 10  # st_size
            return os.stat_result(f)

        with mock.patch.object(mc.os, "fstat", side_effect=lying):
            with self.assertRaises(mc.Inconclusive):
                mc.read_patch_file(str(p))

    def test_the_path_swapped_after_the_open_does_not_change_what_is_read(self):
        p = self.tmp / "swap.patch"
        p.write_bytes(b"original")
        other = self.tmp / "other.patch"
        other.write_bytes(b"swapped-in")

        def swap():
            os.replace(other, p)

        self.addCleanup(setattr, mc, "_AFTER_OPEN_HOOK", None)
        mc._AFTER_OPEN_HOOK = swap
        self.assertEqual(mc.read_patch_file(str(p)), b"original")

    def test_symlink_and_fifo_are_refused_without_blocking(self):
        real = self.tmp / "real.patch"
        real.write_bytes(b"x")
        link = self.tmp / "link.patch"
        link.symlink_to(real)
        with self.assertRaises(mc.Inconclusive):
            mc.read_patch_file(str(link))
        fifo = self.tmp / "fifo.patch"
        os.mkfifo(fifo)
        with self.assertRaises(mc.Inconclusive):
            mc.read_patch_file(str(fifo))


class ExitPaths(Base):
    def test_a_closed_stdout_does_not_change_the_exit_code(self):
        repo, _, c2 = self.make_repo()
        argv = [sys.executable, str(SCRIPT), "--repo", str(repo), f"--head={c2}", f"--revert={c2}"]
        argv += ["--test", "python3 tests/check.py", "--scratch", str(self.scratch), "--settle", "0.2"]
        argv += ["--private-parent", str(self.tmp / "priv")]
        if not host_has_pid_namespace():
            argv.append("--allow-degraded-isolation")
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc.stdout.close()  # the reader goes away before the JSON is printed
        err = proc.stderr.read()
        proc.stderr.close()
        self.assertEqual(proc.wait(), 0, err)
        self.assertNotIn("Traceback", err)

    def test_a_child_that_does_not_read_its_stdin_is_not_an_error(self):
        errors = []
        with mock.patch.object(threading, "excepthook", lambda a: errors.append(a)):
            res = mc.run(
                ["true"], cwd=None, env=mc.sanitized_env("/nonexistent"), timeout=20, cap=1000, stdin=b"x" * (4 << 20)
            )
            time.sleep(0.5)
        self.assertEqual((res.status, res.code), ("exited", 0))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
