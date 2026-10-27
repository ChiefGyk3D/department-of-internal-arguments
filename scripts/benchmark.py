"""Benchmark corpus, rubric and scorer (DoIA #47, unit 1 of #3). No model is run here and nothing is spent.

    python3 scripts/benchmark.py --check                 corpus is well formed; its commits and diffs still resolve
    python3 scripts/benchmark.py --reproduce             re-prove every seed against the git history
    python3 scripts/benchmark.py --score RESULTS.jsonl   print the table for a file of result records

The corpus is evals/benchmark/corpus.json, the record format and the rubric are in evals/benchmark-schema.md. The
scorer reads files and prints text; it never writes, and it never touches policies/model-routing.md (that table
changes only in a pull request that cites the numbers). Standard library only. `--reproduce` runs the probe scripts in
evals/benchmark/probes, and the unit tests of mutated copies, against checkouts of this repository's own history, in a
temporary directory.

The match between a reported finding and a labeled one is a keyword heuristic (see the schema): it cannot detect a
report written to hit the patterns. The controls are a verbatim quote from the code at the head commit, at least two
distinct evidence patterns, the class and the path, and a human reading the false-findings column."""

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from summarize_log import valid_model

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "evals" / "benchmark"
CORPUS = BENCH / "corpus.json"
PROBES = BENCH / "probes"
INPUTS = BENCH / "inputs"

ROLES = ("reviewer", "adversarial-reviewer", "brief-writer")
DIFF_ROLES = ("reviewer", "adversarial-reviewer")
CLASSES = (
    "input-validation",
    "ownership-trust",
    "filesystem-safety",
    "output-injection",
    "concurrency",
    "error-reporting",
    "test-gap",
    "dead-code",
    "doc-accuracy",
    "convention",
    "brief-gap",
)
SEVERITIES = ("blocking", "important", "minor")
SCOPES = ("in_diff", "pre_existing", "brief")
OUTCOMES = ("completed", "refused", "filter-cutoff", "error", "timeout")
REFUSAL_KINDS = ("refusal", "filter-cutoff")
MAX_EVIDENCE = 2000
MAX_FINDINGS = 50
MAX_TEXT = 300
MAX_QUOTE = 200
MAX_TOKENS = 10**10
MAX_SECONDS = 10**7
MAX_CONTEXT = 10**8
MAX_RUN = 10**6
DEFAULT_EVIDENCE_MIN = 2
# Resource bounds. Every input of this tool can be hostile (a pull request may change the corpus; a results file comes
# from outside), so each is read with a cap, each child process has a time and an output cap, and archives have caps.
MAX_CORPUS_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 512 * 1024
MAX_RESULTS_BYTES = 64 * 1024 * 1024
MAX_CHILD_OUTPUT = 1024 * 1024
MAX_GIT_OUTPUT = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 20000
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_DIFF_LINE = 5000
GIT_TIMEOUT = 120
PROBE_TIMEOUT = 120
TEST_TIMEOUT = 600
REGEX_BUDGET_S = 2.0
MAX_PATTERN_LEN = 400
MAX_REPEAT_BOUND = 1000
MAX_PATH_LEN = 300
NOT_EXACT = frozenset({"latest", "inherit", "default", "auto", "current", "newest"})
API_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9.]+)+", re.ASCII)
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}", re.ASCII)
HEX40_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
COMMIT_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}\.py", re.ASCII)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
PRIVATE_PATH_RE = re.compile("/ho" + "me/|/Us" + "ers/|" + r"\\Us" + r"ers\\|~/")
OBSERVED, NOT_OBSERVED = "OBSERVED", "NOT-OBSERVED"
BAD_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
# Default-ignorable and blank-looking code points that the categories above do not all cover (variation selectors,
# the combining grapheme joiner, the Hangul fillers, the braille blank, the tag characters).
INVISIBLE = frozenset(chr(c) for c in (0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0))
INVISIBLE_RANGES = (
    (0x180B, 0x180F),
    (0x2000, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0xFE00, 0xFE0F),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def exact_model_id(model):
    """True for an exact model id. An Ollama tag needs a size or a version and may not be `latest` (a moving tag). A
    hyphenated API id needs a version number and a word after the vendor name (claude-sonnet-5-5, gpt-5.1-codex,
    gemini-2.5-pro, o3-mini). A family name (sonnet, opus), a bare vendor and major (claude-3, gpt-5) and a
    moving alias (anything with latest, inherit, default) are not exact."""
    if not isinstance(model, str) or not model or model != model.strip():
        return False
    if ":" in model:
        return model.rsplit(":", 1)[1] != "latest" and valid_model(model)
    if not API_ID_RE.fullmatch(model) or len(model) > 64:
        return False
    parts = model.split("-")
    rest = parts[1:]
    return any(c.isdigit() for c in model) and any(p[0].isalpha() for p in rest) and not NOT_EXACT.intersection(parts)


def _clean(text, allow_whitespace=False):
    """No control, format (zero-width, bidi), separator, unassigned, default-ignorable or blank-looking character
    (any space but the ASCII one); optionally keep \\n \\r \\t."""
    for ch in text:
        if allow_whitespace and ch in "\n\r\t":
            continue
        if _bad_char(ch):
            return False
    return True


def _bad_char(ch):
    cat = unicodedata.category(ch)
    o = ord(ch)
    return (
        cat in BAD_CATEGORIES
        or ch in INVISIBLE
        or any(a <= o <= b for a, b in INVISIBLE_RANGES)
        or (cat == "Zs" and ch != " ")
    )


def _norm(text):
    return " ".join(text.split())


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _read_capped(path, cap, what):
    with Path(path).open("rb") as fh:
        data = fh.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"{what} is over {cap} bytes")
    return data.decode("utf-8")


def _loads(text, what):
    """json.loads with duplicate keys refused and a hostile nesting depth turned into an ordinary error."""
    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except RecursionError:
        raise ValueError(f"{what} is nested too deeply") from None


def load_corpus(path=CORPUS):
    return _loads(_read_capped(path, MAX_CORPUS_BYTES, "corpus"), "corpus")


# ---------------------------------------------------------------- hostile-input defenses: paths, patterns, processes


def rel_path_error(rel):
    """Why `rel` is not a safe relative path (text), or None. Syntax only: no filesystem access."""
    if not isinstance(rel, str) or not rel:
        return "must be a non-empty relative path"
    if len(rel) > MAX_PATH_LEN or "\0" in rel or "\\" in rel or not _clean(rel):
        return "holds a NUL, backslash, control character or is too long"
    if rel[0] in "/-:":
        return "must be relative and may not start with /, - or :"
    if any(part in ("", ".", "..") for part in rel.split("/")):
        return "may not hold an empty, . or .. component"
    return None


def confine(root, rel):
    """The path root/rel, proven to stay inside root: relative only, no empty, . or .. component, no NUL, no symlink
    in any component, and the real path inside the real root. Raises ValueError otherwise. The path need not exist."""
    err = rel_path_error(rel)
    if err:
        raise ValueError(f"path {str(rel)[:60]!r} {err}")
    real_root = Path(os.path.realpath(root))
    cur = real_root
    for part in rel.split("/"):
        cur = cur / part
        if cur.is_symlink():
            raise ValueError(f"path {rel!r} goes through a symlink")
    if not Path(os.path.realpath(cur)).is_relative_to(real_root):
        raise ValueError(f"path {rel!r} leaves {root}")
    return cur


