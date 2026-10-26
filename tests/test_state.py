"""The trusted per-user state store (scripts/state.py, issue #39): strict reading, confinement, atomic writes.

Every refusal test also proves nothing was written (the state directory holds no new file), and the guard list at
the bottom carries a negative case: each guard is named, and the test that covers it must exist.
"""

import contextlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# DOIA_STATE_SCRIPTS lets the guard table below run this file against a deliberately weakened copy of state.py.
sys.path.insert(0, os.environ.get("DOIA_STATE_SCRIPTS") or str(ROOT / "scripts"))

import state  # noqa: E402  (path set up above)

D1 = "a" * 64
D2 = "b" * 64
LAYOUT = "project"


def entry(**kw):
    base = {"kind": "file", "body_sha256": D1, "release": "0.4.0", "source": "public"}
    base.update(kw)
    return base


def _hang(signum, frame):
    raise AssertionError("test hung (a lock or a read blocked); failing fast instead of wedging the suite")


class StateCase(unittest.TestCase):
    def setUp(self):
        # A regression that makes the lock or a read block must fail the test, not hang the suite.
        old = signal.signal(signal.SIGALRM, _hang)
        signal.alarm(20)
        self.addCleanup(signal.signal, signal.SIGALRM, old)
        self.addCleanup(signal.alarm, 0)
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

    def test_unicode_control_format_and_separator_characters(self):
        for ch in (
            "\u0085",
            "\u0080",
            "\u009f",
            "\u2028",
            "\u2029",
            "\u202e",
            "\u2066",
            "\u200b",
            "\ufeff",
            "\ue000",
            "\u00ad",
        ):
            with self.subTest(ch=hex(ord(ch))):
                self.refused(json.dumps(self.doc(entries={f"a{ch}b.md": entry()})).encode())
        self.plant(
            json.dumps(self.doc(entries={"d\u00e9j\u00e0/\u65e5\u672c.md": entry()}), ensure_ascii=False).encode()
        )
        self.assertEqual(len(state.load(self.target, LAYOUT)), 1)  # ordinary non-ASCII letters are fine

    def test_colliding_names_after_nfc_and_casefold(self):
        pairs = [("A.md", "a.md"), ("d\u00e9.md", "de\u0301.md"), ("Stra\u00dfe.md", "STRASSE.md"), ("x/Y", "x/y")]
        for a, b in pairs:
            with self.subTest(a=a.encode("unicode_escape")):
                with self.assertRaisesRegex(state.StateError, "collide"):
                    state.check_entries({a: entry(), b: entry()})
                self.refused(json.dumps(self.doc(entries={a: entry(), b: entry()})).encode())
        state.check_entries({"a.md": entry(), "b.md": entry(), "a/b.md": entry(), "ab.md": entry()})

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

    def test_symlinked_base_is_resolved_once_and_works(self):
        real = self.tmp / "realparent"
        real.mkdir(mode=0o700)
        os.symlink(real, self.tmp / "link")
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(self.tmp / "link" / "s")}):
            state.save(self.target, LAYOUT, {"x.md": entry()})
            self.assertEqual(list(state.load(self.target, LAYOUT)), ["x.md"])
        self.assertEqual(sorted(os.listdir(real)), ["s"])
        self.assertEqual(stat.S_IMODE((real / "s").stat().st_mode), 0o700)

    def test_symlink_inside_the_base_chain_default_location(self):
        # a dotfiles-managed ~/.local and a /home -> /var/home style link both sit ABOVE the state directory
        real = self.tmp / "realhome"
        (real / ".local").mkdir(parents=True, mode=0o700)
        os.symlink(real, self.tmp / "home")
        env = {"HOME": str(self.tmp / "home")}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("DOIA_STATE_DIR")
            os.environ.pop("XDG_STATE_HOME", None)
            state.save(self.target, LAYOUT, {"x.md": entry()})
            self.assertEqual(list(state.load(self.target, LAYOUT)), ["x.md"])
        self.assertTrue((real / ".local" / "state" / state.APP_DIR).is_dir())

    def test_symlink_below_the_base_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir(mode=0o700)
        os.symlink(outside, self.sdir)  # the state directory itself is the link
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()
        self.assertEqual(os.listdir(outside), [])

    def test_symlink_swapped_in_between_check_and_open_is_not_followed(self):
        outside = self.tmp / "outside"
        outside.mkdir(mode=0o700)
        state.save(self.target, LAYOUT, {"a.md": entry()})
        real_check = state.check_location
        moved = self.tmp / "moved"

        def swap(*a, **kw):
            real_check(*a, **kw)
            os.replace(self.sdir, moved)
            os.symlink(outside, self.sdir)

        with mock.patch.object(state, "check_location", side_effect=swap):
            self.assertLoadRefused()
        os.unlink(self.sdir)
        os.replace(moved, self.sdir)
        with mock.patch.object(state, "check_location", side_effect=swap):
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {"x.md": entry()})
        self.assertEqual(os.listdir(outside), [])

    def test_base_directory_group_or_world_writable_is_refused(self):
        base = self.tmp / "base"
        base.mkdir(mode=0o700)
        os.chmod(base, 0o777)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(base / "s")}):
            with self.assertRaisesRegex(state.StateError, "writable"):
                state.save(self.target, LAYOUT, {})
            with self.assertRaisesRegex(state.StateError, "writable"):
                state.load(self.target, LAYOUT)
        self.assertEqual(os.listdir(base), [])

    def test_sticky_writable_base_is_allowed(self):
        base = self.tmp / "base"
        base.mkdir(mode=0o700)
        os.chmod(base, 0o1777)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(base / "s")}):
            state.save(self.target, LAYOUT, {})
            self.assertEqual(state.load(self.target, LAYOUT), {})

    @unittest.skipUnless(os.path.isdir("/tmp") and os.stat("/tmp").st_uid == 0, "needs a root-owned sticky /tmp")
    def test_root_owned_sticky_base_is_allowed(self):
        d = Path(tempfile.mkdtemp(prefix="doia-rootbase-", dir="/tmp"))
        shutil.rmtree(d)  # keep only the unique name; the state directory is created directly below /tmp
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(d)}):
            state.save(self.target, LAYOUT, {})
            self.assertEqual(state.load(self.target, LAYOUT), {})

    def test_base_owned_by_another_user_is_refused(self):
        base = self.tmp / "base"
        base.mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(base / "s")}):
            with mock.patch.object(state, "_euid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(state.StateError, "not owned"):
                    state.save(self.target, LAYOUT, {})

    def test_errors_show_only_the_last_two_components(self):
        self.mkdir_state(0o775)
        with self.assertRaises(state.StateError) as cm:
            state.load(self.target, LAYOUT)
        self.assertNotIn(str(self.tmp), str(cm.exception))
        self.assertIn("state", str(cm.exception))
        self.assertEqual(state._tail("/u/someone/.local/state/app"), "state/app")
        self.assertEqual(state._tail("/x\x1b/y\n"), "x?/y?")

    def test_home_empty_or_root_refused_unless_a_base_is_explicit(self):
        for home in ("", "/"):
            with self.subTest(home=home):
                with mock.patch.dict(os.environ, {"HOME": home}):
                    os.environ.pop("DOIA_STATE_DIR")
                    os.environ.pop("XDG_STATE_HOME", None)
                    with self.assertRaises(state.StateError):
                        state.state_dir()
                    os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg")
                    self.assertEqual(state.state_dir(), self.tmp / "xdg" / state.APP_DIR)
        with mock.patch.dict(os.environ, {"HOME": ""}):
            self.assertEqual(state.state_dir(), self.sdir)  # DOIA_STATE_DIR is explicit

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

        def euid():
            # the directories pass, the file does not
            f, names = sys._getframe(1), set()
            while f:  # the mock sits between the caller and this function, so look up the whole stack
                names.add(f.f_code.co_name)
                f = f.f_back
            return real_euid + 1 if "_read_file" in names else real_euid

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


class Targets(StateCase):
    def test_unicode_target_round_trips(self):
        target = self.tmp / "t\u00e4rget-\u65e5\u672c"
        target.mkdir()
        state.save(target, LAYOUT, {"a.md": entry()})
        self.assertEqual(state.load(target, LAYOUT), {"a.md": entry()})

    def test_non_utf8_target_is_refused_at_save_and_at_load(self):
        bad = os.path.join(os.fsencode(self.tmp), b"bad-\xff-target")
        os.mkdir(bad)
        bad = os.fsdecode(bad)
        before = self.listing()
        with self.assertRaisesRegex(state.StateError, "UTF-8"):
            state.save(bad, LAYOUT, {"a.md": entry()})
        with self.assertRaisesRegex(state.StateError, "UTF-8"):
            state.load(bad, LAYOUT)
        self.assertEqual(self.listing(), before)
        self.assertFalse(self.sdir.exists())


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

    def test_directory_fsync_failure_after_the_rename_says_the_state_may_be_replaced(self):
        state.save(self.target, LAYOUT, {"old.md": entry()})
        with mock.patch.object(state, "_fsync_dir", side_effect=OSError(5, "boom")):
            with self.assertRaisesRegex(state.StateError, "may already have been replaced"):
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["new.md"])  # the rename did happen

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


