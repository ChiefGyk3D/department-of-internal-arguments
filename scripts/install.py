"""Additive installer. Preview by default; never overwrite a file it did not generate.

python3 scripts/install.py --target <dir> --adapter claude|copilot|both [--pack NAME ...] [--update] [--apply]
                           [--layout project|user] [--overlay DIR]
python3 scripts/install.py --target <dir> --list-managed [--layout project|user] [--overlay DIR]

Layouts:
  project (default)  <target>/.claude/agents, <target>/.claude/skills, <target>/.github/agents
  user               <target>/agents/<role>.md and <target>/skills/pack-<name>/SKILL.md (a user-level Claude Code
                     tree such as a dotfiles checkout). Claude only: Copilot has no user layout.

Without --update every existing destination is a collision and the whole install is refused.
With --update a destination that carries the generator marker is replaced in place; a file
without the marker (one you wrote or edited) is still a collision.

--overlay DIR is a private directory outside this repository (roles/, packs/; see scripts/overlay.py). An overlay
entry with a public name replaces the public one; a new name is installed in addition. Overlay files carry the
generator marker with the word "overlay" and the file's own name, never a path.

--list-managed is read-only: it lists files under the target that carry the marker with their release and whether
they match this checkout (exit 0 when all are current, 3 otherwise).
"""

import argparse
import os
import re
import sys
from pathlib import Path

import generate
from lib import GENERATED_MARK, PACK_NAMES, ROOT, ensure_safe, version
from overlay import load_overlay

ADAPTERS = ("claude", "copilot", "both")
LAYOUTS = ("project", "user")
MAX_MANAGED_BYTES = 1024 * 1024
_RELEASE_RE = re.compile(r"[0-9A-Za-z.+_-]{1,40}")


