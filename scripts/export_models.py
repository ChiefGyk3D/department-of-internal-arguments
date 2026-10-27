"""Export the tier-to-model pins as export/model-pins.json, a generated and drift-checked file.

    python3 scripts/export_models.py --write    write export/model-pins.json
    python3 scripts/export_models.py --check    exit 1 if the committed file differs from what the sources produce

The sources are TIER_MODEL in scripts/lib.py, the tier of each role in roles/*.md, VERSION and
fragments/report-header.md. Other repositories read the file at a pinned commit with `read_pins`, which is strict:
unknown keys, wrong types, duplicate keys, non-canonical bytes and any model id that is not an exact pin are errors.
Fail closed: any problem exits 1 and nothing is written (the file is built in memory, written to a temp file in the
same directory, then renamed)."""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from lib import ROOT, TIER_MODEL, ensure_safe, load_roles

EXPORT_PATH = "export/model-pins.json"
SCHEMA = 1
MAX_BYTES = 64 * 1024
# An exact pin: claude-<family>-<major>[-<minor>...]. No alias, no `latest`, no dated or family-only name, ASCII only.
MODEL_ID_RE = re.compile(r"claude-([a-z]+)-[0-9]+(?:-[0-9]+)*")
TIER_RE = re.compile(r"[a-z]+")
ROLE_RE = re.compile(r"[a-z]+(?:-[a-z]+)*")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
FIELD_RE = re.compile(r"[A-Z][A-Z-]*(?: [A-Z][A-Z-]*)*")
# A header field name starts a line or follows three spaces or a semicolon; it is followed by a colon.
HEADER_FIELD_RE = re.compile(r"(?:^|   |; )([A-Z][A-Z-]*(?: [A-Z][A-Z-]*)*):")
TOP_KEYS = {"schema", "release", "tiers", "roles", "report_header"}
TIER_KEYS = {"model", "family", "roles"}


class ModelPinsError(ValueError):
    """The sources or an export file are not acceptable. Messages never echo raw text (see `_q`)."""


def _q(value):
    """Escape and shorten anything echoed in a message: ASCII only, no control or bidi characters survive."""
    text = ascii(value)
    return text if len(text) <= 60 else text[:57] + "..."


def model_family(model):
    m = MODEL_ID_RE.fullmatch(model) if isinstance(model, str) else None
    if m is None:
        raise ModelPinsError(f"model id {_q(model)} is not an exact pin (claude-<family>-<version>)")
    return m.group(1)


def report_header_fields(text):
    """Ordered field names of the shared report header fragment."""
    fields = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ModelPinsError("report header fragment is empty")
    for ln in lines:
        found = HEADER_FIELD_RE.findall(ln)
        if not found or not ln.startswith(found[0] + ":"):
            raise ModelPinsError(f"report header line does not start with a field name: {_q(ln[:40])}")
        fields.extend(found)
    if len(set(fields)) != len(fields):
        raise ModelPinsError("report header names a field twice")
    return fields


def build(root=ROOT, tier_model=None, version_text=None):
    """The export as a dict, from the sources under root. Raises ModelPinsError; never returns partial data."""
    root = Path(root)
    pins = TIER_MODEL if tier_model is None else tier_model
    try:
        release = (root / "VERSION").read_text(encoding="utf-8").strip() if version_text is None else version_text
        header = (root / "fragments" / "report-header.md").read_text(encoding="utf-8")
        roles = load_roles(root)
    except (OSError, ValueError) as exc:
        raise ModelPinsError(f"cannot read the sources: {_q(str(exc))}") from None
    if not VERSION_RE.fullmatch(release):
        raise ModelPinsError(f"release {_q(release)} is not semver")
    if not isinstance(pins, dict) or not pins:
        raise ModelPinsError("no tiers are pinned")
    tiers = {}
    for tier, model in pins.items():
        if not isinstance(tier, str) or not TIER_RE.fullmatch(tier):
            raise ModelPinsError(f"tier name {_q(tier)} is not a lowercase word")
        if model is None:
            raise ModelPinsError(f"tier {_q(tier)} has no model pin")
        tiers[tier] = {"model": model, "family": model_family(model), "roles": []}
    by_role = {}
    for r in roles:
        name, tier = r.get("name"), r.get("tier")
        if not isinstance(name, str) or not ROLE_RE.fullmatch(name):
            raise ModelPinsError(f"role name {_q(name)} is not a lowercase hyphenated word")
        if name in by_role:
            raise ModelPinsError(f"duplicate role name {_q(name)}")
        if not isinstance(tier, str) or tier not in tiers:
            raise ModelPinsError(f"role {_q(name)}: tier {_q(tier)} is not a tier")
        by_role[name] = tier
        tiers[tier]["roles"].append(name)
    for t in tiers.values():
        t["roles"].sort()
    return {
        "schema": SCHEMA,
        "release": release,
        "tiers": tiers,
        "roles": by_role,
        "report_header": report_header_fields(header),
    }


