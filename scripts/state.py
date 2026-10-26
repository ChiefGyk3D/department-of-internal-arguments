"""Trusted per-user state store: which installed files this installer owns (issue #39, unit 1 of #31).

A marker line inside an installed file is not proof that this installer wrote it: a file that arrived by clone,
synced dotfiles, a copy or another account carries a valid marker too. The proof is a record the user's own
installer wrote, kept OUTSIDE the target and outside this repository, so nothing a repository ships can vouch
for itself.

Location: $DOIA_STATE_DIR (absolute) else ${XDG_STATE_HOME:-$HOME/.local/state}/department-of-internal-arguments/.
HOME must name a real directory (empty and "/" are refused) unless DOIA_STATE_DIR or XDG_STATE_HOME is set.
One file per target and layout: <first 32 hex of sha256(realpath(target) NUL layout)>.json, mode 0600.

    {"schema": 1, "target": <realpath>, "layout": <layout>,
     "entries": {<relative path>: {"kind": "file"|"lead", "release": <str>, "source": "public"|"overlay",
                                   "body_sha256": <64 hex>, "pending_sha256": <64 hex>}}}

body_sha256 and pending_sha256 are each optional but an entry carries at least one. A target path must be valid UTF-8
(a path the JSON file cannot hold is refused by load and save alike, so save never writes a file load cannot read).
Public functions never leak a raw exception: mutators raise StateError on any hostile argument, and accepts() and
verdict() are total (they accept only plain dict entries and plain str digests and answer False or unverified).

Threat model. The defended case: a marker-valid file that arrived by clone, synced dotfiles, a copy or a lower
privileged writer is not treated as owned, because nothing it ships can add an entry here. Not defended, by design:
anyone who can write the state file as this user or as root; a rollback of the state file to an older one; the content
of the installed text itself; and anyone who controls the process environment, HOME or DOIA_STATE_DIR. That last route
is named on purpose: pointing DOIA_STATE_DIR at a directory the attacker built (the file name is predictable, being a
hash of the realpath and layout) makes a forged file read as genuine. A per-directory salt was considered and not
added: whoever builds the directory writes the salt too, so it would add a failure mode (a lost salt orphans every
record) and no protection. The same holds for a same-user attacker, who can read any salt.

What this is not: no device or inode (checkouts, restores and rename-saves change them), no keyed MAC (the key
lifecycle costs more than a local installer earns), no timestamps (stable bytes).

Path trust. The BASE directory (the parent of the state directory: the XDG or HOME-derived directory, or the parent of
DOIA_STATE_DIR) is the user's own choice and is resolved once with realpath, so a symlinked ~/.local, a /home link or a
macOS /var link works. Everything below the resolved base is walked with O_NOFOLLOW through directory descriptors and
no link is ever followed there. The base must be owned by the user or root and not group or world writable unless
sticky; the state directory must be owned by the user and not group or world writable. Created directories are 0700.
Directories above the base are only resolved, not checked: the rule covers the base and the state directory.

Reading is strict and fails closed. Absent, null or mistyped is an error; there is no fallback to anything weaker. A
missing file is the one non-error "no" answer: load returns None (every marked file is then merely unverified).
Relative paths in entries are text only: a consumer must still lstat-verify each path through a no-follow walk of its
own before trusting what is at it. Names are refused if they hold any Unicode C* character (controls including C1,
format and bidi characters, private use, unassigned) or a line or paragraph separator, and two entries whose
NFC and case-folded forms are equal are refused (one file on a case-insensitive or normalizing filesystem).

Writes are atomic: a temporary 0600 file created with O_EXCL through the directory descriptor, fsync, rename, fsync
of the directory, all under an exclusive non-blocking flock so two installers cannot interleave. If the directory
fsync fails after the rename, save raises StateError saying the state may already have been replaced: re-load to learn
the truth.

Hand-off notes for the installer (#40). Use `load_or_empty` or `load` (None = no file) and `verdict(entries, rel,
digest)` with `body_sha256(bytes)` for every ownership decision; `accepts` is only the low-level predicate. Catch
StateLocked and StateCorrupt to tell "another install is running" from "the file is not trustworthy" (a dry run or
`--list-managed` reports either read-only; a write run refuses both, `--force-edited` included); any other StateError
is an environment or location problem and refuses too. `lock_held` is the read-only probe and `state_path` the value
for a `state:` line. `with_entry` and `without_entry` are the copy-and-validate edits for adopt and drop. Hold one
`Store` across both phases of an update. A dry run must call only load, load_or_empty, lock_held and state_path (none
creates the directory or the lock file). The state directory may not be, be inside, or contain the target or this
repository, so a `--layout user` target that is the home directory cannot use the default location under it; set
DOIA_STATE_DIR to a directory elsewhere (to be decided in #40). A raw OSError never escapes this module.

Two-phase update. Phase 1: with_pending records the digest each file is about to get (an existing path gets a
pending_sha256 next to its body_sha256; a NEW path is added with only pending_sha256, via `new=`). Save. Write the
files. Phase 2: settle(entries, written) with the set of relative paths actually written, then save. Settling promotes
the pending digest of written paths, keeps the old digest of paths not written, removes a new path that was not written,
and clears every pending field.

Crash recovery. accepts() and verdict() honor a pending digest only NEXT TO a recorded body digest: that file was
already ours and the pending bytes are the installer's own next release content. An entry with no body digest (a NEW
path recorded in phase 1) is never owned, whatever the digest, because a digest proves equal content and not who wrote
the file, and anyone who controls the target can create the planned bytes. A crashed install therefore leaves its new
paths `unverified`; the files it did write are recovered by the explicit adopt step of the installer (#42), after the
user has looked at the preview; a re-run that writes the path promotes it through settle().

Provenance. `owned` attests that the CONTENT matches a digest the installer recorded, not that the installer wrote
those bytes. For an already-owned path the pending digest means owned for the pending content even if someone else
placed it: that is the same content the installer would write, so it adds no new authority to overwrite anything. A
caller that needs stronger provenance (who wrote the bytes) must not rely on this store alone.

Limits, stated so nobody relies on more than this gives. flock is advisory and its behavior on network filesystems
(NFS and the like) is not guaranteed; keep the state directory on a local filesystem. The sweep of orphaned temporary
files checks a name with lstat and then unlinks it, so a same-user process racing it can swap the file in between: a
same-user attacker is out of scope. lock_held takes a shared lock for an instant, so a same-user process probing in a
loop could starve a writer; that is also out of scope. Hashing a bytearray that another thread mutates while
body_sha256 reads it is out of scope too. Neither flock nor the sweep defends against root.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import unicodedata
import uuid
from pathlib import Path, PosixPath, PurePosixPath

SCHEMA = 1
MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 4096
MAX_DEPTH = 4
MAX_PATH_BYTES = 1024
MIN_TMP_AGE = 5.0  # seconds before an orphaned temporary file may be reaped
APP_DIR = "department-of-internal-arguments"

KINDS = ("file", "lead")
SOURCES = ("public", "overlay")
_TOP_KEYS = {"schema", "target", "layout", "entries"}
_ENTRY_REQUIRED = {"kind", "release", "source"}
_ENTRY_KEYS = _ENTRY_REQUIRED | {"body_sha256", "pending_sha256"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_RELEASE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,31}")
_LAYOUT = re.compile(r"[a-z][a-z0-9-]{0,31}")
# Unicode general categories refused in a relative path: all of C* (control incl. C1, format incl. bidi and
# zero-width, surrogate, private use, unassigned) plus the line and paragraph separators.
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_TMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATH_TYPES = (str, PurePosixPath, PosixPath)


class StateError(ValueError):
    """Anything about the state store that is not fine. The caller refuses the whole write run. Raw OSError never
    escapes this module: every I/O failure is reported as a StateError with escaped text."""


class StateLocked(StateError):
    """Another install holds the lock. A dry run or a listing may carry on read-only; a write run refuses."""


class StateCorrupt(StateError):
    """The state FILE is not trustworthy: bad content, wrong owner, mode, type, links or size, or it belongs to another
    target or layout. A write run refuses; recovery is deleting the file (everything then reads as unverified)."""


def _shown(text, limit=80):
    """Echo-safe form of untrusted text: printable ASCII only, bounded.

    Only an exact str (or exact int) is rendered. Anything else becomes "?": str(), repr() or format() is never called
    on a foreign object, so none of its code runs (and nothing it raises, SystemExit included, escapes)."""
    if type(text) is int:
        text = int.__repr__(text)
    elif type(text) is not str:
        text = "?"
    out = "".join(c if " " <= c <= "~" else "?" for c in text[:limit])
    return out + ("..." if len(text) > limit else "")


def _oserr(exc):
    """The escaped strerror of an OSError raised by the operating system (never a foreign object)."""
    return _shown(exc.strerror if type(exc.strerror) is str else type(exc).__name__)


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
            home = os.environ.get("HOME")
            if home is None:
                home = os.path.expanduser("~")
            if not os.path.isabs(home) or home.rstrip("/") == "":
                raise StateError(
                    "cannot determine a usable home directory for the state store; set DOIA_STATE_DIR or XDG_STATE_HOME"
                )
            root = os.path.join(home, ".local", "state")
        base = os.path.join(root, APP_DIR)
    if "\0" in base or ".." in Path(base).parts:
        raise StateError("the state directory path must not contain '..' or NUL")
    return Path(os.path.normpath(base))


def _inside(path, parent):
    return path == parent or parent in path.parents


def _real_path(target):
    """realpath of a target given as an exact str, PurePosixPath or PosixPath (so os.fspath only ever runs the standard
    library's own code, never a foreign __fspath__); anything else, a NUL, or a failure is a StateError."""
    if not _is_one_of(target, _PATH_TYPES):
        raise StateError("the target must be a str or a pathlib path")
    path = os.fspath(target)
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        raise StateError("the target path cannot be resolved") from None


def check_location(target, directory=None):
    """The state directory must not be, be inside, or contain the target or this repository (resolved and literal)."""
    directory = state_dir() if directory is None else directory
    real = Path(os.path.realpath(directory))
    for label, base in (("the target", Path(_real_path(target))), ("this repository", _REPO_ROOT.resolve())):
        for cand in (real, directory):
            if _inside(cand, base):
                raise StateError(f"the state directory is inside {label}; refused")
            if _inside(base, cand):
                raise StateError(f"the state directory contains {label}; refused")


def state_key(target, layout):
    """(realpath of target, layout, 32-hex file stem)."""
    if type(layout) is not str or not _LAYOUT.fullmatch(layout):
        raise StateError(f"invalid layout: {_shown(layout)}")
    real = _real_path(target)
    if not _text_ok(real):
        raise StateError("target path must be valid UTF-8 (the state file could not hold it)")
    stem = hashlib.sha256(f"{real}\0{layout}".encode()).hexdigest()[:32]
    return real, layout, stem


def state_path(target, layout):
    """The state file path for (target, layout), for display (a `state:` line). Nothing is created or opened."""
    _, _, stem = state_key(target, layout)
    return state_dir() / (stem + ".json")


def _is_one_of(obj, types):
    """Exact-type test that never calls anything on `obj` (no __class__, __eq__ or isinstance hook)."""
    t = type(obj)
    return any(t is c for c in types)


def body_sha256(data):
    """The one definition of a body digest, for the installer and the store alike: lowercase hex sha256 of the bytes."""
    if not _is_one_of(data, (bytes, bytearray, memoryview)):
        raise StateError("body_sha256 takes bytes")
    try:
        return hashlib.sha256(data).hexdigest()
    except (ValueError, TypeError, BufferError):
        raise StateError("body_sha256: the buffer cannot be read (released or not contiguous)") from None


def _check_lock_file(fd, created):
    """The lock file gets the state file's discipline: a regular file, owned by the user, a single link, mode 0600.
    A file this call just created is set to 0600 (a tight umask may have narrowed it); an existing one is validated
    first and never repaired, so a hostile or damaged lock file refuses the run."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise StateCorrupt("state lock: not a regular file")
    if st.st_uid != _euid():
        raise StateCorrupt("state lock: not owned by the current user")
    if st.st_nlink != 1:
        raise StateCorrupt("state lock: has more than one link")
    if created:
        os.fchmod(fd, 0o600)
    elif stat.S_IMODE(st.st_mode) != 0o600:
        raise StateCorrupt("state lock: mode must be 0600")


# --- descriptor walk -------------------------------------------------------------------------------------------


def _tail(path):
    """Echo-safe last two components of a path; the rest (a home directory, a user name) is not repeated."""
    parts = [p for p in Path(str(path)).parts if p != "/"]
    return "/".join(_shown(p, 40) for p in parts[-2:])


def _check_dir(fd, label):
    """The state directory itself: a directory, owned by the current user, not group or world writable."""
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise StateError(f"{label}: not a directory")
    if st.st_uid != _euid():
        raise StateError(f"{label}: not owned by the current user")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StateError(f"{label}: group- or world-writable")


def _check_base(fd, label):
    """The directory that holds the state directory: owned by the user or root, and not group or world writable
    unless sticky."""
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise StateError(f"{label}: not a directory")
    if st.st_uid not in (_euid(), 0):
        raise StateError(f"{label}: not owned by the current user or root")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not st.st_mode & stat.S_ISVTX:
        raise StateError(f"{label}: group- or world-writable")


def _mkdir_private(name, dir_fd):
    """Open `name` below dir_fd, creating it 0700 when missing; returns (fd, created)."""
    made = True
    try:
        os.mkdir(name, 0o700, dir_fd=dir_fd)
    except FileExistsError:
        made = False
    fd = os.open(name, _DIR_FLAGS, dir_fd=dir_fd)
    if made:
        os.fchmod(fd, 0o700)  # a tight umask must not leave the directory unusable
    return fd


def _open_chain(path, create):
    """A descriptor for the absolute, already resolved directory `path`, opened component by component with no link
    followed. None when a component is missing and `create` is false; created components are 0700."""
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
                nxt = _mkdir_private(part, fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_state_dir(path, create):
    """A descriptor for the state directory. The base (its parent) is resolved once with realpath; the state
    directory is opened below it with O_NOFOLLOW. None when `create` is false and something is missing."""
    name = path.name
    if not name:
        raise StateError("the state directory must not be the filesystem root")
    base = Path(os.path.realpath(path.parent))
    try:
        bfd = _open_chain(base, create)
        if bfd is None:
            return None
        try:
            _check_base(bfd, f"state base {_tail(base)}")
            try:
                fd = os.open(name, _DIR_FLAGS, dir_fd=bfd)
            except FileNotFoundError:
                if not create:
                    return None
                fd = _mkdir_private(name, bfd)
        finally:
            os.close(bfd)
        try:
            _check_dir(fd, f"state directory {_tail(path)}")
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        raise StateError(f"state directory {_tail(path)}: cannot open as a plain directory ({exc.strerror})") from None
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
    if type(rel) is not str or not _text_ok(rel) or not rel:
        raise StateError("invalid path: empty or not text")
    if len(rel.encode("utf-8")) > MAX_PATH_BYTES:
        raise StateError(f"invalid path (too long): {_shown(rel)}")
    if rel.startswith("/") or "\\" in rel or any(unicodedata.category(c) in _BAD_CATEGORIES for c in rel):
        raise StateError(f"invalid path: {_shown(rel)}")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise StateError(f"invalid path (not normalized): {_shown(rel)}")


def _fold(rel):
    """The form in which a case-insensitive or normalizing filesystem would see the name."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", rel).casefold())


