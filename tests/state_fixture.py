"""Shared fixture for the installer tests (issue #40): a private DOIA_STATE_DIR so no test writes into the home
directory, plus helpers that keep the ownership state in step when a test rewrites an installed file on purpose.

Use `setUpModule = state_fixture.set_up_module` and `tearDownModule = state_fixture.tear_down_module` in a test file.
The state directory lives in its own temporary directory, which is never inside a target a test creates."""

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import state  # noqa: E402  (path set up above)

_active = []


def _with_write_bit(path, bit):
    """Keep the owner and read/execute bits, drop any other group/world write bit, then add exactly `bit`."""
    current = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, (current & ~(stat.S_IWGRP | stat.S_IWOTH)) | bit)


def make_group_writable(path):
    """Make `path` group-writable and not world-writable, derived from its current mode."""
    _with_write_bit(path, stat.S_IWGRP)


def make_world_writable(path):
    """Make `path` world-writable and not group-writable, derived from its current mode."""
    _with_write_bit(path, stat.S_IWOTH)


def set_up_module():
    tmp = tempfile.mkdtemp(prefix="doia-test-state-")
    previous = os.environ.get("DOIA_STATE_DIR")
    os.environ["DOIA_STATE_DIR"] = os.path.join(os.path.realpath(tmp), "state")
    _active.append((tmp, previous))


def tear_down_module():
    tmp, previous = _active.pop()
    if previous is None:
        os.environ.pop("DOIA_STATE_DIR", None)
    else:
        os.environ["DOIA_STATE_DIR"] = previous
    shutil.rmtree(tmp, ignore_errors=True)


def _release():
    return (ROOT / "VERSION").read_text().strip()


def record(target, rel, layout="user", source="public", roots=None):
    """Record the file at target/rel as installed (its current bytes), as a fresh install would have."""
    target = Path(target)
    entry = {
        "kind": "file",
        "release": _release(),
        "source": source,
        "body_sha256": state.body_sha256((target / rel).read_bytes()),
    }
    roots = [target] if roots is None else roots
    with state.Store(target, layout, roots=roots) as store:
        entries = store.load() or {}
        store.save(state.with_entry(entries, rel, entry))


def rewrite(target, rel, text, layout="user", roots=None):
    """Replace an installed file on purpose AND record the new bytes: the test models an older install."""
    target = Path(target)
    (target / rel).write_text(text)
    record(target, rel, layout, roots=roots)


def entries_of(target, layout="user", roots=None):
    roots = [Path(target)] if roots is None else roots
    return state.load(target, layout, roots=roots)
