"""Prove that a test bites: revert a fix in a disposable clone, expect red, restore, expect green.

    python3 scripts/mutation_check.py --repo PATH --head SHA --revert SHA_OR_PATCH --test "CMD" [--scratch DIR]

Steps, in a clone made with `git clone --local --no-hardlinks` and a detached checkout of --head: run --test at
head (must pass), revert the fix (`git apply -R` of the named commit's non-test hunks, or of a given patch file),
run --test (must fail), restore (`git checkout --force --detach SHA` and `git clean -ffdx`), run --test again (must
pass), then prove the restore: `git status --porcelain` is empty and `git diff SHA` is empty, checked after the
restore and again after the last run. The source repository's status, HEAD, symbolic ref, refs, stash,
.git/config, .git/index and the content of every dirty or untracked file are hashed before the run, after the last
test run, and once more after a settle time (--settle, default 1 s).

Output is one JSON object on stdout: head, base, reverted, commands, green_before, red_after_revert,
green_after_restore, tree_clean (with evidence), the source-unchanged claim (with the hashes), isolation, verdict
and reason. The claim is `source_repo_unchanged` when the run had a PID namespace ("process_isolation":
"pid-namespace": every process the test started died with the run, so the hashes are final) and
`source_unchanged_at_check` when it did not (an escaped process can still change the source later; the claim is
only that nothing had changed when the tool looked, twice, one settle time apart).

Exit codes (usage errors and -h also print JSON and exit 3, so 2 always means "vacuous" and 0 only "bites"):
    0  bites: green, red after the revert, green after the restore, tree clean, source repository unchanged, and
       the run had BOTH a network and a PID namespace. Without them the verdict is inconclusive (exit 3) unless
       --allow-degraded-isolation is given; then exit 0 is the weaker contract and the JSON says
       `isolation_degraded: true` (an escaped process or a network call is possible, see below)
    2  vacuous: the test stays green with the fix reverted (tree clean, source unchanged)
    3  inconclusive: anything else. Not green at head, a revert that does not apply or changes nothing, a test that
       failed with an exit code outside --red-exit, timed out, produced too much output or could not start, an
       unclean tree, a changed source repository, an interrupt (SIGINT, SIGTERM, SIGHUP kill the whole run first),
       an unresolvable argument, any unexpected error. Never a pass.

The copy is deleted on a bites result and kept (its path is in the JSON, "work_dir", and on stderr) on every other
result so evidence is not thrown away; --keep keeps it on bites too. Copies this tool made (a `doia-mc-*`
directory holding a marker file) and left behind are removed after --clean-old days (default 7, 0 turns it off)
when the next run starts; nothing else in the scratch directory is touched and symlinks are never followed.

A test that is stateful (fails only on its second invocation, counts its own runs, depends on the clock or on a
cache the allowlist removed) can be reported `bites` or `inconclusive` for reasons that have nothing to do with
the fix; this tool runs each command three times, it does not detect that, and it is a documented limit (the
documentation pages are issue #44).

What `bites` means. It means the test passed at head, failed with the fix reverted and passed again after the
restore, in a tree the tool measured with git metadata the test never saw, and that the source repository's hashes
did not change. It does not prove that the test is honest: a hostile test can count its own runs, fail only when
it sees the revert, and edit anything in the clone. The source-repository hashes (and the tool's own fresh-metadata
tree check) are the only evidence that does not depend on the test's honesty. Read `commands` before relying on it.

Security. This runs code from the commit under review. This tool is a seatbelt, not a sandbox: run untrusted heads
only as a throwaway user, container or VM. What it does:
  * never uses a shell: every subprocess gets an explicit argument list; --test is split once with shlex into an
    argv. A repository path, a SHA or a patch path is data: `--end-of-options` and absolute paths stop a leading
    "-" from being read as an option.
  * gives every subprocess a short environment: PATH (relative entries dropped, so the clone cannot plant a
    program), LANG, and HOME, XDG_*, PIP_CACHE_DIR, npm_config_cache, TMPDIR and the Python bytecode cache
    pointed into the disposable directory (beside the clone, not inside it, so the tree check stays honest). No
    inherited environment variable is passed. That is all it does about secrets: files the user can read, the
    user's real identity and home directory (pwd still names them), and unix sockets stay reachable.
  * runs the test AND every git step of the tool inside the same namespaces: `unshare --user --map-current-user
    --net --pid --fork --kill-child --mount-proc` (the user maps to itself, not root). Probed by running it; if
    the host refuses it the run may fall back to the network namespace alone, then to nothing (--isolation
    auto|net|none), but such a degraded run is inconclusive unless --allow-degraded-isolation is given, and
    --require-no-network makes "nothing" inconclusive even then. The JSON says which, and
    `isolation_degraded` too: network_isolation is
    "net-namespace" or "none", process_isolation is "pid-namespace" or "none". The claim is network namespace only:
    outgoing TCP and UDP to the host's addresses fail inside; filesystem, unix sockets bound in the filesystem (the
    agent and D-Bus sockets in the user's runtime directory, say) and every readable secret file are not
    isolated. A private /tmp was tried (tmpfs inside the mount namespace); the kernel refuses it for a user mapped
    to itself.
  * with a PID namespace, when the test (the namespace's first process) exits the kernel kills everything that
    was started in it, including daemons that left the process group. Without one, only the process group is
    killed and a process that called setsid survives; that is why the field name changes.
  * never reads git metadata the test could have written. The clone's own .git is used only for the clone and the
    first checkout, before the test ran. Before the test starts the tool makes a private directory (random name,
    0700, under ~/.local/share/doia-mutation-private/, outside the test's work directory, its path never put in the
    test's environment) holding a bare copy of the source's objects. After every test run each tool step (apply,
    checkout, clean, status, diff) gets a new random 0700 bare repository there (own config; objects borrowed from
    the private copy through objects/info/alternates, created O_EXCL and O_NOFOLLOW; own index) and GIT_DIR,
    GIT_WORK_TREE and GIT_INDEX_FILE point at it. A directory that is not new, empty and ours is refused.
    What the trusted steps trust: the tool's own code, the private copy of the objects (made before the test
    ran), git itself, and the worktree FILES (compared with the private objects by hashing them from disk; no
    object written by the test is ever read). What they do not trust: the clone's .git (config, attributes, hooks,
    index, refs, replace refs, grafts, objects), the source repository's object store after the copy, and the
    test's HOME and XDG directories. The trusted steps run with their own environment (an empty HOME and
    XDG_CONFIG_HOME the test never sees, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM=/dev/null, GIT_CONFIG_NOSYSTEM,
    GIT_ATTR_NOSYSTEM, no other GIT_* variable except the three per-step paths) and with core.hooksPath,
    core.fsmonitor, core.pager, core.sshCommand, core.askPass, core.attributesFile, core.excludesFile,
    credential.helper and diff.external overridden and protocol.allow=never. Limits: the private directory is
    reachable by any process of the same user that lists its parent; with a PID namespace none of the test's
    processes exist when the trusted steps run, without one that is a race the tool does not claim to win. A
    .gitignore the test writes inside the worktree can still hide untracked files from `git status`; that is the
    test being dishonest, and the source hashes do not depend on it.
  * reads the source repository with its own configuration, minus hooks, fsmonitor and every filter driver it
    defines (neutralized one by one), and never runs anything the head's .gitattributes could name.
  * hides the private repository from the test: the test runs in a mount namespace where the parent of the
    private directory is covered by a bind mount of an empty directory (made in the private root with a random
    name, opened without following a link, mounted through /proc/self/fd), and every capability set of the test
    (ambient, bounding, effective, permitted, inheritable) is dropped and read back as zero from /proc/self/status
    before it starts, so it can neither list nor unmount it. The shim exits 126 on any failure of this, a status
    no verdict treats as a failing test. `isolation.private_dir_hidden` says whether it is in place;
    `--hide-private off` turns it off, a host that cannot do it reports false, and the listing is re-checked from
    inside the test's namespace before every run. The tool's own steps run outside that mount. The private
    material (the repository and the private home and tmp) is also guarded whether or not it is hidden: identity,
    mode, owner and a digest of every entry and byte are recorded before the test and checked before every trusted
    step and after every run; a symlink or any file that is not a regular file or directory is refused and never
    opened; any change or failure is inconclusive. The same user can still deny a verdict this way (suppression,
    never a forged bite) because the check refuses to continue.
  * authenticates what `--clean-old` deletes: a marker is a regular file (opened O_NOFOLLOW, never blocking),
    owned by the caller, mode 0600, holding a per-installation random token (a 0600 file in the private parent)
    and the directory's own name, so a `touch`ed or copied marker deletes nothing. Candidates are opened relative
    to a pinned directory descriptor, must be on the parent's device with no mount point inside, are emptied
    through their own descriptor, bottom-up, every removal relative to its own parent's descriptor (no multi-component
    path is resolved), and removed only if the name still refers to the same inode.
  * hashes every dirty or untracked source file whole, whatever its size, against a byte budget
    (--max-source-bytes, default 4 GiB; over budget is inconclusive), so an equal-length rewrite with restored
    times is seen.
  * never counts a crash as a failing test: a run killed by a signal (a negative status or 128+N) is `crashed`;
    --red-exit takes 1 to 125 only (0, 126, 127, 128 and above, 255 and negatives are refused); a run that dies by
    signal at any step is inconclusive. The test runs as the namespace's PID 2 under a tiny PID 1 that reports
    128+N for a signal death (a test that was PID 1 would ignore its own fatal signals and hang).
  * checks storage before copying anything: estimate (object store plus tracked files), free space at the scratch
    and private locations at least 2.5 times the estimate plus 64 MiB, and --max-copy-bytes (default 16 GiB);
    otherwise inconclusive with the numbers. A failure mid-copy (no space left, say) removes the partial copies.
  * reads a patch file through one descriptor (O_NOFOLLOW, O_NONBLOCK, fstat on that descriptor, at most 1 MiB
    read whatever the size claims) and never opens the path a second time.
  * puts a timeout, an output cap and a process-group kill on every subprocess (--timeout, --max-output), and
    kills the whole run when it is interrupted.
  * refuses a revert patch (given or generated) that has an absolute path, a ".." component, a ".git"
    component, a quoted name, or a path that goes through a symlink in the clone; refuses a symlinked or
    oversize patch file.
  * never works in the maintainer's checkout: refuses a scratch directory that equals, contains or lies inside
    the repository root. The default scratch is a new directory under ~/.local/share/doia-mutation/, never /tmp.

What cannot be enforced. The test is arbitrary code running as the invoking user. It can write anywhere that user
can write, including the source repository by absolute path, and read anything that user can read. The tool
detects a changed source repository and reports it (exit 3); it cannot prevent or undo it. Where the private
repository is not hidden, the same user can delete or damage it: that denies the verdict (exit 3) but cannot
forge a bite. Without `unshare`
there is no network or process isolation. The isolated environment is not CI: a test that needs a variable or a
cache the allowlist removes can be reported inconclusive. A revert that breaks the import of the module under test
makes the test fail like a real bite does. Standard library only."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

EXIT_BITES, EXIT_VACUOUS, EXIT_INCONCLUSIVE = 0, 2, 3
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
TAIL_BYTES = 4000
MAX_PATCH = 1024 * 1024
OWNER_ONLY = stat.S_IRWXU  # 0700 by name: the permissions are the owner's alone
DEFAULT_MAX_SOURCE = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_COPY = 16 * 1024 * 1024 * 1024
COPY_MARGIN = 64 * 1024 * 1024
# Test hooks (set by the tests only): called between validation and use to inject a race.
_PRUNE_HOOK = None
_AFTER_OPEN_HOOK = None
_PRE_OPEN_HOOK = None
SHIM_FAILURE = 126  # the PID 1 shim's own failure status: outside 1-125, so never a failing test
GIT_TIMEOUT = 300
GIT_CAP = 4 * 1024 * 1024
DEFAULT_SCRATCH = "~/.local/share/doia-mutation"
DEVNULL_PATH = "/dev/null"
# Directories and file names that mark a test; a "non-test hunk" is a hunk in a file that matches none of these.
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
TEST_FILE = re.compile(r"(^test_.*|.*_test\.[A-Za-z0-9]+|.*\.(test|spec)\.[A-Za-z0-9]+|conftest\.py)$")


class Inconclusive(Exception):
    """Anything that stops the check from reaching a clear verdict. Never turned into a pass."""


@dataclass
class Result:
    status: str  # exited | timeout | output-cap | spawn-error
    code: int | None
    seconds: float
    tail: str
    total: int
    data: bytes = b""  # stdout only, whole, when run(keep=True)
    err: str = ""  # tail of stderr, when run(keep=True)


def run(argv, *, cwd, env, timeout, cap, stdin=None, keep=False):
    """Run argv (a list, never a shell) with a timeout, an output cap and a process-group kill.

    By default stdout and stderr are merged and only the last TAIL_BYTES are kept. With keep=True stdout is kept
    whole (it is what the caller parses) and stderr is a separate tail; either way the cap bounds the memory used."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            shell=False,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if keep else subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError) as e:
        return Result("spawn-error", None, 0.0, f"{type(e).__name__}: {e}", 0)

    def kill():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    state = {"total": 0, "over": False}
    tail, whole, err_tail = bytearray(), bytearray(), bytearray()

    def pump(stream, sink, counted, whole_sink=None):
        while True:
            try:
                chunk = stream.read1(65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            sink.extend(chunk)
            if len(sink) > TAIL_BYTES:
                del sink[: len(sink) - TAIL_BYTES]
            if whole_sink is not None and len(whole_sink) <= cap:
                whole_sink.extend(chunk)
            if counted:
                state["total"] += len(chunk)
                if state["total"] > cap and not state["over"]:
                    state["over"] = True
                    kill()

    def feed():
        try:
            proc.stdin.write(stdin)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    readers = [threading.Thread(target=pump, args=(proc.stdout, tail, True, whole if keep else None), daemon=True)]
    if keep:
        readers.append(threading.Thread(target=pump, args=(proc.stderr, err_tail, False), daemon=True))
    for t in readers:
        t.start()
    if stdin is not None:
        threading.Thread(target=feed, daemon=True).start()
    status = "exited"
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "timeout"
        kill()
        proc.wait()
    except BaseException:
        # An interrupt (the tool's signal handler raises) or any other abort: the whole group dies with the check.
        kill()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        raise
    kill()  # stragglers left in the process group
    for t in readers:
        t.join(5)
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream:
                stream.close()
        except OSError:
            pass
    if state["over"] and status == "exited":
        status = "output-cap"
    return Result(
        status,
        proc.returncode,
        round(time.monotonic() - started, 3),
        tail.decode("utf-8", "replace"),
        state["total"],
        bytes(whole),
        err_tail.decode("utf-8", "replace"),
    )


def clean_path():
    """PATH without empty or relative entries (they would resolve inside the clone)."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and os.path.isabs(p)]
    return os.pathsep.join(parts) or "/usr/local/bin:/usr/bin:/bin"


def sanitized_env(home, work=None):
    """The whole environment a subprocess sees. `work` is the disposable directory (caches live inside it)."""
    env = {"PATH": clean_path(), "LANG": os.environ.get("LANG", "C.UTF-8"), "HOME": str(home)}
    if work is not None:
        w = Path(work)
        env.update(
            XDG_CACHE_HOME=str(w / "cache"),
            XDG_CONFIG_HOME=str(w / "config"),
            XDG_DATA_HOME=str(w / "data"),
            PIP_CACHE_DIR=str(w / "pip-cache"),
            npm_config_cache=str(w / "npm-cache"),
            TMPDIR=str(w / "tmp"),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPYCACHEPREFIX=str(w / "pycache"),
            GIT_CONFIG_GLOBAL=DEVNULL_PATH,
            GIT_CONFIG_SYSTEM=DEVNULL_PATH,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
        )
    return env


class Git:
    """Git with hooks, fsmonitor, pager, ssh, askpass, credential helpers and external diff switched off.

    `wrap` is the namespace prefix (see isolation_wrapper) put in front of every command; `strict` adds
    protocol.allow=never (used for every step after the test has run). `env_extra` per call adds GIT_DIR and friends."""

    OVERRIDES = (
        f"core.hooksPath={DEVNULL_PATH}",
        "core.fsmonitor=false",
        "core.pager=cat",
        "core.sshCommand=",
        "core.askPass=",
        f"core.attributesFile={DEVNULL_PATH}",
        f"core.excludesFile={DEVNULL_PATH}",
        "credential.helper=",
        "diff.external=",
    )

    def __init__(self, exe, env, record=None, wrap=(), strict=False):
        self.exe, self.env, self.record, self.wrap, self.strict = exe, env, record, list(wrap), strict
        self.last_err = ""

    def argv(self, *args):
        overrides = self.OVERRIDES + (("protocol.allow=never",) if self.strict else ())
        opts = [x for o in overrides for x in ("-c", o)]
        return [*self.wrap, self.exe, *opts, *args]

    def run(self, cwd, *args, phase=None, stdin=None, ok=(0,), env_extra=None):
        env = {**self.env, **(env_extra or {})}
        res = run(self.argv(*args), cwd=cwd, env=env, timeout=GIT_TIMEOUT, cap=GIT_CAP, stdin=stdin)
        if self.record is not None and phase:
            self.record(phase, self.argv(*args), res)
        if res.status != "exited" or res.code not in ok:
            raise Inconclusive(f"git {args[0]} failed ({res.status}, exit {res.code}): {res.tail[-300:]}")
        return res

    def out(self, cwd, *args, env_extra=None):
        """(exit code, stdout bytes) of a read-only git command, bounded by GIT_TIMEOUT and GIT_CAP."""
        env = {**self.env, **(env_extra or {})}
        res = run(self.argv(*args), cwd=cwd, env=env, timeout=GIT_TIMEOUT, cap=GIT_CAP, keep=True)
        if res.status != "exited":
            raise Inconclusive(f"git {args[-1]} did not finish cleanly ({res.status}): {res.tail[-200:]}")
        self.last_err = res.err[-300:]
        return res.code, res.data


class StoreGuard:
    """Detects damage to the private trusted material. If the test could reach it (same user) it can suppress a
    verdict, but a changed byte, mode, owner, added or missing entry must make the run inconclusive, never a bite.
    Every entry is hashed (type, mode, owner, size and content); a symlink or any file that is not a regular file or
    a directory is refused outright, and is never opened."""

    def __init__(self, base, extra=()):
        self.base, self.roots = base, [base, *extra]
        self.quick_ref = self._quick()
        self.digest = self._digest()

    def _quick(self):
        out = []
        for root, sub in [(self.base, ""), (self.base, "objects"), *[(r, "") for r in self.roots[1:]]]:
            try:
                st = os.lstat(os.path.join(root, sub))
            except OSError as e:
                raise Inconclusive(f"the private trusted repository is not readable ({type(e).__name__})") from e
            out.append((st.st_ino, st.st_mode, st.st_uid))
        return out

    def _digest(self):
        h = hashlib.sha256()

        def visit(path, label):
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode) or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise Inconclusive(f"the private trusted material holds a link or special file: {label}")
            h.update(f"{'D' if stat.S_ISDIR(st.st_mode) else 'F'} {label} {st.st_mode:o} {st.st_uid}\n".encode())
            if stat.S_ISREG(st.st_mode):
                h.update(f"{st.st_size}\n".encode())
                with open(path, "rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        h.update(block)
                return
            for name in sorted(os.listdir(path)):
                visit(os.path.join(path, name), f"{label}/{name}")

        try:
            for root in self.roots:
                visit(root, os.path.basename(root))
        except OSError as e:
            raise Inconclusive(f"the private trusted repository is not readable ({type(e).__name__})") from e
        return h.hexdigest()

    def quick(self):
        if self._quick() != self.quick_ref:
            raise Inconclusive("the private trusted repository changed (mode, owner or identity) while the test ran")

    def verify(self):
        self.quick()
        if self._digest() != self.digest:
            raise Inconclusive("the private trusted repository changed while the test ran")


class Trusted:
    """Git metadata the test never saw and cannot reach. After the test has run, nothing is read from the clone's
    own .git, from the source repository's object store, from the test's HOME or XDG directories, or from any path
    the test could predict.

    Everything lives in `private`, a random 0700 directory the tool made outside the test's work directory before
    the test started; its path is never put in the test's environment. It holds a private object store (a bare
    copy of the source made before the test ran, `objects`) and, for every step, a new random 0700 bare repository
    that borrows those objects through objects/info/alternates (created O_EXCL and O_NOFOLLOW) and has its own index
    inside it. GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE point there. The worktree is the only thing the test
    controls: git compares its files against the private objects by hashing them from disk, and reads nothing
    executable from a worktree."""

    def __init__(self, git, private, clone, objects, sha, guard=None):
        self.git, self.private, self.clone, self.objects, self.sha = git, private, clone, objects, sha
        self.guard = guard

    def prepare(self):
        if self.guard is not None:
            self.guard.quick()
        gd = tempfile.mkdtemp(prefix="g-", dir=self.private)  # random name, created atomically, mode 0700
        st = os.lstat(gd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or os.listdir(gd):
            raise Inconclusive("the trusted git directory is not a fresh empty directory of ours")
        self.git.run(self.private, "init", "--bare", "-q", "--", gd, phase="trusted-init")
        alternates = os.path.join(gd, "objects", "info", "alternates")
        fd = os.open(alternates, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.objects + "\n")
        envx = {"GIT_DIR": gd, "GIT_WORK_TREE": self.clone, "GIT_INDEX_FILE": os.path.join(gd, "index")}
        self.git.run(
            self.clone, "-c", "core.bare=false", "read-tree", self.sha, phase="trusted-read-tree", env_extra=envx
        )
        self.git.run(self.clone, "update-ref", "--no-deref", "HEAD", self.sha, phase="trusted-head", env_extra=envx)
        return envx

    def run(self, *args, phase=None, stdin=None, envx=None):
        return self.git.run(
            self.clone, "-c", "core.bare=false", *args, phase=phase, stdin=stdin, env_extra=envx or self.prepare()
        )

    def out(self, *args, envx=None):
        return self.git.out(self.clone, "-c", "core.bare=false", *args, env_extra=envx or self.prepare())


def post_test_env(home, tmp):
    """The environment of the tool's own git steps after the test ran: nothing from the test's environment, an empty
    HOME and XDG_CONFIG_HOME the test never sees, no global or system configuration or attributes, and no GIT_*
    variable except these four (the caller adds GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE per step)."""
    return {
        "PATH": clean_path(),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "TMPDIR": str(tmp),
        "GIT_CONFIG_GLOBAL": DEVNULL_PATH,
        "GIT_CONFIG_SYSTEM": DEVNULL_PATH,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    }


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Budget:
    """A byte budget for hashing the source repository's dirty files. Over budget is inconclusive, never skipped."""

    def __init__(self, limit):
        self.left = limit

    def take(self, n):
        self.left -= n
        if self.left < 0:
            raise Inconclusive(
                "the source hashing budget (--max-source-bytes) is exhausted; raise it or clean the source"
            )


