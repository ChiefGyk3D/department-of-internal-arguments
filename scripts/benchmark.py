"""Benchmark corpus, rubric and scorer (DoIA #47, unit 1 of #3). No model is run here and nothing is spent.

    python3 scripts/benchmark.py --check                 corpus is well formed; its commits and diffs still resolve
    python3 scripts/benchmark.py --reproduce             re-prove every seed against the git history
    python3 scripts/benchmark.py --score RESULTS.jsonl   print the table for a file of result records

The corpus is evals/benchmark/corpus.json, the record format and the rubric are in evals/benchmark-schema.md. The
scorer reads files and prints text; it never writes, and it never touches policies/model-routing.md (that table
changes only in a pull request that cites the numbers). Standard library only. `--reproduce` runs the probe scripts in
evals/benchmark/probes against checkouts of this repository's own history, in a temporary directory."""

import argparse
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
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
OUTCOMES = ("completed", "refused", "filter-cutoff", "error", "timeout")
REFUSAL_KINDS = ("refusal", "filter-cutoff")
MAX_EVIDENCE = 2000
MAX_FINDINGS = 50
MAX_TEXT = 300
NOT_EXACT = frozenset({"latest", "inherit", "default", "auto", "current", "newest"})
API_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9.]+)+", re.ASCII)
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}", re.ASCII)
HEX40_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
COMMIT_RE = re.compile(r"[0-9a-f]{7,40}", re.ASCII)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
PRIVATE_PATH_RE = re.compile("/ho" + "me/|/Us" + "ers/|" + r"\\Us" + r"ers\\|~/")
OBSERVED, NOT_OBSERVED = "OBSERVED", "NOT-OBSERVED"


def exact_model_id(model):
    """True for an exact model id: an Ollama tag with a size or version, or a hyphenated API id that carries a version
    number (claude-sonnet-5-5, gpt-5.1-codex). A family name (sonnet, opus), a bare family-and-tier (claude-sonnet)
    and a moving alias (anything with latest, inherit, default) are not exact."""
    if not isinstance(model, str) or not model or model != model.strip():
        return False
    if ":" in model:
        return valid_model(model)
    if not API_ID_RE.fullmatch(model) or len(model) > 64:
        return False
    parts = model.split("-")
    return any(c.isdigit() for c in model) and not NOT_EXACT.intersection(parts)


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def load_corpus(path=CORPUS):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs)


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
        try:
            re.compile(p)
        except re.error as exc:
            errs.append(f"{label}: bad regular expression {p!r} ({exc})")
    return errs


