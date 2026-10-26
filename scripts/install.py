"""Additive installer. Preview by default; never overwrite a file it did not generate.

python3 scripts/install.py --target <dir> --adapter claude|copilot|both [--pack NAME ...] [--update] [--apply]
                           [--layout project|user] [--overlay DIR] [--force-edited]
python3 scripts/install.py --target <dir> --list-managed [--layout project|user] [--overlay DIR]

Layouts:
  project (default)  <target>/.claude/agents, <target>/.claude/skills, <target>/.github/agents
  user               <target>/agents/<role>.md and <target>/skills/pack-<name>/SKILL.md (a user-level Claude Code
                     tree such as a dotfiles checkout). Claude only: Copilot has no user layout.

Without --update every existing destination is a collision and the whole install is refused.
With --update a destination is replaced in place only if its marker is well-formed and its body still matches the
sha256 in the marker (or it is a known release-0.2.0 file). A file you edited is "edited" and refused; --force-edited
discards your edits. A file without the marker is still a collision.

--overlay DIR is a private directory outside this repository (roles/, packs/; see scripts/overlay.py). An overlay
entry with a public name is append-only (the public file unchanged, then a labelled additions section); a new name is
installed as a whole persona. Overlay files carry the
generator marker with the word "overlay" and the file's own name, never a path.

--list-managed is read-only: it lists files under the target that carry the marker with their release and whether
they match this checkout (exit 0 when all are current, 3 otherwise).
"""

import argparse
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

import generate
from lib import (
    GENERATED_MARK,
    LEGACY_BODY_SHA256,
    PACK_NAMES,
    ROOT,
    body_sha256,
    ensure_safe,
    load_packs,
    load_roles,
    split_marker,
    version,
)
from overlay import load_overlay

ADAPTERS = ("claude", "copilot", "both")
LAYOUTS = ("project", "user")
MAX_MANAGED_BYTES = 1024 * 1024
_RELEASE_RE = re.compile(r"[0-9A-Za-z.+_-]{1,40}")


