"""Prove that a test bites: revert a fix in a disposable clone, expect red, restore, expect green.

    python3 scripts/mutation_check.py --repo PATH --head SHA --revert SHA_OR_PATCH --test "CMD" [--scratch DIR]

Steps, all in a clone made with `git clone --local --no-hardlinks` and a detached checkout of --head:
run --test at head (must pass), revert the fix (`git apply -R` of the named commit's non-test hunks, or of a
given patch file), run --test (must fail), restore (`git checkout --force --detach SHA` and `git clean`), run
--test again (must pass), then prove the restore: `git status --porcelain` is empty and `git diff SHA` is empty
(checked after the restore and again after the last run). The source repository's status, HEAD, symbolic ref, refs,
stash, .git/config, .git/index and the content of every dirty or untracked file are hashed before and after.

Output is one JSON object on stdout: head, base, reverted, commands, green_before, red_after_revert,
green_after_restore, tree_clean (with evidence), source_repo_unchanged (with the hashes), verdict and reason.

Exit codes (a usage error is also reported as JSON and exits 3, so 2 always means "vacuous"):
    0  bites: green, red after the revert, green after the restore, tree clean, source repository unchanged
    2  vacuous: the test stays green with the fix reverted (tree clean, source unchanged)
    3  inconclusive: anything else. Not green at head, a revert that does not apply or changes nothing, a test that
       failed with an exit code outside --red-exit, timed out, produced too much output or could not start, an
       unclean tree, a changed source repository, an unresolvable argument, any unexpected error. Never a pass.

The copy is deleted on a bites result and kept (its path is in the JSON, "work_dir", and on stderr) on every other
result so evidence is not thrown away; --keep keeps it on bites too.

Security. This runs code from the commit under review. The tool itself:
  * never uses a shell: every subprocess gets an explicit argument list; --test is split once with shlex into an
    argv. A repository path, a SHA or a patch path is data: `--end-of-options` and absolute paths stop a leading
    "-" from being read as an option.
  * gives every subprocess a short environment: PATH (relative entries dropped, so the clone cannot plant a
    program), LANG, and HOME, XDG_*, PIP_CACHE_DIR, npm_config_cache, TMPDIR and the Python bytecode cache
    pointed into the disposable directory (beside the clone, not inside it, so the tree check stays honest). No
    token or other inherited variable is passed. Git reads no user or system configuration in the clone.
  * runs git with core.hooksPath=/dev/null, core.fsmonitor=false and no template directory, so no hook or
    filter driver of the source or the head runs (the clone gets none of the source's .git/config, hooks or
    drivers; filters named by the head's .gitattributes simply do not exist there).
  * puts a timeout, an output cap and a process-group kill on every subprocess (--timeout, --max-output).
  * refuses a revert patch (given or generated) that has an absolute path, a ".." component, a ".git"
    component, a quoted name, or a path that goes through a symlink in the clone; refuses a symlinked or
    oversize patch file.
  * never works in the maintainer's checkout: refuses a scratch directory that equals, contains or lies inside
    the repository root. The default scratch is a new directory under ~/.local/share/doia-mutation/, never /tmp.
  * best effort no network: the test commands run under `unshare --net --map-current-user` when the host allows
    it ("network_isolation": "unshare" in the JSON), else without ("none"; --require-no-network then makes that
    inconclusive). Git steps are not wrapped.

What cannot be enforced (this is not a sandbox). The test is arbitrary code running as the invoking user. It can
write anywhere that user can write, including the source repository by absolute path, and read anything that user
can read. The tool detects a changed source repository and reports it (`source_repo_unchanged: false`, exit 3); it
cannot prevent or undo it. Without `unshare` there is no network isolation. A process that leaves its process
group escapes the timeout kill. The test can alter the clone's own .git, so the clone's evidence is only as good
as the test is honest; the source repository is what the hashes protect. The isolated environment is not CI: a
test that needs a variable or a cache the allowlist removes can be reported inconclusive. A revert that breaks the
import of the module under test makes the test fail like a real bite does: read `commands` output before trusting
`bites`. Standard library only."""

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
            GIT_CONFIG_NOSYSTEM="1",
            GIT_TERMINAL_PROMPT="0",
        )
    return env