def _check_entry(rel, entry):
    check_rel_path(rel)
    if type(entry) is not dict:
        raise StateError(f"entry {_shown(rel)}: not an object")
    if any(type(k) is not str for k in entry):
        raise StateError(f"entry {_shown(rel)}: keys must be text")
    keys = set(entry)
    if not _ENTRY_REQUIRED <= keys or not keys <= _ENTRY_KEYS:
        raise StateError(f"entry {_shown(rel)}: missing or unknown keys")
    kind, source = entry["kind"], entry["source"]
    # The exact type is tested FIRST: `in` on a tuple would call a hostile value's __eq__.
    if type(kind) is not str or kind not in KINDS:
        raise StateError(f"entry {_shown(rel)}: invalid kind")
    if type(source) is not str or source not in SOURCES:
        raise StateError(f"entry {_shown(rel)}: invalid source")
    if "body_sha256" not in entry and "pending_sha256" not in entry:
        raise StateError(f"entry {_shown(rel)}: no digest")
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
    seen = {_fold(rel) for rel in entries}
    if len(seen) != len(entries):
        raise StateError("entries collide after Unicode normalization and case folding")
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
    """Strictly parse state bytes for (real, layout); returns the entries mapping or raises StateCorrupt."""
    try:
        return _parse(data, real, layout)
    except StateCorrupt:
        raise
    except StateError as exc:
        raise StateCorrupt(str(exc)) from None


