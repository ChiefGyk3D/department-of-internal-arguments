"""The trusted per-user state store (scripts/state.py, issue #39): strict reading, confinement, atomic writes.

Every refusal test also proves nothing was written (the state directory holds no new file), and the guard list at
the bottom carries a negative case: each guard is named, and the test that covers it must exist.
"""

import contextlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import state  # noqa: E402  (path set up above)

D1 = "a" * 64
D2 = "b" * 64
LAYOUT = "project"


def entry(**kw):
    base = {"kind": "file", "body_sha256": D1, "release": "0.4.0", "source": "public"}
    base.update(kw)
    return base


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="doia-state-")).resolve()
        self.addCleanup(self._rm)
        self.target = self.tmp / "target"
        self.target.mkdir()
        self.sdir = self.tmp / "state"
        env = mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.sdir)})
        env.start()
        self.addCleanup(env.stop)
        _, _, self.stem = state.state_key(self.target, LAYOUT)
        self.file = self.sdir / (self.stem + ".json")

    def _rm(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def real(self):
        return os.path.realpath(self.target)

    def mkdir_state(self, mode=0o700):
        self.sdir.mkdir(mode=mode, exist_ok=True)
        os.chmod(self.sdir, mode)

    def plant(self, raw, mode=0o600):
        """Put raw bytes at the state file path, the way a tamperer would."""
        self.mkdir_state()
        self.file.unlink(missing_ok=True)
        self.file.write_bytes(raw)
        os.chmod(self.file, mode)

    def doc(self, **over):
        d = {"schema": 1, "target": self.real(), "layout": LAYOUT, "entries": {"a/b.md": entry()}}
        d.update(over)
        return d

    def plant_doc(self, **over):
        self.plant(json.dumps(self.doc(**over)).encode())

    def listing(self):
        """State and temp files; the empty lock file beside them is not state and is left in place by design."""
        names = os.listdir(self.sdir) if self.sdir.exists() else []
        return sorted(n for n in names if not n.endswith(".lock"))

    def assertLoadRefused(self):
        with self.assertRaises(state.StateError):
            state.load(self.target, LAYOUT)

    def assertSaveRefusedAndNothingWritten(self, entries=None):
        before = self.listing()
        with self.assertRaises(state.StateError):
            state.save(self.target, LAYOUT, {"x.md": entry()} if entries is None else entries)
        self.assertEqual(self.listing(), before)


class RoundTrip(StateCase):
    def test_round_trip_keeps_entries_exactly(self):
        entries = {
            "a/b.md": entry(),
            "c.md": entry(kind="lead", source="overlay", body_sha256=D2, pending_sha256=D1, release="1.0.0-rc1"),
        }
        state.save(self.target, LAYOUT, entries)
        self.assertEqual(state.load(self.target, LAYOUT), entries)
        first = self.file.read_bytes()
        state.save(self.target, LAYOUT, state.load(self.target, LAYOUT))
        self.assertEqual(self.file.read_bytes(), first)

    def test_modes_and_name(self):
        state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.sdir.stat().st_mode), 0o700)
        self.assertRegex(self.file.name, r"^[0-9a-f]{32}\.json$")
        self.assertEqual(self.listing(), [self.file.name])

    def test_key_depends_on_target_and_layout(self):
        other = self.tmp / "other"
        other.mkdir()
        self.assertNotEqual(state.state_key(other, LAYOUT)[2], self.stem)
        self.assertNotEqual(state.state_key(self.target, "user")[2], self.stem)

    def test_missing_file_is_none(self):
        self.assertIsNone(state.load(self.target, LAYOUT))  # no directory at all
        self.mkdir_state()
        self.assertIsNone(state.load(self.target, LAYOUT))  # directory, no file
        self.assertEqual(self.listing(), [])

    def test_load_never_creates_the_directory(self):
        state.load(self.target, LAYOUT)
        self.assertFalse(self.sdir.exists())

    def test_empty_entries_is_a_mapping_not_none(self):
        state.save(self.target, LAYOUT, {})
        self.assertEqual(state.load(self.target, LAYOUT), {})