def inspect(path):
    """(state, release, source, bytes) for what is on disk at path.

    state: none (absent, not a regular file, a symlink, or no marker text at all) | owned (well-formed marker whose
    sha256 matches the body) | legacy (0.2.0 marker without a hash, body is a known 0.2.0 output) | legacy-unverified
    (hashless marker, unknown body) | edited (marker text present but malformed, forged, or the body no longer matches).
    The marker text alone is never ownership."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return "none", None, None, b""
    try:
        data = _read_capped(path)
    except OSError:
        return "none", None, None, b""
    text = data.decode("utf-8", "replace")
    if GENERATED_MARK not in text:
        return "none", None, None, data
    parts = split_marker(text) if len(data) <= MAX_MANAGED_BYTES else None
    if parts is None:
        return "edited", "unknown", "unknown", data
    m, body = parts
    release = m.group(1) if _RELEASE_RE.fullmatch(m.group(1)) else "unknown"
    source = "overlay" if m.group(2).startswith("overlay ") else "public"
    digest = body_sha256(body)
    if m.group(3):
        state = "owned" if m.group(3) == digest else "edited"
    else:
        state = "legacy" if digest in LEGACY_BODY_SHA256 else "legacy-unverified"
    return state, release, source, data


def is_generated(path):
    """True only for a file the installer may replace without --force-edited."""
    return inspect(path)[0] in ("owned", "legacy")


def assert_outside_repo(path, root=ROOT):
    """Defense in depth: nothing this installer writes may land inside the repository (overlay content included)."""
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
    role_adds = ov.role_additions if ov else {}
    pack_adds = ov.pack_additions if ov else {}
    pub_roles = {r["name"]: r for r in load_roles(root)} if role_adds else {}
    pub_packs = {p["dir"]: p for p in load_packs(root)} if pack_adds else {}
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
            elif name in role_adds:
                text = generate.render_claude_agent(
                    pub_roles[name],
                    policies,
                    ver,
                    source=f"overlay additions role {name}.md",
                    additions=role_adds[name],
                )
                out.append((f"overlay-additions:{name}.md", _layout_rel(rel, layout), text.encode("utf-8"), "overlay"))
            else:
                src = _public_source(root, rel)
                out.append((src, _layout_rel(rel, layout), src.read_bytes(), "public"))
        for name in packs:
            rel = f".claude/skills/pack-{name}/SKILL.md"
            if name in ov_packs:
                p = ov_packs[name]
                text = generate.render_skill(p, ver, source=f"overlay pack {p['dir']}")
                out.append((f"overlay:pack-{name}", _layout_rel(rel, layout), text.encode("utf-8"), "overlay"))
            elif name in pack_adds:
                text = generate.render_skill(
                    pub_packs[name], ver, source=f"overlay additions pack {name}", additions=pack_adds[name]
                )
                out.append(
                    (f"overlay-additions:pack-{name}", _layout_rel(rel, layout), text.encode("utf-8"), "overlay")
                )
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
            elif name in role_adds:
                text = generate.render_copilot_agent(
                    pub_roles[name],
                    policies,
                    ver,
                    source=f"overlay additions role {name}.md",
                    additions=role_adds[name],
                )
                out.append((f"overlay-additions:{name}.md", rel, text.encode("utf-8"), "overlay"))
            else:
                src = _public_source(root, rel)
                out.append((src, rel, src.read_bytes(), "public"))
    return out


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _plan(target, adapter, packs=None, root=ROOT, update=False, layout="project", overlay=None, force_edited=False):
    """[(source, dest Path, action, bytes, sha of the existing file or None, overlay-sourced)]; raises before writing.

    action: new | update | unchanged | edited (not replaceable without force) | forced (edited, will be discarded)."""
    _check_options(adapter, layout)
    if force_edited and not update:
        raise ValueError("--force-edited only makes sense with --update")
    ov = load_overlay(overlay, root) if overlay is not None else None
    target = _check_target(target, root)
    items = []
    for src, rel, data, origin in _items(adapter, packs, layout, root, ov):
        dest = target / rel
        ensure_safe(dest)
        assert_outside_repo(dest, root)
        action, seen = "new", None
        if dest.exists() or dest.is_symlink():
            if not update:
                raise ValueError(f"collision; merge manually (or use --update): {dest.relative_to(target)}")
            state, _rel, _src, existing = inspect(dest)
            if state in ("owned", "legacy"):
                action = "unchanged" if existing == data else "update"
            elif state in ("edited", "legacy-unverified"):
                action = "forced" if force_edited else "edited"
            else:
                raise ValueError(f"not a generated file; merge manually: {dest.relative_to(target)}")
            seen = _sha(existing)
        # Reject a file in place of a directory before writing anything.
        for parent in dest.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"parent is not a directory: {parent}")
        items.append((src, dest, action, data, seen, origin == "overlay"))
    return items


def plan(target, adapter, packs=None, root=ROOT, update=False, layout="project", overlay=None, force_edited=False):
    """[(source, destination, action)]; raises before anything is written. See _plan for the actions."""
    return [
        (s, d, a) for s, d, a, _, _, _ in _plan(target, adapter, packs, root, update, layout, overlay, force_edited)
    ]


def _refuse_edited(items):
    edited = [str(d.name) for _, d, a, _, _, _ in items if a == "edited"]
    if edited:
        raise ValueError(
            "edited files refused (merge manually, or --force-edited discards your changes): " + ", ".join(edited)
        )


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _race_seam(stage, dest):
    """No-op hook run after the pre-write checks ('checked') and after the write ('written'); tests swap paths here."""


def _open_dir_chain(path, create):
    """A descriptor for the directory `path`, opened component by component with no link followed anywhere.

    Every component is opened O_DIRECTORY|O_NOFOLLOW relative to the previous descriptor, so a component that is (or
    later becomes) a symlink can never redirect what is done through the returned descriptor."""
    parts = Path(path).absolute().parts
    fd = os.open(parts[0], _DIR_FLAGS)
    try:
        for part in parts[1:]:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o777, dir_fd=fd)  # the umask applies, as it did for Path.mkdir
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _same_dir(dfd, path):
    """True if `path` (not following a final link) is still the directory the descriptor holds."""
    try:
        now = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    held = os.fstat(dfd)
    return stat.S_ISDIR(now.st_mode) and (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _changed(dest):
    return ValueError(f"destination changed during install: {dest.name}")


def _write_one(dest, action, data, seen, private):
    try:
        dfd = _open_dir_chain(dest.parent, create=True)
    except OSError as exc:
        raise ValueError(f"not a plain directory chain ({exc.strerror}): {dest.parent}") from None
    tmp = None
    try:
        name, old = dest.name, None
        if action in ("update", "forced"):
            try:
                ofd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
            except OSError:
                raise _changed(dest) from None
            try:
                st = os.fstat(ofd)
                if not stat.S_ISREG(st.st_mode):
                    raise _changed(dest)
                content = os.read(ofd, MAX_MANAGED_BYTES + 1)
                keep_mode = stat.S_IMODE(st.st_mode)
            finally:
                os.close(ofd)
            if _sha(content) != seen:
                raise _changed(dest)
            old = (st.st_dev, st.st_ino)
        _race_seam("checked", dest)
        if not _same_dir(dfd, dest.parent):
            raise ValueError(f"directory replaced during install: {dest.parent}")
        if old is None:
            try:
                fd = os.open(name, _CREATE_FLAGS, 0o600 if private else 0o644, dir_fd=dfd)
            except OSError:
                raise _changed(dest) from None
            try:
                _write_all(fd, data)
            finally:
                os.close(fd)
        else:
            tmp = name + ".doia-tmp"
            try:
                fd = os.open(tmp, _CREATE_FLAGS, 0o600, dir_fd=dfd)
            except OSError as exc:
                tmp = None
                raise ValueError(f"cannot create temporary file for {name}: {exc.strerror}") from None
            try:
                # Never widen: keep the old mode; owner-only if the new content carries overlay text.
                os.fchmod(fd, keep_mode & 0o600 if private else keep_mode)
                _write_all(fd, data)
            finally:
                os.close(fd)
            try:
                cur = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError:
                raise _changed(dest) from None
            if not stat.S_ISREG(cur.st_mode) or (cur.st_dev, cur.st_ino) != old:
                raise _changed(dest)
            os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            tmp = None
        _race_seam("written", dest)
        if not _same_dir(dfd, dest.parent):
            raise ValueError(
                f"directory replaced during install (the file went to the original directory): {dest.parent}"
            )
    finally:
        if tmp:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        os.close(dfd)


def _apply(items, root=ROOT):
    _refuse_edited(items)
    for _src, dest, action, data, seen, private in items:
        if action == "unchanged":
            continue
        ensure_safe(dest)
        assert_outside_repo(dest, root)
        _write_one(dest, action, data, seen, private)


def install(target, adapter, apply=False, packs=None, root=ROOT, update=False, layout="project", overlay=None, **kw):
    items = _plan(target, adapter, packs, root, update, layout, overlay, **kw)
    _refuse_edited(items)
    if apply:
        _apply(items, root)
    return [str(dest.relative_to(Path(target).absolute())) for _, dest, _, _, _, _ in items]


def _scan_patterns(layout):
    if layout == "user":
        return ("agents/*.md", "skills/*/SKILL.md")
    return (".claude/agents/*.md", ".claude/skills/*/SKILL.md", ".github/agents/*.agent.md")


def _read_capped(path):
    with path.open("rb") as f:
        return f.read(MAX_MANAGED_BYTES + 1)


def list_managed(target, layout="project", overlay=None, root=ROOT):
    """Read-only: one row per file under target whose text carries the marker.

    status: current | stale (older release, differs) | differs (this release, content differs) |
            orphan (this checkout and overlay no longer produce it) | unchecked (overlay-sourced, no --overlay given) |
            edited (marker present but the body no longer matches it, or the marker is malformed) |
            legacy-unverified (0.2.0 marker without a hash and a body 0.2.0 never produced).
    Files without the marker text and symlinks are not listed."""
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
            state, release, source, data = inspect(path)
            if state == "none":
                continue
            rel = str(path.relative_to(target))
            if state in ("edited", "legacy-unverified"):
                status = state
            elif source == "overlay" and ov is None:
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


LABELS = {
    "edited": "edited (refused; --force-edited discards your changes)",
    "forced": "update (forced; discards local edits)",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--adapter", choices=ADAPTERS, default="claude")
    ap.add_argument("--layout", choices=LAYOUTS, default="project")
    ap.add_argument("--overlay", type=Path, help="private directory outside this repository (roles/, packs/)")
    ap.add_argument("--pack", action="append", help="install only this pack (repeatable); default all")
    ap.add_argument("--update", action="store_true", help="replace files whose marker and body hash still match")
    ap.add_argument("--force-edited", action="store_true", help="with --update: also replace edited marker files")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--list-managed", action="store_true", help="read-only: list marker files and their drift")
    a = ap.parse_args(argv)
    try:
        if a.list_managed:
            if a.apply or a.update or a.pack or a.force_edited:
                raise ValueError("--list-managed is read-only and takes no --apply, --update, --force-edited or --pack")
            rows = list_managed(a.target, a.layout, a.overlay)
            for r in rows:
                print(f"{r['status']:17} {r['release']:10} {r['source']:8} {_safe(r['path'])}")
            bad = [r for r in rows if r["status"] != "current"]
            print(f"{len(rows)} managed files, {len(bad)} not current")
            return 3 if bad else 0
        items = _plan(
            a.target,
            a.adapter,
            a.pack,
            update=a.update,
            layout=a.layout,
            overlay=a.overlay,
            force_edited=a.force_edited,
        )
        target = Path(a.target).absolute()
        for _, dest, action, _, _, _ in items:
            print(f"{LABELS.get(action, action)}: {dest.relative_to(target)}")
        _refuse_edited(items)
        if a.apply:
            _apply(items)
    except (OSError, ValueError) as exc:
        print(f"Installation refused: {exc}", file=sys.stderr)
        return 1
    print(("Installed" if a.apply else "Preview only; rerun with --apply after review") + f": {len(items)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
