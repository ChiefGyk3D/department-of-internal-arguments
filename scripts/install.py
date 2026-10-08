"""Additive project-only installer. Preview by default; never overwrite existing files.

python3 scripts/install.py --target <project> --adapter claude|copilot|both [--pack NAME ...] [--apply]
"""

import argparse
import shutil
import sys
from pathlib import Path

from lib import PACK_NAMES, ROOT

ADAPTERS = ("claude", "copilot", "both")


def ensure_safe(path):
    """Refuse a path that is, or sits under, a symlink."""
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink destination refused: {parent}")


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


def plan(target, adapter, packs=None, root=ROOT):
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
    pairs = []
    for src in _sources(adapter, packs, root):
        if src.is_symlink():
            raise ValueError(f"source symlink refused: {src.name}")
        if not src.is_file():
            raise ValueError(f"missing generated source (run scripts/generate.py): {src.name}")
        dest = target / src.relative_to(root)
        ensure_safe(dest)
        if dest.exists():
            raise ValueError(f"collision; merge manually: {dest.relative_to(target)}")
        # Reject a file in place of a directory before writing anything.
        for parent in dest.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"parent is not a directory: {parent}")
        pairs.append((src, dest))
    return pairs


def install(target, adapter, apply=False, packs=None, root=ROOT):
    pairs = plan(target, adapter, packs, root)
    if apply:
        for src, dest in pairs:
            ensure_safe(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation: a file that appeared after the preflight is never overwritten.
            with dest.open("xb") as out, src.open("rb") as inp:
                shutil.copyfileobj(inp, out)
    return [str(dest.relative_to(Path(target).absolute())) for _, dest in pairs]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--adapter", choices=ADAPTERS, default="claude")
    ap.add_argument("--pack", action="append", help="install only this pack (repeatable); default all")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    try:
        files = install(a.target, a.adapter, a.apply, a.pack)
    except (OSError, ValueError) as exc:
        print(f"Installation refused: {exc}", file=sys.stderr)
        return 1
    print("\n".join(files))
    print(("Installed" if a.apply else "Preview only; rerun with --apply after review") + f": {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
