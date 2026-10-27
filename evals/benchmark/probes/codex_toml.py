"""Probe: the Codex adapter renders a file that is not valid TOML when a role text holds a control character.

Usage: python3 -I codex_toml.py TREE SCRATCH. Renders in memory only."""

import sys
import tomllib
from pathlib import Path

tree = Path(sys.argv[1])
sys.path.insert(0, str(tree / "scripts"))
import generate  # noqa: E402  (path set up above)
import lib  # noqa: E402

role = dict(lib.load_roles(tree)[0])
role["body"] = role["body"] + "\nstray control character: \x07\n"
if not hasattr(generate, "render_codex_agent"):
    print("NOT-OBSERVED: this release has no Codex adapter")
    sys.exit(0)
try:
    text = generate.render_codex_agent(role, generate.policies_block(tree), lib.version(tree))
except ValueError as exc:  # the fixed adapter refuses to render it
    print(f"NOT-OBSERVED: render refused ({type(exc).__name__})")
else:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        print("OBSERVED: render_codex_agent returned text that tomllib cannot parse")
    else:
        print("NOT-OBSERVED: the rendered text parses")