def _parse(data, real, layout):
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
        raise StateCorrupt(f"state file {name}: cannot open as a plain file ({_oserr(exc)})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StateCorrupt(f"state file {name}: not a regular file")
        if st.st_uid != _euid():
            raise StateCorrupt(f"state file {name}: not owned by the current user")
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise StateCorrupt(f"state file {name}: mode must be 0600")
        if st.st_nlink != 1:
            raise StateCorrupt(f"state file {name}: has more than one link")
        if st.st_size > MAX_BYTES:
            raise StateCorrupt(f"state file {name}: over the size limit")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise StateCorrupt(f"state file {name}: over the size limit")
            chunks.append(chunk)
    except OSError as exc:
        raise StateError(f"state file {name}: read failed ({_oserr(exc)})") from None
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


def load_or_empty(target, layout):
    """Like load, but a missing file is {} (corrupt still raises); for callers that treat no record as no entries."""
    got = load(target, layout)
    return {} if got is None else got


def lock_held(target, layout):
    """True if another process holds the write lock. Read-only probe: creates no directory and no lock file, holds
    nothing afterwards (it takes a shared lock for an instant), so a dry run or a listing can use it."""
    _, _, stem = state_key(target, layout)
    directory = state_dir()
    check_location(target, directory)
    dfd = _open_state_dir(directory, create=False)
    if dfd is None:
        return False
    try:
        try:
            lfd = os.open(stem + ".lock", _READ_FLAGS, dir_fd=dfd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StateError(f"state lock: cannot open as a plain file ({_oserr(exc)})") from None
        try:
            _check_lock_file(lfd, created=False)
            try:
                fcntl.flock(lfd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(lfd, fcntl.LOCK_UN)
            return False
        except OSError as exc:
            raise StateError(f"state lock: probe failed ({_oserr(exc)})") from None
        finally:
            os.close(lfd)
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

    def _open_lock(self, name):
        """(descriptor, created): create the lock file exclusively, else open the existing one without following it."""
        for _ in range(3):
            try:
                return os.open(name, _LOCK_FLAGS | os.O_EXCL, 0o600, dir_fd=self._dfd), True
            except FileExistsError:
                pass
            try:
                return os.open(name, _READ_FLAGS, dir_fd=self._dfd), False
            except FileNotFoundError:
                continue  # removed between the two calls: try again
        raise StateError("state lock: the lock file keeps vanishing")

    def _lock(self):
        try:
            lfd, created = self._open_lock(self.stem + ".lock")
        except OSError as exc:
            raise StateError(f"state lock: cannot open ({_oserr(exc)})") from None
        try:
            _check_lock_file(lfd, created)
            try:
                fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StateLocked("another install is running") from None
        except OSError as exc:
            os.close(lfd)
            raise StateError(f"state lock: {_oserr(exc)}") from None
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

    def _require_open(self):
        if self._dfd is None:
            raise StateError("the state store is closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def load(self):
        self._require_open()
        return _read_file(self._dfd, self.stem + ".json", self.real, self.layout)

    def _discard(self, tmp):
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=self._dfd)

    def save(self, entries):
        """Replace the state atomically. An existing file that does not read cleanly refuses the write. Orphaned
        temporary files of this state (a killed earlier run) are reaped first."""
        self._require_open()
        data = serialize(self.real, self.layout, entries)
        self.load()  # tampered or corrupt state refuses the write; recovery is deleting the file
        _sweep(self._dfd, self.stem)
        name = self.stem + ".json"
        tmp = f".{self.stem}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(tmp, _TMP_FLAGS, 0o600, dir_fd=self._dfd)
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, name, src_dir_fd=self._dfd, dst_dir_fd=self._dfd)
        except OSError as exc:
            self._discard(tmp)
            raise StateError(f"cannot write the state ({_oserr(exc)})") from None
        except BaseException:
            self._discard(tmp)
            raise
        try:
            _fsync_dir(self._dfd)
        except OSError:
            raise StateError(
                "state directory fsync failed after the rename: the state may already have been replaced; "
                "re-load to learn which"
            ) from None


def _sweep(dfd, stem):
    """Remove orphaned `.<stem>.<32 hex>.tmp` files: regular files owned by the current user, older than
    MIN_TMP_AGE. Exact name pattern only, nothing followed, every failure ignored. Call with the lock held."""
    pattern = re.compile(rf"\.{re.escape(stem)}\.[0-9a-f]{{32}}\.tmp")
    try:
        names = os.listdir(dfd)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not pattern.fullmatch(name):
            continue
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != _euid() or now - st.st_mtime < MIN_TMP_AGE:
                continue
            os.unlink(name, dir_fd=dfd)
        except OSError:
            continue


def save(target, layout, entries):
    """One-shot atomic save under the lock. For a two-phase update use `with Store(...) as s`."""
    real, layout, _ = state_key(target, layout)
    serialize(real, layout, entries)  # refuse bad entries before the directory or the lock is touched
    with Store(target, layout) as store:
        store.save(entries)


# --- two-phase helpers (pure) ----------------------------------------------------------------------------------


def with_pending(entries, pending, new=None):
    """Copy of entries for phase 1 of an update.

    `pending` is {relative path: digest} for paths already recorded: pending_sha256 is set next to the old digest.
    `new` is {relative path: {"kind", "release", "source", "pending_sha256"}} for paths not yet recorded: each is added
    WITHOUT a body digest. Such an entry is never owned (accepts and verdict refuse it whatever the digest): it
    becomes owned only when settle() promotes it because phase 2 reported that exact path as written, and settle
    removes it if the file was never written. A crash after phase 1 therefore leaves the path unverified."""
    out = check_entries(entries)
    if type(pending) is not dict:
        raise StateError("with_pending: pending must be a plain dict of path to digest")
    if new is not None and type(new) is not dict:
        raise StateError("with_pending: new must be a plain dict or None")
    for rel, digest in pending.items():
        if type(rel) is not str or rel not in out:
            raise StateError(f"no entry to update: {_shown(rel)}")
        out[rel]["pending_sha256"] = digest
    for rel, spec in (new or {}).items():
        if type(rel) is not str or rel in out:
            raise StateError(f"new entry is not a fresh path: {_shown(rel)}")
        if (
            type(spec) is not dict
            or any(type(k) is not str for k in spec)
            or set(spec) != {"kind", "release", "source", "pending_sha256"}
        ):
            raise StateError(f"new entry {_shown(rel)}: needs exactly kind, release, source and pending_sha256")
        out[rel] = dict(spec)
    return check_entries(out)


def settle(entries, written):
    """Copy of entries for phase 2: `written` is the set of relative paths the update actually wrote.

    The pending digest of a written path becomes its body digest; a path that was not written keeps its old digest;
    an entry that exists only as a pending new path and was not written is removed; every pending field is cleared
    (including stale ones). A path in `written` that has no entry is an error."""
    out = check_entries(entries)
    if not _is_one_of(written, (set, frozenset, list, tuple)):
        raise StateError("settle: written must be a set (or list or tuple) of paths")
    for rel in written:
        if type(rel) is not str:
            raise StateError(f"settle: unknown path {_shown(rel)}")
    written = set(written)
    for rel in written:
        if rel not in out:
            raise StateError(f"settle: unknown path {_shown(rel)}")
    for rel in list(out):
        entry = out[rel]
        if rel in written and "pending_sha256" in entry:
            entry["body_sha256"] = entry["pending_sha256"]
        entry.pop("pending_sha256", None)
        if "body_sha256" not in entry:
            del out[rel]
    return out


def accepts(entry, digest):
    """True only if digest is exactly the recorded body digest, or the pending one NEXT TO a recorded body digest.

    Total: any argument that is not a plain dict entry and a plain str digest is False. An entry with no body_sha256
    (a pending-only entry, a path the installer has not yet been seen to write) is NEVER owned, whatever the digest:
    a digest proves the content is equal, not who wrote the file, and anyone who controls the target can create the
    planned bytes. For a path that already has a body digest the file was already ours, and the pending bytes are the
    installer's own next release content, so a crash after phase 1 still reads as owned there."""
    if type(entry) is not dict or type(digest) is not str:
        return False
    body = entry.get("body_sha256")
    if type(body) is not str:
        return False  # pending-only: not owned until settle() promotes it because phase 2 wrote that exact path
    stored = [body]
    pending = entry.get("pending_sha256")
    if type(pending) is str:
        stored.append(pending)
    return any(d == digest for d in stored)


def with_entry(entries, rel, entry):
    """Copy of entries with `rel` set to `entry` (added or replaced), validated. For adopt and record-after-write."""
    if type(rel) is not str:
        raise StateError("with_entry: path must be text")
    out = check_entries(entries)
    out[rel] = dict(entry) if type(entry) is dict else entry
    return check_entries(out)


def without_entry(entries, rel):
    """Copy of entries without `rel`; an unknown path is an error (no silent no-op)."""
    out = check_entries(entries)
    if type(rel) is not str or rel not in out:
        raise StateError(f"no entry to remove: {_shown(rel)}")
    del out[rel]
    return out


def verdict(entries, rel, digest):
    """'owned' if the entry for `rel` accepts `digest`, else 'unverified'. Total: a missing state file (None), a
    missing entry or a missing, mistyped or malformed digest is 'unverified', never an exception."""
    if type(entries) is not dict or type(rel) is not str:
        return "unverified"
    return "owned" if accepts(entries.get(rel), digest) else "unverified"  # accepts() is total