class Git:
    """Git with hooks, fsmonitor and (for the clone) all user configuration switched off."""

    def __init__(self, exe, env, record=None):
        self.exe, self.env, self.record = exe, env, record
        self.last_err = ""

    def argv(self, *args):
        return [self.exe, "-c", f"core.hooksPath={DEVNULL_PATH}", "-c", "core.fsmonitor=false", *args]

    def run(self, cwd, *args, phase=None, stdin=None, ok=(0,)):
        res = run(self.argv(*args), cwd=cwd, env=self.env, timeout=GIT_TIMEOUT, cap=GIT_CAP, stdin=stdin)
        if self.record is not None and phase:
            self.record(phase, self.argv(*args), res)
        if res.status != "exited" or res.code not in ok:
            raise Inconclusive(f"git {args[0]} failed ({res.status}, exit {res.code}): {res.tail[-300:]}")
        return res

    def out(self, cwd, *args):
        """(exit code, stdout bytes) of a read-only git command, bounded by GIT_TIMEOUT and GIT_CAP."""
        res = run(self.argv(*args), cwd=cwd, env=self.env, timeout=GIT_TIMEOUT, cap=GIT_CAP, keep=True)
        if res.status != "exited":
            raise Inconclusive(f"git {args[-1]} did not finish cleanly ({res.status}): {res.tail[-200:]}")
        self.last_err = res.err[-300:]
        return res.code, res.data


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
    snap["status_sha256"] = sha256(raw)
    snap["dirty_content_sha256"] = sha256(
        b"\n".join(p + b"\0" + hash_file(os.path.join(os.fsencode(root), p)).encode() for p in status_paths(raw))
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
        if not p or p.startswith("/") or "\0" in p:
            raise Inconclusive(f"revert patch path refused (absolute or empty): {p!r}")
        parts = p.split("/")
        if any(c in ("", ".", "..") or c.lower() == ".git" for c in parts):
            raise Inconclusive(f"revert patch path refused (dot component or .git): {p!r}")
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
    code, data = git.out(root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    sha = data.decode().strip()
    if code != 0 or not SHA_RE.fullmatch(sha):
        raise Inconclusive(f"{what} does not resolve to a commit")
    return sha


def build_revert(git, root, spec, head):
    """Return (patch bytes, files reverted, description of the source of the patch)."""
    info = {}
    file_first = spec.startswith(".") or "/" in spec
    commit = None
    if not file_first:
        code, data = git.out(root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{spec}^{{commit}}")
        if code == 0 and SHA_RE.fullmatch(data.decode().strip()):
            commit = data.decode().strip()
    if commit is None:
        path = os.path.abspath(spec)
        try:
            st = os.lstat(path)
        except OSError as e:
            raise Inconclusive("--revert is neither a commit nor a patch file") from e
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
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
        "--binary",
        "--full-index",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
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


def network_wrapper(probe_env):
    """argv prefix that removes the network from a test, or [] when the host cannot."""
    exe = shutil.which("unshare", path=clean_path())
    if not exe:
        return []
    argv = [exe, "--net", "--map-current-user", "--", "true"]
    res = run(argv, cwd=None, env=probe_env, timeout=20, cap=4096)
    if res.status == "exited" and res.code == 0:
        return argv[:-2] + ["--"]
    return []


def clean_evidence(git, clone, sha):
    code, status = git.out(clone, "status", "--porcelain=v1", "--untracked-files=all")
    dcode, _ = git.out(clone, "diff", "--exit-code", "--quiet", "--no-ext-diff", "--no-textconv", sha, "--")
    code2, head = git.out(clone, "rev-parse", "HEAD")
    ok = code == 0 and not status and dcode == 0 and head.decode().strip() == sha
    return {
        "status_porcelain": status.decode("utf-8", "replace")[:2000],
        "status_exit": code,
        "diff_exit": dcode,
        "head_is_sha": code2 == 0 and head.decode().strip() == sha,
        "clean": ok,
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
        self.record(phase, argv, res, {"result": kind, "network_wrapper": bool(wrap)})
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
    before = snapshot(src_git, root)
    state["before"] = before

    work = tempfile.mkdtemp(prefix="doia-mc-", dir=scratch)  # mkdtemp creates it 0700
    state["work"], state["scratch"] = work, scratch
    report["work_dir"] = work
    for d in ("home", "cache", "config", "data", "pip-cache", "npm-cache", "tmp", "pycache", "template"):
        os.mkdir(os.path.join(work, d), 0o700)
    clone = os.path.join(work, "repo")
    chk = Checker(args, report)
    env = sanitized_env(os.path.join(work, "home"), work)
    git = Git(git_exe, env, chk.record)
    wrap = network_wrapper(env)
    report["network_isolation"] = "unshare" if wrap else "none"
    if args.require_no_network and not wrap:
        raise Inconclusive("--require-no-network but this host cannot isolate the network (no usable unshare)")
    report["environment"] = sorted(env)

    git.run(
        work,
        "clone",
        "--local",
        "--no-hardlinks",
        "--no-checkout",
        f"--template={os.path.join(work, 'template')}",
        "--",
        root,
        clone,
        phase="clone",
    )
    git.run(clone, "checkout", "--detach", "--force", "--end-of-options", head, phase="checkout-head")
    check_patch_paths(files, clone)

    kind = chk.test_run("test-at-head", test_argv, clone, env, wrap)
    report["green_before"] = kind == "green"
    if kind != "green":
        raise Inconclusive(f"the test is not green at head (result: {kind}); nothing else was run")

    git.run(clone, "apply", "-R", "--whitespace=nowarn", "-", phase="revert", stdin=patch)
    _, dirty = git.out(clone, "status", "--porcelain=v1", "--untracked-files=all")
    if not dirty:
        raise Inconclusive("the revert changed nothing in the clone")
    kind = chk.test_run("test-after-revert", test_argv, clone, env, wrap)
    report["red_after_revert"] = {"green": False, "red": True}.get(kind) if kind in ("green", "red") else None
    if kind == "error":
        report["red_after_revert"] = None

    restore(git, clone, head)
    after_restore = clean_evidence(git, clone, head)
    report["tree_clean"] = {"after_restore": after_restore}
    if kind == "green":
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "vacuous"
    elif kind == "red":
        k3 = chk.test_run("test-after-restore", test_argv, clone, env, wrap)
        report["green_after_restore"] = k3 == "green"
        report["tree_clean"]["final"] = clean_evidence(git, clone, head)
        state["conclusion"] = "bites" if k3 == "green" else "error"
        state["why"] = f"the test was not green after the restore (result: {k3})"
    else:
        report["green_after_restore"] = None
        report["tree_clean"]["final"] = after_restore
        state["conclusion"] = "error"
        state["why"] = "the test failed after the revert in a way that is not a clean failure (see commands)"
    report["tree_clean"]["clean"] = bool(after_restore["clean"] and report["tree_clean"]["final"]["clean"])
    state["after"] = snapshot(src_git, root)


def restore(git, clone, sha):
    git.run(clone, "checkout", "--force", "--detach", "--end-of-options", sha, phase="restore-checkout")
    git.run(clone, "clean", "-ffdxq", phase="restore-clean")


def build_parser():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise Inconclusive(f"usage: {message}")

    p = Parser(prog="mutation_check.py", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    p.add_argument("--repo", required=True, help="source git repository (read only)")
    p.add_argument("--head", required=True, help="commit to test (resolved to a full SHA)")
    p.add_argument("--revert", required=True, help="commit whose non-test hunks are reverted, or a patch file")
    p.add_argument("--test", required=True, help="the test command, split once with shlex into an argv (no shell)")
    p.add_argument("--base", help="optional base commit, recorded in the output")
    p.add_argument("--scratch", help="parent directory of the disposable copy (default ~/.local/share/doia-mutation)")
    p.add_argument("--keep", action="store_true", help="keep the disposable copy even on bites")
    p.add_argument("--timeout", type=int, default=600, help="seconds per test run (default 600)")
    p.add_argument("--max-output", type=int, default=1048576, help="output bytes per test run before it is killed")
    p.add_argument("--red-exit", default="1", help="comma list of exit codes that count as a failing test (default 1)")
    p.add_argument("--require-no-network", action="store_true", help="inconclusive when the network cannot be removed")
    return p


def parse(argv):
    args = build_parser().parse_args(argv)
    try:
        args.red_exit_codes = {int(c) for c in args.red_exit.split(",") if c.strip()}
    except ValueError as e:
        raise Inconclusive("--red-exit must be a comma list of integers") from e
    if not args.red_exit_codes or 0 in args.red_exit_codes:
        raise Inconclusive("--red-exit must be non-empty and must not contain 0")
    if args.timeout < 1 or args.max_output < 1:
        raise Inconclusive("--timeout and --max-output must be positive")
    return args


class Interrupted(Exception):
    pass


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
        "source_repo_unchanged": None,
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

    before, after = state.get("before"), state.get("after")
    if before is not None:
        report["source_repo_state"] = {"before": before, "after": after}
        report["source_repo_unchanged"] = after is not None and before == after
    verdict = "inconclusive"
    conclusion = state.get("conclusion")
    if conclusion in ("bites", "vacuous") and not report["reason"]:
        if not report["tree_clean"].get("clean"):
            report["reason"] = "the clone's tree is not clean after the restore"
        elif not report["source_repo_unchanged"]:
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
