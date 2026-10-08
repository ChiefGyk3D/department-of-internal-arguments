"""Additive project-only installer. Preview by default; never overwrite a file it did not generate.

python3 scripts/install.py --target <project> --adapter claude|copilot|both [--pack NAME ...] [--update] [--apply]

Without --update every existing destination is a collision and the whole install is refused.
With --update a destination that carries the generator marker is replaced in place; a file
without the marker (one you wrote or edited) is still a collision.
"""

import argparse
import os
import sys
from pathlib import Path

from lib import GENERATED_MARK, PACK_NAMES, ROOT

ADAPTERS = ("claude", "copilot", "both")


def ensure_safe(path):
    """Refuse a path that is, or sits under, a symlink."""
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink destination refused: {parent}")


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


def _sources(adapter, packs, root):
    root = Path(root)
    out = []
    if adapter in ("claude", "both"):
        out += sorted((root / ".claude/agents").glob("*.md"))
        for name in packs:
            out.append(root / f".claude/skills/pack-{name}/SKILL.md")
    if adapter in ("copilot", "both"):
        out += sorted((root / ".github/agents").glob("*.agent.md"))
    return out


def plan(target, adapter, packs=None, root=ROOT, update=False):
    """[(source, destination, 'new' | 'update')]; raises before anything is written."""
    if adapter not in ADAPTERS:
        raise ValueError("unknown adapter")
    packs = list(PACK_NAMES) if packs is None else list(packs)
    for name in packs:
        if name not in PACK_NAMES:
            raise ValueError(f"unknown pack: {name}")
    target = Path(target).absolute()
    ensure_safe(target)
    if not target.is_dir():
        raise ValueError("target must be an existing project directory")
    if target.resolve() == Path(root).resolve():
        raise ValueError("target cannot be this repository")
    triples = []
    for src in _sources(adapter, packs, root):
        if src.is_symlink():
            raise ValueError(f"source symlink refused: {src.name}")
        if not src.is_file():
            raise ValueError(f"missing generated source (run scripts/generate.py): {src.name}")
        dest = target / src.relative_to(root)
        ensure_safe(dest)
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
        triples.append((src, dest, action))
    return triples


def install(target, adapter, apply=False, packs=None, root=ROOT, update=False):
    triples = plan(target, adapter, packs, root, update)
    if apply:
        for src, dest, action in triples:
            ensure_safe(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
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
    return [str(dest.relative_to(Path(target).absolute())) for _, dest, _ in triples]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--adapter", choices=ADAPTERS, default="claude")
    ap.add_argument("--pack", action="append", help="install only this pack (repeatable); default all")
    ap.add_argument("--update", action="store_true", help="replace files that carry the generator marker")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    try:
        triples = plan(a.target, a.adapter, a.pack, update=a.update)
        files = install(a.target, a.adapter, a.apply, a.pack, update=a.update)
    except (OSError, ValueError) as exc:
        print(f"Installation refused: {exc}", file=sys.stderr)
        return 1
    for (_, _, action), rel in zip(triples, files, strict=True):
        print(f"{action}: {rel}")
    print(("Installed" if a.apply else "Preview only; rerun with --apply after review") + f": {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