def render(data):
    """Canonical bytes: sorted keys, two-space indent, ASCII only, one trailing newline."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def generate_text(root=ROOT, tier_model=None):
    return render(build(root, tier_model))


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ModelPinsError(f"duplicate key {_q(k)}")
        out[k] = v
    return out


def _no_constant(name):
    raise ModelPinsError(f"non-finite number {_q(name)} is not allowed")


def _exact(node, keys, label):
    if not isinstance(node, dict) or set(node) != keys:
        raise ModelPinsError(f"{label}: must be an object with exactly the keys {sorted(keys)}")


def validate_data(data):
    """Strict structural and cross-field checks on a parsed export. Returns data."""
    _exact(data, TOP_KEYS, "export")
    if type(data["schema"]) is not int or data["schema"] != SCHEMA:
        raise ModelPinsError(f"schema must be the integer {SCHEMA}")
    if not isinstance(data["release"], str) or not VERSION_RE.fullmatch(data["release"]):
        raise ModelPinsError(f"release {_q(data['release'])} is not semver")
    tiers, roles = data["tiers"], data["roles"]
    if not isinstance(tiers, dict) or not tiers:
        raise ModelPinsError("tiers must be a non-empty object")
    if not isinstance(roles, dict):
        raise ModelPinsError("roles must be an object")
    for tier, node in tiers.items():
        if not TIER_RE.fullmatch(tier):
            raise ModelPinsError(f"tier name {_q(tier)} is not a lowercase word")
        _exact(node, TIER_KEYS, f"tier {_q(tier)}")
        if node["family"] != model_family(node["model"]):
            raise ModelPinsError(f"tier {_q(tier)}: family does not match the model id")
        want = sorted(r for r, t in roles.items() if t == tier)
        if node["roles"] != want:
            raise ModelPinsError(f"tier {_q(tier)}: roles list disagrees with the roles map or is not sorted")
    for name, tier in roles.items():
        if not ROLE_RE.fullmatch(name) or not isinstance(tier, str) or tier not in tiers:
            raise ModelPinsError(f"role {_q(name)}: tier {_q(tier)} is not a tier")
    header = data["report_header"]
    if (
        not isinstance(header, list)
        or not header
        or not all(isinstance(f, str) and FIELD_RE.fullmatch(f) for f in header)
        or len(set(header)) != len(header)
    ):
        raise ModelPinsError("report_header must be a non-empty list of distinct upper-case field names")
    return data


def read_pins(path):
    """Strict reader for other scripts and tests: the parsed, validated export, or ModelPinsError.

    Refuses a missing, non-regular, symlinked, oversized or non-UTF-8 file, duplicate or unknown keys, wrong types,
    a model id that is not an exact pin, and any bytes other than the canonical rendering of the data."""
    path = Path(path)
    try:
        ensure_safe(path)
        if not path.is_file():
            raise ModelPinsError(f"{_q(path.name)} is not a regular file")
        with path.open("rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ModelPinsError(f"{_q(path.name)} is over {MAX_BYTES} bytes")
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, ModelPinsError):
            raise
        raise ModelPinsError(f"cannot read {_q(path.name)}: {type(exc).__name__}") from None
    try:
        data = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constant)
    except json.JSONDecodeError:
        raise ModelPinsError(f"{_q(path.name)} is not valid JSON") from None
    except RecursionError:
        raise ModelPinsError(f"{_q(path.name)} is nested too deeply") from None
    validate_data(data)
    if render(data) != text:
        raise ModelPinsError(f"{_q(path.name)} is not in canonical form (sorted keys, ASCII, 2-space indent)")
    return data


def drift(root=ROOT):
    """Problem messages naming the export file; empty means current."""
    root = Path(root)
    try:
        want = generate_text(root)
    except ModelPinsError as exc:
        return [f"{EXPORT_PATH}: cannot generate: {exc}"]
    p = root / EXPORT_PATH
    try:
        if not p.is_file() or p.is_symlink():
            return [f"missing generated file: {EXPORT_PATH}"]
        if p.read_text(encoding="utf-8") != want:
            return [f"stale generated file (run scripts/export_models.py --write): {EXPORT_PATH}"]
    except (OSError, UnicodeDecodeError):
        return [f"unreadable generated file: {EXPORT_PATH}"]
    return []


def write(root=ROOT):
    """Write the export atomically. Everything is built before the first byte is written."""
    root = Path(root)
    text = generate_text(root)
    dest = root / EXPORT_PATH
    ensure_safe(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".model-pins-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write export/model-pins.json")
    mode.add_argument("--check", action="store_true", help="verify the committed file is current; write nothing")
    args = ap.parse_args(argv)
    try:
        if args.check:
            problems = drift()
            for p in problems:
                print("FAIL:", p)
            if problems:
                return 1
            print(f"PASS: {EXPORT_PATH} is current")
            return 0
        write()
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"Wrote {EXPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
