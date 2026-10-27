"""Probe: an edit made in place to the lead file between the installer's read and its rename is lost.

Usage: python3 -I lead_lost_update.py TREE SCRATCH. Installs the lead block into a file that already holds the user's
notes, using the installer's own race seam to rewrite that file in place (same inode) just before the write. OBSERVED
means the install finished and the in-place edit is gone; NOT-OBSERVED means the installer refused or kept the edit.
Any other failure is a crash of the probe. Everything happens inside SCRATCH."""

import sys
from pathlib import Path

tree, scratch = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(tree / "scripts"))
import install  # noqa: E402  (path set up above)

target = scratch / "project"
target.mkdir()
lead = target / "LEAD.md"
lead.write_text("my own notes\n", encoding="utf-8")
EDIT = "an edit made while the installer was working\n"


def seam(stage, dest):
    if stage == "checked" and dest.name == "LEAD.md":
        with open(dest, "r+", encoding="utf-8") as fh:  # in place: the inode does not change
            fh.seek(0, 2)
            fh.write(EDIT)


install._race_seam = seam
try:
    install.install(target, "claude", apply=True, lead="LEAD.md", root=tree)
except ValueError as exc:
    print(f"NOT-OBSERVED: the installer refused ({exc})")
else:
    kept = EDIT in lead.read_text(encoding="utf-8")
    print("NOT-OBSERVED: the edit survived" if kept else "OBSERVED: install finished and the in-place edit is gone")