class Accepts(StateCase):
    def test_non_digest_values_are_never_accepted(self):
        bad = [
            None,
            "",
            b"a" * 64,
            123,
            0,
            True,
            ["a" * 64],
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            "a" * 64 + "\n",
            " " + "a" * 63,
        ]
        for e in (
            entry(),
            entry(pending_sha256=D2),
            {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2},
        ):
            for value in bad:
                with self.subTest(value=repr(value)[:20], keys=sorted(e)):
                    self.assertFalse(state.accepts(e, value))

    def test_accepts_exactly_the_stored_digests(self):
        e = entry(pending_sha256=D2)
        self.assertTrue(state.accepts(e, D1))
        self.assertTrue(state.accepts(e, D2))
        self.assertFalse(state.accepts(e, "c" * 64))
        self.assertFalse(state.accepts(entry(), D2))

    def test_pending_only_entry_accepts_only_the_pending_digest(self):
        e = {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2}
        self.assertTrue(state.accepts(e, D2))
        self.assertFalse(state.accepts(e, D1))
        self.assertFalse(state.accepts(e, None))


class TwoPhase(StateCase):
    NEW = {"kind": "file", "release": "0.5.0", "source": "public"}

    def test_pending_then_settle_with_written_set(self):
        entries = {"a.md": entry(), "b.md": entry(body_sha256=D1)}
        pend = state.with_pending(entries, {"a.md": D2})
        self.assertEqual(pend["a.md"]["pending_sha256"], D2)
        self.assertNotIn("pending_sha256", pend["b.md"])
        self.assertNotIn("pending_sha256", entries["a.md"])  # the input is not mutated
        with state.Store(self.target, LAYOUT) as store:
            store.save(pend)
            self.assertEqual(store.load(), pend)
            self.assertTrue(state.accepts(pend["a.md"], D1) and state.accepts(pend["a.md"], D2))
            store.save(state.settle(store.load(), {"a.md"}))
        got = state.load(self.target, LAYOUT)
        self.assertEqual(got["a.md"]["body_sha256"], D2)
        self.assertNotIn("pending_sha256", got["a.md"])
        self.assertFalse(state.accepts(got["a.md"], D1))
        self.assertTrue(state.accepts(got["a.md"], D2))

    def test_settle_promotes_only_what_was_written(self):
        pend = state.with_pending({"a.md": entry(), "b.md": entry()}, {"a.md": D2, "b.md": "c" * 64})
        done = state.settle(pend, {"a.md"})
        self.assertEqual(done["a.md"]["body_sha256"], D2)
        self.assertEqual(done["b.md"]["body_sha256"], D1)  # not written: the old digest stays
        self.assertTrue(all("pending_sha256" not in e for e in done.values()))

    def test_settle_with_nothing_written_drops_every_pending(self):
        pend = state.with_pending({"a.md": entry()}, {"a.md": D2}, new={"n.md": {**self.NEW, "pending_sha256": D2}})
        done = state.settle(pend, set())
        self.assertEqual(done, {"a.md": entry()})

    def test_settle_unknown_written_path_is_an_error(self):
        pend = state.with_pending({"a.md": entry()}, {"a.md": D2})
        with self.assertRaisesRegex(state.StateError, "unknown"):
            state.settle(pend, {"zzz.md"})
        with self.assertRaises(state.StateError):
            state.settle(pend, {"../x"})

    def test_settle_clears_stale_pending_even_for_written_paths_without_one(self):
        stale = {"a.md": entry(pending_sha256=D2), "b.md": entry()}
        done = state.settle(stale, {"b.md"})
        self.assertEqual(done, {"a.md": entry(), "b.md": entry()})

    def test_new_file_in_pending_form(self):
        base = {"a.md": entry()}
        pend = state.with_pending(base, {}, new={"n.md": {**self.NEW, "pending_sha256": D2}})
        self.assertNotIn("body_sha256", pend["n.md"])
        self.assertNotIn("n.md", base)
        state.save(self.target, LAYOUT, pend)  # phase 1 hits disk
        got = state.load(self.target, LAYOUT)  # ... and the process dies here
        self.assertTrue(state.accepts(got["n.md"], D2))  # a file with exactly the intended content reads as owned
        self.assertFalse(state.accepts(got["n.md"], D1))  # nothing else does
        self.assertFalse(state.accepts(got["n.md"], None))
        self.assertEqual(state.settle(got, {"n.md"})["n.md"]["body_sha256"], D2)
        self.assertNotIn("n.md", state.settle(got, set()))  # never written: no ownership claim remains

    def test_new_entry_rules(self):
        base = {"a.md": entry()}
        bad = [
            {"a.md": {**self.NEW, "pending_sha256": D2}},  # already exists
            {"n.md": {**self.NEW}},  # no digest at all
            {"n.md": {**self.NEW, "pending_sha256": D2, "body_sha256": D1}},  # new means no body digest yet
            {"n.md": {**self.NEW, "pending_sha256": "nope"}},
            {"../n.md": {**self.NEW, "pending_sha256": D2}},
        ]
        for i, new in enumerate(bad):
            with self.subTest(i), self.assertRaises(state.StateError):
                state.with_pending(base, {}, new=new)

    def test_pending_only_entry_needs_a_pending_digest(self):
        neither = {"a.md": {"kind": "file", "release": "1", "source": "public"}}
        self.plant(json.dumps(self.doc(entries=neither)).encode())
        self.assertLoadRefused()
        self.assertSaveRefusedAndNothingWritten()
        only_pending = {"a.md": {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2}}
        self.plant(json.dumps(self.doc(entries=only_pending)).encode())
        self.assertEqual(state.load(self.target, LAYOUT), only_pending)

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