def hash_file(path, budget):
    """Content hash of a regular file of any size (streamed), a link target hash for a symlink, a type tag for
    anything else. Every byte counts against the budget. A path that cannot be examined or read is inconclusive:
    a constant stand-in hash would let an unreadable file change unseen."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        raise Inconclusive(f"source snapshot: a path is unreadable ({type(e).__name__}): {path}") from e
    if stat.S_ISLNK(st.st_mode):
        return "link:" + sha256(os.readlink(path).encode("utf-8", "surrogateescape"))
    if not stat.S_ISREG(st.st_mode):
        return f"type:{stat.S_IFMT(st.st_mode):o}"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                budget.take(len(block))
                h.update(block)
    except OSError as e:
        raise Inconclusive(f"source snapshot: a file is unreadable ({type(e).__name__}): {path}") from e
    return h.hexdigest()


def entry_meta(st):
    """Everything about an entry that a hash of its content leaves out: type and permission bits, owner, size, link
    count, device and inode (so a replaced FIFO, socket or device node differs), the device number of a special
    file, and the times. Directories carry no times: git creating and removing a lock file changes them, and the
    entries inside are hashed on their own. mtime is kept for files because a quiet source repository does not see
    git rewrite them between the two snapshots (the snapshot itself runs `git status` first); ctime is kept
    because chmod, link and rename all move it, so a change made and undone in an unreadable file shows."""
    fields = [stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_dev, st.st_ino]
    if not stat.S_ISDIR(st.st_mode):
        fields += [st.st_nlink, st.st_size, st.st_rdev, st.st_mtime_ns, st.st_ctime_ns]
    return ",".join(str(x) for x in fields)


def status_paths(raw):
    """Paths named by `git status --porcelain=v1 -z` output (a rename or copy entry carries a second path)."""
    parts = raw.split(b"\0")
    paths, i = [], 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[:1] in b"RC" or entry[1:2] in b"RC":
            if i < len(parts) and parts[i]:
                paths.append(parts[i])
            i += 1
    return paths


def filter_overrides(git, root):
    """-c options that stop every filter driver configured in the source from running during `git status`.

    The head's .gitattributes is untrusted and can name any driver the source's own configuration defines (git-lfs,
    say). Each command is set to the empty string (git then runs nothing; checked with git 2.47) and
    the driver is marked not required. The before and after snapshots are taken the
    same way, so they stay comparable."""
    pattern = r"^filter\..*\.(clean|smudge|process)$"
    _, data = git.out(root, "config", "--name-only", "-z", "--get-regexp", pattern)
    names = {n.rsplit(".", 1)[0] for n in data.decode("utf-8", "replace").split("\0") if n.startswith("filter.")}
    opts = []
    for name in sorted(names):
        opts += ["-c", f"{name}.process=", "-c", f"{name}.clean=", "-c", f"{name}.smudge="]
        opts += ["-c", f"{name}.required=false"]
    return opts


def gitdir_digest(git, root, budget):
    """One hash over everything inside the source repository's git directories (the private one of a linked
    worktree and the common one): every entry's name and metadata (see entry_meta), the content of every file (hooks,
    reflogs, refs, object files and packs included) and the target of every link. Content counts against the
    budget, so a repository too large to hash is inconclusive, never skipped."""
    dirs = []
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        code, out = git.out(root, "rev-parse", flag)
        if code != 0:
            raise Inconclusive(f"source snapshot: git rev-parse {flag} exited {code}: {git.last_err}")
        d = os.path.realpath(os.path.join(root, out.decode("utf-8", "surrogateescape").strip()))
        if d not in dirs:
            dirs.append(d)
    h = hashlib.sha256()
    for top in dirs:
        try:
            h.update(b"t\0" + os.fsencode(top) + b"\0" + entry_meta(os.lstat(top)).encode() + b"\0")
        except OSError as exc:
            raise Inconclusive(f"source snapshot: the git directory is unreadable ({type(exc).__name__})") from exc
        stack = [top]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                raise Inconclusive(f"the source git directory is not readable ({type(e).__name__})") from e
            for e in entries:
                rel = os.path.relpath(e.path, top)
                try:
                    meta = entry_meta(os.lstat(e.path)).encode()
                except OSError as exc:
                    raise Inconclusive(
                        f"source snapshot: a path is unreadable ({type(exc).__name__}): {e.path}"
                    ) from exc
                if e.is_dir(follow_symlinks=False):
                    h.update(b"d\0" + os.fsencode(rel) + b"\0" + meta + b"\0")
                    stack.append(e.path)
                else:
                    digest = hash_file(e.path, budget).encode()
                    h.update(b"f\0" + os.fsencode(rel) + b"\0" + meta + b"\0" + digest + b"\0")
    return h.hexdigest()


def snapshot(git, root, max_bytes=DEFAULT_MAX_SOURCE):
    """Everything about the source repository the tool promises not to change, as hashes."""

    def read(*args):
        code, data = git.out(root, *args)
        if code != 0:
            raise Inconclusive(f"source snapshot: git {args[-1]} exited {code}: {git.last_err}")
        return data

    budget = Budget(max_bytes)
    snap = {}
    code, head = git.out(root, "rev-parse", "--verify", "--quiet", "HEAD")
    snap["head"] = head.decode().strip() if code == 0 else "unborn"
    code, sym = git.out(root, "symbolic-ref", "--quiet", "HEAD")
    snap["symbolic_ref"] = sym.decode("utf-8", "replace").strip() if code == 0 else "detached"
    refs = read("for-each-ref", "--format=%(refname)%00%(objectname)")
    snap["refs_sha256"] = sha256(b"\n".join(sorted(refs.splitlines())))
    snap["refs_count"] = len(refs.splitlines())
    snap["stash_sha256"] = sha256(read("stash", "list", "--format=%H %gs"))
    raw = read(*filter_overrides(git, root), "status", "--porcelain=v1", "-z", "--untracked-files=all")
    # One hash over the status text and the content of every path it names: a change of either shows.
    snap["status_and_content_sha256"] = sha256(
        raw
        + b"\0\0"
        + b"\n".join(
            p + b"\0" + hash_file(os.path.join(os.fsencode(root), p), budget).encode() for p in status_paths(raw)
        )
    )
    for name in ("config", "index"):
        gp = read("rev-parse", "--git-path", name).decode("utf-8", "surrogateescape").strip()
        snap[f"{name}_sha256"] = hash_file(os.path.join(root, gp), budget)
    snap["gitdir_sha256"] = gitdir_digest(git, root, budget)
    snap["combined_sha256"] = sha256(json.dumps(snap, sort_keys=True).encode())
    return snap


def is_test_path(path):
    parts = path.split("/")
    return any(p in TEST_DIRS for p in parts[:-1]) or bool(TEST_FILE.match(parts[-1]))


def unquote_patch_name(raw):
    """A name from a ---/+++/rename line. Anything git would quote is refused: fail closed."""
    name = raw.split("\t", 1)[0]
    if name.startswith('"') or "\\" in name:
        raise Inconclusive("revert patch has a quoted or backslash path")
    return name


def patch_paths(patch):
    """All paths a patch touches, from ---/+++, rename/copy and `diff --git` header lines."""
    text = patch.decode("utf-8", "surrogateescape")
    paths = set()
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            rest = line[len("diff --git ") :]
            if rest.startswith('"') or rest.count('"'):
                raise Inconclusive("revert patch has a quoted path")
            cuts = [m.start() for m in re.finditer(" b/", rest)]
            for c in cuts:
                a, b = rest[:c], rest[c + 3 :]
                if a.startswith("a/") and a[2:] == b:
                    paths.add(b)
                    break
            else:
                if not any(rest.startswith(p) for p in ("a/", "/dev/null")):
                    raise Inconclusive("revert patch header not understood")
        elif line.startswith(("--- ", "+++ ")):
            name = unquote_patch_name(line[4:])
            if name != DEVNULL_PATH:
                if name[:2] not in ("a/", "b/"):
                    raise Inconclusive("revert patch path has no a/ or b/ prefix")
                paths.add(name[2:])
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            paths.add(unquote_patch_name(line.split(" ", 2)[2]))
    if not paths:
        raise Inconclusive("revert patch names no file")
    return sorted(paths)


def check_patch_paths(paths, clone):
    """Refuse an absolute path, "..", ".git" and any path whose parent in the clone is a symlink."""
    for p in paths:
        if "\0" in p:
            raise Inconclusive(f"revert patch path refused (NUL): {p!r}")
        parts = p.split("/")
        # An absolute path or an empty path starts with an empty component, so it is refused here too.
        if any(c in ("", ".", "..") or c.lower() == ".git" for c in parts):
            raise Inconclusive(f"revert patch path refused (absolute, empty, dot component or .git): {p!r}")
        cur = Path(clone)
        for c in parts:
            cur = cur / c
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                break
            if stat.S_ISLNK(st.st_mode):
                raise Inconclusive(f"revert patch path goes through a symlink: {p!r}")


def under(child, parent):
    """True when child is parent or inside it (both real paths)."""
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def sha_of(git, root, rev, what):
    if rev.startswith("-"):
        raise Inconclusive(f"{what} must not start with '-'")
    code, data = git.out(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    sha = data.decode().strip()
    if code != 0 or not SHA_RE.fullmatch(sha):
        raise Inconclusive(f"{what} does not resolve to a commit")
    return sha


def read_patch_file(path):
    """Read a revert patch through one descriptor: opened once without following a symlink or blocking, checked with
    fstat on that descriptor, read through it, never longer than MAX_PATCH bytes whatever the file size claims."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        raise Inconclusive("revert patch file could not be opened (a symlink, or not readable)") from e
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Inconclusive("revert patch file must be a regular file, not a symlink")
        if _AFTER_OPEN_HOOK is not None:
            _AFTER_OPEN_HOOK()
        chunks, total = [], 0
        while total <= MAX_PATCH:
            block = os.read(fd, min(65536, MAX_PATCH + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > MAX_PATCH:
        raise Inconclusive("revert patch file over 1 MiB")
    return data


def build_revert(git, root, spec, head):
    """Return (patch bytes, files reverted, description of the source of the patch)."""
    info = {}
    if spec.startswith("-"):
        raise Inconclusive("--revert must not start with '-' (write ./-name for a patch file)")
    file_first = spec.startswith(".") or "/" in spec
    commit = None
    if not file_first:
        code, data = git.out(root, "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}")
        if code == 0 and SHA_RE.fullmatch(data.decode().strip()):
            commit = data.decode().strip()
    if commit is None:
        path = os.path.abspath(spec)
        if not os.path.lexists(path):
            raise Inconclusive("--revert is neither a commit nor a patch file")
        patch = read_patch_file(path)
        info["kind"] = "patch"
        info["sha256"] = sha256(patch)
        return patch, patch_paths(patch), info
    code, _ = git.out(root, "merge-base", "--is-ancestor", commit, head)
    if code != 0:
        raise Inconclusive("the revert commit is not an ancestor of --head")
    code, data = git.out(root, "rev-list", "--parents", "-n", "1", commit)
    fields = data.decode().split()
    if code != 0 or len(fields) != 2:
        raise Inconclusive("the revert commit must have exactly one parent")
    parent = fields[1]
    code, names = git.out(root, "diff-tree", "-r", "--no-renames", "--name-only", "-z", parent, commit)
    if code != 0:
        raise Inconclusive("could not list the files of the revert commit")
    all_files = [n.decode("utf-8", "surrogateescape") for n in names.split(b"\0") if n]
    files = [n for n in all_files if not is_test_path(n)]
    info.update(kind="commit", commit=commit, parent=parent, excluded_test_files=sorted(set(all_files) - set(files)))
    if not files:
        raise Inconclusive("the revert commit has no non-test file to revert")
    code, patch = git.out(
        root,
        "-c",
        "core.quotePath=false",
        "diff-tree",
        "-p",
        "-r",
        "--full-index",
        "--no-renames",
        parent,
        commit,
        "--",
        *[f":(literal){f}" for f in files],
    )
    if code != 0 or not patch:
        raise Inconclusive("could not build the revert patch")
    if len(patch) > MAX_PATCH:
        raise Inconclusive("revert patch over 1 MiB")
    return patch, patch_paths(patch), info


HIDECHECK_CODE = (
    "# HIDECHECK\n"
    "import os, sys\n"
    "try:\n"
    "    sys.exit(0 if os.listdir(sys.argv[1]) == [] else 1)\n"
    "except OSError:\n"
    "    sys.exit(1)\n"
)
ISOLATION_MODES = ("auto", "net", "none")


# Runs as PID 1 of the PID namespace (python3 -I -c INIT_HELPER EMPTY TARGET -- CMD...). A process that is PID 1 of a
# namespace ignores fatal signals it sends itself or that the kernel raises with the default action, so a test run
# directly as PID 1 that segfaults would hang instead of dying. This shim forks the command as PID 2, reaps every
# orphan, and exits with the command's status (128 + N for a signal), which ends the namespace. When TARGET is not
# "-" it first opens EMPTY without following a link (it must be an empty directory), bind-mounts it over
# TARGET through /proc/self/fd, then drops every capability (bounding set, then capset to zero, which clears the
# ambient set with them)
# and reads /proc/self/status to check they are all zero; any failure exits SHIM_FAILURE (126), never a red code.
INIT_HELPER = (
    "import ctypes, os, sys\n"
    "empty, target = sys.argv[1], sys.argv[2]\n"
    "if target != '-':\n"
    "    libc = ctypes.CDLL(None, use_errno=True)\n"
    "    try:\n"
    "        fd = os.open(empty, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)\n"
    "        if os.listdir(fd):\n"
    "            sys.exit(126)\n"
    "    except OSError:\n"
    "        sys.exit(126)\n"
    "    if libc.mount(('/proc/self/fd/%d' % fd).encode(), target.encode(), None, 4096, None) != 0:\n"
    "        sys.exit(126)\n"
    "    os.close(fd)\n"
    "    last = int(open('/proc/sys/kernel/cap_last_cap').read())\n"
    "    for cap in range(last + 1):\n"
    "        if libc.prctl(24, cap, 0, 0, 0) != 0:\n"  # PR_CAPBSET_DROP
    "            sys.exit(126)\n"
    "    class Hdr(ctypes.Structure):\n"
    "        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]\n"
    "    class Data(ctypes.Structure):\n"
    "        _fields_ = [('e', ctypes.c_uint32), ('p', ctypes.c_uint32), ('i', ctypes.c_uint32)]\n"
    "    hdr, data = Hdr(0x20080522, 0), (Data * 2)()\n"
    "    if libc.capset(ctypes.byref(hdr), ctypes.byref(data)) != 0:\n"
    "        sys.exit(126)\n"
    "    for line in open('/proc/self/status'):\n"
    "        if line.startswith(('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')):\n"
    "            if int(line.split()[1], 16) != 0:\n"
    "                sys.exit(126)\n"
    "pid = os.fork()\n"
    "if pid == 0:\n"
    "    try:\n"
    "        os.execvp(sys.argv[4], sys.argv[4:])\n"
    "    finally:\n"
    "        os._exit(127)\n"
    "code = 0\n"
    "while True:\n"
    "    try:\n"
    "        done, st = os.wait()\n"
    "    except ChildProcessError:\n"
    "        break\n"
    "    if done == pid:\n"
    "        code = os.WEXITSTATUS(st) if os.WIFEXITED(st) else 128 + os.WTERMSIG(st)\n"
    "        break\n"
    "sys.exit(code)\n"
)


def isolation_wrapper(mode, probe_env, hide=None):
    """(argv prefix for the tool's own git steps, argv prefix for the test, what is isolated).

    Namespaces for an unprivileged user, probed by really running them. "auto" tries a user + network + PID
    namespace (`unshare --user --map-current-user --net --pid --fork --kill-child --mount-proc`: when the leader
    exits the kernel kills every process in the namespace), then a network namespace alone, then nothing. The user
    is mapped to itself, not to root. For the TEST only, when `hide` is (empty_dir, target) and the full mode works,
    the target (the parent of the private trusted repository) is covered by a bind mount of the empty directory
    inside the test's mount namespace and every capability is dropped before the test starts (`--keep-caps` gives
    the helper the right to mount; the helper drops every capability and verifies it before the exec), so the
    test can neither see nor unmount it. The tool's own steps run in a namespace without that mount. A private /tmp
    (tmpfs) is refused by the kernel for a user mapped to itself and is not offered. Files, unix sockets and the
    user's identity are NOT isolated."""
    exe = shutil.which("unshare", path=clean_path())
    probe_cmd = shutil.which("true", path=clean_path())
    plan = {"auto": (("net", "pid"), ("net",)), "net": (("net",),), "none": ()}[mode]
    none = {"network": "none", "process": "none", "private_dir_hidden": False}
    if not (exe and probe_cmd):
        return [], [], none
    for parts in plan:
        argv = [exe, "--user", "--map-current-user"]
        if "net" in parts:
            argv.append("--net")
        if "pid" in parts:
            argv += ["--pid", "--fork", "--kill-child", "--mount-proc"]
        shim = ["--", sys.executable, "-I", "-c", INIT_HELPER] if "pid" in parts else ["--"]
        tail = ["-", "-", "--"] if "pid" in parts else []
        tool_argv = [*argv, *shim, *tail]
        res = run([*tool_argv, probe_cmd], cwd=None, env=probe_env, timeout=20, cap=4096)
        if res.status != "exited" or res.code != 0:
            continue
        iso = {
            "network": "net-namespace" if "net" in parts else "none",
            "process": "pid-namespace" if "pid" in parts else "none",
            "private_dir_hidden": False,
        }
        test_argv = tool_argv
        if hide is not None and parts == ("net", "pid"):
            empty, target = hide
            hidden = [*argv, "--keep-caps", *shim, empty, target, "--"]
            res = run([*hidden, probe_cmd], cwd=None, env=probe_env, timeout=20, cap=4096)
            if res.status == "exited" and res.code == 0:
                test_argv, iso["private_dir_hidden"] = hidden, True
        return tool_argv, test_argv, iso
    return [], [], none


def clean_evidence(trusted):
    """Is the clone's worktree exactly the head? Measured with git metadata the test never saw (see Trusted)."""
    envx = trusted.prepare()
    code, status = trusted.out("status", "--porcelain=v1", "--untracked-files=all", envx=envx)
    dcode, _ = trusted.out("diff", "--exit-code", "--quiet", trusted.sha, "--", envx=envx)
    return {
        "status_porcelain": status.decode("utf-8", "replace")[:2000],
        "status_exit": code,
        "diff_exit": dcode,
        "clean": code == 0 and not status and dcode == 0,
    }


class CleanupIncomplete(RuntimeError):
    """The disposable directory is still on disk after the delete. Carries the leftover path."""

    def __init__(self, path, problems):
        super().__init__(f"cleanup incomplete: {path} still exists ({'; '.join(problems[:5]) or 'no detail'})")
        self.path, self.problems = path, problems


def _grant_owner(parent_fd, name, pinned):
    """Give the owner full access to the directory pinned as `pinned` (an O_PATH descriptor, opened without
    following a link). Three routes, in order of how little they trust a name: chmod through /proc/self/fd (follows
    the pinned inode; needs procfs); chmod of `name` relative to the parent descriptor without following a link
    (Linux fchmodat with AT_SYMLINK_NOFOLLOW, which needs a kernel and libc that support it and otherwise fails);
    chmod of `name` relative to the parent descriptor, taken only if the entry still has the pinned inode (a
    residual check-then-use window that matters only while a hostile process is still alive, and the test's process
    tree is gone before cleanup). fchmod on the O_PATH descriptor itself is not allowed on Linux (EBADF). Returns
    None on success or a short description of why every route failed."""
    try:
        os.chmod(f"/proc/self/fd/{pinned}", OWNER_ONLY)
        return None
    except OSError:
        pass
    try:
        os.chmod(name, OWNER_ONLY, dir_fd=parent_fd, follow_symlinks=False)
        return None
    except (OSError, NotImplementedError):
        pass
    try:
        want = os.fstat(pinned)
        got = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (want.st_dev, want.st_ino) != (got.st_dev, got.st_ino):
            return f"{name}: swapped before chmod"
        os.chmod(name, OWNER_ONLY, dir_fd=parent_fd)
        return None
    except (OSError, NotImplementedError) as e:
        return f"{name}: cannot chmod ({type(e).__name__})"


def _enter(parent_fd, name, problems, files=None):
    """Pin the child directory `name` of `parent_fd`, open it up, and return (fd, identity, subdirectory names),
    or None (with the reason appended to `problems`) when it cannot be opened. A link is refused. When `files` is a
    list, the names of the entries that are not directories (links included) are appended to it."""
    try:
        pinned = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        problems.append(f"{name}: cannot pin ({type(e).__name__})")
        return None
    fd = None
    try:
        st = os.fstat(pinned)
        if not stat.S_ISDIR(st.st_mode):
            return None
        why = _grant_owner(parent_fd, name, pinned)
        try:
            fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=pinned)
        except OSError:
            problems.append(why or f"{name}: cannot open")
            return None
        names = []
        for e in os.scandir(fd):
            if e.is_dir(follow_symlinks=False):
                names.append(e.name)
            elif files is not None:
                files.append(e.name)
        return fd, (st.st_dev, st.st_ino), names
    except OSError as e:
        if fd is not None:
            os.close(fd)
        problems.append(f"{name}: cannot open ({type(e).__name__})")
        return None
    finally:
        os.close(pinned)


def open_up(path):
    """Give the owner full access to every directory under `path`, so a test that took its own permissions away
    cannot keep the tool from deleting the disposable directory. Returns a list of what could not be opened (empty
    when everything was); remove_tree reports a tree that survives the delete either way.

    The walk is iterative, so no tree depth hits Python's recursion limit, and it holds at most three descriptors at
    once whatever the depth or width, so there is no descriptor budget to run out of: it descends by replacing the
    current descriptor with the child's, remembers the identity and the unvisited names of each ancestor on a list,
    and climbs back with `..` from the current descriptor (the climb is checked against the remembered identity).
    Nothing is resolved by path after the first open: a link, or a directory swapped for one, is refused."""
    problems = []
    try:
        parent = os.open(os.path.dirname(path) or ".", os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as e:
        return [f"parent: cannot open ({type(e).__name__})"]
    try:
        entered = _enter(parent, os.path.basename(path), problems)
    finally:
        os.close(parent)
    if entered is None:
        return problems
    cur, ident, names = entered
    ancestors = []  # (identity, names still to visit) of each directory above `cur`
    try:
        while True:
            if names:
                name = names.pop()
                child = _enter(cur, name, problems)
                if child is None:
                    continue
                ancestors.append((ident, names))
                os.close(cur)
                cur, ident, names = child
                continue
            if not ancestors:
                return problems
            want, rest = ancestors.pop()
            try:
                up = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=cur)
            except OSError as e:
                problems.append(f"cannot climb ({type(e).__name__}); the rest of the tree was not opened")
                return problems
            st = os.fstat(up)
            os.close(cur)
            cur = up
            if (st.st_dev, st.st_ino) != want:
                problems.append("a directory changed while climbing; the rest of the tree was not opened")
                return problems
            ident, names = want, rest
    finally:
        os.close(cur)


def _entries(fd):
    """The entries of the directory `fd` as they are now. scandir on a descriptor continues from the descriptor's
    current offset, so it is rewound first; without that a second listing can come back short."""
    os.lseek(fd, 0, os.SEEK_SET)
    return list(os.scandir(fd))


def _unlink_all(fd, files, problems):
    """Unlink the named non-directory entries of the directory `fd`. unlink never follows a link and acts only inside
    the directory the descriptor names, so a swapped entry cannot lead it elsewhere."""
    for f in files:
        try:
            os.unlink(f, dir_fd=fd)
        except FileNotFoundError:
            pass
        except OSError as e:
            problems.append(f"{f}: cannot unlink ({type(e).__name__})")


def _delete_from_pin(parent_fd, name, problems):
    """Delete the directory `name` of `parent_fd` and everything under it, and return the root's (dev, ino) as
    pinned, or None when there was no such directory. Same shape as open_up: iterative, three descriptors at most,
    every step relative to a descriptor, and each climb with `..` checked against the identity remembered on the way
    down. Each directory is emptied (files and links unlinked) before it is entered or removed; a subdirectory is
    removed from its parent's descriptor only while it still has the identity it was entered with. The one residual
    window is between that identity check and the rmdir of a directory that is already empty, and an rmdir cannot
    remove anything that has contents. The root itself is not removed here (see remove_tree)."""
    files = []
    root = _enter(parent_fd, name, problems, files)
    if root is None:
        return None
    cur, ident, names = root
    root_ident = ident
    stack = []  # (identity, names still to visit) of each directory above `cur`, and cur's name inside it
    cname = name
    try:
        _unlink_all(cur, files, problems)
        while True:
            if names:
                child_name = names.pop()
                files = []
                child = _enter(cur, child_name, problems, files)
                if child is None:
                    continue
                stack.append((ident, names, child_name))
                os.close(cur)
                cur, ident, names = child
                _unlink_all(cur, files, problems)
                continue
            if not stack:
                return root_ident
            want, rest, cname = stack.pop()
            have = ident
            try:
                up = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=cur)
            except OSError as e:
                problems.append(f"cannot climb ({type(e).__name__}); the rest of the tree was not deleted")
                return root_ident
            os.close(cur)
            cur = up
            st = os.fstat(up)
            if (st.st_dev, st.st_ino) != want:
                problems.append("a directory changed while climbing; the rest of the tree was not deleted")
                return root_ident
            ident, names = want, rest
            try:
                got = os.stat(cname, dir_fd=up, follow_symlinks=False)
                if (got.st_dev, got.st_ino) != have:
                    problems.append(f"{cname}: swapped before removal, left alone")
                else:
                    os.rmdir(cname, dir_fd=up)
            except FileNotFoundError:
                pass
            except OSError as e:
                problems.append(f"{cname}: cannot remove ({type(e).__name__})")
    finally:
        os.close(cur)


