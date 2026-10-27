"""Probe: the installer accepts the filesystem root as its target directory.

Usage: python3 -I root_target.py TREE SCRATCH. Only the target check runs; nothing is installed or written."""

import sys
from pathlib import Path

tree = Path(sys.argv[1])
sys.path.insert(0, str(tree / "scripts"))
import install  # noqa: E402  (path set up above)

try:
    install._check_target("/", tree)
except ValueError as exc:
    print(f"NOT-OBSERVED: refused with {exc}")
else:
    print("OBSERVED: the filesystem root passed the target check")