# Each row weakens one guard in a scratch copy of scripts/state.py and runs ONE named test against the copy in a
# subprocess (DOIA_STATE_SCRIPTS). The test must go red (or hang past the timeout, which also counts as red). A row
# whose "old" text is no longer in state.py fails loudly, so the table cannot go stale unnoticed.
MUTANTS = [
    (
        "duplicate key",
        '            raise StateError(f"duplicate key: {_shown(key)}")',
        "            pass",
        "StrictParse.test_duplicate_key",
    ),
    (
        "NaN",
        '    raise StateError(f"not allowed in state: {_shown(name)}")',
        "    return 0",
        "StrictParse.test_nan_is_named_in_the_error",
    ),
    ("unknown entry keys", "not keys <= _ENTRY_KEYS", "False", "StrictParse.test_unknown_entry_key"),
    ("entry count", "    if len(entries) > MAX_ENTRIES:", "    if False:", "StrictParse.test_too_many_entries"),
    ("dotdot path", 'any(part in ("", ".", "..") for part in rel.split("/"))', "False", "StrictParse.test_bad_paths"),
    (
        "C* characters",
        "in _BAD_CATEGORIES",
        "in ()",
        "StrictParse.test_unicode_control_format_and_separator_characters",
    ),
    (
        "collisions",
        "    if len(seen) != len(entries):",
        "    if False:",
        "StrictParse.test_colliding_names_after_nfc_and_casefold",
    ),
    (
        "digest regex",
        '_DIGEST = re.compile(r"[0-9a-f]{64}")',
        '_DIGEST = re.compile(r"[0-9a-fA-F]{60,64}")',
        "StrictParse.test_bad_digests",
    ),
    (
        "utf-8",
        '        text = data.decode("utf-8")',
        '        text = data.decode("utf-8", "replace")',
        "StrictParse.test_not_utf8",
    ),
    (
        "size cap read",
        "            if total > MAX_BYTES:",
        "            if False:",
        "StrictParse.test_size_cap_has_three_independent_layers",
    ),
    ("target field", 'or doc["target"] != real', "or False", "StrictParse.test_target_field_not_matching_the_key"),
    (
        "file owner",
        '        if st.st_uid != _euid():\n            raise StateError(f"state file',
        '        if False:\n            raise StateError(f"state file',
        "Confinement.test_file_owned_by_another_user",
    ),
    ("file mode", "        if stat.S_IMODE(st.st_mode) != 0o600:", "        if False:", "Confinement.test_file_modes"),
    (
        "final dir mode",
        '    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\n        raise StateError(f"{label}: group',
        '    if False:\n        raise StateError(f"{label}: group',
        "Confinement.test_directory_mode_0775",
    ),
    (
        "root-owned base",
        "st.st_uid not in (_euid(), 0)",
        "st.st_uid != _euid()",
        "Confinement.test_root_owned_sticky_base_is_allowed",
    ),
    (
        "directory fchmod",
        "        os.fchmod(fd, 0o700)  # a tight umask",
        "        pass  # a tight umask",
        "WriteSafety.test_file_mode_is_0600_even_with_a_tight_umask",
    ),
    (
        "base writable",
        "            _check_base(bfd",
        "            (lambda *a: None)(bfd",
        "Confinement.test_base_directory_group_or_world_writable_is_refused",
    ),
    (
        "nofollow below base",
        "_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC",
        "_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC",
        "Confinement.test_symlink_below_the_base_is_refused",
    ),
    (
        "base not resolved",
        "Path(os.path.realpath(path.parent))",
        "Path(path.parent)",
        "Confinement.test_symlinked_base_is_resolved_once_and_works",
    ),
    (
        "inside target",
        'for label, base in (("the target", Path(os.path.realpath(target))), ',
        "for label, base in (",
        "Confinement.test_state_directory_inside_the_target",
    ),
    (
        "HOME root",
        'home.rstrip("/") == ""',
        "False",
        "Confinement.test_home_empty_or_root_refused_unless_a_base_is_explicit",
    ),
    (
        "lock nonblocking",
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        "fcntl.LOCK_EX",
        "WriteSafety.test_second_save_while_lock_held_fails_and_first_file_unchanged",
    ),
    (
        "crash cleanup",
        "            with contextlib.suppress(OSError):\n                os.unlink(tmp",
        "            with contextlib.suppress(OSError):\n                pass #",
        "WriteSafety.test_crash_before_rename_keeps_old_file_and_leaves_no_temp",
    ),
    (
        "corrupt blocks write",
        "        self.load()  # tampered",
        "        # tampered",
        "WriteSafety.test_existing_corrupt_state_blocks_the_write_but_deleting_it_recovers",
    ),
    (
        "fsync wrap",
        "rename: the state may already have been replaced",
        "rename: the state replaced",
        "WriteSafety.test_directory_fsync_failure_after_the_rename_says_the_state_may_be_replaced",
    ),
    (
        "non-utf8 target",
        '    if not _text_ok(real):\n        raise StateError("target path must be valid UTF-8',
        '    if False:\n        raise StateError("target path must be valid UTF-8',
        "Targets.test_non_utf8_target_is_refused_at_save_and_at_load",
    ),
    ("accepts None", " if d is not None]", "]", "Accepts.test_non_digest_values_are_never_accepted"),
    (
        "settle promotes all",
        '        if rel in written and "pending_sha256" in entry:',
        '        if "pending_sha256" in entry:',
        "TwoPhase.test_settle_promotes_only_what_was_written",
    ),
    (
        "settle keeps stale pending",
        'entry.pop("pending_sha256", None)',
        "None",
        "TwoPhase.test_settle_clears_stale_pending_even_for_written_paths_without_one",
    ),
    (
        "pending-new not removed",
        '        if "body_sha256" not in entry:\n            del out[rel]',
        "        if False:\n            del out[rel]",
        "TwoPhase.test_settle_with_nothing_written_drops_every_pending",
    ),
    (
        "pending-only needs digest",
        'if "body_sha256" not in entry and "pending_sha256" not in entry',
        "if False",
        "TwoPhase.test_pending_only_entry_needs_a_pending_digest",
    ),
]