def remove_tree(path, scratch):
    """Delete the disposable directory, never following a symlink and never leaving the scratch directory. Raises
    CleanupIncomplete (carrying the leftover path) unless the directory is gone afterwards.

    The directory is pinned once, by name, from a descriptor of its parent (which must be the scratch directory),
    and its identity is remembered. The whole delete then runs through descriptors (see _delete_from_pin) and the
    pathname is never resolved again, so a process that renames the directory away and puts a sibling under the same
    name cannot make this delete the sibling. shutil.rmtree is not used. The root is removed from the pinned parent
    by the name that still has the pinned identity (the entry is looked up by identity, so the original is removed
    even if it was renamed). Verification is against the pinned identity: no parent entry may still have it, and an
    entry under the original name with any other identity is reported as unexpected, not as success."""
    real = os.path.realpath(path)
    if os.path.islink(path) or os.path.dirname(real) != os.path.realpath(scratch):
        raise RuntimeError("refusing to delete a path that is not a direct child of the scratch directory")
    name = os.path.basename(path)
    problems = []
    try:
        pin = os.open(os.path.dirname(path) or ".", os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as e:
        raise CleanupIncomplete(path, [f"parent: cannot open ({type(e).__name__})"]) from e
    try:
        want = os.fstat(pin)
        have = os.stat(scratch)
        if (want.st_dev, want.st_ino) != (have.st_dev, have.st_ino):
            raise RuntimeError("refusing to delete: the parent is not the scratch directory")
        try:
            pdir = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=pin)
        except OSError as e:
            raise CleanupIncomplete(path, [f"parent: cannot list ({type(e).__name__})"]) from e
        try:
            root_ident = _delete_from_pin(pdir, name, problems)
            if root_ident is not None:
                for entry in _entries(pdir):
                    st = entry.stat(follow_symlinks=False)
                    if (st.st_dev, st.st_ino) == root_ident:
                        try:
                            os.rmdir(entry.name, dir_fd=pdir)
                        except FileNotFoundError:
                            pass
                        except OSError as e:
                            problems.append(f"{entry.name}: cannot remove root ({type(e).__name__})")
            for entry in _entries(pdir):  # verify against the pinned identity, then the original name
                st = entry.stat(follow_symlinks=False)
                if root_ident is not None and (st.st_dev, st.st_ino) == root_ident:
                    problems.append(f"{entry.name}: the pinned directory is still there")
                elif entry.name == name:
                    problems.append(f"{name}: an entry with a different identity is there; left alone")
        finally:
            os.close(pdir)
    finally:
        os.close(pin)
    if problems:
        raise CleanupIncomplete(path, problems)