class RegexBudget(ValueError):
    """A pattern took longer than its budget to match (catastrophic backtracking)."""


@contextlib.contextmanager
def _regex_budget(seconds):
    """A hard time limit for the matching inside the block. Python's matcher checks for signals, so SIGALRM can stop a
    runaway pattern; this works in the main thread on POSIX and is skipped elsewhere (the static check at corpus load is
    the other defense)."""
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def boom(signum, frame):
        raise RegexBudget(f"a regular expression took more than {seconds} s to match")

    old = signal.signal(signal.SIGALRM, boom)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _search(pattern, text, flags=0, budget=None):
    with _regex_budget(REGEX_BUDGET_S if budget is None else budget):
        return re.search(pattern, text, flags) is not None


def _spans(pattern, text, limit=8, budget=None):
    out = []
    with _regex_budget(REGEX_BUDGET_S if budget is None else budget):
        for m in re.finditer(pattern, text):
            if m.end() > m.start():
                out.append((m.start(), m.end(), m.group(0)))
                if len(out) >= limit:
                    break
    return out


def pattern_errors(pattern):
    """Why a corpus pattern is refused (list of text). Conservative: no repeat nested in a repeat, no alternation or
    group reference inside a repeat that can run more than once, no repeat bound above MAX_REPEAT_BOUND, a bounded
    length. A pattern that passes cannot backtrack exponentially; `_regex_budget` is the second defense."""
    if len(pattern) > MAX_PATTERN_LEN:
        return [f"longer than {MAX_PATTERN_LEN} characters"]
    try:
        from re import _constants as k
        from re import _parser as parser

        tree = parser.parse(pattern)
    except re.error as exc:
        return [f"bad regular expression ({exc})"]
    except ImportError:
        return ["cannot analyze patterns on this Python"]
    errs = []
    repeats = {k.MAX_REPEAT, k.MIN_REPEAT, getattr(k, "POSSESSIVE_REPEAT", k.MAX_REPEAT)}

    def walk(items, inside):
        for op, av in items:
            if op in repeats:
                lo, hi, sub = av
                wide = hi > 1
                if hi != k.MAXREPEAT and hi > MAX_REPEAT_BOUND:
                    errs.append(f"repeat bound above {MAX_REPEAT_BOUND}")
                if wide and inside:
                    errs.append("a repeat inside a repeat")
                walk(sub, inside or wide)
            elif op == k.BRANCH:
                if inside:
                    errs.append("an alternation inside a repeat")
                for branch in av[1]:
                    walk(branch, inside)
            elif op == k.SUBPATTERN:
                walk(av[3], inside)
            elif op in (k.ASSERT, k.ASSERT_NOT):
                walk(av[1], inside)
            elif op == k.GROUPREF:
                errs.append("a group reference")
            elif op == getattr(k, "ATOMIC_GROUP", None):
                walk(av, inside)

    walk(tree, False)
    return sorted(set(errs))


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run(cmd, *, timeout, env=None, cwd=None, max_output=None):
    """Run a child in its own session and return (CompletedProcess, truncated). stdout and stderr are capped at
    max_output bytes each (a marker is appended and the child's process group is killed when output was cut). On a
    timeout, an error or an interrupt the whole process group is killed, not only the child, and reaped. A stray
    grandchild that outlives the child is killed as soon as the child has exited."""
    max_output = MAX_CHILD_OUTPUT if max_output is None else max_output
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    truncated = False
    swept = False
    sel = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        for f in bufs:
            os.set_blocking(f.fileno(), False)
            sel.register(f, selectors.EVENT_READ)
        while sel.get_map():
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)
            for key, _ in sel.select(min(left, 0.2)):
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                buf = bufs[key.fileobj]
                room = max_output - len(buf)
                buf += chunk[: max(room, 0)]
                if len(chunk) > room:
                    truncated = True
            if truncated:
                break
            if not swept and proc.poll() is not None:
                _kill_group(proc)  # the child is gone: whatever still runs in its group is a stray
                swept = True
        if truncated:
            _kill_group(proc)
        proc.wait(timeout=max(deadline - time.monotonic(), 0.1))
    except BaseException:
        if proc.returncode is None:
            _kill_group(proc)
        proc.wait()
        raise
    finally:
        if proc.returncode is None:  # never signal a group whose leader was already reaped (its id may be reused)
            _kill_group(proc)
        sel.close()
        proc.stdout.close()
        proc.stderr.close()
    out, err = bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr])
    if truncated:
        marker = f"\n[output truncated at {max_output} bytes]\n".encode()
        out, err = out + marker, err + marker
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err), truncated


# ---------------------------------------------------------------- corpus structure


def _is_int(v, low=0):
    return isinstance(v, int) and not isinstance(v, bool) and v >= low


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _regex_errors(label, patterns):
    if not (isinstance(patterns, list) and patterns and all(isinstance(p, str) and p for p in patterns)):
        return [f"{label}: must be a non-empty list of regular expressions"]
    errs = []
    for p in patterns:
        for why in pattern_errors(p):
            errs.append(f"{label}: refused pattern {p[:60]!r}: {why}")
    return errs


FINDING_KEYS = {
    "id",
    "class",
    "severity",
    "round",
    "scope",
    "summary",
    "source",
    "paths",
    "evidence_any",
    "fixed",
    "at_base",
    "repro",
}
FINDING_OPTIONAL = {"evidence_min", "quotes", "touched", "absent_from_input", "proxy"}
TASK_KEYS = {"id", "roles", "kind", "title", "findings"}
DIFF_KEYS = {"base", "head", "diff_sha256"}
BRIEF_KEYS = {"input_file", "input_sha256"}


def corpus_errors(c, check_files=False, probes_dir=PROBES, bench_dir=BENCH):
    """Every structural fault in a corpus, as text. Needs no git and no network."""
    errs = []
    if not isinstance(c, dict) or set(c) != {"version", "tasks", "dropped"} or c.get("version") != 1:
        return ["corpus must be an object with exactly version 1, tasks and dropped"]
    if any(PRIVATE_PATH_RE.search(s) for s in _strings(c)):
        errs.append("corpus holds a path that looks private (a home directory)")
    tasks = c["tasks"]
    if not (isinstance(tasks, list) and tasks):
        return errs + ["tasks must be a non-empty list"]
    by_key = {}
    seen_tasks = set()
    for t in tasks:
        tid = t.get("id") if isinstance(t, dict) else None
        if not (isinstance(tid, str) and SLUG_RE.fullmatch(tid)):
            errs.append(f"task id must be a slug: {tid!r}")
            continue
        if tid in seen_tasks:
            errs.append(f"duplicate task id {tid}")
        seen_tasks.add(tid)
        for f in t.get("findings", []) if isinstance(t.get("findings"), list) else []:
            if isinstance(f, dict) and isinstance(f.get("id"), str):
                by_key.setdefault(f"{tid}/{f['id']}", f)
    for t in tasks:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str):
            continue
        errs += _task_errors(t, by_key, check_files, probes_dir, bench_dir)
    dropped = c["dropped"]
    if not isinstance(dropped, list):
        errs.append("dropped must be a list")
    else:
        for d in dropped:
            ok = isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"]
            if not (ok and isinstance(d.get("reason"), str) and d["reason"].strip()):
                errs.append(f"dropped entry needs an id and a reason: {d!r}")
    return errs