class StrictParse(StateCase):
    def refused(self, raw):
        self.plant(raw)
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()  # a bad existing file also blocks the write run
        self.assertEqual(self.file.read_bytes(), raw)

    def test_valid_planted_file_loads(self):
        self.plant_doc()
        self.assertEqual(state.load(self.target, LAYOUT), {"a/b.md": entry()})

    def test_duplicate_key(self):
        good = json.dumps(self.doc())
        self.refused(good.replace('"schema": 1', '"schema": 1, "schema": 1').encode())

    def test_duplicate_entry_path(self):
        e = json.dumps(entry())
        raw = f'{{"schema":1,"target":{json.dumps(self.real())},"layout":"project","entries":{{"a":{e},"a":{e}}}}}'
        self.refused(raw.encode())

    def test_trailing_garbage(self):
        self.refused(json.dumps(self.doc()).encode() + b" x")

    def test_nan_and_infinity(self):
        for const in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(const):
                raw = json.dumps(self.doc()).replace('"schema": 1', '"schema": ' + const).encode()
                self.refused(raw)

    def test_depth_five(self):
        nested = '{"a":' * 4 + "1" + "}" * 4  # five object levels with the outer document
        raw = f'{{"schema":1,"target":{json.dumps(self.real())},"layout":"project","entries":{nested}}}'
        self.refused(raw.encode())

    def test_nan_is_named_in_the_error(self):
        self.plant(b'{"schema":1,"target":"x","layout":"project","entries":NaN}')
        with self.assertRaisesRegex(state.StateError, "not allowed in state: NaN"):
            state.load(self.target, LAYOUT)

    def test_depth_is_refused_before_json_parses(self):
        self.plant(b"[" * 5 + b"]" * 5)
        with mock.patch.object(state.json, "loads", side_effect=AssertionError("parsed")):
            self.assertLoadRefused()
        self.assertEqual(state._max_depth("[[[[[1]]]]]"), 5)
        self.assertEqual(state._max_depth('{"a":"[[[[[[[["}'), 1)
        self.plant(b"[" * 100000)
        self.assertLoadRefused()

    def test_depth_limit_counts_only_brackets_outside_strings(self):
        self.plant(json.dumps(self.doc(entries={"a/[[[[[[.md": entry(release="1.0")})).encode())
        self.assertIn("a/[[[[[[.md", state.load(self.target, LAYOUT))

    def test_unknown_top_level_key(self):
        self.refused(json.dumps(self.doc(extra=1)).encode())

    def test_unknown_entry_key(self):
        self.refused(json.dumps(self.doc(entries={"a.md": entry(device=1)})).encode())

    def test_missing_null_and_mistyped_fields(self):
        cases = [
            self.doc(schema=None),
            self.doc(schema=True),
            self.doc(schema="1"),
            self.doc(schema=2),
            self.doc(target=None),
            self.doc(layout=None),
            self.doc(entries=None),
            self.doc(entries=[]),
            self.doc(entries={"a.md": None}),
            self.doc(entries={"a.md": entry(kind="dir")}),
            self.doc(entries={"a.md": entry(kind=None)}),
            self.doc(entries={"a.md": entry(source="x")}),
            self.doc(entries={"a.md": entry(release=None)}),
            self.doc(entries={"a.md": entry(release="")}),
            self.doc(entries={"a.md": entry(pending_sha256=None)}),
            self.doc(entries={"a.md": entry(body_sha256=5)}),
        ]
        for i, doc in enumerate(cases):
            with self.subTest(i):
                self.refused(json.dumps(doc).encode())
        for key in ("schema", "target", "layout", "entries"):
            with self.subTest(missing=key):
                doc = self.doc()
                del doc[key]
                self.refused(json.dumps(doc).encode())
        for key in ("kind", "body_sha256", "release", "source"):
            with self.subTest(missing_entry_key=key):
                e = entry()
                del e[key]
                self.refused(json.dumps(self.doc(entries={"a.md": e})).encode())

    def test_not_an_object(self):
        for raw in (b"[]", b"null", b"1", b'"x"', b""):
            with self.subTest(raw):
                self.refused(raw)

    def test_too_many_entries(self):
        many = {f"f{i}.md": entry() for i in range(4097)}
        self.refused(json.dumps(self.doc(entries=many)).encode())
        # exactly the limit is fine
        self.plant(json.dumps(self.doc(entries=dict(list(many.items())[:4096]))).encode())
        self.assertEqual(len(state.load(self.target, LAYOUT)), 4096)

    def test_absolute_path_is_named_as_such(self):
        with self.assertRaisesRegex(state.StateError, r"invalid path: /abs$"):
            state.check_rel_path("/abs")

    def test_bad_paths(self):
        bad = [
            "../x",
            "a/../x",
            "/abs",
            "a//b",
            "./a",
            "a/.",
            "a/",
            "",
            "a\\b",
            "a\x00b",
            "a\nb",
            "a\x7fb",
            "a\x1b[31m",
            "x" * 1025,
            "\ud800",
        ]
        for rel in bad:
            with self.subTest(rel=rel[:20]):
                self.refused(json.dumps(self.doc(entries={rel: entry()})).encode())

    def test_bad_digests(self):
        for digest in ("a" * 63, "a" * 65, "A" * 64, "g" * 64, "", " " + "a" * 63, "a" * 63 + "\n"):
            for key in ("body_sha256", "pending_sha256"):
                with self.subTest(digest=digest[:8], key=key):
                    self.refused(json.dumps(self.doc(entries={"a.md": entry(**{key: digest})})).encode())

    def test_not_utf8(self):
        key = b'"a\xffb.md"'
        e = json.dumps(entry()).encode()
        tgt = json.dumps(self.real()).encode()
        self.refused(b'{"schema":1,"target":' + tgt + b',"layout":"project","entries":{' + key + b":" + e + b"}}")
        self.refused(json.dumps(self.doc()).encode() + b"\xff")
        self.refused(b"\xef\xbb\xbf" + json.dumps(self.doc()).encode())  # a BOM is not tolerated

    def test_over_size_cap(self):
        raw = json.dumps(self.doc()).encode()
        self.refused(raw + b" " * (state.MAX_BYTES + 1 - len(raw)))

    def test_exactly_at_size_cap_is_read(self):
        raw = json.dumps(self.doc()).encode()
        self.plant(raw + b" " * (state.MAX_BYTES - len(raw)))
        self.assertEqual(len(state.load(self.target, LAYOUT)), 1)

    def test_size_cap_has_three_independent_layers(self):
        big = b" " * (state.MAX_BYTES + 1)
        with self.assertRaisesRegex(state.StateError, "size limit"):
            state.parse(big, self.real(), LAYOUT)  # layer 1: parse itself
        self.plant(big)
        with mock.patch.object(state.os, "read", side_effect=AssertionError("read")):
            with self.assertRaisesRegex(state.StateError, "size limit"):
                state.load(self.target, LAYOUT)  # layer 2: fstat size, before any read
        real_fstat = os.fstat

        def lying(fd):
            st = real_fstat(fd)
            fields = (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, 10, 0, 0, 0)
            return os.stat_result(fields)

        with mock.patch.object(state, "parse", side_effect=AssertionError("parsed")):
            with mock.patch.object(state.os, "fstat", side_effect=lying):
                with self.assertRaisesRegex(state.StateError, "size limit"):
                    state.load(self.target, LAYOUT)  # layer 3: bytes actually read

    def test_save_refuses_a_document_over_the_cap(self):
        entries = {f"{i:04d}" + "x" * 1000: entry() for i in range(1100)}
        self.assertLess(len(entries), state.MAX_ENTRIES)
        self.assertSaveRefusedAndNothingWritten(entries)

    def test_target_field_not_matching_the_key(self):
        self.refused(json.dumps(self.doc(target=str(self.tmp / "elsewhere"))).encode())

    def test_layout_field_not_matching_the_key(self):
        self.refused(json.dumps(self.doc(layout="user")).encode())

    def test_file_for_another_target_copied_under_this_key_is_refused(self):
        other = self.tmp / "other"
        other.mkdir()
        state.save(other, LAYOUT, {"a.md": entry()})
        _, _, ostem = state.state_key(other, LAYOUT)
        self.assertEqual(state.load(other, LAYOUT), {"a.md": entry()})
        (self.sdir / (self.stem + ".json")).write_bytes((self.sdir / (ostem + ".json")).read_bytes())
        os.chmod(self.file, 0o600)
        self.assertLoadRefused()

    def test_errors_do_not_echo_control_characters(self):
        raw = json.dumps(self.doc(entries={"a\x1b[31mred\nb": entry()})).encode()
        self.plant(raw)
        with self.assertRaises(state.StateError) as cm:
            state.load(self.target, LAYOUT)
        msg = str(cm.exception)
        self.assertTrue(all(" " <= c <= "~" for c in msg), repr(msg))
        self.assertNotIn("\x1b", msg)

    def test_long_echo_is_bounded(self):
        self.assertLess(len(state._shown("x" * 5000)), 100)