FINDING_KEYS = {
    "id",
    "class",
    "severity",
    "round",
    "summary",
    "source",
    "paths",
    "evidence_any",
    "fixed",
    "at_base",
    "repro",
}
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
        if check_files and isinstance(t.get("input_file"), str):
            path = Path(bench_dir) / t["input_file"]
            if not path.is_file():
                errs.append(f"{tid}: input file missing: {t['input_file']}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != t.get("input_sha256"):
                errs.append(f"{tid}: input file changed since it was recorded: {t['input_file']}")
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
    extra = set(f) - FINDING_KEYS - {"absent_from_input"}
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
    errs += _regex_errors(f"{label}: evidence_any", f["evidence_any"])
    if f["fixed"] is not None and not (isinstance(f["fixed"], str) and HEX40_RE.fullmatch(f["fixed"])):
        errs.append(f"{label}: fixed must be null or a full commit id")
    if f["at_base"] not in ("observed", "not-observed", "n/a"):
        errs.append(f"{label}: at_base must be observed, not-observed or n/a (the code did not exist at the base)")
    errs += _repro_errors(label, kind, f, by_key, check_files, probes_dir)
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
    if "absent_from_input" in f:
        return [f"{label}: absent_from_input belongs to brief findings"]
    if k == "probe":
        if set(r) != {"kind", "probe", "args"} or not isinstance(r["probe"], str):
            return [f"{label}: probe repro needs kind, probe and args"]
        if not (isinstance(r["args"], list) and all(isinstance(a, str) for a in r["args"])):
            return [f"{label}: probe args must be a list of strings"]
        if check_files and not (Path(probes_dir) / r["probe"]).is_file():
            return [f"{label}: probe file missing: {r['probe']}"]
        return []
    if k == "pattern":
        if set(r) != {"kind", "path", "regex", "present"} or not isinstance(r["present"], bool):
            return [f"{label}: pattern repro needs kind, path, regex and present (true or false)"]
        return _regex_errors(f"{label}: repro regex", [r["regex"]]) if isinstance(r["regex"], str) else [label]
    return [f"{label}: repro kind must be probe or pattern"]


# ---------------------------------------------------------------- rubric


def _expected(task):
    return task["findings"]


def match_findings(task, reported):
    """Deterministic matching of reported findings to a task's expected ones.

    A reported finding matches an expected one only if the class label is the same, the path is one of the expected
    paths, and the model's own evidence matches one of the expected evidence patterns. Each expected finding is
    matched once; a second report of it is a duplicate (not false). A finding with no usable class, path or evidence
    counts as false and as unevidenced. Everything else unmatched is false. Report order does not change the counts."""
    expected = _expected(task)
    matched, false, dup, uneved = [], 0, 0, 0
    for r in reported:
        ok_shape = (
            isinstance(r, dict)
            and all(isinstance(r.get(k), str) and r.get(k) for k in ("class", "path", "evidence"))
            and r["class"] in CLASSES
        )
        if not ok_shape:
            false += 1
            uneved += 1
            continue
        cands = [
            e
            for e in expected
            if e["class"] == r["class"]
            and r["path"] in e["paths"]
            and any(re.search(p, r["evidence"]) for p in e["evidence_any"])
        ]
        if not cands:
            false += 1
            continue
        fresh = [e for e in cands if e["id"] not in matched]
        if fresh:
            matched.append(fresh[0]["id"])
        else:
            dup += 1
    return {"matched": matched, "false": false, "duplicates": dup, "unevidenced": uneved}


def rounds_avoided(task, matched_ids):
    """(rounds whose every expected finding was matched, rounds in the history). A round is one fix round the real
    history needed; matching all of a round's findings is a fix round that review would have avoided."""
    rounds = defaultdict(list)
    for f in _expected(task):
        rounds[f["round"]].append(f["id"])
    got = sum(all(i in matched_ids for i in ids) for ids in rounds.values())
    return got, len(rounds)


def score_record(rec, c):
    task = next(t for t in c["tasks"] if t["id"] == rec["task"])
    m = match_findings(task, rec["findings"])
    got, total = rounds_avoided(task, m["matched"])
    return {
        "role": rec["role"],
        "model": rec["model"],
        "task": rec["task"],
        "expected": len(_expected(task)),
        "matched": len(m["matched"]),
        "matched_ids": m["matched"],
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
    "refusals",
)
OPTIONAL = ("quantization", "context", "cost_usd", "notes")


def parse_result(line, n, c):
    """One result record: the #24 result fields (task for case, client, client_version, model, quantization, context,
    tools_enabled, commit, date, outcome, cost) plus the benchmark fields. A malformed record is an error, never a
    skipped row, so a typo cannot quietly shrink the sample."""
    try:
        rec = json.loads(line, object_pairs_hook=_pairs)
    except (json.JSONDecodeError, ValueError) as exc:
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
        if not (isinstance(rec[key], str) and rec[key].strip() and len(rec[key]) <= 200):
            bad(f"{key} must be non-empty text (an exact version and an approval reference are required)")
    if not exact_model_id(rec["model"]):
        bad("model must be an exact model id, not a family name or an alias (claude-sonnet-5-5, gemma4:12b)")
    task = next((t for t in c["tasks"] if t["id"] == rec["task"]), None) if isinstance(rec["task"], str) else None
    if task is None:
        bad(f"unknown task {rec['task']!r}")
    if rec["role"] not in ROLES:
        bad(f"role must be one of {ROLES}")
    if rec["role"] not in task["roles"]:
        bad(f"task {task['id']} is not scored for role {rec['role']}")
    if not _is_int(rec["run"], 1):
        bad("run must be an integer of 1 or more")
    if not (isinstance(rec["date"], str) and DATE_RE.fullmatch(rec["date"])):
        bad("date must be YYYY-MM-DD")
    if not (isinstance(rec["commit"], str) and COMMIT_RE.fullmatch(rec["commit"])):
        bad("commit must be a hex commit id of this repository")
    if not (isinstance(rec["tools_enabled"], list) and all(isinstance(x, str) for x in rec["tools_enabled"])):
        bad("tools_enabled must be a list of names")
    if rec["outcome"] not in OUTCOMES:
        bad(f"outcome must be one of {OUTCOMES}")
    w = rec["wall_clock_s"]
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
        bad("wall_clock_s must be a positive number of seconds")
    tk = rec["tokens"]
    if not (isinstance(tk, dict) and set(tk) == {"input", "output"} and all(_is_int(v) for v in tk.values())):
        bad("tokens must be {input, output} non-negative integers (cost is required)")
    if "cost_usd" in rec:
        v = rec["cost_usd"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            bad("cost_usd must be a non-negative number")
    local = ":" in rec["model"]
    if local:
        if not (
            isinstance(rec.get("quantization"), str) and rec["quantization"].strip() and _is_int(rec.get("context"), 1)
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
        if r["kind"] not in REFUSAL_KINDS:
            bad(f"refusal kind must be one of {REFUSAL_KINDS}")
    if rec["outcome"] in REFUSAL_KINDS + ("refused",) and not refusals:
        bad("an outcome of refused or filter-cutoff needs at least one refusal entry")
    return rec


def _check_findings(findings, bad):
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        bad(f"findings must be a list of at most {MAX_FINDINGS}")
    for i, f in enumerate(findings):
        if not (
            isinstance(f, dict)
            and set(f) == {"class", "path", "evidence"}
            and all(isinstance(v, str) for v in f.values())
        ):
            bad(f"findings[{i}] must be exactly {{class, path, evidence}} as text")
        if len(f["evidence"]) > MAX_EVIDENCE:
            bad(f"findings[{i}].evidence is longer than {MAX_EVIDENCE} characters")
        if len(f["class"]) > 64 or len(f["path"]) > 300:
            bad(f"findings[{i}] class or path is too long")


def load_results(path, c):
    rows, keys = [], set()
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            rec = parse_result(line, n, c)
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
    """The table per role and model. Code does every count; sums for counts and tokens, a mean for wall-clock."""
    groups = defaultdict(list)
    for rec in rows:
        groups[(rec["role"], rec["model"])].append(score_record(rec, c))
    lines = [
        "| Role | Model | Tasks | Recall % | Precision % | False findings | Fix rounds avoided | "
        "Wall clock s (mean) | Tokens in | Tokens out | Refusals |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (role, model), ms in sorted(groups.items()):
        pool = sum(role in t["roles"] for t in c["tasks"])
        exp, hit, fls = (sum(m[k] for m in ms) for k in ("expected", "matched", "false"))
        got, tot = sum(m["rounds_avoided"] for m in ms), sum(m["rounds_total"] for m in ms)
        wall = sum(m["wall_clock_s"] for m in ms) / len(ms)
        lines.append(
            f"| {role} | {model} | {len({m['task'] for m in ms})} of {pool} | {_pct(hit, exp)} | "
            f"{_pct(hit, hit + fls)} | {fls} | {got} of {tot} | {wall:.1f} | "
            f"{sum(m['tokens_input'] for m in ms)} | {sum(m['tokens_output'] for m in ms)} | "
            f"{sum(m['refusals'] for m in ms)} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- git: resolve and reproduce


def _git(repo, *args, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL",)}
    env["LC_ALL"] = "C"
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=120)
    if check and p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return p


def diff_digest(repo, base, head):
    """sha256 of the full diff base..head in a fixed form (no renames, no textconv, fixed prefixes, full index)."""
    out = _git(
        repo, "diff", "--full-index", "--binary", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
        "--src-prefix=a/", "--dst-prefix=b/", base, head,
    ).stdout  # fmt: skip
    return hashlib.sha256(out).hexdigest()


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


def _commits(c):
    for t in c["tasks"]:
        if t["kind"] == "diff":
            yield t["id"], "base", t["base"]
            yield t["id"], "head", t["head"]
            for f in t["findings"]:
                if f["fixed"]:
                    yield f"{t['id']}/{f['id']}", "fixed", f["fixed"]


FETCH_HINT = "git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'"


def check_commits(repo, c):
    """('ok'|'fail'|'not-run', errors). Local git only, no network. Not-run when there is no full (non-shallow)
    clone at repo; fail when a recorded commit is missing or a recorded diff changed."""
    if not _has_clone(repo):
        return "not-run", []
    errs = []
    missing = set()
    for where, what, sha in _commits(c):
        if not _has_commit(repo, sha):
            missing.add(sha)
            errs.append(f"{where}: {what} commit {sha[:12]} is not in this clone (try: {FETCH_HINT})")
    for t in c["tasks"]:
        if t["kind"] == "diff" and not {t["base"], t["head"]} & missing:
            if diff_digest(repo, t["base"], t["head"]) != t["diff_sha256"]:
                errs.append(f"{t['id']}: the diff {t['base'][:12]}..{t['head'][:12]} no longer matches diff_sha256")
    return ("fail" if errs else "ok"), errs


def _extract(repo, sha, dest):
    data = _git(repo, "archive", sha).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def _show(repo, sha, path):
    p = _git(repo, "show", f"{sha}:{path}", check=False)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def _pattern_observed(repo, sha, repro):
    text = _show(repo, sha, repro["path"])
    found = text is not None and re.search(repro["regex"], text, re.MULTILINE) is not None
    return found == repro["present"]


def _probe_observed(repo, sha, repro, probes_dir, work):
    tree = Path(work) / f"tree-{sha[:12]}"
    if not tree.exists():
        tree.mkdir()
        _extract(repo, sha, tree)
    scratch = Path(tempfile.mkdtemp(dir=work))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(scratch),
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cmd = [sys.executable, "-I", str(Path(probes_dir) / repro["probe"]), str(tree), str(scratch), *repro["args"]]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120, cwd=scratch)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    if p.returncode != 0:
        raise RuntimeError(f"probe exit {p.returncode}: {(p.stderr.strip().splitlines() or ['no output'])[-1]}")
    if last.startswith(NOT_OBSERVED):
        return False, last
    if last.startswith(OBSERVED):
        return True, last
    raise RuntimeError(f"probe printed neither {OBSERVED} nor {NOT_OBSERVED}")


def _defect_at(repo, sha, repro, probes_dir, work):
    if repro["kind"] == "pattern":
        return _pattern_observed(repo, sha, repro), repro["regex"]
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
                except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                    ok = False
                    notes.append(f"error: {exc}")
                results.append({"task": t["id"], "finding": f["id"], "ok": ok, "notes": "; ".join(notes)})
    return results


def _reproduce_one(repo, t, f, by_key, probes_dir, bench_dir, work, notes):
    if t["kind"] == "brief":
        ref_task, ref = by_key[f["repro"]["finding"]]
        ok = _reproduce_one(repo, ref_task, ref, by_key, probes_dir, bench_dir, work, notes)
        text = (Path(bench_dir) / t["input_file"]).read_text(encoding="utf-8")
        for p in f["absent_from_input"]:
            if re.search(p, text):
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
    a = ap.parse_args(argv)
    if not (a.check or a.reproduce or a.score):
        ap.error("give --check, --reproduce or --score RESULTS")
    try:
        c = load_corpus(a.corpus)
        errs = corpus_errors(c, check_files=True, probes_dir=a.probes)
        if errs:
            raise ValueError("corpus: " + "; ".join(errs))
        if a.score:
            print(summarize(load_results(a.score, c), c))
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