class Checker:
    def __init__(self, args, report):
        self.args, self.report = args, report
        self.commands = report["commands"]

    def record(self, phase, argv, res, extra=None):
        entry = {
            "phase": phase,
            "argv": [str(a) for a in argv],
            "status": res.status,
            "exit_code": res.code,
            "seconds": res.seconds,
            "output_bytes": res.total,
            "output_tail": res.tail,
        }
        if extra:
            entry.update(extra)
        self.commands.append(entry)

    def test_run(self, phase, argv, clone, env, wrap):
        res = run(wrap + argv, cwd=clone, env=env, timeout=self.args.timeout, cap=self.args.max_output)
        if res.status != "exited":
            kind = "error"
        elif res.code == 0:
            kind = "green"
        elif res.code < 0 or res.code >= 128:
            kind = "crashed"  # killed by a signal (negative, or 128+N from a launcher): never a failing test
        elif res.code in self.args.red_exit_codes:
            kind = "red"
        else:
            kind = "error"
        self.record(phase, argv, res, {"result": kind, "namespace_wrapper": bool(wrap)})
        return kind


def resolve_scratch(args, root):
    explicit = args.scratch is not None
    scratch = os.path.abspath(os.path.expanduser(args.scratch if explicit else DEFAULT_SCRATCH))
    real = os.path.realpath(scratch)  # resolves symlinks even for a path that does not exist yet
    if under(root, real) or under(real, root):
        raise Inconclusive("the scratch directory equals, contains or lies inside the repository")
    if not explicit and any(under(real, t) for t in ("/tmp", "/var/tmp", os.path.realpath(tempfile.gettempdir()))):
        raise Inconclusive("the default scratch directory must not be under /tmp")
    os.makedirs(real, mode=0o700, exist_ok=True)
    return real