class Confinement(StateCase):
    def test_symlinked_state_file(self):
        self.mkdir_state()
        real = self.tmp / "real.json"
        real.write_text(json.dumps(self.doc()))
        os.chmod(real, 0o600)
        os.symlink(real, self.file)
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()

    def test_symlinked_state_directory(self):
        real = self.tmp / "realstate"
        real.mkdir(mode=0o700)
        os.symlink(real, self.sdir)
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()
        self.assertEqual(os.listdir(real), [])

    def test_symlinked_parent_component(self):
        real = self.tmp / "realparent"
        real.mkdir(mode=0o700)
        os.symlink(real, self.tmp / "link")
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.tmp / "link" / "s")}):
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {"x.md": entry()})
        self.assertEqual(os.listdir(real), [])

    def test_directory_mode_0775(self):
        self.mkdir_state(0o775)
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()

    def test_directory_world_writable(self):
        self.mkdir_state(0o703)
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()

    def test_directory_owned_by_another_user(self):
        self.mkdir_state()
        with mock.patch.object(state, "_euid", return_value=os.geteuid() + 1):
            self.assertLoadRefused()
            self.assertSaveRefusedAndNothingWritten()

    def test_file_owned_by_another_user(self):
        self.plant_doc()
        real_euid = os.geteuid()
        calls = []

        def euid():
            calls.append(1)
            return real_euid if len(calls) == 1 else real_euid + 1  # the directory passes, the file does not

        with mock.patch.object(state, "_euid", side_effect=euid):
            with self.assertRaisesRegex(state.StateError, "not owned"):
                state.load(self.target, LAYOUT)

    def test_file_modes(self):
        for mode in (0o644, 0o660, 0o640, 0o400, 0o700, 0o666):
            with self.subTest(oct(mode)):
                self.plant(json.dumps(self.doc()).encode(), mode=mode)
                self.assertLoadRefused()

    def test_file_not_regular(self):
        self.mkdir_state()
        os.mkfifo(self.file, 0o600)
        with self.assertRaisesRegex(state.StateError, "not a regular file"):  # returns, never blocks on the FIFO
            state.load(self.target, LAYOUT)
        self.file.unlink()
        self.file.mkdir(mode=0o700)
        with self.assertRaisesRegex(state.StateError, "not a regular file"):
            state.load(self.target, LAYOUT)

    def test_hard_linked_state_file(self):
        self.plant_doc()
        os.link(self.file, self.tmp / "second-name")
        self.assertLoadRefused()

    def test_state_directory_inside_the_target(self):
        inside = self.target / ".state"
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(inside)}):
            with self.assertRaisesRegex(state.StateError, "inside the target"):
                state.save(self.target, LAYOUT, {"x.md": entry()})
            with self.assertRaisesRegex(state.StateError, "inside the target"):
                state.load(self.target, LAYOUT)
        self.assertEqual(os.listdir(self.target), [])

    def test_state_directory_equal_to_the_target(self):
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.target)}):
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {"x.md": entry()})

    def test_state_directory_inside_target_by_symlinked_alias(self):
        alias = self.tmp / "alias"
        os.symlink(self.target, alias)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(alias / "s")}):
            with self.assertRaisesRegex(state.StateError, "inside the target"):  # resolved path is inside
                state.load(self.target, LAYOUT)

    def test_state_directory_inside_target_by_literal_path_only(self):
        outside = self.tmp / "outside"
        outside.mkdir(mode=0o700)
        os.symlink(outside, self.target / "lnk")
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.target / "lnk" / "s")}):
            with self.assertRaisesRegex(state.StateError, "inside the target"):  # literal path is inside
                state.load(self.target, LAYOUT)

    def test_state_directory_inside_this_repository(self):
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(ROOT / "scripts" / ".state-test")}):
            with self.assertRaisesRegex(state.StateError, "inside this repository"):
                state.save(self.target, LAYOUT, {"x.md": entry()})
        self.assertFalse((ROOT / "scripts" / ".state-test").exists())

    def test_target_inside_the_repository_does_not_matter_but_state_must_be_outside(self):
        with self.assertRaises(state.StateError):
            state.check_location(self.target, ROOT / "x")

    def test_env_must_be_absolute(self):
        for bad in ("relative/dir", "", "~/x"):
            with self.subTest(bad), mock.patch.dict(os.environ, {"DOIA_STATE_DIR": bad}):
                with self.assertRaises(state.StateError):
                    state.state_dir()
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.tmp / "a" / ".." / "b")}):
            with self.assertRaises(state.StateError):
                state.state_dir()

    def test_default_location(self):
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("DOIA_STATE_DIR")
            os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg")
            self.assertEqual(state.state_dir(), self.tmp / "xdg" / state.APP_DIR)
            os.environ.pop("XDG_STATE_HOME")
            os.environ["HOME"] = str(self.tmp / "home")
            self.assertEqual(state.state_dir(), self.tmp / "home" / ".local" / "state" / state.APP_DIR)
            os.environ["XDG_STATE_HOME"] = "rel"
            with self.assertRaises(state.StateError):
                state.state_dir()

    def test_invalid_layout(self):
        for bad in ("", "A", "../x", "a b", None, "x" * 40):
            with self.subTest(bad), self.assertRaises(state.StateError):
                state.save(self.target, bad, {})

    def test_parents_are_created_private(self):
        deep = self.tmp / "p" / "q" / "r"
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(deep)}):
            state.save(self.target, LAYOUT, {})
        for d in (self.tmp / "p", self.tmp / "p" / "q", deep):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)