def _task_errors(t, by_key, check_files, probes_dir, bench_dir):
    tid = t["id"]
    kind = t.get("kind")
    errs = []
    extra = set(t) - TASK_KEYS - (DIFF_KEYS if kind == "diff" else BRIEF_KEYS if kind == "brief" else set())
    missing = TASK_KEYS - set(t)
    if kind not in ("diff", "brief"):
        return [f"{tid}: kind must be diff or brief"]
    missing |= (DIFF_KEYS if kind == "diff" else BRIEF_KEYS) - set(t)
    if missing or extra:
        errs.append(f"{tid}: missing {sorted(missing)} extra {sorted(extra)}")
    roles = t.get("roles")
    allowed = DIFF_ROLES if kind == "diff" else ("brief-writer",)
    if not (isinstance(roles, list) and roles and all(r in allowed for r in roles) and len(set(roles)) == len(roles)):
        errs.append(f"{tid}: roles must be a non-empty subset of {allowed}")
    if kind == "diff":
        for key in ("base", "head"):
            if not (isinstance(t.get(key), str) and HEX40_RE.fullmatch(t[key])):
                errs.append(f"{tid}: {key} must be a full 40-character commit id")
        if not (isinstance(t.get("diff_sha256"), str) and HEX64_RE.fullmatch(t["diff_sha256"])):
            errs.append(f"{tid}: diff_sha256 must be 64 hex characters")
    else:
        if not (isinstance(t.get("input_sha256"), str) and HEX64_RE.fullmatch(t["input_sha256"])):
            errs.append(f"{tid}: input_sha256 must be 64 hex characters")
        if isinstance(t.get("input_file"), str) and rel_path_error(t["input_file"]):
            errs.append(f"{tid}: input_file {t['input_file'][:60]!r} {rel_path_error(t['input_file'])}")
        elif check_files and isinstance(t.get("input_file"), str):
            try:
                path = confine(bench_dir, t["input_file"])
                if not path.is_file():
                    errs.append(f"{tid}: input file missing: {t['input_file']}")
                elif hashlib.sha256(path.read_bytes()).hexdigest() != t.get("input_sha256"):
                    errs.append(f"{tid}: input file changed since it was recorded: {t['input_file']}")
            except ValueError as exc:
                errs.append(f"{tid}: {exc}")
    if not isinstance(t.get("title"), str) or not t.get("title"):
        errs.append(f"{tid}: title must be text")
    findings = t.get("findings")
    if not (isinstance(findings, list) and findings):
        return errs + [f"{tid}: needs at least one finding"]
    ids = set()
    for f in findings:
        errs += _finding_errors(tid, kind, f, ids, by_key, check_files, probes_dir)
    return errs


def _finding_errors(tid, kind, f, ids, by_key, check_files, probes_dir):
    if not isinstance(f, dict):
        return [f"{tid}: finding must be an object"]
    fid = f.get("id")
    label = f"{tid}/{fid}"
    errs = []
    if not (isinstance(fid, str) and SLUG_RE.fullmatch(fid)):
        return [f"{tid}: finding id must be a slug: {fid!r}"]
    if fid in ids:
        errs.append(f"{label}: duplicate finding id")
    ids.add(fid)
    missing = FINDING_KEYS - set(f)
    extra = set(f) - FINDING_KEYS - FINDING_OPTIONAL
    if missing or extra:
        return errs + [f"{label}: missing {sorted(missing)} extra {sorted(extra)}"]
    if f["class"] not in CLASSES:
        errs.append(f"{label}: class must be one of {CLASSES}")
    if f["severity"] not in SEVERITIES:
        errs.append(f"{label}: severity must be one of {SEVERITIES}")
    if not _is_int(f["round"], 1):
        errs.append(f"{label}: round must be an integer of 1 or more")
    for key in ("summary", "source"):
        if not (isinstance(f[key], str) and 0 < len(f[key]) <= MAX_TEXT):
            errs.append(f"{label}: {key} must be short text")
    if not (isinstance(f["paths"], list) and f["paths"] and all(isinstance(p, str) and p for p in f["paths"])):
        errs.append(f"{label}: paths must be a non-empty list of paths")
    elif kind != "brief":
        for path in f["paths"]:
            if rel_path_error(path):
                errs.append(f"{label}: path {path[:60]!r} {rel_path_error(path)}")
    errs += _regex_errors(f"{label}: evidence_any", f["evidence_any"])
    if "evidence_min" in f:
        n = f["evidence_min"]
        if not (_is_int(n, 1) and isinstance(f["evidence_any"], list) and n <= len(f["evidence_any"])):
            errs.append(f"{label}: evidence_min must be between 1 and the number of evidence patterns")
    elif isinstance(f["evidence_any"], list) and len(f["evidence_any"]) < DEFAULT_EVIDENCE_MIN:
        errs.append(f"{label}: needs at least {DEFAULT_EVIDENCE_MIN} evidence patterns, or an evidence_min of 1")
    if f["fixed"] is not None and not (isinstance(f["fixed"], str) and HEX40_RE.fullmatch(f["fixed"])):
        errs.append(f"{label}: fixed must be null or a full commit id")
    if f["at_base"] not in ("observed", "not-observed", "n/a"):
        errs.append(f"{label}: at_base must be observed, not-observed or n/a (the code did not exist at the base)")
    errs += _scope_errors(label, kind, f)
    errs += _repro_errors(label, kind, f, by_key, check_files, probes_dir)
    return errs


def _scope_errors(label, kind, f):
    errs = []
    if kind == "brief":
        if f["scope"] != "brief":
            errs.append(f"{label}: a brief finding has scope brief")
        for key in ("quotes", "touched"):
            if key in f:
                errs.append(f"{label}: {key} belongs to diff findings")
        return errs
    if f["scope"] not in ("in_diff", "pre_existing"):
        return [f"{label}: scope must be in_diff or pre_existing"]
    q = f.get("quotes")
    if not (isinstance(q, list) and q and all(isinstance(x, str) and 8 <= len(_norm(x)) <= MAX_QUOTE for x in q)):
        errs.append(f"{label}: quotes must be a non-empty list of code lines (8 to {MAX_QUOTE} characters)")
    if f["scope"] == "in_diff":
        errs += _regex_errors(f"{label}: touched", f.get("touched"))
    elif "touched" in f:
        errs.append(f"{label}: a pre_existing finding has no touched line")
    return errs


