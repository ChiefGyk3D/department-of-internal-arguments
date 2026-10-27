"""Probe: does the pilot-log reader accept or reject a local_model tag?

Usage: python3 -I model_tag.py TREE SCRATCH accepts|rejects TAG. Prints OBSERVED when the stated behavior holds.
Works on every release that has summarize_log.parse_line."""

import json
import sys
from pathlib import Path

tree, mode, tag = Path(sys.argv[1]), sys.argv[3], sys.argv[4]
sys.path.insert(0, str(tree / "scripts"))
import summarize_log  # noqa: E402  (path set up above)

row = {
    "date": "2026-10-08",
    "task": "probe",
    "host": "local",
    "role": "reviewer",
    "model": "claude-sonnet-5-5",
    "packs": [],
    "first_review": "n-a",
    "fix_rounds": 0,
    "findings": {"blocking": 0, "important": 0, "minor": 0},
    "violations": 0,
    "gate_exit": None,
    "local_model": tag,
    "local_discrepancies": 0,
}
try:
    summarize_log.parse_line(json.dumps(row), 1)
    accepted = True
except ValueError:
    accepted = False
held = accepted if mode == "accepts" else not accepted
print(("OBSERVED" if held else "NOT-OBSERVED") + f": {tag!r} accepted={accepted}")
