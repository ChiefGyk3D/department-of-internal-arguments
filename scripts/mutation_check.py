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
    0  bites: green, red after the revert, green after the restore, tree clean, source repository unchanged
    2  vacuous: the test stays green with the fix reverted (tree clean, source unchanged)
    3  inconclusive: anything else. Not green at head, a revert that does not apply or changes nothing, a test that
       failed with an exit code outside --red-exit, timed out, produced too much output or could not start, an
       unclean tree, a changed source repository, an interrupt (SIGINT, SIGTERM, SIGHUP kill the whole run first),
       an unresolvable argument, any unexpected error. Never a pass.

The copy is deleted on a bites result and kept (its path is in the JSON, "work_dir", and on stderr) on every other
result so evidence is not thrown away; --keep keeps it on bites too. Copies this tool made (a `doia-mc-*`
directory holding a marker file) and left behind are removed after --clean-old days (default 7, 0 turns it off)
when the next run starts; nothing else in the scratch directory is touched and symlinks are never followed.

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
    the host refuses it falls back to the network namespace alone, then to nothing (--isolation auto|net|none;
    --require-no-network makes "nothing" inconclusive). The JSON says which: network_isolation is
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
  * puts a timeout, an output cap and a process-group kill on every subprocess (--timeout, --max-output), and
    kills the whole run when it is interrupted.
  * refuses a revert patch (given or generated) that has an absolute path, a ".." component, a ".git"
    component, a quoted name, or a path that goes through a symlink in the clone; refuses a symlinked or
    oversize patch file.
  * never works in the maintainer's checkout: refuses a scratch directory that equals, contains or lies inside
    the repository root. The default scratch is a new directory under ~/.local/share/doia-mutation/, never /tmp.

What cannot be enforced. The test is arbitrary code running as the invoking user. It can write anywhere that user
can write, including the source repository by absolute path, and read anything that user can read. The tool
detects a changed source repository and reports it (exit 3); it cannot prevent or undo it. Without `unshare`
there is no network or process isolation. The isolated environment is not CI: a test that needs a variable or a
cache the allowlist removes can be reported inconclusive. A revert that breaks the import of the module under test
makes the test fail like a real bite does. Standard library only."""

import argparse
import hashlib
import json
import os
import re
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
MAX_HASH_FILE = 64 * 1024 * 1024
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

    def __init__(self, git, private, clone, objects, sha):
        self.git, self.private, self.clone, self.objects, self.sha = git, private, clone, objects, sha

    def prepare(self):
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


def trusted_env(home, tmp):
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


def hash_file(path):
    """Content hash of a regular file, a link target hash for a symlink, a type tag for anything else."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError as e:
        return f"error:{type(e).__name__}"
    if stat.S_ISLNK(st.st_mode):
        return "link:" + sha256(os.readlink(path).encode("utf-8", "surrogateescape"))
    if not stat.S_ISREG(st.st_mode):
        return f"type:{stat.S_IFMT(st.st_mode):o}"
    if st.st_size > MAX_HASH_FILE:
        return f"large:{st.st_size}:{st.st_mtime_ns}"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError as e:
        return f"error:{type(e).__name__}"
    return h.hexdigest()


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


def snapshot(git, root):
    """Everything about the source repository the tool promises not to change, as hashes."""

    def read(*args):
        code, data = git.out(root, *args)
        if code != 0:
            raise Inconclusive(f"source snapshot: git {args[-1]} exited {code}: {git.last_err}")
        return data

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
        + b"\n".join(p + b"\0" + hash_file(os.path.join(os.fsencode(root), p)).encode() for p in status_paths(raw))
    )
    for name in ("config", "index"):
        gp = read("rev-parse", "--git-path", name).decode("utf-8", "surrogateescape").strip()
        snap[f"{name}_sha256"] = hash_file(os.path.join(root, gp))
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
        try:
            st = os.lstat(path)
        except OSError as e:
            raise Inconclusive("--revert is neither a commit nor a patch file") from e
        if not stat.S_ISREG(st.st_mode):  # lstat: a symlink is never a regular file
            raise Inconclusive("revert patch file must be a regular file, not a symlink")
        if st.st_size > MAX_PATCH:
            raise Inconclusive("revert patch file over 1 MiB")
        with open(path, "rb") as f:
            patch = f.read(MAX_PATCH + 1)
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


ISOLATION_MODES = ("auto", "net", "none")