def _repro_errors(label, kind, f, by_key, check_files, probes_dir):
    r = f["repro"]
    if not isinstance(r, dict):
        return [f"{label}: repro must be an object"]
    k = r.get("kind")
    if kind == "brief":
        if k != "ref" or set(r) != {"kind", "finding"}:
            return [f"{label}: a brief finding must reproduce through a ref"]
        errs = _regex_errors(f"{label}: absent_from_input", f.get("absent_from_input"))
        ref = by_key.get(r["finding"])
        if ref is None or (ref.get("repro") or {}).get("kind") == "ref":
            errs.append(f"{label}: ref {r['finding']!r} is not a diff-task finding")
        if f["paths"] != ["brief"]:
            errs.append(f"{label}: a brief finding's path is brief")
        return errs
    errs = []
    if "absent_from_input" in f:
        errs.append(f"{label}: absent_from_input belongs to brief findings")
    proxy_needed = k == "pattern" and r.get("present") is False
    if proxy_needed != ("proxy" in f):
        errs.append(f"{label}: a repro that only checks something is absent must say so in proxy (and only that one)")
    if "proxy" in f and not (isinstance(f["proxy"], str) and f["proxy"].strip()):
        errs.append(f"{label}: proxy must say what the repro stands in for")
    if k == "probe":
        if set(r) != {"kind", "probe", "args"} or not isinstance(r["probe"], str):
            return errs + [f"{label}: probe repro needs kind, probe and args"]
        if not (isinstance(r["args"], list) and all(isinstance(a, str) for a in r["args"])):
            return errs + [f"{label}: probe args must be a list of strings"]
        if not NAME_RE.fullmatch(r["probe"]):
            return errs + [f"{label}: probe must be a plain file name ending in .py"]
        if check_files:
            try:
                if not confine(probes_dir, r["probe"]).is_file():
                    errs.append(f"{label}: probe file missing: {r['probe']}")
            except ValueError as exc:
                errs.append(f"{label}: {exc}")
        return errs
    if k == "pattern":
        if set(r) != {"kind", "path", "regex", "present"} or not isinstance(r["present"], bool):
            return errs + [f"{label}: pattern repro needs kind, path, regex and present (true or false)"]
        if not isinstance(r["regex"], str):
            return errs + [f"{label}: pattern regex must be text"]
        if rel_path_error(r["path"]):
            errs.append(f"{label}: repro path {str(r['path'])[:60]!r} {rel_path_error(r['path'])}")
        return errs + _regex_errors(f"{label}: repro regex", [r["regex"]])
    if k == "mutation":
        keys = {"kind", "path", "old", "new", "tests_file", "k"}
        ok = set(r) == keys and all(isinstance(r[x], str) for x in ("path", "old", "new", "tests_file"))
        if not (ok and r["old"] and r["old"] != r["new"] and (r["k"] is None or isinstance(r["k"], str))):
            return errs + [f"{label}: mutation repro needs kind, path, old, new, tests_file and k"]
        if rel_path_error(r["path"]):
            errs.append(f"{label}: repro path {r['path'][:60]!r} {rel_path_error(r['path'])}")
        if not NAME_RE.fullmatch(r["tests_file"]):
            errs.append(f"{label}: tests_file must be a plain file name ending in .py")
        return errs
    return errs + [f"{label}: repro kind must be probe, pattern or mutation"]


# ---------------------------------------------------------------- rubric


def _enough_hits(patterns, text, need):
    """True when `need` different patterns each match a different, non-overlapping, non-empty piece of the text. Two
    patterns that are satisfied by the same words (\\.\\. and \\.\\./x on "../x") count once."""
    spans = []
    for p in patterns:
        found = _spans(p, text)
        if found:
            spans.append(found)
    if len(spans) < need:
        return False

    def pick(i, chosen):
        if len(chosen) >= need:
            return True
        if len(spans) - i < need - len(chosen):
            return False
        for s in spans[i]:
            if all(s[1] <= c[0] or c[1] <= s[0] for c in chosen) and all(s[2] != c[2] for c in chosen):
                if pick(i + 1, [*chosen, s]):
                    return True
        return pick(i + 1, chosen)

    return pick(0, [])


def _matches(task, e, r):
    """Does the reported finding r count for expected finding e? Class, path, a verbatim quote of the head code (diff
    tasks) and at least evidence_min distinct patterns of the model's own evidence."""
    if e["class"] != r["class"] or r["path"] not in e["paths"]:
        return False
    if not _enough_hits(e["evidence_any"], r["evidence"], e.get("evidence_min", DEFAULT_EVIDENCE_MIN)):
        return False
    if task["kind"] == "diff":
        text = _norm(r["evidence"])
        return any(_norm(q) in text for q in e["quotes"])
    return True


def match_findings(task, reported):
    """Deterministic matching of reported findings to a task's expected ones.

    A reported finding is a candidate for an expected one when `_matches` holds. The assignment is a maximum
    bipartite matching (augmenting paths), reports taken in a canonical order and expected findings in corpus order, so
    the numbers do not depend on the order of the report even when evidence patterns overlap. A candidate report that
    loses its expected finding to another report is a duplicate (counted, not false). A report with no usable class,
    path or evidence counts as false and as unevidenced; any other report with no candidate is false."""
    expected = task["findings"]
    well_formed, false, uneved = [], 0, 0
    for r in reported:
        ok_shape = (
            isinstance(r, dict)
            and all(isinstance(r.get(k), str) and r.get(k) for k in ("class", "path", "evidence"))
            and r["class"] in CLASSES
        )
        if ok_shape:
            well_formed.append(r)
        else:
            false += 1
            uneved += 1
    well_formed.sort(key=lambda r: (r["class"], r["path"], r["evidence"]))
    cands = [[i for i, e in enumerate(expected) if _matches(task, e, r)] for r in well_formed]
    owner = {}

    def place(ri, seen):
        for ei in cands[ri]:
            if ei in seen:
                continue
            seen.add(ei)
            if ei not in owner or place(owner[ei], seen):
                owner[ei] = ri
                return True
        return False

    for ri in range(len(well_formed)):
        place(ri, set())
    with_candidates = sum(bool(c) for c in cands)
    false += len(cands) - with_candidates
    return {
        "matched": [expected[i]["id"] for i in sorted(owner)],
        "false": false,
        "duplicates": with_candidates - len(owner),
        "unevidenced": uneved,
    }


def rounds_avoided(task, matched_ids):
    """(rounds whose every expected finding was matched, rounds in the history). A round is one fix round the real
    history needed; matching all of a round's findings is a fix round that review would have avoided."""
    rounds = defaultdict(list)
    for f in task["findings"]:
        rounds[f["round"]].append(f["id"])
    got = sum(all(i in matched_ids for i in ids) for ids in rounds.values())
    return got, len(rounds)


def score_record(rec, c):
    task = next(t for t in c["tasks"] if t["id"] == rec["task"])
    m = match_findings(task, rec["findings"])
    got, total = rounds_avoided(task, m["matched"])
    by_scope = {s: [0, 0] for s in SCOPES}  # [expected, matched]
    for f in task["findings"]:
        by_scope[f["scope"]][0] += 1
        by_scope[f["scope"]][1] += f["id"] in m["matched"]
    return {
        "role": rec["role"],
        "model": rec["model"],
        "client": rec["client"],
        "client_version": rec["client_version"],
        "task": rec["task"],
        "expected": len(task["findings"]),
        "matched": len(m["matched"]),
        "matched_ids": m["matched"],
        "by_scope": by_scope,
        "false": m["false"],
        "duplicates": m["duplicates"],
        "unevidenced": m["unevidenced"],
        "reported": len(rec["findings"]),
        "rounds_avoided": got,
        "rounds_total": total,
        "wall_clock_s": rec["wall_clock_s"],
        "tokens_input": rec["tokens"]["input"],
        "tokens_output": rec["tokens"]["output"],
        "refusals": len(rec["refusals"]),
    }