class WriteSafety(StateCase):
    def test_invalid_entries_refused_and_nothing_written(self):
        bad = [
            {"../x": entry()},
            {"/abs": entry()},
            {"a.md": entry(body_sha256="a" * 63)},
            {"a.md": entry(body_sha256="A" * 64)},
            {"a.md": entry(extra=1)},
            {f"f{i}.md": entry() for i in range(4097)},
        ]
        for i, entries in enumerate(bad):
            with self.subTest(i):
                self.assertSaveRefusedAndNothingWritten(entries)
        self.assertFalse(self.sdir.exists())  # not even the directory or the lock file was created

    def test_second_save_while_lock_held_fails_and_first_file_unchanged(self):
        state.save(self.target, LAYOUT, {"first.md": entry()})
        first = self.file.read_bytes()
        with state.Store(self.target, LAYOUT):
            with self.assertRaisesRegex(state.StateError, "another install is running"):
                state.save(self.target, LAYOUT, {"second.md": entry()})
            with self.assertRaisesRegex(state.StateError, "another install is running"):
                state.Store(self.target, LAYOUT)
        self.assertEqual(self.file.read_bytes(), first)
        self.assertEqual(self.listing(), [self.file.name])
        state.save(self.target, LAYOUT, {"second.md": entry()})  # the lock is released on exit
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["second.md"])

    def test_lock_released_when_the_block_raises(self):
        with contextlib.suppress(RuntimeError):
            with state.Store(self.target, LAYOUT):
                raise RuntimeError("boom")
        state.save(self.target, LAYOUT, {})

    def test_crash_before_rename_keeps_old_file_and_leaves_no_temp(self):
        state.save(self.target, LAYOUT, {"old.md": entry()})
        old = self.file.read_bytes()
        with mock.patch.object(state.os, "rename", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(self.listing(), [self.file.name])

    def test_crash_during_write_keeps_old_file_and_leaves_no_temp(self):
        state.save(self.target, LAYOUT, {"old.md": entry()})
        old = self.file.read_bytes()
        with mock.patch.object(state.os, "fsync", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(self.listing(), [self.file.name])

    def test_temp_file_is_private_and_created_exclusively(self):
        seen = []
        real_open = os.open

        def spy(path, flags, mode=0o777, *, dir_fd=None):
            if str(path).endswith(".tmp"):
                seen.append((flags, mode))
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(state.os, "open", side_effect=spy):
            state.save(self.target, LAYOUT, {})
        self.assertEqual(len(seen), 1)
        flags, mode = seen[0]
        self.assertEqual(mode, 0o600)
        self.assertTrue(flags & os.O_EXCL and flags & os.O_NOFOLLOW)

    def test_file_mode_is_0600_even_with_a_loose_umask(self):
        old = os.umask(0)
        try:
            state.save(self.target, LAYOUT, {})
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_file_mode_is_0600_even_with_a_tight_umask(self):
        old = os.umask(0o277)
        try:
            state.save(self.target, LAYOUT, {})
            state.save(self.target, LAYOUT, {"a.md": entry()})  # the lock file must be reopenable
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["a.md"])

    def test_fsync_of_file_and_directory(self):
        calls = []
        real = os.fsync
        with mock.patch.object(state.os, "fsync", side_effect=lambda fd: (calls.append(fd), real(fd))[1]):
            state.save(self.target, LAYOUT, {})
        self.assertEqual(len(calls), 2)

    def test_rename_swap_of_directory_path_does_not_redirect_the_write(self):
        self.mkdir_state()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        moved = self.tmp / "moved"
        real_rename = os.rename

        def swap(src, dst, **kw):
            os.replace(self.sdir, moved)
            os.symlink(elsewhere, self.sdir)
            return real_rename(src, dst, **kw)

        with mock.patch.object(state.os, "rename", side_effect=swap):
            state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertTrue((moved / self.file.name).exists())

    def test_existing_corrupt_state_blocks_the_write_but_deleting_it_recovers(self):
        self.plant(b"not json")
        self.assertSaveRefusedAndNothingWritten()
        self.file.unlink()
        state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["a.md"])


