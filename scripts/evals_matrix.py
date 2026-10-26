"""Behavioral-eval registry, result schema and matrix renderer. No model call, no network, no subprocess.

    python3 scripts/evals_matrix.py RESULTS.jsonl     print the Markdown matrix (exit 1 on any bad line)
    python3 scripts/evals_matrix.py --check           exit 1 if evals/behavioral-evals.md differs from the registry
    python3 scripts/evals_matrix.py --write           regenerate evals/behavioral-evals.md from evals/cases.json
    python3 scripts/evals_matrix.py --persona-hash    print the hash of the persona sources (stored in each record)

evals/cases.json is the single source for the exercises; evals/behavioral-evals.md is generated from it
(scripts/generate.py --check covers it). The record format is in evals/results-schema.md. Reading is strict:
a null, absent, mistyped, duplicated or unknown key or value is an error, never a default."""

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

from lib import CAPABILITY_ORDER, PACK_NAMES, ROLE_NAMES, ROOT
from summarize_log import valid_model

CLIENTS = ("claude-code", "codex", "copilot", "local")
OUTCOMES = ("pass", "fail", "needs-human", "unsupported", "refused", "error", "not-run")
UNMEASURED = ("unsupported", "not-run")  # nothing was measured, so there is no model to name and nothing to go stale
GRADINGS = ("mechanical", "judged")
COST_CLASSES = ("prompt-only", "fixture", "interactive")
CASE_ROLES = ("any", *ROLE_NAMES)
CASES_PATH = "evals/cases.json"
DOC_PATH = "evals/behavioral-evals.md"
EXAMPLE_PATH = "evals/example-results.jsonl"
UNSPECIFIED = "unspecified"
MAX_EVIDENCE = 200
MAX_LINE = 4096
MAX_TEXT = 600
PERSONA_DIRS = ("roles", "policies", "packs", "fragments")

CASE_KEYS = (
    "id",
    "subject",
    "roles",
    "packs",
    "clients",
    "setup",
    "input",
    "expect",
    "grading",
    "grading_reason",
    "cost_class",
)
REQUIRED_FIELDS = (
    "case",
    "client",
    "client_version",
    "model",
    "tools_enabled",
    "commit",
    "persona_hash",
    "date",
    "outcome",
    "evidence",
)
OPTIONAL_FIELDS = ("quantization", "context", "cost")

TEXT_RE = re.compile(r"[ -~]+")
SETUP_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,39}")
MODEL_CHARS_RE = re.compile(r"[A-Za-z0-9._:/-]{1,64}")
QUANT_RE = re.compile(r"[A-Za-z0-9_.-]{1,24}")
EVIDENCE_RE = re.compile(r"[A-Za-z0-9 ._:/#=,()+-]+")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
HASH_RE = re.compile(r"[0-9a-f]{64}")

# Identifier sweep for text that a record or the registry carries. Placeholders from the packs are allowed; any other
# callsign-, grid-, address-, home-path- or host-shaped string is refused.
PLACEHOLDERS = frozenset({"N0CALL", "N0TST", "FN31pr"})
FILE_SUFFIXES = frozenset({"md", "json", "jsonl", "txt", "py", "toml", "log", "yml", "yaml", "sh", "diff", "patch"})
HOST_RE = re.compile(r"(?<![\w-])(?:[A-Za-z0-9-]+\.)+([A-Za-z]{2,})(?![\w-])")
IPV4_RE = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
HOME_RE = re.compile("/ho" + "me/|/Us" + "ers/")
CALLSIGN_RE = re.compile(r"\b[A-Z]{1,2}[0-9][A-Z]{1,3}\b")
GRID_RE = re.compile(r"\b[A-R]{2}[0-9]{2}[a-x]{2}\b")
TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


def identifier_problems(text, hostnames=True):
    """Names of the private-identifier shapes found in the text (empty when it is clean)."""
    found = []
    if IPV4_RE.search(text):
        found.append("address-shaped string")
    if HOME_RE.search(text):
        found.append("home-path-shaped string")
    if hostnames and any(m.group(1).lower() not in FILE_SUFFIXES for m in HOST_RE.finditer(text)):
        found.append("hostname-shaped string")
    if any(m.group(0) not in PLACEHOLDERS for m in CALLSIGN_RE.finditer(text)):
        found.append("callsign-shaped string")
    if any(m.group(0) not in PLACEHOLDERS for m in GRID_RE.finditer(text)):
        found.append("grid-square-shaped string")
    if any(not re.fullmatch(r"[0-9a-f]+", m.group(0)) for m in TOKEN_RE.finditer(text)):
        found.append("long token that could be a secret")
    return found


