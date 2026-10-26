"""Probe: `list_managed(..., lead=FILE)` gives no row to a lead file that is a directory, oversized or unreadable.

Usage: python3 -I lead_list.py TREE SCRATCH directory|oversized|unreadable. OBSERVED means the named file got no
row at all (it reads as clean by omission) or, for `unreadable` only, the call raised PermissionError instead of
returning a row. Any other exception is a crash and fails the probe."""

import os
import sys
from pathlib import Path

tree, scratch, kind = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(tree / "scripts"))
import install  # noqa: E402  (path set up above)

target = scratch / "project"
target.mkdir()
lead = target / "LEAD.md"
if kind == "directory":
    lead.mkdir()
elif kind == "oversized":
    lead.write_bytes(b"x" * (install.MAX_MANAGED_BYTES + 1))
elif kind == "unreadable":
    if os.geteuid() == 0:
        print("NOT-OBSERVED: running as root, permissions do not apply; rerun as an ordinary user")
        sys.exit(0)
    lead.write_text("notes\n", encoding="utf-8")
    lead.chmod(0)
else:
    raise SystemExit("unknown kind")
try:
    rows = install.list_managed(target, lead="LEAD.md", root=tree)
except PermissionError as exc:
    if kind != "unreadable":  # any other exception is a crash of the probe, not the finding
        raise
    print(f"OBSERVED: list_managed raised {type(exc).__name__} instead of returning a row")
else:
    got = [r["status"] for r in rows if r["path"] == "LEAD.md"]
    print(("NOT-OBSERVED: row " + got[0]) if got else "OBSERVED: no row for the named lead file")