# ---------------------------------------------------------------- result records

REQUIRED = (
    "task",
    "role",
    "run",
    "client",
    "client_version",
    "model",
    "date",
    "commit",
    "tools_enabled",
    "outcome",
    "findings",
    "wall_clock_s",
    "tokens",
    "approval_ref",
    "corpus_hidden",
    "refusals",
)
OPTIONAL = ("quantization", "context", "cost_usd", "notes")


def parse_result(line, n, c, repo=None):
    """One result record: the #24 result fields (task for case, client, client_version, model, quantization, context,
    tools_enabled, commit, date, outcome, cost) plus the benchmark fields. A malformed record is an error, never a
    skipped row, so a typo cannot quietly shrink the sample. An abbreviated commit must resolve in `repo`."""
    if len(line) > MAX_RECORD_BYTES:
        raise ValueError(f"line {n}: over {MAX_RECORD_BYTES} bytes")
    try:
        rec = _loads(line, "record")
    except ValueError as exc:  # includes json.JSONDecodeError
        raise ValueError(f"line {n}: not JSON ({exc})") from None
    if not isinstance(rec, dict):
        raise ValueError(f"line {n}: not an object")
    missing = [k for k in REQUIRED if k not in rec]
    extra = [k for k in rec if k not in REQUIRED + OPTIONAL]
    if missing or extra:
        raise ValueError(f"line {n}: missing {missing} extra {extra}")

    def bad(msg):
        raise ValueError(f"line {n}: {msg}")

    for key in ("client", "client_version", "approval_ref"):
        v = rec[key]
        if not (isinstance(v, str) and v.strip() and len(v) <= 200 and _clean(v)):
            bad(f"{key} must be non-empty text without control characters (an exact version and an approval reference)")
    if rec["corpus_hidden"] is not True:
        bad(
            "corpus_hidden must be true: a scored run never shows the model corpus.json, because the labels, quotes "
            "and evidence patterns in it are public and a report that copies them scores full recall"
        )
    if not exact_model_id(rec["model"]):
        bad("model must be an exact model id, not a family name or an alias (claude-sonnet-5-5, gemma4:12b)")
    task = next((t for t in c["tasks"] if t["id"] == rec["task"]), None) if isinstance(rec["task"], str) else None
    if task is None:
        bad(f"unknown task {rec['task']!r}")
    if rec["role"] not in ROLES:
        bad(f"role must be one of {ROLES}")
    if rec["role"] not in task["roles"]:
        bad(f"task {task['id']} is not scored for role {rec['role']}")
    if not _is_int(rec["run"], 1) or rec["run"] > MAX_RUN:
        bad(f"run must be an integer from 1 to {MAX_RUN}")
    _check_date(rec["date"], bad)
    _check_commit(rec["commit"], repo, bad, task)
    tools = rec["tools_enabled"]
    if not (isinstance(tools, list) and all(isinstance(x, str) and x and len(x) <= 64 and _clean(x) for x in tools)):
        bad("tools_enabled must be a list of names")
    if rec["outcome"] not in OUTCOMES:
        bad(f"outcome must be one of {OUTCOMES}")
    w = rec["wall_clock_s"]
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or not 0 < w <= MAX_SECONDS:
        bad(f"wall_clock_s must be a positive number of seconds, at most {MAX_SECONDS}")
    tk = rec["tokens"]
    if not (
        isinstance(tk, dict)
        and set(tk) == {"input", "output"}
        and all(_is_int(v) and v <= MAX_TOKENS for v in tk.values())
    ):
        bad(f"tokens must be {{input, output}} integers from 0 to {MAX_TOKENS} (cost is required)")
    if "cost_usd" in rec:
        v = rec["cost_usd"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= MAX_SECONDS:
            bad("cost_usd must be a non-negative number")
    if "notes" in rec and not (isinstance(rec["notes"], str) and len(rec["notes"]) <= 300 and _clean(rec["notes"])):
        bad("notes must be short text without control characters")
    local = ":" in rec["model"]
    if local:
        q, ctx = rec.get("quantization"), rec.get("context")
        if not (
            isinstance(q, str) and q.strip() and len(q) <= 64 and _clean(q) and _is_int(ctx, 1) and ctx <= MAX_CONTEXT
        ):
            bad("a local model needs quantization (text) and context (a positive integer)")
    elif "quantization" in rec or "context" in rec:
        bad("quantization and context are only local-model fields")
    _check_findings(rec["findings"], bad)
    refusals = rec["refusals"]
    if not isinstance(refusals, list):
        bad("refusals must be a list")
    for r in refusals:
        if not (isinstance(r, dict) and set(r) == {"kind", "note"} and isinstance(r["note"], str)):
            bad("each refusal is {kind, note}")
        if r["kind"] not in REFUSAL_KINDS or len(r["note"]) > 200 or not _clean(r["note"]):
            bad(f"refusal kind must be one of {REFUSAL_KINDS} and the note short text")
    if rec["outcome"] in REFUSAL_KINDS + ("refused",) and not refusals:
        bad("an outcome of refused or filter-cutoff needs at least one refusal entry")
    return rec


def _check_date(value, bad):
    if not (isinstance(value, str) and DATE_RE.fullmatch(value)):
        bad("date must be YYYY-MM-DD")
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        bad("date is not a real calendar date")
    if day > datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=1):
        bad("date is in the future")


def _check_commit(value, repo, bad, task=None):
    """A record names its commit by the full 40-character id: an abbreviation can resolve to another object in a fork.
    With a repository given, the commit must exist in it and must be the checkout the task pins: for a diff task the
    task's `head`; for a brief task a commit whose tree holds the task's recorded input file with the recorded
    `input_sha256` (a brief task pins its input, not a commit). Without a repository the id is only checked for shape,
    and the output of `--score` says so (it is headed 'commits: NOT VERIFIED')."""
    if not (isinstance(value, str) and COMMIT_RE.fullmatch(value)):
        bad("commit must be the full 40-character lowercase hex id of a commit of this repository")
    if repo is None:
        return
    if not (_is_git_repo(repo) and _has_commit(repo, value)):
        bad("commit is not in the given repository (fetch it, or pass --skip-commit-check to skip the check)")
    if task is None:
        return
    if task["kind"] == "diff":
        if value != task["head"]:
            bad(f"commit is not the pinned head of task {task['id']} ({task['head']}): the run must use that checkout")
    else:
        rel = (BENCH.relative_to(ROOT) / task["input_file"]).as_posix()
        blob = _git(repo, "show", f"{value}:{rel}", check=False)
        if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != task["input_sha256"]:
            bad(f"commit does not hold the recorded input {task['input_file']} of task {task['id']} unchanged")