class GuardTable(unittest.TestCase):
    def run_one(self, scripts_dir, test_id):
        env = dict(os.environ, DOIA_STATE_SCRIPTS=str(scripts_dir))
        try:
            r = subprocess.run(
                [sys.executable, "-m", "unittest", f"tests.test_state.{test_id}"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return False  # a hang is a failure of the guard's test, which is the point
        return r.returncode == 0

    def test_every_listed_guard_goes_red_when_weakened_and_green_when_intact(self):
        self.assertGreaterEqual(len(MUTANTS), 28)
        source = (ROOT / "scripts" / "state.py").read_text()
        tmp = Path(tempfile.mkdtemp(prefix="doia-mut-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        # <copy>/scripts/state.py: the module derives the repository root from its own location (parents[1]), which
        # must not swallow the temp directories the tests create
        intact = tmp / "intact" / "scripts"
        intact.mkdir(parents=True)
        (intact / "state.py").write_text(source)
        survivors = []
        for n, (name, old, new, test_id) in enumerate(MUTANTS):
            with self.subTest(name):
                self.assertTrue(old in source, f"stale mutant row: {name}")
                self.assertTrue(hasattr(sys.modules[__name__], test_id.split(".")[0]))
                self.assertTrue(self.run_one(intact, test_id), f"{test_id} is not green on the intact copy")
                mutant = tmp / f"m{n}" / "scripts"
                mutant.mkdir(parents=True)
                (mutant / "state.py").write_text(source.replace(old, new, 1))
                if self.run_one(mutant, test_id):
                    survivors.append(name)
        self.assertEqual(survivors, [], "weakened guards whose test stayed green")

    def test_the_runner_itself_detects_a_survivor(self):
        """Negative case: a no-op 'mutation' leaves the test green, and the runner reports it as green."""
        tmp = Path(tempfile.mkdtemp(prefix="doia-mut-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = tmp / "x" / "scripts"
        copy.mkdir(parents=True)
        (copy / "state.py").write_text((ROOT / "scripts" / "state.py").read_text())
        self.assertTrue(self.run_one(copy, "StrictParse.test_duplicate_key"))
        (copy / "state.py").write_text(
            (ROOT / "scripts" / "state.py")
            .read_text()
            .replace('            raise StateError(f"duplicate key: {_shown(key)}")', "            pass", 1)
        )
        self.assertFalse(self.run_one(copy, "StrictParse.test_duplicate_key"))


if __name__ == "__main__":
    unittest.main()
