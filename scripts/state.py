"""Trusted per-user state store: which installed files this installer owns (issue #39, unit 1 of #31).

A marker line inside an installed file is not proof that this installer wrote it: a file that arrived by clone,
synced dotfiles, a copy or another account carries a valid marker too. The proof is a record the user's own
installer wrote, kept OUTSIDE the target and outside this repository, so nothing a repository ships can vouch
for itself.

Location: $DOIA_STATE_DIR (absolute) else ${XDG_STATE_HOME:-$HOME/.local/state}/department-of-internal-arguments/.
One file per target and layout: <first 32 hex of sha256(realpath(target) NUL layout)>.json, mode 0600.

    {"schema": 1, "target": <realpath>, "layout": <layout>,
     "entries": {<relative path>: {"kind": "file"|"lead", "body_sha256": <64 hex>, "release": <str>,
                                   "source": "public"|"overlay", "pending_sha256": <64 hex, optional>}}}

What this is not: no device or inode (checkouts, restores and rename-saves change them), no keyed MAC (the key
lifecycle costs more than a local installer earns), no timestamps (stable bytes). It does not defend against
anyone who can write the file as this user or as root, or against a rollback to an older state file.

Reading is strict and fails closed. Absent, null or mistyped is an error; there is no fallback to anything weaker. A
missing file is the one non-error "no" answer: load returns None (every marked file is then merely unverified).

Writes are atomic: a temporary 0600 file created with O_EXCL through the directory descriptor, fsync, rename, fsync
of the directory, all under an exclusive non-blocking flock so two installers cannot interleave. An update that
rewrites files is two-phase: save the new digests as pending (with_pending), write the files, save again settled
(settle). A crash in between leaves both digests accepted (accepts) and a re-run converges.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

SCHEMA = 1
MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 4096
MAX_DEPTH = 4
MAX_PATH_BYTES = 1024
APP_DIR = "department-of-internal-arguments"

KINDS = ("file", "lead")
SOURCES = ("public", "overlay")
_TOP_KEYS = {"schema", "target", "layout", "entries"}
_ENTRY_REQUIRED = {"kind", "body_sha256", "release", "source"}
_ENTRY_KEYS = _ENTRY_REQUIRED | {"pending_sha256"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_RELEASE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}")
_LAYOUT = re.compile(r"[a-z][a-z0-9-]{0,31}")

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_TMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

_REPO_ROOT = Path(__file__).resolve().parents[1]


class StateError(ValueError):
    """Anything about the state store that is not fine. The caller refuses the whole write run."""


def _shown(text, limit=80):
    """Echo-safe form of untrusted text: printable ASCII only, bounded."""
    text = str(text)
    out = "".join(c if " " <= c <= "~" else "?" for c in text[:limit])
    return out + ("..." if len(text) > limit else "")


def _euid():
    return os.geteuid()


# --- location --------------------------------------------------------------------------------------------------


def state_dir():
    """The state directory path (nothing is created or opened)."""
    explicit = os.environ.get("DOIA_STATE_DIR")
    if explicit is not None:
        base = explicit
        if not base or not os.path.isabs(base):
            raise StateError("DOIA_STATE_DIR must be an absolute path")
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        if xdg:
            if not os.path.isabs(xdg):
                raise StateError("XDG_STATE_HOME must be an absolute path")
            root = xdg
        else:
            home = os.path.expanduser("~")
            if not os.path.isabs(home):
                raise StateError("cannot determine the home directory for the state store")
            root = os.path.join(home, ".local", "state")
        base = os.path.join(root, APP_DIR)
    if "\0" in base or ".." in Path(base).parts:
        raise StateError("the state directory path must not contain '..' or NUL")
    return Path(os.path.normpath(base))


def _inside(path, parent):
    return path == parent or parent in path.parents


def check_location(target, directory=None):
    """The state directory must not be inside the target or inside this repository (checked on resolved paths)."""
    directory = state_dir() if directory is None else directory
    real = Path(os.path.realpath(directory))
    for label, base in (("the target", Path(os.path.realpath(target))), ("this repository", _REPO_ROOT.resolve())):
        for cand in (real, directory):
            if _inside(cand, base):
                raise StateError(f"the state directory is inside {label}; refused")


def state_key(target, layout):
    """(realpath of target, layout, 32-hex file stem)."""
    if not isinstance(layout, str) or not _LAYOUT.fullmatch(layout):
        raise StateError(f"invalid layout: {_shown(layout)}")
    real = os.path.realpath(target)
    stem = hashlib.sha256(f"{real}\0{layout}".encode("utf-8", "surrogateescape")).hexdigest()[:32]
    return real, layout, stem


# --- descriptor walk -------------------------------------------------------------------------------------------


def _check_dir(fd, label):
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise StateError(f"{label}: not a directory")
    if st.st_uid != _euid():
        raise StateError(f"{label}: not owned by the current user")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StateError(f"{label}: group- or world-writable")


def _open_state_dir(path, create):
    """A descriptor for the state directory, opened component by component with no link followed anywhere.

    Returns None when `create` is false and the directory does not exist. Created components get mode 0700. The final
    directory is checked for owner and mode on the open descriptor."""
    parts = path.parts
    fd = os.open(parts[0], _DIR_FLAGS)
    try:
        for part in parts[1:]:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                made = True
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    made = False
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
                if made:
                    os.fchmod(nxt, 0o700)  # a tight umask must not leave the directory unusable
            os.close(fd)
            fd = nxt
        _check_dir(fd, f"state directory {_shown(path)}")
    except OSError as exc:
        os.close(fd)
        raise StateError(f"state directory {_shown(path)}: cannot open as a plain directory ({exc.strerror})") from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _fsync_dir(dfd):
    os.fsync(dfd)


# --- strict parse ----------------------------------------------------------------------------------------------


def _text_ok(s):
    if not isinstance(s, str):
        return False
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def check_rel_path(rel):
    """A relative, normalized path with no link-like or control content."""
    if not _text_ok(rel) or not rel:
        raise StateError("invalid path: empty or not text")
    if len(rel.encode("utf-8")) > MAX_PATH_BYTES:
        raise StateError(f"invalid path (too long): {_shown(rel)}")
    if rel.startswith("/") or "\\" in rel or any(ord(c) < 32 or ord(c) == 127 for c in rel):
        raise StateError(f"invalid path: {_shown(rel)}")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise StateError(f"invalid path (not normalized): {_shown(rel)}")


def _check_entry(rel, entry):
    check_rel_path(rel)
    if type(entry) is not dict:
        raise StateError(f"entry {_shown(rel)}: not an object")
    keys = set(entry)
    if not _ENTRY_REQUIRED <= keys or not keys <= _ENTRY_KEYS:
        raise StateError(f"entry {_shown(rel)}: missing or unknown keys")
    if entry["kind"] not in KINDS or type(entry["kind"]) is not str:
        raise StateError(f"entry {_shown(rel)}: invalid kind")
    if entry["source"] not in SOURCES or type(entry["source"]) is not str:
        raise StateError(f"entry {_shown(rel)}: invalid source")
    for key in ("body_sha256", "pending_sha256"):
        if key in entry and not (type(entry[key]) is str and _DIGEST.fullmatch(entry[key])):
            raise StateError(f"entry {_shown(rel)}: {key} is not 64 lowercase hex")
    rel_ver = entry["release"]
    if not (type(rel_ver) is str and _RELEASE.fullmatch(rel_ver)):
        raise StateError(f"entry {_shown(rel)}: invalid release")


def check_entries(entries):
    """Validate an entries mapping; returns a deep, plain copy."""
    if type(entries) is not dict:
        raise StateError("entries: not an object")
    if len(entries) > MAX_ENTRIES:
        raise StateError(f"too many entries (limit {MAX_ENTRIES})")
    for rel, entry in entries.items():
        _check_entry(rel, entry)
    return {rel: dict(entry) for rel, entry in entries.items()}


def _max_depth(text):
    """Deepest bracket nesting, outside strings; stops early once over the limit."""
    depth = deepest = 0
    in_str = esc = False
    for c in text:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
            deepest = max(deepest, depth)
            if deepest > MAX_DEPTH:
                break
        elif c in "}]":
            depth -= 1
    return deepest


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise StateError(f"duplicate key: {_shown(key)}")
        seen[key] = value
    return seen


def _no_constant(name):
    raise StateError(f"not allowed in state: {_shown(name)}")


def parse(data, real, layout):
    """Strictly parse state bytes for (real, layout); returns the entries mapping or raises StateError."""
    if len(data) > MAX_BYTES:
        raise StateError("state file over the size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise StateError("state file is not valid UTF-8") from None
    if _max_depth(text) > MAX_DEPTH:
        raise StateError("state file nests too deeply")
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except StateError:
        raise
    except (ValueError, RecursionError) as exc:
        raise StateError(f"state file is not valid JSON ({_shown(getattr(exc, 'msg', 'error'))})") from None
    if type(doc) is not dict or set(doc) != _TOP_KEYS:
        raise StateError("state file: missing or unknown top-level keys")
    if type(doc["schema"]) is not int or doc["schema"] != SCHEMA:
        raise StateError("state file: unknown schema")
    if not _text_ok(doc["target"]) or doc["target"] != real:
        raise StateError("state file: target does not match")
    if not _text_ok(doc["layout"]) or doc["layout"] != layout:
        raise StateError("state file: layout does not match")
    return check_entries(doc["entries"])


def serialize(real, layout, entries):
    entries = check_entries(entries)
    doc = {"schema": SCHEMA, "target": real, "layout": layout, "entries": entries}
    data = (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    if len(data) > MAX_BYTES:
        raise StateError("state would exceed the size limit")
    return data


# --- read ------------------------------------------------------------------------------------------------------


def _read_file(dfd, name, real, layout):
    """The parsed entries, or None when the file does not exist. Never follows a link, never blocks on a FIFO."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateError(f"state file {name}: cannot open as a plain file ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StateError(f"state file {name}: not a regular file")
        if st.st_uid != _euid():
            raise StateError(f"state file {name}: not owned by the current user")
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise StateError(f"state file {name}: mode must be 0600")
        if st.st_nlink != 1:
            raise StateError(f"state file {name}: has more than one link")
        if st.st_size > MAX_BYTES:
            raise StateError(f"state file {name}: over the size limit")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise StateError(f"state file {name}: over the size limit")
            chunks.append(chunk)
    finally:
        os.close(fd)
    return parse(b"".join(chunks), real, layout)