def _check_findings(findings, bad):
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        bad(f"findings must be a list of at most {MAX_FINDINGS}")
    for i, f in enumerate(findings):
        shape = isinstance(f, dict) and set(f) == {"class", "path", "evidence"}
        if not (shape and all(isinstance(v, str) for v in f.values())):
            bad(f"findings[{i}] must be exactly {{class, path, evidence}} as text")
        if len(f["evidence"]) > MAX_EVIDENCE:
            bad(f"findings[{i}].evidence is longer than {MAX_EVIDENCE} characters")
        if len(f["class"]) > 64 or len(f["path"]) > 300:
            bad(f"findings[{i}] class or path is too long")
        if not (_clean(f["class"]) and _clean(f["path"]) and _clean(f["evidence"], allow_whitespace=True)):
            bad(f"findings[{i}] holds a control character")


def load_results(path, c, repo=None):
    rows, keys = [], set()
    total = 0
    with Path(path).open("rb") as fh:
        for n, raw in enumerate(iter(lambda: fh.readline(MAX_RECORD_BYTES + 1), b""), 1):
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError(f"line {n}: over {MAX_RECORD_BYTES} bytes")
            total += len(raw)
            if total > MAX_RESULTS_BYTES:
                raise ValueError(f"results file is over {MAX_RESULTS_BYTES} bytes")
            line = raw.decode("utf-8")
            if not line.strip():
                continue
            rec = parse_result(line, n, c, repo)
            key = (rec["task"], rec["role"], rec["model"], rec["client"], rec["client_version"], rec["run"])
            if key in keys:
                raise ValueError(f"line {n}: duplicate record for {key}")
            keys.add(key)
            rows.append(rec)
    if not rows:
        raise ValueError("results file is empty")
    return rows


def _pct(a, b):
    return "n/a" if b == 0 else f"{100 * a / b:.1f}"