MARKER = ".doia-mutation-check"
PREFIX = "doia-mc-"


def get_token(parent):
    """The per-installation secret that authenticates this tool's markers: random, kept 0600 in the private root,
    created O_EXCL, read back with O_NOFOLLOW and refused unless it is a regular file of ours with mode 0600."""
    os.makedirs(parent, mode=0o700, exist_ok=True)
    path = os.path.join(parent, ".token")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = None
    if fd is not None:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_hex(32))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        raise Inconclusive("the token file could not be opened (a symlink, or not readable)") from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
            raise Inconclusive("the token file must be a regular file of ours with mode 0600")
        token = os.read(fd, 200).decode("ascii", "replace").strip()
    finally:
        os.close(fd)
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise Inconclusive("the token file does not hold a token")
    return token


def write_marker(dirpath, token):
    """Mark a directory as made by this tool: token and directory name, 0600, created O_EXCL, never through a link."""
    fd = os.open(os.path.join(dirpath, MARKER), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(f"{token}\n{os.path.basename(dirpath)}\n")


def validate_marker(st, content, name, token, cutoff):
    """Is this marker genuine and old? A regular file of ours, mode 0600, holding the token and this directory name."""
    return (
        stat.S_ISREG(st.st_mode)
        and st.st_uid == os.getuid()
        and stat.S_IMODE(st.st_mode) == 0o600
        and content == f"{token}\n{name}\n".encode()
        and st.st_mtime < cutoff
    )


def is_mount_boundary(parent_st, child_st):
    return parent_st.st_dev != child_st.st_dev


def refuse_foreign_device(cfd, dev):
    """Raise when the open directory or anything under it is on another device than `dev` (a mount point)."""
    for _dp, _dn, _fn, rfd in os.fwalk(".", dir_fd=cfd, topdown=True):
        if is_mount_boundary(os.fstat(rfd), os.stat_result((0, 0, dev) + (0,) * 7)):
            raise OSError("a mount point in or at the candidate")


def _delete_pinned(cfd, dev):
    """Empty the directory open as `cfd`, bottom-up, every removal relative to the descriptor of its own parent
    directory (no multi-component path is ever resolved), after refusing any other device."""
    refuse_foreign_device(cfd, dev)
    for _dp, dn, fn, rfd in os.fwalk(".", dir_fd=cfd, topdown=False):
        os.fchmod(rfd, OWNER_ONLY)
        for f in fn:
            os.unlink(f, dir_fd=rfd)
        for d in dn:
            if stat.S_ISLNK(os.stat(d, dir_fd=rfd, follow_symlinks=False).st_mode):
                os.unlink(d, dir_fd=rfd)
            else:
                os.rmdir(d, dir_fd=rfd)


def prune_old(parent, days, token):
    """Remove disposable copies this tool made and left behind. A candidate is a PREFIX directory directly under
    `parent` (a pinned descriptor, nothing is followed; O_DIRECTORY keeps a FIFO from blocking the open) on the
    same device, holding an authenticated marker (see
    validate_marker) older than `days`. It is emptied through its own descriptor and removed only if the name still
    refers to the same inode; a mount point or a swapped candidate is left alone."""
    pruned = []
    if days <= 0:
        return pruned
    cutoff = time.time() - days * 86400
    pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        pst = os.fstat(pfd)
        for name in sorted(os.listdir(pfd)):
            if not name.startswith(PREFIX):
                continue
            try:
                st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                if _PRE_OPEN_HOOK is not None:
                    _PRE_OPEN_HOOK(name)
                cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=pfd)  # inode compared below
            except OSError:
                continue
            try:
                fst = os.fstat(cfd)
                if (fst.st_ino, fst.st_dev) != (st.st_ino, st.st_dev):
                    continue
                try:
                    mfd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=cfd)
                except OSError:
                    continue
                try:
                    mst = os.fstat(mfd)
                    content = os.read(mfd, 512) if stat.S_ISREG(mst.st_mode) else b""
                finally:
                    os.close(mfd)
                if not validate_marker(mst, content, name, token, cutoff):
                    continue
                if _PRUNE_HOOK is not None:
                    _PRUNE_HOOK(name)
                _delete_pinned(cfd, pst.st_dev)
                now = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                if (now.st_ino, now.st_dev) == (fst.st_ino, fst.st_dev):
                    os.rmdir(name, dir_fd=pfd)
                    pruned.append(name)
            except OSError:
                continue  # an old copy that cannot be removed is left; the check goes on
            finally:
                os.close(cfd)
    finally:
        os.close(pfd)
    return pruned