class TwoPhase(StateCase):
    def test_pending_then_settle(self):
        entries = {"a.md": entry(), "b.md": entry(body_sha256=D1)}
        pend = state.with_pending(entries, {"a.md": D2})
        self.assertEqual(pend["a.md"]["pending_sha256"], D2)
        self.assertNotIn("pending_sha256", pend["b.md"])
        self.assertNotIn("pending_sha256", entries["a.md"])  # the input is not mutated
        with state.Store(self.target, LAYOUT) as store:
            store.save(pend)
            self.assertEqual(store.load(), pend)
            self.assertTrue(state.accepts(pend["a.md"], D1) and state.accepts(pend["a.md"], D2))
            done = state.settle(store.load())
            store.save(done)
        got = state.load(self.target, LAYOUT)
        self.assertEqual(got["a.md"]["body_sha256"], D2)
        self.assertNotIn("pending_sha256", got["a.md"])
        self.assertFalse(state.accepts(got["a.md"], D1))
        self.assertTrue(state.accepts(got["a.md"], D2))

    def test_pending_for_unknown_path_or_bad_digest_refused(self):
        with self.assertRaises(state.StateError):
            state.with_pending({"a.md": entry()}, {"zzz.md": D2})
        with self.assertRaises(state.StateError):
            state.with_pending({"a.md": entry()}, {"a.md": "nope"})

    def test_crash_between_phases_leaves_both_digests_accepted(self):
        state.save(self.target, LAYOUT, state.with_pending({"a.md": entry()}, {"a.md": D2}))
        got = state.load(self.target, LAYOUT)
        self.assertTrue(state.accepts(got["a.md"], D1) and state.accepts(got["a.md"], D2))
        self.assertFalse(state.accepts(got["a.md"], "c" * 64))