def summarize(rows, c):
    """The table per role, model (with quantization and context for a local one) and client version. Code does every
    count; sums for counts and tokens, a mean for wall-clock. Recall is also reported for findings the diff touches
    and for pre-existing ones separately."""
    groups = defaultdict(list)
    for rec in rows:
        key = (
            rec["role"],
            rec["model"],
            rec.get("quantization", ""),
            rec.get("context", 0),
            rec["client"],
            rec["client_version"],
        )
        groups[key].append(score_record(rec, c))
    lines = [
        "| Role | Model | Client | Tasks | Recall % | In-diff recall % | Pre-existing recall % | Precision % | "
        "False findings | Fix rounds avoided | Wall clock s (mean) | Tokens in | Tokens out | Refusals |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (role, model, quant, ctx, client, version), ms in sorted(groups.items()):
        label = f"{model} ({quant}, {ctx})" if quant else model
        pool = sum(role in t["roles"] for t in c["tasks"])
        exp, hit, fls, rep = (sum(m[k] for m in ms) for k in ("expected", "matched", "false", "reported"))
        sc = {s: (sum(m["by_scope"][s][0] for m in ms), sum(m["by_scope"][s][1] for m in ms)) for s in SCOPES}
        got, tot = sum(m["rounds_avoided"] for m in ms), sum(m["rounds_total"] for m in ms)
        wall = sum(m["wall_clock_s"] for m in ms) / len(ms)
        lines.append(
            f"| {role} | {label} | {client} {version} | {len({m['task'] for m in ms})} of {pool} | {_pct(hit, exp)} | "
            f"{_pct(sc['in_diff'][1], sc['in_diff'][0])} | {_pct(sc['pre_existing'][1], sc['pre_existing'][0])} | "
            f"{_pct(hit, rep)} | {fls} | {got} of {tot} | {wall:.1f} | "
            f"{sum(m['tokens_input'] for m in ms)} | {sum(m['tokens_output'] for m in ms)} | "
            f"{sum(m['refusals'] for m in ms)} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- git: resolve and reproduce

# Every diff option that a user's or repository's git configuration could change is pinned on the command line, and
# the configuration files are not read, so the digest is the same on any machine with a git that supports the options.
DIFF_ARGS = (
    "--full-index",
    "--binary",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--no-relative",
    "--indent-heuristic",
    "--diff-algorithm=myers",
    "-U3",
    "--inter-hunk-context=0",
    "-O/dev/null",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
DIFF_CONFIG = (
    "diff.suppressBlankEmpty=false",
    "diff.context=3",
    "diff.interHunkContext=0",
    "diff.algorithm=myers",
    "diff.renames=false",
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "core.quotePath=true",
    "core.attributesFile=" + os.devnull,
)


def _git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        LC_ALL="C",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_ATTR_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_LITERAL_PATHSPECS="1",
    )
    env.pop("XDG_CONFIG_HOME", None)
    return env


def _git(repo, *args, check=True):
    pins = [x for kv in DIFF_CONFIG for x in ("-c", kv)]
    p, cut = _run(
        ["git", *pins, "-C", str(repo), *args], timeout=GIT_TIMEOUT, env=_git_env(), max_output=MAX_GIT_OUTPUT
    )
    if cut:
        raise RuntimeError(f"git {args[0]} produced more than {MAX_GIT_OUTPUT} bytes")
    if check and p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return p


@contextlib.contextmanager
def _object_view(repo):
    """A bare repository with no configuration, no info/attributes and no working tree that borrows the objects of
    `repo` through an alternates file. Git reads attributes from the working tree, the index and info/attributes of the
    repository it runs in, none of which can be neutralized by a command-line option on every git version; running in
    this view makes them empty, and the commits are read as objects, never from the working tree."""
    common = _git(repo, "rev-parse", "--git-common-dir").stdout.decode().strip()
    objects = (Path(repo) / common).resolve() / "objects"
    with tempfile.TemporaryDirectory() as tmp:
        p, _ = _run(["git", "init", "--bare", "-q", tmp], timeout=GIT_TIMEOUT, env=_git_env())
        if p.returncode:
            raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "git init failed")
        (Path(tmp) / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (Path(tmp) / "objects" / "info" / "alternates").write_text(f"{objects}\n", encoding="utf-8")
        yield tmp


def _run_diff(repo, base, head, *paths):
    extra = ["--", *paths] if paths else []
    return _git(repo, "diff", *DIFF_ARGS, base, head, *extra).stdout


def diff_bytes(repo, base, head, *paths):
    with _object_view(repo) as view:
        return _run_diff(view, base, head, *paths)


def diff_digest(repo, base, head):
    """sha256 of the full diff base..head in a fixed form (see DIFF_ARGS), independent of git configuration."""
    return hashlib.sha256(diff_bytes(repo, base, head)).hexdigest()


def _has_clone(repo):
    repo = Path(repo)
    if not repo.is_dir():
        return False
    top = _git(repo, "rev-parse", "--show-toplevel", check=False)
    if top.returncode or Path(top.stdout.decode().strip()).resolve() != repo.resolve():
        return False
    shallow = _git(repo, "rev-parse", "--is-shallow-repository", check=False)
    return shallow.returncode == 0 and shallow.stdout.decode().strip() == "false"


def _has_commit(repo, sha):
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _is_git_repo(repo):
    return Path(repo).is_dir() and _git(repo, "rev-parse", "--git-dir", check=False).returncode == 0


def git_version():
    p, _ = _run(["git", "--version"], timeout=GIT_TIMEOUT, env=_git_env())
    return p.stdout.decode("utf-8", "replace").strip() or "git version unknown"


def _commits(c):
    for t in c["tasks"]:
        if t["kind"] == "diff":
            yield t["id"], "base", t["base"]
            yield t["id"], "head", t["head"]
            for f in t["findings"]:
                if f["fixed"]:
                    yield f"{t['id']}/{f['id']}", "fixed", f["fixed"]


FETCH_HINT = "git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'"


def _touched_lines(diff_text):
    return [ln[1:] for ln in diff_text.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def _added_text(diff_text):
    return _norm(" ".join(ln[1:] for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++")))


def check_commits(repo, c):
    """('ok'|'fail'|'not-run', errors). Local git only, no network. Not-run when there is no full (non-shallow)
    clone at repo; fail when a recorded commit is missing, a recorded diff changed, a quote is not in the head code,
    an in_diff finding touches no line of the diff, or a pre_existing one has a quote on an added line."""
    if not _has_clone(repo):
        return "not-run", []
    errs = []
    missing = set()
    for where, what, sha in _commits(c):
        if not _has_commit(repo, sha):
            missing.add(sha)
            errs.append(f"{where}: {what} commit {sha[:12]} is not in this clone (try: {FETCH_HINT})")
    for t in c["tasks"]:
        if t["kind"] != "diff" or {t["base"], t["head"]} & missing:
            continue
        diff = diff_bytes(repo, t["base"], t["head"])
        if hashlib.sha256(diff).hexdigest() != t["diff_sha256"]:
            errs.append(
                f"{t['id']}: the diff {t['base'][:12]}..{t['head'][:12]} does not match diff_sha256: "
                "either history was rewritten or this git (version or configuration) renders diffs differently"
            )
            continue
        for f in t["findings"]:
            errs += _scope_check(repo, t, f)
    return ("fail" if errs else "ok"), errs


def _scope_check(repo, t, f):
    label = f"{t['id']}/{f['id']}"
    errs = []
    texts = [_show(repo, t["head"], p) for p in f["paths"]]
    heads = [_norm(x) for x in texts if x is not None]
    for q in f["quotes"]:
        if not any(_norm(q) in h for h in heads):
            errs.append(f"{label}: quote not found in the head code of its paths: {q[:60]!r}")
    paths = [p for p in f["paths"] if _show(repo, t["head"], p) is not None or _show(repo, t["base"], p) is not None]
    text = diff_bytes(repo, t["base"], t["head"], *paths).decode("utf-8", "replace") if paths else ""
    if f["scope"] == "in_diff":
        lines = _touched_lines(text)
        if not any(_search(p, ln[:MAX_DIFF_LINE]) for p in f["touched"] for ln in lines):
            errs.append(f"{label}: labeled in_diff but no touched line of the diff matches {f['touched']}")
    else:  # pre_existing: no quote of it may be a line this diff adds, whatever the base shows
        added = _added_text(text)
        for q in f["quotes"]:
            if _norm(q) in added:
                errs.append(f"{label}: labeled pre_existing but its quote is added by the diff: {q[:60]!r}")
    return errs


def _check_member(m):
    """True for a regular file or directory; False for anything else (links, devices: not extracted). Raises for a
    member name that would leave the destination."""
    parts = Path(m.name).parts
    if Path(m.name).is_absolute() or ".." in parts:
        raise RuntimeError(f"archive member escapes the destination: {m.name}")
    return m.isfile() or m.isdir()


def _safe_members(tf):
    for m in tf.getmembers():
        if _check_member(m):
            yield m


class _Capped(io.RawIOBase):
    """A readable stream that fails once more than `cap` bytes have been read from `raw`."""

    def __init__(self, raw, cap):
        self.raw, self.cap, self.seen = raw, cap, 0

    def readable(self):
        return True

    def readinto(self, buf):
        data = self.raw.read(len(buf))
        self.seen += len(data)
        if self.seen > self.cap:
            raise RuntimeError(f"archive is over {self.cap} bytes")
        buf[: len(data)] = data
        return len(data)


def _extract_stream(tf, dest):
    dest_root = Path(dest).resolve()
    files = total = 0
    for m in tf:
        if not _check_member(m):
            continue
        if m.isfile():
            files += 1
            total += m.size
            if files > MAX_ARCHIVE_FILES:
                raise RuntimeError(f"archive holds more than {MAX_ARCHIVE_FILES} files")
            if m.size > MAX_MEMBER_BYTES:
                raise RuntimeError(f"archive member {m.name!r} is over {MAX_MEMBER_BYTES} bytes")
            if total > MAX_EXPANDED_BYTES:
                raise RuntimeError(f"archive expands to more than {MAX_EXPANDED_BYTES} bytes")
        _write_member(tf, m, dest_root)


def _write_member(tf, m, dest_root):
    """Write one vetted member (a regular file or a directory) under dest_root. The path is joined and re-confined
    here, so nothing depends on the tarfile extraction filters, which differ between 3.11 patch releases."""
    target = (dest_root / m.name).resolve()
    if target != dest_root and dest_root not in target.parents:
        raise RuntimeError(f"archive member escapes the destination: {m.name}")
    if m.isdir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    src = tf.extractfile(m)
    with target.open("wb") as out:
        shutil.copyfileobj(src, out)


def _extract(repo, sha, dest):
    """Stream `git archive sha` into dest with caps on the stream, the file count and the expanded size; the git
    process group is killed on any failure or after GIT_TIMEOUT."""
    pins = [x for kv in DIFF_CONFIG for x in ("-c", kv)]
    with _object_view(repo) as view, tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(
            ["git", *pins, "-C", str(view), "archive", sha],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errf,
            env=_git_env(),
            start_new_session=True,
        )
        watchdog = threading.Timer(GIT_TIMEOUT, _kill_group, [proc])
        watchdog.start()
        try:
            try:
                with tarfile.open(fileobj=_Capped(proc.stdout, MAX_ARCHIVE_BYTES), mode="r|") as tf:
                    _extract_stream(tf, dest)
            except tarfile.TarError as exc:
                raise RuntimeError(f"git archive did not produce a readable archive ({exc})") from None
            code = proc.wait()
        except BaseException:
            _kill_group(proc)
            proc.wait()
            raise
        finally:
            watchdog.cancel()
            if proc.returncode is None:
                _kill_group(proc)
            proc.stdout.close()
        if code:
            errf.seek(0)
            raise RuntimeError(errf.read(2000).decode("utf-8", "replace").strip() or "git archive failed")


def _show(repo, sha, path):
    err = rel_path_error(path)
    if err:
        raise ValueError(f"path {str(path)[:60]!r} {err}")
    p = _git(repo, "show", f"{sha}:{path}", check=False)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def _tree(repo, sha, work):
    tree = Path(work) / f"tree-{sha[:12]}"
    if not tree.exists():
        tree.mkdir()
        _extract(repo, sha, tree)
    return tree


def _pattern_observed(repo, sha, repro):
    text = _show(repo, sha, repro["path"])
    found = text is not None and _search(repro["regex"], text, re.MULTILINE)
    return found == repro["present"]


def _run_env(scratch):
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(scratch),
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _probe_observed(repo, sha, repro, probes_dir, work):
    tree = _tree(repo, sha, work)
    scratch = Path(tempfile.mkdtemp(dir=work))
    if not NAME_RE.fullmatch(str(repro["probe"])):
        raise ValueError(f"probe name {str(repro['probe'])[:60]!r} is not a plain file name ending in .py")
    script = confine(probes_dir, repro["probe"])
    cmd = [sys.executable, "-I", str(script), str(tree), str(scratch), *repro["args"]]
    p, cut = _run(cmd, timeout=PROBE_TIMEOUT, env=_run_env(scratch), cwd=scratch)
    stdout, stderr = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    last = (stdout.strip().splitlines() or [""])[-1]
    if cut:
        raise RuntimeError(f"probe printed more than {MAX_CHILD_OUTPUT} bytes")
    if p.returncode != 0:
        raise RuntimeError(f"probe exit {p.returncode}: {(stderr.strip().splitlines() or ['no output'])[-1]}")
    if last.startswith(NOT_OBSERVED):
        return False, last
    if last.startswith(OBSERVED):
        return True, last
    raise RuntimeError(f"probe printed neither {OBSERVED} nor {NOT_OBSERVED}")


def _unittests_pass(tree, repro, scratch):
    if not NAME_RE.fullmatch(str(repro["tests_file"])):
        raise ValueError(f"tests_file {str(repro['tests_file'])[:60]!r} is not a plain file name ending in .py")
    cmd = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", repro["tests_file"]]
    if repro["k"]:
        cmd += ["-k", repro["k"]]
    p, cut = _run(cmd, timeout=TEST_TIMEOUT, env=_run_env(scratch), cwd=tree)
    return p.returncode == 0 and not cut


def _mutation_observed(repo, sha, repro, work):
    """The defect is a survivor: the tests pass on the unmutated tree and still pass once the stated change is made. If
    the code to change is no longer there, the defect is gone."""
    scratch = Path(tempfile.mkdtemp(dir=work))
    tree = scratch / "tree"
    shutil.copytree(_tree(repo, sha, work), tree)
    if not _unittests_pass(tree, repro, scratch):
        raise RuntimeError(f"the tests are not green on the unmutated tree at {sha[:12]}")
    target = confine(tree, repro["path"])
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    if text.count(repro["old"]) != 1:
        return False, "the code to mutate is not there (exactly once)"
    target.write_text(text.replace(repro["old"], repro["new"]), encoding="utf-8")
    survived = _unittests_pass(tree, repro, scratch)
    return survived, "mutation survived: the tests stayed green" if survived else "mutation killed: the tests failed"


def _defect_at(repo, sha, repro, probes_dir, work):
    if repro["kind"] == "pattern":
        return _pattern_observed(repo, sha, repro), repro["regex"]
    if repro["kind"] == "mutation":
        return _mutation_observed(repo, sha, repro, work)
    return _probe_observed(repo, sha, repro, probes_dir, work)


def reproduce(repo, c, probes_dir=PROBES, bench_dir=BENCH):
    """[{task, finding, ok, notes}]: each finding must show the defect at the head, not at the fix, and match its
    at_base expectation; a brief finding must also be absent from the recorded brief input."""
    results = []
    by_key = {f"{t['id']}/{f['id']}": (t, f) for t in c["tasks"] for f in t["findings"]}
    with tempfile.TemporaryDirectory() as work:
        for t in c["tasks"]:
            for f in t["findings"]:
                notes = []
                try:
                    ok = _reproduce_one(repo, t, f, by_key, probes_dir, bench_dir, work, notes)
                except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
                    ok = False
                    notes.append(f"error: {exc}")
                if "proxy" in f:
                    notes.append(f"proxy: {f['proxy']}")
                results.append({"task": t["id"], "finding": f["id"], "ok": ok, "notes": "; ".join(notes)})
    return results


def _reproduce_one(repo, t, f, by_key, probes_dir, bench_dir, work, notes):
    if t["kind"] == "brief":
        ref_task, ref = by_key[f["repro"]["finding"]]
        ok = _reproduce_one(repo, ref_task, ref, by_key, probes_dir, bench_dir, work, notes)
        text = confine(bench_dir, t["input_file"]).read_text(encoding="utf-8")
        for p in f["absent_from_input"]:
            if _search(p, text):
                ok = False
                notes.append(f"the brief input already covers it ({p!r})")
        return ok
    ok = True
    steps = (("defect at head", t["head"], True),)
    if f["at_base"] != "n/a":
        steps += (("base", t["base"], f["at_base"] == "observed"),)
    if f["fixed"]:
        steps += (("fix", f["fixed"], False),)
    for name, sha, want in steps:
        seen, detail = _defect_at(repo, sha, f["repro"], probes_dir, work)
        notes.append(f"{name}: {'observed' if seen else 'not observed'}")
        if seen != want:
            ok = False
            notes.append(f"expected {'observed' if want else 'not observed'} at {name} ({detail})")
    return ok


# ---------------------------------------------------------------- command line


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate the corpus; confirm commits and diffs resolve")
    ap.add_argument("--reproduce", action="store_true", help="re-prove every seed against the git history")
    ap.add_argument("--score", type=Path, metavar="RESULTS", help="print the table for a results file")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--probes", type=Path, default=PROBES)
    ap.add_argument(
        "--skip-commit-check",
        action="store_true",
        help="do not check that the commits named by result records exist in --repo (the output says so)",
    )
    a = ap.parse_args(argv)
    if not (a.check or a.reproduce or a.score):
        ap.error("give --check, --reproduce or --score RESULTS")
    try:
        c = load_corpus(a.corpus)
        errs = corpus_errors(c, check_files=True, probes_dir=a.probes)
        if errs:
            raise ValueError("corpus: " + "; ".join(errs))
        if a.score:
            repo = None if a.skip_commit_check else a.repo
            rows = load_results(a.score, c, repo)
            print("commits: NOT VERIFIED (--skip-commit-check)" if repo is None else f"commits: verified in {a.repo}")
            print()
            print(summarize(rows, c))
        if a.check or a.reproduce:
            status, cerrs = check_commits(a.repo, c)
            if status == "fail":
                raise ValueError("; ".join(cerrs))
            print(f"corpus ok: {len(c['tasks'])} tasks, {len(c['dropped'])} dropped; commits and diffs: {status}")
            if status == "not-run":
                print(f"not-run: no full clone at {a.repo} (absent or shallow), so no commit was checked")
                if a.reproduce:
                    return 2
        if a.reproduce:
            print(f"git: {git_version()}")
            res = reproduce(a.repo, c, probes_dir=a.probes)
            for r in res:
                print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['task']}/{r['finding']}: {r['notes']}")
            if not all(r["ok"] for r in res):
                raise ValueError(f"{sum(not r['ok'] for r in res)} of {len(res)} findings did not reproduce")
            print(f"reproduced: {len(res)} findings in {len(c['tasks'])} tasks")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