PRIVATE_PARENT = "~/.local/share/doia-mutation-private"


def resolve_private_parent(args, root):
    parent = os.path.realpath(os.path.expanduser(args.private_parent or PRIVATE_PARENT))
    if under(root, parent) or under(parent, root):
        raise Inconclusive(
            "the private directory for trusted git material equals, contains or lies inside the repository"
        )
    return parent


def free_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def required_free(estimate):
    """Free space needed before copying: 2.5 times the estimate plus a margin."""
    return int(2.5 * estimate) + COPY_MARGIN


def estimate_copy_bytes(git, root, objects_dir, limit=5_000_000):
    """Disk the copies will need at most: the object store plus the tracked files (bounded walk)."""
    total, seen = 0, 0
    for dirpath, _dn, filenames in os.walk(objects_dir, followlinks=False):
        for name in filenames:
            seen += 1
            if seen > limit:
                raise Inconclusive("too many files in the object store to estimate the copy size")
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            total += max(st.st_size, st.st_blocks * 512)
    code, data = git.out(root, "ls-files", "-z")
    if code != 0:
        raise Inconclusive("could not list the tracked files to estimate the copy size")
    for rel in data.split(b"\0")[:limit]:
        if rel:
            try:
                total += os.lstat(os.path.join(os.fsencode(root), rel)).st_size
            except OSError:
                continue
    return total