def is_generated(path):
    """True only for a regular, non-symlink file whose first lines carry the generator marker."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8") as f:
            head = f.read(4096)
    except (OSError, UnicodeDecodeError):
        return False
    return GENERATED_MARK in head


def assert_outside_repo(path, root=ROOT):
    """Defence in depth: nothing this installer writes may land inside the repository (overlay content included)."""
    resolved = Path(os.path.realpath(path))
    if resolved.is_relative_to(Path(os.path.realpath(root))):
        raise ValueError(f"destination inside this repository refused: {Path(path).name}")


def _check_options(adapter, layout):
    if adapter not in ADAPTERS:
        raise ValueError("unknown adapter")
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    if layout == "user" and adapter != "claude":
        raise ValueError("copilot has no user layout; use --adapter claude with --layout user")


def _check_target(target, root):
    target = Path(target).absolute()
    ensure_safe(target)
    if not target.is_dir():
        raise ValueError("target must be an existing project directory")
    if Path(os.path.realpath(target)).is_relative_to(Path(os.path.realpath(root))):
        raise ValueError("target cannot be this repository or inside it")
    return target


def _layout_rel(rel, layout):
    if layout == "user":
        return rel.removeprefix(".claude/")
    return rel


def _public_source(root, rel):
    src = Path(root) / rel
    if src.is_symlink():
        raise ValueError(f"source symlink refused: {src.name}")
    if not src.is_file():
        raise ValueError(f"missing generated source (run scripts/generate.py): {src.name}")
    return src


def _items(adapter, packs, layout, root, ov):
    """Ordered [(source label, relative destination, bytes, origin)] for this checkout plus the overlay."""
    root = Path(root)
    ov_roles = {r["name"]: r for r in ov.roles} if ov else {}
    ov_packs = {p["dir"]: p for p in ov.packs} if ov else {}
    known = list(PACK_NAMES) + sorted(set(ov_packs) - set(PACK_NAMES))
    packs = known if packs is None else list(packs)
    for name in packs:
        if name not in known:
            raise ValueError(f"unknown pack: {name}")
    ver = version(root)
    policies = generate.policies_block(root) if ov else None
    out = []

    def role_names(pattern, suffix):
        names = {p.name.removesuffix(suffix) for p in (root / pattern).glob("*" + suffix)}
        return sorted(names | set(ov_roles))

    if adapter in ("claude", "both"):
        for name in role_names(".claude/agents", ".md"):
            rel = f".claude/agents/{name}.md"
            if name in ov_roles:
                r = ov_roles[name]
                text = generate.render_claude_agent(r, policies, ver, source=f"overlay role {r['file']}")
                out.append((f"overlay:{r['file']}", _layout_rel(rel, layout), text.encode("utf-8"), "overlay"))
            else:
                src = _public_source(root, rel)
                out.append((src, _layout_rel(rel, layout), src.read_bytes(), "public"))
        for name in packs:
            rel = f".claude/skills/pack-{name}/SKILL.md"
            if name in ov_packs:
                p = ov_packs[name]
                text = generate.render_skill(p, ver, source=f"overlay pack {p['dir']}")
                out.append((f"overlay:pack-{name}", _layout_rel(rel, layout), text.encode("utf-8"), "overlay"))
            else:
                src = _public_source(root, rel)
                out.append((src, _layout_rel(rel, layout), src.read_bytes(), "public"))
    if adapter in ("copilot", "both"):
        for name in role_names(".github/agents", ".agent.md"):
            rel = f".github/agents/{name}.agent.md"
            if name in ov_roles:
                r = ov_roles[name]
                text = generate.render_copilot_agent(r, policies, ver, source=f"overlay role {r['file']}")
                out.append((f"overlay:{r['file']}", rel, text.encode("utf-8"), "overlay"))
            else:
                src = _public_source(root, rel)
                out.append((src, rel, src.read_bytes(), "public"))
    return out


def _plan(target, adapter, packs=None, root=ROOT, update=False, layout="project", overlay=None):
    """[(source, destination Path, action, bytes)]; raises before anything is written."""
    _check_options(adapter, layout)
    ov = load_overlay(overlay, root) if overlay is not None else None
    target = _check_target(target, root)
    items = []
    for src, rel, data, _origin in _items(adapter, packs, layout, root, ov):
        dest = target / rel
        ensure_safe(dest)
        assert_outside_repo(dest, root)
        action = "new"
        if dest.exists() or dest.is_symlink():
            if update and is_generated(dest):
                action = "update"
            elif update:
                raise ValueError(f"not a generated file; merge manually: {dest.relative_to(target)}")
            else:
                raise ValueError(f"collision; merge manually (or use --update): {dest.relative_to(target)}")
        # Reject a file in place of a directory before writing anything.
        for parent in dest.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"parent is not a directory: {parent}")
        items.append((src, dest, action, data))
    return items


def plan(target, adapter, packs=None, root=ROOT, update=False, layout="project", overlay=None):
    """[(source, destination, 'new' | 'update')]; raises before anything is written."""
    return [(s, d, a) for s, d, a, _ in _plan(target, adapter, packs, root, update, layout, overlay)]


def _apply(items, root=ROOT):
    for _src, dest, action, data in items:
        ensure_safe(dest)
        assert_outside_repo(dest, root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if action == "update":
            if not is_generated(dest):  # changed between preflight and write
                raise ValueError(f"destination changed during install: {dest}")
            tmp = dest.with_name(dest.name + ".doia-tmp")
            with tmp.open("xb") as out:
                out.write(data)
            os.replace(tmp, dest)
        else:
            # Exclusive creation: a file that appeared after the preflight is never overwritten.
            with dest.open("xb") as out:
                out.write(data)


def install(target, adapter, apply=False, packs=None, root=ROOT, update=False, layout="project", overlay=None):
    items = _plan(target, adapter, packs, root, update, layout, overlay)
    if apply:
        _apply(items, root)
    return [str(dest.relative_to(Path(target).absolute())) for _, dest, _, _ in items]


def _scan_patterns(layout):
    if layout == "user":
        return ("agents/*.md", "skills/*/SKILL.md")
    return (".claude/agents/*.md", ".claude/skills/*/SKILL.md", ".github/agents/*.agent.md")


def _read_capped(path):
    with path.open("rb") as f:
        return f.read(MAX_MANAGED_BYTES + 1)


def list_managed(target, layout="project", overlay=None, root=ROOT):
    """Read-only: one row per marker-carrying file under target.

    status: current | stale (older release, differs) | differs (this release, content differs) |
            orphan (this checkout and overlay no longer produce it) | unchecked (overlay-sourced, no --overlay given).
    Files without the marker and symlinks are not listed."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    ov = load_overlay(overlay, root) if overlay is not None else None
    target = _check_target(target, root)
    adapter = "claude" if layout == "user" else "both"
    expected = {rel: data for _s, rel, data, _o in _items(adapter, None, layout, root, ov)}
    ver = version(root)
    rows = []
    for pattern in _scan_patterns(layout):
        for path in sorted(target.glob(pattern)):
            try:
                ensure_safe(path)
            except ValueError:
                continue
            if not is_generated(path):
                continue
            rel = str(path.relative_to(target))
            data = _read_capped(path)
            head = data[:4096].decode("utf-8", "replace")
            line = next(ln for ln in head.splitlines() if GENERATED_MARK in ln)
            m = re.search(r"\(release ([^)]*)\)", line)
            release = m.group(1) if m and _RELEASE_RE.fullmatch(m.group(1)) else "unknown"
            source = "overlay" if "from overlay " in line else "public"
            if source == "overlay" and ov is None:
                status = "unchecked"
            elif rel not in expected:
                status = "orphan"
            elif data == expected[rel]:
                status = "current"
            else:
                status = "differs" if release == ver else "stale"
            rows.append({"path": rel, "release": release, "source": source, "status": status})
    return rows


def _safe(text):
    return "".join(c if " " <= c <= "~" else "?" for c in text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--adapter", choices=ADAPTERS, default="claude")
    ap.add_argument("--layout", choices=LAYOUTS, default="project")
    ap.add_argument("--overlay", type=Path, help="private directory outside this repository (roles/, packs/)")
    ap.add_argument("--pack", action="append", help="install only this pack (repeatable); default all")
    ap.add_argument("--update", action="store_true", help="replace files that carry the generator marker")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list-managed", action="store_true", help="read-only: list marker files and their drift")
    a = ap.parse_args(argv)
    try:
        if a.list_managed:
            if a.apply or a.update or a.pack:
                raise ValueError("--list-managed is read-only and takes no --apply, --update or --pack")
            rows = list_managed(a.target, a.layout, a.overlay)
            for r in rows:
                print(f"{r['status']:10} {r['release']:10} {r['source']:8} {_safe(r['path'])}")
            bad = [r for r in rows if r["status"] != "current"]
            print(f"{len(rows)} managed files, {len(bad)} not current")
            return 3 if bad else 0
        items = _plan(a.target, a.adapter, a.pack, update=a.update, layout=a.layout, overlay=a.overlay)
        if a.apply:
            _apply(items)
    except (OSError, ValueError) as exc:
        print(f"Installation refused: {exc}", file=sys.stderr)
        return 1
    target = Path(a.target).absolute()
    for _, dest, action, _ in items:
        print(f"{action}: {dest.relative_to(target)}")
    print(("Installed" if a.apply else "Preview only; rerun with --apply after review") + f": {len(items)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