def isolation_wrapper(mode, probe_env):
    """(argv prefix, what it isolates). Namespaces for an unprivileged user, probed by really running them.

    "auto" tries a user + network + PID namespace (`unshare --user --map-current-user --net --pid --fork
    --kill-child --mount-proc`: when the leader exits the kernel kills every process in the namespace, so nothing
    the test started can outlive the run), then a network namespace alone, then nothing. The user is mapped to
    itself, not to root, so permission-dependent tests behave as they would outside. A private /tmp was tried
    (a tmpfs mount inside the mount namespace) and is refused by the kernel for a mapped current user, so it is not
    offered. Files, unix sockets and the user's identity are NOT isolated."""
    exe = shutil.which("unshare", path=clean_path())
    probe_cmd = shutil.which("true", path=clean_path())
    plan = {"auto": (("net", "pid"), ("net",)), "net": (("net",),), "none": ()}[mode]
    if exe and probe_cmd:
        for parts in plan:
            argv = [exe, "--user", "--map-current-user"]
            if "net" in parts:
                argv.append("--net")
            if "pid" in parts:
                argv += ["--pid", "--fork", "--kill-child", "--mount-proc"]
            argv.append("--")
            res = run([*argv, probe_cmd], cwd=None, env=probe_env, timeout=20, cap=4096)
            if res.status == "exited" and res.code == 0:
                return argv, {
                    "network": "net-namespace" if "net" in parts else "none",
                    "process": "pid-namespace" if "pid" in parts else "none",
                }
    return [], {"network": "none", "process": "none"}


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


def remove_tree(path, scratch):
    """Delete the disposable directory, never following a symlink and never leaving the scratch directory."""
    real = os.path.realpath(path)
    if os.path.islink(path) or os.path.dirname(real) != os.path.realpath(scratch):
        raise RuntimeError("refusing to delete a path that is not a direct child of the scratch directory")

    def fix(func, p, _exc):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=fix)


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


def prune_old(scratch, days):
    """Remove disposable copies this tool made (a PREFIX directory holding MARKER, owned by this user, not a
    symlink) whose marker is older than `days`. Nothing else in the scratch directory is looked at or touched."""
    pruned = []
    if days <= 0:
        return pruned
    cutoff = time.time() - days * 86400
    for entry in sorted(os.listdir(scratch)):
        path = os.path.join(scratch, entry)
        if not entry.startswith(PREFIX):
            continue
        try:
            mark = os.lstat(os.path.join(path, MARKER))
        except OSError:
            continue
        if mark.st_mtime >= cutoff:
            continue
        try:  # remove_tree refuses a symlink and anything that is not a direct child of the scratch directory
            remove_tree(path, scratch)
            pruned.append(entry)
        except Exception:  # an old copy that cannot be removed is left; the check goes on
            continue
    return pruned


PRIVATE_PARENT = "~/.local/share/doia-mutation-private"


