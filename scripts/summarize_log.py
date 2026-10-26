"""Summarise a pilot log (one JSON object per line, schema in evals/log-schema.md) as a Markdown table.

    python3 scripts/summarize_log.py evals/example-log.jsonl

Reads a file and prints text. Nothing else: no network, no model, no writes. A malformed
line is an error, not a skipped row, so a typo cannot quietly shrink the sample."""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from lib import PACK_NAMES, ROLE_NAMES

HOSTS = ("claude-code", "copilot", "chatgpt", "local")
FIRST_REVIEW = ("approved", "changes", "n-a")
SEVERITIES = ("blocking", "important", "minor")
REQUIRED = (
    "date",
    "task",
    "host",
    "role",
    "model",
    "packs",
    "first_review",
    "fix_rounds",
    "findings",
    "violations",
    "gate_exit",
)
OPTIONAL = ("notes", "local_model", "local_discrepancies")
MAX_MODEL = 64
# name:size with an optional tag suffix such as a quantization (gemma4:12b, qwen3.5:4b, llama3.2:1b-instruct-q4_K_M).
# fullmatch, ASCII only: no surrounding whitespace, newline, control or zero-width character gets through.
MODEL_RE = re.compile(r"[A-Za-z0-9._-]+:[0-9]+(?:\.[0-9]+)?[bB](?:-[A-Za-z0-9_.]+)*", re.ASCII)
MAX_LINE = 4096


def parse_line(line, n):
    if len(line) > MAX_LINE:
        raise ValueError(f"line {n}: longer than {MAX_LINE} bytes")
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {n}: not JSON ({exc.msg})") from None
    if not isinstance(row, dict):
        raise ValueError(f"line {n}: not an object")
    missing = [k for k in REQUIRED if k not in row]
    extra = [k for k in row if k not in REQUIRED + OPTIONAL]
    if missing or extra:
        raise ValueError(f"line {n}: missing {missing} extra {extra}")
    checks = (
        (isinstance(row["date"], str) and len(row["date"]) == 10, "date must be YYYY-MM-DD"),
        (isinstance(row["task"], str) and 0 < len(row["task"]) <= 64, "task must be a short label"),
        (row["host"] in HOSTS, f"host must be one of {HOSTS}"),
        (row["role"] in ROLE_NAMES, f"role must be one of {ROLE_NAMES}"),
        (isinstance(row["model"], str) and row["model"] and row["model"] != "inherit", "model must be an exact id"),
        (isinstance(row["packs"], list) and all(p in PACK_NAMES for p in row["packs"]), "packs must be known names"),
        (row["first_review"] in FIRST_REVIEW, f"first_review must be one of {FIRST_REVIEW}"),
        (
            isinstance(row["fix_rounds"], int) and not isinstance(row["fix_rounds"], bool) and row["fix_rounds"] >= 0,
            "fix_rounds must be a non-negative int",
        ),
        (
            isinstance(row["findings"], dict)
            and set(row["findings"]) == set(SEVERITIES)
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in row["findings"].values()),
            f"findings must be counts for {SEVERITIES}",
        ),
        (
            isinstance(row["violations"], int) and not isinstance(row["violations"], bool) and row["violations"] >= 0,
            "violations must be a non-negative int",
        ),
        (
            row["gate_exit"] is None or (isinstance(row["gate_exit"], int) and not isinstance(row["gate_exit"], bool)),
            "gate_exit must be an int or null",
        ),
        (isinstance(row.get("notes", ""), str), "notes must be a string"),
    )
    for ok, msg in checks:
        if not ok:
            raise ValueError(f"line {n}: {msg}")
    local = ("local_model", "local_discrepancies")
    if (local[0] in row) != (local[1] in row):
        raise ValueError(f"line {n}: local_model and local_discrepancies come as a pair")
    if local[0] in row:
        model, count = row["local_model"], row["local_discrepancies"]
        if not (isinstance(model, str) and len(model) <= MAX_MODEL and MODEL_RE.fullmatch(model)):
            raise ValueError(f"line {n}: local_model must be an exact name:size tag such as gemma4:12b")
        if not (isinstance(count, int) and not isinstance(count, bool) and count >= 0):
            raise ValueError(f"line {n}: local_discrepancies must be a non-negative int")
    return row


def load(path):
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                rows.append(parse_line(line, n))
    if not rows:
        raise ValueError("log is empty")
    return rows


def summarise(rows):
    """Return a Markdown table as text. Code does every count; nothing here is estimated."""
    by_role = defaultdict(list)
    for r in rows:
        by_role[r["role"]].append(r)
    lines = [
        "| Measure | Value |",
        "| --- | --- |",
        f"| Tasks logged | {len(rows)} |",
        f"| Hosts | {', '.join(f'{h} {c}' for h, c in sorted(Counter(r['host'] for r in rows).items()))} |",
        f"| Models | {', '.join(f'{m} {c}' for m, c in sorted(Counter(r['model'] for r in rows).items()))} |",
        f"| Roles | {', '.join(f'{k} {len(v)}' for k, v in sorted(by_role.items()))} |",
    ]
    reviewed = [r for r in rows if r["role"] in ("implementer", "transcriber") and r["first_review"] != "n-a"]
    if reviewed:
        approved = sum(r["first_review"] == "approved" for r in reviewed)
        lines.append(f"| First-time approval of reviewed implementation tasks | {approved} of {len(reviewed)} |")
        clean = sum(r["fix_rounds"] == 0 for r in reviewed)
        lines.append(f"| Implementation tasks with no fix round | {clean} of {len(reviewed)} |")
        lines.append(f"| Fix rounds on implementation tasks | {sum(r['fix_rounds'] for r in reviewed)} |")
    found = Counter()
    for r in rows:
        found.update(r["findings"])
    lines.append(f"| Findings | {', '.join(f'{s} {found[s]}' for s in SEVERITIES)} |")
    lines.append(f"| Rule slips (violations) | {sum(r['violations'] for r in rows)} in {len(rows)} tasks |")
    gated = [r for r in rows if r["gate_exit"] is not None]
    lines.append(f"| Gate exit non-zero | {sum(r['gate_exit'] != 0 for r in gated)} of {len(gated)} logged gates |")
    local = [r for r in rows if "local_model" in r]
    if local:
        used = Counter(r["local_model"] for r in local)
        lines.append(
            f"| Local-model runs | {len(local)} task{'s' if len(local) != 1 else ''}; "
            f"discrepancies {sum(r['local_discrepancies'] for r in local)}; "
            f"{', '.join(f'{m} {c}' for m, c in sorted(used.items()))} |"
        )
    packs = Counter(p for r in rows for p in r["packs"])
    lines.append(f"| Packs loaded | {', '.join(f'{p} {c}' for p, c in sorted(packs.items())) or 'none'} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    a = ap.parse_args(argv)
    try:
        print(summarise(load(a.log)))
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