def load(target, layout):
    """The entries mapping for (target, layout), None when there is no state file, StateError for anything else."""
    real, layout, stem = state_key(target, layout)
    directory = state_dir()
    check_location(target, directory)
    dfd = _open_state_dir(directory, create=False)
    if dfd is None:
        return None
    try:
        return _read_file(dfd, stem + ".json", real, layout)
    finally:
        os.close(dfd)


# --- write -----------------------------------------------------------------------------------------------------


class Store:
    """Exclusive write access for one (target, layout). Hold it across both phases of a two-phase update."""

    def __init__(self, target, layout):
        self.real, self.layout, self.stem = state_key(target, layout)
        directory = state_dir()
        check_location(target, directory)
        self._dfd = _open_state_dir(directory, create=True)
        self._lfd = None
        try:
            self._lock()
        except BaseException:
            self.close()
            raise

    def _lock(self):
        try:
            lfd = os.open(self.stem + ".lock", _LOCK_FLAGS, 0o600, dir_fd=self._dfd)
        except OSError as exc:
            raise StateError(f"state lock: cannot open ({exc.strerror})") from None
        try:
            st = os.fstat(lfd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != _euid():
                raise StateError("state lock: not a regular file owned by the current user")
            os.fchmod(lfd, 0o600)
            try:
                fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise StateError("another install is running") from None
        except BaseException:
            os.close(lfd)
            raise
        self._lfd = lfd

    def close(self):
        for attr in ("_lfd", "_dfd"):
            fd = getattr(self, attr, None)
            if fd is not None:
                os.close(fd)
                setattr(self, attr, None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def load(self):
        return _read_file(self._dfd, self.stem + ".json", self.real, self.layout)

    def save(self, entries):
        """Replace the state atomically. An existing file that does not read cleanly refuses the write."""
        data = serialize(self.real, self.layout, entries)
        self.load()  # tampered or corrupt state refuses the write; recovery is deleting the file
        name = self.stem + ".json"
        tmp = f".{self.stem}.{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, _TMP_FLAGS, 0o600, dir_fd=self._dfd)
        try:
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, name, src_dir_fd=self._dfd, dst_dir_fd=self._dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=self._dfd)
            raise
        _fsync_dir(self._dfd)


def save(target, layout, entries):
    """One-shot atomic save under the lock. For a two-phase update use `with Store(...) as s`."""
    real, layout, _ = state_key(target, layout)
    serialize(real, layout, entries)  # refuse bad entries before the directory or the lock is touched
    with Store(target, layout) as store:
        store.save(entries)


# --- two-phase helpers (pure) ----------------------------------------------------------------------------------


def with_pending(entries, pending):
    """Copy of entries with pending_sha256 set from {relative path: digest}; the other entries are unchanged."""
    out = check_entries(entries)
    for rel, digest in pending.items():
        if rel not in out:
            raise StateError(f"no entry to update: {_shown(rel)}")
        out[rel]["pending_sha256"] = digest
    return check_entries(out)


def settle(entries):
    """Copy of entries with every pending digest promoted to body_sha256 and removed."""
    out = check_entries(entries)
    for entry in out.values():
        if "pending_sha256" in entry:
            entry["body_sha256"] = entry.pop("pending_sha256")
    return out


def accepts(entry, digest):
    """True if digest is the recorded body digest or the pending one (a crash left both acceptable)."""
    return digest in (entry["body_sha256"], entry.get("pending_sha256"))