def make_private_dir(root):
    """A random 0700 directory for the trusted git material, outside the scratch directory and the repository."""
    parent = os.path.realpath(os.path.expanduser(PRIVATE_PARENT))
    if under(root, parent) or under(parent, root):
        raise Inconclusive(
            "the private directory for trusted git material equals, contains or lies inside the repository"
        )
    os.makedirs(parent, mode=0o700, exist_ok=True)
    return parent, tempfile.mkdtemp(prefix=PREFIX + "private-", dir=parent)


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
    pparent, private = make_private_dir(root)
    state["private"], state["private_parent"] = private, pparent
    with open(os.path.join(private, MARKER), "w", encoding="utf-8") as f:
        f.write("made by mutation_check.py\n")
    report["pruned"] = prune_old(scratch, args.clean_old) + prune_old(pparent, args.clean_old)
    state["src_git"], state["root"] = src_git, root
    before = snapshot(src_git, root)
    state["before"] = before

    work = tempfile.mkdtemp(prefix=PREFIX, dir=scratch)  # mkdtemp creates it 0700
    state["work"], state["scratch"] = work, scratch
    report["work_dir"] = work
    with open(os.path.join(work, MARKER), "w", encoding="utf-8") as f:
        f.write("made by mutation_check.py\n")
    for d in ("home", "cache", "config", "data", "pip-cache", "npm-cache", "tmp", "pycache"):
        os.mkdir(os.path.join(work, d), 0o700)
    clone = os.path.join(work, "repo")
    chk = Checker(args, report)
    env = sanitized_env(os.path.join(work, "home"), work)
    wrap, iso = isolation_wrapper(args.isolation, env)
    state["process_isolation"] = iso["process"]
    report["network_isolation"], report["process_isolation"] = iso["network"], iso["process"]
    report["isolation"] = {
        **iso,
        "filesystem": "none: files and unix sockets the user can read or write stay reachable",
        "identity": "none: the real user, home directory and any secret file the user can read",
        "environment": "allowlist only (no inherited variable is passed)",
    }
    if args.require_no_network and iso["network"] == "none":
        raise Inconclusive("--require-no-network but this host cannot isolate the network (no usable unshare)")
    report["environment"] = sorted(env)

    # Every git step of the tool runs inside the same namespaces as the test.
    git = Git(git_exe, env, chk.record, wrap, strict=False)
    for d in ("home", "tmp"):
        os.mkdir(os.path.join(private, d), 0o700)
    tenv = trusted_env(os.path.join(private, "home"), os.path.join(private, "tmp"))
    report["trusted"] = {"env_keys": sorted(tenv), "private_dir": private}
    base = os.path.join(private, "base.git")
    # The private object store: a bare copy of the source, made before the test runs, with its own configuration.
    Git(git_exe, tenv, chk.record, wrap, strict=False).run(
        private, "clone", "--bare", "--local", "--no-hardlinks", "--", root, base, phase="trusted-base"
    )
    trusted = Trusted(
        Git(git_exe, tenv, chk.record, wrap, strict=True), private, clone, os.path.join(base, "objects"), head
    )

    git.run(work, "clone", "--local", "--no-hardlinks", "--no-checkout", "--", root, clone, phase="clone")
    git.run(clone, "checkout", "--detach", "--force", head, phase="checkout-head")
    check_patch_paths(files, clone)

    kind = chk.test_run("test-at-head", test_argv, clone, env, wrap)
    report["green_before"] = kind == "green"
    if kind != "green":
        raise Inconclusive(f"the test is not green at head (result: {kind}); nothing else was run")

    envx = trusted.prepare()
    trusted.run("apply", "-R", "--whitespace=nowarn", "-", phase="revert", stdin=patch, envx=envx)
    _, dirty = trusted.out("status", "--porcelain=v1", "--untracked-files=all", envx=envx)
    if not dirty:
        raise Inconclusive("the revert changed nothing in the clone")
    kind = chk.test_run("test-after-revert", test_argv, clone, env, wrap)
    report["red_after_revert"] = True if kind == "red" else (False if kind == "green" else None)

    restore(trusted)
    after_restore = clean_evidence(trusted)
    report["tree_clean"] = {"after_restore": after_restore}
    if kind == "green":
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "vacuous"
    elif kind == "red":
        k3 = chk.test_run("test-after-restore", test_argv, clone, env, wrap)
        report["green_after_restore"] = k3 == "green"
        report["tree_clean"]["final"] = clean_evidence(trusted)
        state["conclusion"] = "bites" if k3 == "green" else "error"
        state["why"] = f"the test was not green after the restore (result: {k3})"
    else:
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "error"
        state["why"] = "the test failed after the revert in a way that is not a clean failure (see commands)"
    report["tree_clean"]["clean"] = bool(after_restore["clean"] and report["tree_clean"]["final"]["clean"])
    state["after"] = snapshot(src_git, root)


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
    if not args.red_exit_codes or 0 in args.red_exit_codes:
        raise Inconclusive("--red-exit must be non-empty and must not contain 0")
    if args.timeout < 1 or args.max_output < 1:
        raise Inconclusive("--timeout and --max-output must be positive")
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

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
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
            after_settle = snapshot(state["src_git"], state["root"])
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

    private = state.get("private")
    if private:
        try:
            remove_tree(private, state["private_parent"])
        except Exception as e:  # reported, never hidden
            report["cleanup_error_private"] = f"{type(e).__name__}: {e}"

    work = state.get("work")
    if work:
        keep = verdict != "bites" or (args is not None and args.keep)
        if keep:
            report["kept"] = True
            print(f"mutation_check: kept the disposable copy at {work}", file=sys.stderr)
        else:
            try:
                remove_tree(work, state["scratch"])
                report["kept"] = False
            except Exception as e:  # leaving a directory behind is reported, never hidden
                report["kept"] = True
                report["cleanup_error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
