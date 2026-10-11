"""Private overlay: personas and packs that must not live in the public tree.

An overlay is a directory OUTSIDE this repository with the repository's own source shape:

    <overlay>/roles/<name>.md             same format as roles/ (frontmatter + body, fragments allowed)
    <overlay>/packs/<name>/PACK.md        same format as packs/

It is read through the same parsers as the public sources (lib.parse_frontmatter, lib.resolve_fragments) and
checked by the same structural rules (validate.role_errors, validate.pack_errors).

What is deliberately NOT applied: the public-repo privacy sweep (validate.privacy_hits). That sweep exists to keep
identifiers out of the public tree; an overlay is private by definition and is never written into this repository
(load_overlay refuses a path inside it and install.py refuses any destination inside it). Everything else is
applied, and reading is confined: no symlinks anywhere on the path, only regular files, size and count caps, owned
by the current user, not group- or world-writable, strict names.
"""

import os
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path

from lib import ROOT, ensure_safe, load_roles, parse_frontmatter, resolve_fragments
from validate import override_errors, pack_errors, role_errors

MAX_FILE_BYTES = 64 * 1024
MAX_ENTRIES = 64
MAX_NESTING = 8
NAME_PATTERN = r"[a-z0-9][a-z0-9-]{0,63}"

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class OverlayPermissionError(ValueError):
    """Owner or mode refusal; load_overlay adds the exact remedy."""


@dataclass(frozen=True)
class Overlay:
    roles: tuple
    packs: tuple

    @property
    def role_names(self):
        return {r["name"] for r in self.roles}

    @property
    def pack_names(self):
        return {p["dir"] for p in self.packs}


def _name_ok(name):
    return re.fullmatch(NAME_PATTERN, name) is not None


def _check_owner_and_mode(st, label):
    if st.st_uid != os.geteuid():
        raise OverlayPermissionError(f"overlay {label}: not owned by the current user")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OverlayPermissionError(f"overlay {label}: group- or world-writable")


def _open_dir(name, label, dir_fd=None):
    """Open a directory without following a symlink and check owner and mode on the open descriptor."""
    try:
        fd = os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    except OSError as exc:
        raise ValueError(f"overlay {label}: cannot open as a plain directory ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"overlay {label}: not a directory")
        _check_owner_and_mode(st, label)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _entries(fd, label):
    names = sorted(os.listdir(fd))
    if len(names) > MAX_ENTRIES:
        raise ValueError(f"overlay {label}: too many entries (limit {MAX_ENTRIES})")
    out = []
    for name in names:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"overlay {label}/{_shown(name)}: symlink refused")
        out.append((name, st))
    return out


def _shown(name):
    return "".join(c if " " <= c <= "~" else "?" for c in name)


def _read_file(dir_fd, name, label):
    """Read one regular file, at most MAX_FILE_BYTES, never following a link and never blocking on a FIFO."""
    try:
        fd = os.open(name, _OPEN_FILE, dir_fd=dir_fd)
    except OSError as exc:
        raise ValueError(f"overlay {label}: cannot open ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"overlay {label}: not a regular file")
        _check_owner_and_mode(st, label)
        if st.st_size > MAX_FILE_BYTES:
            raise ValueError(f"overlay {label}: too large (limit {MAX_FILE_BYTES} bytes)")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ValueError(f"overlay {label}: too large (limit {MAX_FILE_BYTES} bytes)")
        data = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"overlay {label}: not valid UTF-8") from None


def nesting_depth(value):
    """Container nesting depth of a parsed JSON value (a scalar is 0), computed without recursion."""
    deepest, stack = 0, [(value, 0)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, (list, dict)):
            d += 1
            deepest = max(deepest, d)
            stack.extend((c, d) for c in (v.values() if isinstance(v, dict) else v))
    return deepest


def _parse(text, label):
    try:
        meta, body = parse_frontmatter(text, f"overlay {label}")
    except RecursionError:
        raise ValueError(f"overlay {label}: frontmatter nested too deeply") from None
    if nesting_depth(meta) > MAX_NESTING:
        raise ValueError(f"overlay {label}: frontmatter nested too deeply (limit {MAX_NESTING})")
    return meta, body


