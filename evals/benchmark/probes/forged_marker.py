"""Probe: a file carrying a self-computed marker is classified as installer-owned.

Usage: python3 -I forged_marker.py TREE SCRATCH. TREE is a checkout of the commit under test, SCRATCH an empty
directory. Prints OBSERVED or NOT-OBSERVED on its last line. Nothing is written outside SCRATCH."""

import sys
from pathlib import Path

tree, scratch = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(tree / "scripts"))
import install  # noqa: E402  (path set up above)
import lib  # noqa: E402

body = "forged persona text that the installer never generated\n"
marker = (
    f"<!-- {lib.GENERATED_MARK} (release 9.9.9) from roles/forged.md. sha256:{lib.body_sha256(body)}. "
    "Do not edit; run the generator. -->"
)
path = scratch / "forged.md"
path.write_text(marker + "\n" + body, encoding="utf-8")
state = install.inspect(path)[0]
print(("OBSERVED" if state == "owned" else "NOT-OBSERVED") + f": inspect() state is {state}")