# Each guard in scripts/state.py, named, with the test that bites it. The mutation check in the report removes each
# guard in turn; the second test here proves this table is not stale or empty.
GUARDS = {
    "duplicate key": StrictParse.test_duplicate_key,
    "NaN and Infinity": StrictParse.test_nan_and_infinity,
    "depth": StrictParse.test_depth_five,
    "unknown keys": StrictParse.test_unknown_entry_key,
    "entry count": StrictParse.test_too_many_entries,
    "paths": StrictParse.test_bad_paths,
    "digests": StrictParse.test_bad_digests,
    "utf-8": StrictParse.test_not_utf8,
    "size cap": StrictParse.test_over_size_cap,
    "target field": StrictParse.test_target_field_not_matching_the_key,
    "layout field": StrictParse.test_layout_field_not_matching_the_key,
    "null and mistyped": StrictParse.test_missing_null_and_mistyped_fields,
    "symlinked file": Confinement.test_symlinked_state_file,
    "symlinked directory": Confinement.test_symlinked_state_directory,
    "directory mode": Confinement.test_directory_mode_0775,
    "directory owner": Confinement.test_directory_owned_by_another_user,
    "file owner": Confinement.test_file_owned_by_another_user,
    "file mode": Confinement.test_file_modes,
    "inside target": Confinement.test_state_directory_inside_the_target,
    "inside repository": Confinement.test_state_directory_inside_this_repository,
    "lock": WriteSafety.test_second_save_while_lock_held_fails_and_first_file_unchanged,
    "crash cleanup": WriteSafety.test_crash_before_rename_keeps_old_file_and_leaves_no_temp,
    "corrupt blocks write": WriteSafety.test_existing_corrupt_state_blocks_the_write_but_deleting_it_recovers,
}


class GuardTable(unittest.TestCase):
    def test_table_collects_something_and_every_entry_is_a_real_test(self):
        self.assertGreaterEqual(len(GUARDS), 20)
        for name, fn in GUARDS.items():
            with self.subTest(name):
                self.assertTrue(callable(fn) and fn.__name__.startswith("test_"))
                self.assertTrue(hasattr(fn, "__qualname__"))

    def test_a_weakened_guard_turns_its_test_red(self):
        """Negative case: with the duplicate-key check replaced by a last-wins one, its test fails."""
        weak = mock.patch.object(state, "_no_duplicates", side_effect=lambda pairs: dict(pairs))
        suite = unittest.TestSuite([StrictParse("test_duplicate_key")])
        result = unittest.TestResult()
        with weak:
            suite.run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures) + len(result.errors), 1)


if __name__ == "__main__":
    unittest.main()