def _load_roles(root_fd, root):
    out = []
    fd = _open_dir("roles", "roles", dir_fd=root_fd)
    try:
        for name, st in _entries(fd, "roles"):
            label = f"roles/{_shown(name)}"
            if not name.endswith(".md") or not _name_ok(name[:-3]):
                raise ValueError(f"overlay {label}: unexpected entry (expected <name>.md)")
            if not stat.S_ISREG(st.st_mode):
                raise ValueError(f"overlay {label}: not a regular file")
            meta, body = _parse(_read_file(fd, name, label), label)
            meta = dict(meta)
            if meta.get("name") != name[:-3]:
                raise ValueError(f"overlay {label}: name must match the file name")
            meta["source"] = body
            meta["body"] = resolve_fragments(body, root, f"overlay {label}")
            meta["file"] = name
            out.append(meta)
    finally:
        os.close(fd)
    return out


def _load_packs(root_fd):
    out = []
    fd = _open_dir("packs", "packs", dir_fd=root_fd)
    try:
        for name, st in _entries(fd, "packs"):
            label = f"packs/{_shown(name)}"
            if not _name_ok(name) or not stat.S_ISDIR(st.st_mode):
                raise ValueError(f"overlay {label}: unexpected entry (expected a <name>/ directory)")
            sub = _open_dir(name, label, dir_fd=fd)
            try:
                inner = _entries(sub, label)
                if [n for n, _ in inner] != ["PACK.md"] or not stat.S_ISREG(inner[0][1].st_mode):
                    raise ValueError(f"overlay {label}: must hold exactly PACK.md")
                meta, body = _parse(_read_file(sub, "PACK.md", f"{label}/PACK.md"), f"{label}/PACK.md")
            finally:
                os.close(sub)
            meta = dict(meta)
            meta["body"] = body
            meta["dir"] = name
            out.append(meta)
    finally:
        os.close(fd)
    return out


def _outside_repo(path, root):
    resolved = Path(os.path.realpath(path))
    repo = Path(os.path.realpath(root))
    if resolved.is_relative_to(repo) or repo.is_relative_to(resolved):
        raise ValueError("overlay must be outside this repository")


def load_overlay(path, root=ROOT):
    """Read, confine and validate an overlay. Raises ValueError for anything wrong; returns an Overlay."""
    path = Path(path).absolute()
    ensure_safe(path)
    _outside_repo(path, root)
    try:
        return _load(path, root)
    except OverlayPermissionError as exc:
        raise ValueError(f"{exc}; remedy: chmod -R g-w,o-w {shlex.quote(str(path))}") from None


def _load(path, root):
    root_fd = _open_dir(str(path), "directory")
    try:
        present = {}
        for name, st in _entries(root_fd, "directory"):
            if name not in ("roles", "packs") or not stat.S_ISDIR(st.st_mode):
                raise ValueError(f"overlay: unexpected entry {_shown(name)!r} (only roles/ and packs/ are allowed)")
            present[name] = True
        if not present:
            raise ValueError("overlay holds neither roles/ nor packs/")
        try:
            roles = _load_roles(root_fd, root) if "roles" in present else []
            packs = _load_packs(root_fd) if "packs" in present else []
        except (TypeError, KeyError, RecursionError) as exc:  # hostile-but-parseable frontmatter shapes
            raise ValueError(f"overlay: malformed content ({type(exc).__name__})") from None
    finally:
        os.close(root_fd)
    errors = []
    try:
        public = {r["name"]: r for r in load_roles(root)}
        for r in roles:
            own = role_errors(r)
            errors.extend(own)
            if not own and r["name"] in public:  # a replacement of a public persona also keeps its hard rules
                errors.extend(override_errors(r, public[r["name"]]))
        for p in packs:
            errors.extend(pack_errors(p))
    except (TypeError, KeyError) as exc:
        raise ValueError(f"overlay: malformed content ({type(exc).__name__})") from None
    # NOTE: validate.privacy_hits is intentionally not called: the sweep protects the PUBLIC tree.
    if errors:
        raise ValueError("overlay invalid: " + "; ".join(errors))
    return Overlay(tuple(roles), tuple(packs))