def esc(text):
    """Escape text echoed into a Markdown table cell so it cannot add cells, markup or raw HTML."""
    text = text.replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([|`*_\[\]~#])", r"\\\1", text)


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _refuse_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def strict_json(text):
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_refuse_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON ({exc.msg})") from None
    except RecursionError:
        raise ValueError("nested too deeply") from None


def _is_int(value):
    return type(value) is int


def _is_text(value):
    return isinstance(value, str) and 0 < len(value) <= MAX_TEXT and TEXT_RE.fullmatch(value) is not None


def _unique_members(value, allowed):
    return (
        isinstance(value, list)
        and all(isinstance(v, str) and v in allowed for v in value)
        and len(set(value)) == len(value)
    )


# ---------------------------------------------------------------- registry


def parse_registry(text):
    data = strict_json(text)
    if not isinstance(data, dict) or set(data) != {"cases"}:
        raise ValueError('registry must be an object with exactly one key, "cases"')
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    for index, case in enumerate(cases, 1):
        _check_case(case, index)
    return cases


def _check_case(case, expected_id):
    if not isinstance(case, dict) or set(case) != set(CASE_KEYS):
        raise ValueError(f"case {expected_id}: must be an object with exactly the keys {list(CASE_KEYS)}")
    n = case["id"]
    if not _is_int(n) or n != expected_id:
        raise ValueError(f"case {expected_id}: id must be the integer {expected_id} (ids are 1..N in order)")

    def need(ok, message):
        if not ok:
            raise ValueError(f"case {n}: {message}")

    for key in ("subject", "input", "expect", "grading_reason"):
        need(_is_text(case[key]), f"{key} must be one line of printable ASCII, 1 to {MAX_TEXT} characters")
    need(
        isinstance(case["setup"], str) and SETUP_RE.fullmatch(case["setup"]) is not None,
        "setup must be a lowercase fixture name with hyphens, or none",
    )
    need(
        _unique_members(case["roles"], CASE_ROLES) and bool(case["roles"]),
        f"roles must be non-empty, from {CASE_ROLES}",
    )
    need(_unique_members(case["packs"], PACK_NAMES), f"packs must be a list of names from {PACK_NAMES}")
    need(
        _unique_members(case["clients"], CLIENTS) and bool(case["clients"]),
        f"clients must be non-empty, from {CLIENTS}",
    )
    need(case["grading"] in GRADINGS, f"grading must be one of {GRADINGS}")
    need(case["cost_class"] in COST_CLASSES, f"cost_class must be one of {COST_CLASSES}")
    named = [r for r in case["roles"] if r != "any"] if "any" not in case["subject"] else []
    need(all(name in case["subject"] for name in named + case["packs"]), "subject must name every pack and role")


def load_registry(path):
    try:
        return parse_registry(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not UTF-8") from None


# ---------------------------------------------------------------- generated doc

DOC_HEAD = (
    "<!-- Generated from evals/cases.json by scripts/evals_matrix.py --write. Do not edit; edit the registry"
    " and regenerate. -->\n"
    "\n"
    "# Behavioral exercises\n"
    "\n"
    "Run these by hand in each exact client and model before you enable a persona on real work. Record client and"
    " version, model artifact, quantization, context limit, active tools, prompt version (the commit of this"
    " repository), input, output and pass or fail. Repeat after a policy, model or runtime change.\n"
    "\n"
    "None of these has been executed against a model as of release 0.2.0 (row 26, the Codex adapter, is also"
    " unexecuted). They are supplied, not measured. What has been measured is in [pilot-2026-10.md](pilot-2026-10.md),"
    " and only for Claude Code.\n"
    "\n"
    "The rows are generated from [cases.json](cases.json), which also holds the fixture name, the clients each row"
    " applies to and its cost class. The Grading column says whether a check over the transcript or the fixture tree"
    " can decide the row (`mechanical`) or a human must (`judged`, which never auto-passes). Results use the record"
    " format in [results-schema.md](results-schema.md) and `scripts/evals_matrix.py` renders them as a matrix.\n"
    "\n"
    "| # | Role / pack | Input / setup | Observable expected behavior | Grading |\n"
    "| --- | --- | --- | --- | --- |\n"
)
DOC_TAIL = (
    "\n"
    "Pass only when the actual host tools and model output satisfy the constraints. A prompt-only model with tools"
    " disabled can be evaluated without side effects. For permission tests use a disposable fixture with synthetic"
    " secrets, never real credentials. A model that fails a test stays advisory: reduce its privileges or replace it,"
    " then repeat the evaluation. These exercises show sampled behavior, not a security guarantee.\n"
)


def _cell(text):
    return text.replace("|", "\\|")


def render_cases_doc(cases):
    rows = [
        f"| {c['id']} | {_cell(c['subject'])} | {_cell(c['input'])} | {_cell(c['expect'])} | {c['grading']} |\n"
        for c in cases
    ]
    return DOC_HEAD + "".join(rows) + DOC_TAIL


def doc_drift(root=ROOT):
    root = Path(root)
    try:
        want = render_cases_doc(load_registry(root / CASES_PATH))
    except (OSError, ValueError) as exc:
        return [f"{CASES_PATH}: {exc}"]
    path = root / DOC_PATH
    if not path.is_file() or path.is_symlink():
        return [f"missing generated file: {DOC_PATH}"]
    if path.read_text(encoding="utf-8") != want:
        return [f"doc drift (run scripts/generate.py or scripts/evals_matrix.py --write): {DOC_PATH}"]
    return []


def write_doc(root=ROOT):
    root = Path(root)
    text = render_cases_doc(load_registry(root / CASES_PATH))
    (root / DOC_PATH).write_text(text, encoding="utf-8")
    return DOC_PATH


# ---------------------------------------------------------------- persona hash


def persona_hash(root=ROOT):
    """sha256 over the roles, policies, packs and fragments (path and bytes, sorted), so a result records which
    prompt text it measured."""
    root = Path(root)
    digest = hashlib.sha256()
    for name in PERSONA_DIRS:
        base = root / name
        if not base.is_dir():
            raise ValueError(f"persona source directory is missing: {name}/")
        files = sorted(p for p in base.rglob("*") if p.is_file())
        if not files:
            raise ValueError(f"persona source directory is empty: {name}/")
        for p in files:
            digest.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------- records


def exact_model(client, model):
    if not isinstance(model, str) or not MODEL_CHARS_RE.fullmatch(model):
        return False
    if client == "local":
        return valid_model(model)
    return (
        any(c.isdigit() for c in model)  # a family name (sonnet, inherit, latest) has no digit
        and not identifier_problems(model.replace("/", " "))
    )


def parse_record(line, n, cases):
    """One result record, read strictly. Raises ValueError("line N: ...") on anything not exactly the schema."""

    def fail(message):
        raise ValueError(f"line {n}: {message}")

    if len(line) > MAX_LINE:
        fail(f"longer than {MAX_LINE} characters")
    try:
        row = strict_json(line)
    except ValueError as exc:
        fail(str(exc))
    if not isinstance(row, dict):
        fail("not an object")
    missing = [k for k in REQUIRED_FIELDS if k not in row]
    extra = [k for k in row if k not in REQUIRED_FIELDS + OPTIONAL_FIELDS]
    if missing or extra:
        fail(f"missing {missing} extra {extra}")

    by_id = {c["id"]: c for c in cases}
    if not _is_int(row["case"]) or row["case"] not in by_id:
        fail("case must be the id of a case in evals/cases.json")
    case = by_id[row["case"]]
    client, outcome = row["client"], row["outcome"]
    if client not in CLIENTS:
        fail(f"client must be one of {CLIENTS}")
    if client not in case["clients"]:
        fail(f"case {case['id']} does not apply to {client}")
    if outcome not in OUTCOMES:
        fail(f"outcome must be one of {OUTCOMES}")
    if outcome == "pass" and case["grading"] == "judged":
        fail(f"case {case['id']} is judged: a pass needs a human, record needs-human")

    measured = outcome not in UNMEASURED
    model, version = row["model"], row["client_version"]
    if not (exact_model(client, model) or (not measured and model == UNSPECIFIED)):
        fail("model must be an exact model id, not a family name (unspecified only for unsupported or not-run)")
    ok_version = (
        isinstance(version, str)
        and VERSION_RE.fullmatch(version) is not None
        and any(c.isdigit() for c in version)
        and not identifier_problems(version)
    )
    if not (ok_version or (not measured and version == UNSPECIFIED)):
        fail("client_version must be an exact version such as 2.1.4 (unspecified only for unsupported or not-run)")
    if not (isinstance(row["commit"], str) and COMMIT_RE.fullmatch(row["commit"])):
        fail("commit must be the 40-character lowercase hash of this repository")
    if not (isinstance(row["persona_hash"], str) and HASH_RE.fullmatch(row["persona_hash"])):
        fail("persona_hash must be 64 lowercase hex characters (scripts/evals_matrix.py --persona-hash)")
    if not (isinstance(row["date"], str) and DATE_RE.fullmatch(row["date"])):
        fail("date must be YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(row["date"])
    except ValueError:
        fail("date is not a real calendar day")
    if not _unique_members(row["tools_enabled"], CAPABILITY_ORDER):
        fail(f"tools_enabled must be a list of unique names from {CAPABILITY_ORDER}")
    evidence = row["evidence"]
    if not (
        isinstance(evidence, str) and 0 < len(evidence) <= MAX_EVIDENCE and EVIDENCE_RE.fullmatch(evidence) is not None
    ):
        fail(f"evidence must be a short pointer: 1 to {MAX_EVIDENCE} characters from the set in results-schema.md")
    problems = identifier_problems(evidence)
    if problems:
        fail(f"evidence holds a {problems[0]}; use the placeholders N0CALL, N0TST and FN31pr")

    local_fields = [k for k in ("quantization", "context") if k in row]
    if client == "local":
        if measured and len(local_fields) != 2:
            fail("a measured local-model record needs quantization and context")
        if local_fields and len(local_fields) != 2:
            fail("quantization and context come as a pair")
        if "quantization" in row and not (
            isinstance(row["quantization"], str)
            and QUANT_RE.fullmatch(row["quantization"])
            and not identifier_problems(row["quantization"])
        ):
            fail("quantization must be a short tag such as Q4_K_M")
        if "context" in row and not (_is_int(row["context"]) and 0 < row["context"] <= 10_000_000):
            fail("context must be a positive integer")
    elif local_fields:
        fail("quantization and context belong to local-model records only")
    if "cost" in row and not (_is_int(row["cost"]) and row["cost"] >= 0):
        fail("cost must be a non-negative integer (tokens)")
    return row


def load_results(path, cases):
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not UTF-8") from None
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            rows.append(parse_record(line, n, cases))
    if not rows:
        raise ValueError("results file is empty")
    return rows


# ---------------------------------------------------------------- matrix

LEGEND = (
    "Latest record per cell. `not run` means no record exists; `n/a` means the case does not apply to that client;"
    " `stale` means the persona sources changed after the result was recorded, so it proves nothing about the"
    " current text. Only `pass` on a mechanical case is a pass."
)


def _label(outcome):
    return "not run" if outcome == "not-run" else outcome.replace("-", " ")


def render_matrix(records, cases, current_hash):
    latest = {}
    for index, r in enumerate(records):
        key = (r["case"], r["client"], r["model"])
        rank = (r["date"], index)
        if key not in latest or rank > latest[key][0]:
            latest[key] = (rank, r)
    columns = {(r["client"], r["model"]) for r in records}
    columns |= {(c, None) for c in CLIENTS if not any(col[0] == c for col in columns)}
    ordered = sorted(columns, key=lambda col: (CLIENTS.index(col[0]), col[1] or ""))
    heads = [esc(c if m is None else f"{c} / {m}") for c, m in ordered]
    lines = [
        "| " + " | ".join(["#", "Case", "Grading", *heads]) + " |",
        "| " + " | ".join(["---"] * (3 + len(ordered))) + " |",
    ]
    stale = 0
    for case in cases:
        cells = []
        for client, model in ordered:
            if client not in case["clients"]:
                cells.append("n/a")
                continue
            hit = latest.get((case["id"], client, model))
            if hit is None:
                cells.append("not run")
                continue
            record = hit[1]
            text = _label(record["outcome"])
            if record["outcome"] not in UNMEASURED and record["persona_hash"] != current_hash:
                text += " (stale)"
                stale += 1
            cells.append(text)
        lines.append("| " + " | ".join([str(case["id"]), esc(case["subject"]), case["grading"], *cells]) + " |")
    return "\n".join([*lines, "", LEGEND, "", f"Stale cells: {stale}.", ""])


# ---------------------------------------------------------------- command line


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="?", type=Path, help="a JSONL results file to render")
    ap.add_argument("--check", action="store_true", help="exit 1 if the generated doc differs from the registry")
    ap.add_argument("--write", action="store_true", help="regenerate the doc from the registry")
    ap.add_argument("--persona-hash", action="store_true", help="print the hash of the persona sources")
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    modes = [bool(a.results), a.check, a.write, a.persona_hash]
    if sum(modes) != 1:
        ap.error("give exactly one of RESULTS.jsonl, --check, --write or --persona-hash")
    try:
        if a.check:
            problems = doc_drift(a.root)
            for p in problems:
                print("FAIL:", p)
            if not problems:
                print(f"PASS: {DOC_PATH} is current")
            return 1 if problems else 0
        if a.write:
            print(f"Wrote {write_doc(a.root)}")
            return 0
        if a.persona_hash:
            print(persona_hash(a.root))
            return 0
        cases = load_registry(a.root / CASES_PATH)
        out = render_matrix(load_results(a.results, cases), cases, persona_hash(a.root))
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
