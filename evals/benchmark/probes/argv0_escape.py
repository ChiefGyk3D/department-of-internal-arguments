"""Probe: installer usage output prints the program name (sys.argv[0]) with a raw escape character.

Usage: python3 -I argv0_escape.py TREE SCRATCH. Parses one bad option in memory; nothing is installed."""

import contextlib
import io
import sys
from pathlib import Path

tree = Path(sys.argv[1])
sys.path.insert(0, str(tree / "scripts"))
import install  # noqa: E402  (path set up above)

sys.argv[0] = "/opt/in\x1b[31mstall.py"
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    try:
        install.main(["--no-such-option"])
    except SystemExit:
        pass
raw = "\x1b" in err.getvalue()
print(("OBSERVED" if raw else "NOT-OBSERVED") + ": raw escape character in usage output is " + str(raw))