def check_storage(estimate, args, targets):
    if estimate > args.max_copy_bytes:
        raise Inconclusive(
            f"the copies would need about {estimate} bytes, over --max-copy-bytes {args.max_copy_bytes}; "
            "nothing was copied"
        )
    need = required_free(estimate)
    for target in targets:
        probe = target
        while not os.path.exists(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
        free = free_bytes(probe)
        if free < need:
            raise Inconclusive(
                f"not enough free space at {target}: {free} bytes free, {need} needed "
                f"(2.5 x the estimate of {estimate} bytes plus a margin); nothing was copied"
            )


def make_private_dir(parent):
    """A random 0700 directory for the trusted git material, outside the scratch directory and the repository."""
    os.makedirs(parent, mode=0o700, exist_ok=True)
    return tempfile.mkdtemp(prefix=PREFIX + "private-", dir=parent)


def check(args, report, state):
    git_exe = shutil.which("git", path=clean_path())
    if not git_exe:
        raise Inconclusive("git not found on PATH")
    real_home = os.path.expanduser("~")
    src_git = Git(git_exe, {**sanitized_env(real_home), "GIT_OPTIONAL_LOCKS": "0"})
    repo = os.path.realpath(args.repo)
    if not os.path.isdir(repo):
        raise Inconclusive("--repo is not a directory")
    code, top = src_git.out(repo, "rev-parse", "--show-toplevel")
    if code != 0:
        raise Inconclusive("--repo is not a git repository")
    root = os.path.realpath(top.decode("utf-8", "surrogateescape").strip())
    report["repo"] = root
    head = sha_of(src_git, root, args.head, "--head")
    report["head"] = head
    if args.base is not None:
        report["base"] = sha_of(src_git, root, args.base, "--base")
    try:
        test_argv = shlex.split(args.test)
    except ValueError as e:
        raise Inconclusive(f"--test is not a valid command line: {e}") from e
    if not test_argv:
        raise Inconclusive("--test is empty")
    patch, files, info = build_revert(src_git, root, args.revert, head)
    report["reverted"] = {"files": files, **info}
    scratch = resolve_scratch(args, root)
    pparent = resolve_private_parent(args, root)
    code, objs = src_git.out(root, "rev-parse", "--git-path", "objects")
    src_objects = os.path.join(root, objs.decode("utf-8", "surrogateescape").strip())
    estimate = estimate_copy_bytes(src_git, root, src_objects)
    report["storage"] = {"estimate_bytes": estimate, "required_free_bytes": required_free(estimate)}
    check_storage(estimate, args, [scratch, pparent])
    private = make_private_dir(pparent)
    state["private"], state["private_parent"] = private, pparent
    token = get_token(pparent)
    write_marker(private, token)
    report["pruned"] = prune_old(scratch, args.clean_old, token) + prune_old(pparent, args.clean_old, token)
    state["src_git"], state["root"], state["max_source"] = src_git, root, args.max_source_bytes
    before = snapshot(src_git, root, args.max_source_bytes)
    state["before"] = before

    work = tempfile.mkdtemp(prefix=PREFIX, dir=scratch)  # mkdtemp creates it 0700
    state["work"], state["scratch"] = work, scratch
    report["work_dir"] = work
    write_marker(work, token)
    for d in ("home", "cache", "config", "data", "pip-cache", "npm-cache", "tmp", "pycache"):
        os.mkdir(os.path.join(work, d), 0o700)
    clone = os.path.join(work, "repo")
    chk = Checker(args, report)
    env = sanitized_env(os.path.join(work, "home"), work)
    hide_source = tempfile.mkdtemp(prefix="empty-", dir=private)  # random, 0700, in the private root, not in work
    hide = (hide_source, pparent) if args.hide_private == "auto" else None
    wrap, test_wrap, iso = isolation_wrapper(args.isolation, env, hide)
    state["process_isolation"] = iso["process"]
    degraded = iso["network"] != "net-namespace" or iso["process"] != "pid-namespace"
    report["isolation_degraded"] = degraded
    report["network_isolation"], report["process_isolation"] = iso["network"], iso["process"]
    report["isolation"] = {
        **iso,
        "filesystem": "none: files and unix sockets the user can read or write stay reachable",
        "identity": "none: the real user, home directory and any secret file the user can read",
        "environment": "allowlist only (no inherited variable is passed)",
    }
    if degraded and not args.allow_degraded_isolation:
        raise Inconclusive(
            f"the isolation a bite needs (network and PID namespaces) is not in place (network: {iso['network']}, "
            f"process: {iso['process']}); no test was run. Use a host with unshare, or pass "
            "--allow-degraded-isolation to accept the weaker contract"
        )
    if args.require_no_network and iso["network"] == "none":
        raise Inconclusive("--require-no-network but this host cannot isolate the network (no usable unshare)")
    report["environment"] = sorted(env)

    # Every git step of the tool runs inside the same namespaces as the test.
    git = Git(git_exe, env, chk.record, wrap, strict=False)
    for d in ("home", "tmp"):
        os.mkdir(os.path.join(private, d), 0o700)
    post_env = post_test_env(os.path.join(private, "home"), os.path.join(private, "tmp"))
    report["trusted"] = {"env_keys": sorted(post_env), "private_dir": private, "hide_source": hide_source}
    state["copy_failed"] = True  # cleared once both copies exist; any failure in between deletes the partial copies
    base = os.path.join(private, "base.git")
    # The private object store: a bare copy of the source, made before the test runs, with its own configuration.
    Git(git_exe, post_env, chk.record, wrap, strict=False).run(
        private, "clone", "--bare", "--local", "--no-hardlinks", "--", root, base, phase="trusted-base"
    )
    guard = StoreGuard(base, [os.path.join(private, "home"), os.path.join(private, "tmp")])
    trusted = Trusted(
        Git(git_exe, post_env, chk.record, wrap, strict=True),
        private,
        clone,
        os.path.join(base, "objects"),
        head,
        guard,
    )

    git.run(work, "clone", "--local", "--no-hardlinks", "--no-checkout", "--", root, clone, phase="clone")
    git.run(clone, "checkout", "--detach", "--force", head, phase="checkout-head")
    state["copy_failed"] = False
    check_patch_paths(files, clone)

    def recheck_hidden():
        """Before every test run: from inside the test's own namespace the private parent must list as empty."""
        if not iso["private_dir_hidden"]:
            return
        argv = [*test_wrap, sys.executable, "-I", "-c", HIDECHECK_CODE, pparent]
        res = run(argv, cwd=clone, env=env, timeout=30, cap=4096)
        chk.record("hide-check", argv, res)
        if res.status != "exited" or res.code != 0:
            raise Inconclusive("the private directory is no longer hidden from the test (re-check failed)")

    recheck_hidden()
    kind = chk.test_run("test-at-head", test_argv, clone, env, test_wrap)
    report["green_before"] = kind == "green"
    if kind != "green":
        raise Inconclusive(
            f"the test is not green at head (result: {kind}"
            + (", it crashed: killed by a signal" if kind == "crashed" else "")
            + "); nothing else was run"
        )

    guard.verify()
    envx = trusted.prepare()
    trusted.run("apply", "-R", "--whitespace=nowarn", "-", phase="revert", stdin=patch, envx=envx)
    _, dirty = trusted.out("status", "--porcelain=v1", "--untracked-files=all", envx=envx)
    if not dirty:
        raise Inconclusive("the revert changed nothing in the clone")
    recheck_hidden()
    kind = chk.test_run("test-after-revert", test_argv, clone, env, test_wrap)
    guard.verify()
    report["red_after_revert"] = True if kind == "red" else (False if kind == "green" else None)

    restore(trusted)
    after_restore = clean_evidence(trusted)
    report["tree_clean"] = {"after_restore": after_restore}
    if kind == "green":
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "vacuous"
    elif kind == "red":
        recheck_hidden()
        k3 = chk.test_run("test-after-restore", test_argv, clone, env, test_wrap)
        guard.verify()
        report["green_after_restore"] = k3 == "green"
        report["tree_clean"]["final"] = clean_evidence(trusted)
        state["conclusion"] = "bites" if k3 == "green" else "error"
        state["why"] = f"the test was not green after the restore (result: {k3})"
    else:
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "error"
        state["why"] = (
            "the test failed after the revert in a way that is not a clean failure "
            "(crash by signal, timeout, unlisted exit code; see commands)"
        )
    report["tree_clean"]["clean"] = bool(after_restore["clean"] and report["tree_clean"]["final"]["clean"])
    state["after"] = snapshot(src_git, root, args.max_source_bytes)


def restore(trusted):
    envx = trusted.prepare()
    trusted.run("checkout", "--force", "--detach", trusted.sha, phase="restore-checkout", envx=envx)
    trusted.run("clean", "-ffdxq", phase="restore-clean", envx=envx)


def build_parser():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise Inconclusive(f"usage: {message}")

    p = Parser(add_help=False, prog="mutation_check.py", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    p.add_argument("--repo", help="source git repository (read only)")
    p.add_argument("--head", help="commit to test (resolved to a full SHA)")
    p.add_argument("--revert", help="commit whose non-test hunks are reverted, or a patch file")
    p.add_argument("--test", help="the test command, split once with shlex into an argv (no shell)")
    p.add_argument("--base", help="optional base commit, recorded in the output")
    p.add_argument("--scratch", help="parent directory of the disposable copy (default ~/.local/share/doia-mutation)")
    p.add_argument("--keep", action="store_true", help="keep the disposable copy even on bites")
    p.add_argument("--timeout", type=int, default=600, help="seconds per test run (default 600)")
    p.add_argument("--max-output", type=int, default=1048576, help="output bytes per test run before it is killed")
    p.add_argument("--red-exit", default="1", help="comma list of exit codes that count as a failing test (default 1)")
    p.add_argument("--require-no-network", action="store_true", help="inconclusive when the network cannot be removed")
    p.add_argument(
        "--isolation", choices=ISOLATION_MODES, default="auto", help="namespaces for the test and the git steps"
    )
    p.add_argument(
        "--clean-old", type=int, default=7, metavar="DAYS", help="remove this tool's copies older than DAYS (0: off)"
    )
    p.add_argument(
        "--allow-degraded-isolation",
        action="store_true",
        help="accept a run without both the network and PID namespaces (weaker exit-0 contract)",
    )
    p.add_argument(
        "--hide-private", choices=("auto", "off"), default="auto", help="hide the private repository's parent"
    )
    p.add_argument(
        "--private-parent",
        help="where the private trusted repository lives (default ~/.local/share/doia-mutation-private)",
    )
    p.add_argument(
        "--max-source-bytes", type=int, default=DEFAULT_MAX_SOURCE, help="byte budget for hashing the source"
    )
    p.add_argument(
        "--max-copy-bytes", type=int, default=DEFAULT_MAX_COPY, help="refuse to copy more than this (estimate)"
    )
    p.add_argument("--settle", type=float, default=1.0, help="seconds to wait before the last source check (default 1)")
    p.add_argument("-h", "--help", action="store_true", help="print this help to stderr and exit 3")
    return p


def parse(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help:
        parser.print_help(sys.stderr)
        raise Inconclusive("help was printed to stderr; no check was run (exit 3, never 0)")
    for name in ("repo", "head", "revert", "test"):
        if getattr(args, name) is None:
            raise Inconclusive(f"usage: --{name} is required")
    if args.clean_old < 0 or args.settle < 0:
        raise Inconclusive("--clean-old and --settle must not be negative")
    try:
        args.red_exit_codes = {int(c) for c in args.red_exit.split(",") if c.strip()}
    except ValueError as e:
        raise Inconclusive("--red-exit must be a comma list of integers") from e
    if not args.red_exit_codes or any(not 1 <= c <= 125 for c in args.red_exit_codes):
        raise Inconclusive(
            "--red-exit takes exit codes 1 to 125 only (0 is a pass; 126, 127, 128 and above, 255 and negative "
            "values are launcher or signal codes, never a failing test)"
        )
    if args.timeout < 1 or args.max_output < 1 or args.max_source_bytes < 1 or args.max_copy_bytes < 1:
        raise Inconclusive("--timeout, --max-output, --max-source-bytes and --max-copy-bytes must be positive")
    return args


class Interrupted(BaseException):
    """Raised by the signal handler; BaseException so no `except Exception` in the tool can swallow it."""


def main(argv=None):
    report = {
        "head": None,
        "base": None,
        "reverted": None,
        "commands": [],
        "green_before": None,
        "red_after_revert": None,
        "green_after_restore": None,
        "tree_clean": {"clean": False},
        "verdict": "inconclusive",
        "reason": "",
    }
    state = {}
    args = None

    def on_signal(signum, _frame):
        raise Interrupted(f"interrupted by signal {signum}")

    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    for s in saved:
        signal.signal(s, on_signal)
    try:
        args = parse(sys.argv[1:] if argv is None else argv)
        check(args, report, state)
    except Inconclusive as e:
        report["reason"] = str(e)
    except Interrupted as e:
        report["reason"] = str(e)
    except Exception as e:  # fail closed: an unexpected error is never a pass
        report["reason"] = f"unexpected {type(e).__name__}: {e}"
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):  # a second signal must not break the cleanup
        signal.signal(s, signal.SIG_IGN)

    # The source repository is hashed once more after the test's process tree is gone and after a short settle
    # time, so a process that outlived a run and wrote late is caught if it wrote within that window.
    before, after = state.get("before"), state.get("after")
    after_settle = None
    if before is not None and after is not None:
        try:
            time.sleep(args.settle)
            after_settle = snapshot(state["src_git"], state["root"], state["max_source"])
        except Exception as e:
            report["reason"] = report["reason"] or f"final source check failed: {type(e).__name__}"
    unchanged = after_settle is not None and before == after == after_settle
    if before is not None:
        report["source_repo_state"] = {"before": before, "after": after, "after_settle": after_settle}
        if state.get("process_isolation") == "pid-namespace":
            report["source_repo_unchanged"] = unchanged
        else:
            # No PID namespace: a process that escaped the process-group kill can still write later, so the claim
            # is only that nothing had changed when this check looked.
            report["source_unchanged_at_check"] = unchanged
    verdict = "inconclusive"
    conclusion = state.get("conclusion")
    if conclusion in ("bites", "vacuous") and not report["reason"]:
        if not report["tree_clean"].get("clean"):
            report["reason"] = "the clone's tree is not clean after the restore"
        elif not unchanged:
            report["reason"] = "the source repository changed while the test ran (it can write outside the copy)"
        else:
            verdict = conclusion
            report["reason"] = (
                "red with the fix reverted, green again after the restore"
                if verdict == "bites"
                else "the test stays green with the fix reverted"
            )
    elif conclusion == "error" and not report["reason"]:
        report["reason"] = state.get("why", "unexpected result")
    if not report["reason"]:
        report["reason"] = "no verdict reached"
    report["verdict"] = verdict
    code = {"bites": EXIT_BITES, "vacuous": EXIT_VACUOUS}.get(verdict, EXIT_INCONCLUSIVE)
    report["exit_code"] = code

    cleanup_failed = False
    private = state.get("private")
    if private:
        try:
            remove_tree(private, state["private_parent"])
        except Exception as e:  # reported, never hidden
            cleanup_failed = True
            report["kept"] = True
            report["cleanup_error"] = f"{type(e).__name__}: {e}"
            report["leftover"] = private
            report["cleanup_error_private"] = f"{type(e).__name__}: {e}"
            report["private_leftover"] = private
            print(f"mutation_check: cleanup incomplete, the private directory is still at {private}", file=sys.stderr)

    work = state.get("work")
    if work:
        keep = (verdict != "bites" or (args is not None and args.keep)) and not state.get("copy_failed")
        if keep:
            report["kept"] = True
            print(f"mutation_check: kept the disposable copy at {work}", file=sys.stderr)
        else:
            try:
                remove_tree(work, state["scratch"])
                report["kept"] = cleanup_failed  # true only when the private directory is what was left behind
            except Exception as e:  # leaving a directory behind is reported, never hidden
                cleanup_failed = True
                report["kept"] = True
                report["cleanup_error"] = f"{type(e).__name__}: {e}"
                report["leftover"] = work
                print(f"mutation_check: cleanup incomplete, the disposable copy is still at {work}", file=sys.stderr)
    if cleanup_failed:  # a verdict that claims a deletion that did not happen is not a verdict
        verdict, code = "inconclusive", EXIT_INCONCLUSIVE
        report["verdict"], report["exit_code"] = verdict, code
        report["reason"] = "cleanup incomplete: " + report["reason"]
    for s, handler in saved.items():
        signal.signal(s, handler)
    try:
        print(json.dumps(report, indent=2, sort_keys=True))
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # The reader went away: the exit code is the contract, not the print. Silence the interpreter's own flush.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
