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
sys.path.append(str(ROOT / "scripts"))

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
        signal.alarm(10)
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
            state._parse_strict(big, self.real(), LAYOUT)  # layer 1: parse itself
        self.plant(big)
        with mock.patch.object(state.os, "read", side_effect=AssertionError("read")):
            with self.assertRaisesRegex(state.StateError, "size limit"):
                state.load(self.target, LAYOUT)  # layer 2: fstat size, before any read
        real_fstat = os.fstat

        def lying(fd):
            st = real_fstat(fd)
            fields = (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, 10, 0, 0, 0)
            return os.stat_result(fields)

        with mock.patch.object(state, "_parse_strict", side_effect=AssertionError("parsed")):
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
        real_check = state._check_location
        moved = self.tmp / "moved"

        def swap(*a, **kw):
            real_check(*a, **kw)
            os.replace(self.sdir, moved)
            os.symlink(outside, self.sdir)

        with mock.patch.object(state, "_check_location", side_effect=swap):
            self.assertLoadRefused()
        os.unlink(self.sdir)
        os.replace(moved, self.sdir)
        with mock.patch.object(state, "_check_location", side_effect=swap):
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
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(state._REPO_ROOT / "scripts" / ".state-test")}):
            with self.assertRaisesRegex(state.StateError, "inside this repository"):
                state.save(self.target, LAYOUT, {"x.md": entry()})
        self.assertFalse((state._REPO_ROOT / "scripts" / ".state-test").exists())

    def test_target_inside_the_repository_does_not_matter_but_state_must_be_outside(self):
        with self.assertRaises(state.StateError):
            state._check_location(self.target, ROOT / "x")

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
            with self.assertRaisesRegex(state.StateLocked, "another install is running"):
                state.save(self.target, LAYOUT, {"second.md": entry()})
            with self.assertRaisesRegex(state.StateLocked, "another install is running"):
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
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(self.listing(), [self.file.name])

    def test_crash_during_write_keeps_old_file_and_leaves_no_temp(self):
        state.save(self.target, LAYOUT, {"old.md": entry()})
        old = self.file.read_bytes()
        with mock.patch.object(state.os, "fsync", side_effect=OSError("crash")):
            with self.assertRaises(state.StateError):
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

    def test_pending_only_entry_is_never_owned(self):
        e = {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2}
        for digest in (D2, D1, "c" * 64, None, "", b"x"):
            with self.subTest(digest=repr(digest)[:12]):
                self.assertFalse(state.accepts(e, digest))
                self.assertEqual(state.verdict({"new.md": e}, "new.md", digest), "unverified")

    def test_exact_repro_of_the_review_finding(self):
        entries = {"new.md": {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2}}
        self.assertEqual(state.verdict(entries, "new.md", D2), "unverified")

    def test_existing_owned_path_still_accepts_its_pending_digest(self):
        # The file was already ours (it has a recorded body digest); the pending bytes are the installer's own next
        # release content. This is the only case in which a pending digest is evidence.
        e = entry(pending_sha256=D2)
        self.assertTrue(state.accepts(e, D1))
        self.assertTrue(state.accepts(e, D2))
        self.assertEqual(state.verdict({"a.md": e}, "a.md", D2), "owned")


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
        for digest in (D2, D1, None):  # a crashed install leaves a NEW path unverified, whatever the bytes are
            self.assertFalse(state.accepts(got["n.md"], digest))
        self.assertEqual(state.settle(got, {"n.md"})["n.md"]["body_sha256"], D2)
        self.assertEqual(state.verdict(state.settle(got, {"n.md"}), "n.md", D2), "owned")  # only once written
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


CHILD = r"""
import os, signal, sys
sys.path.insert(0, sys.argv[1])
import state
target, step, tag = sys.argv[2], sys.argv[3], sys.argv[4]
entries = {"f-" + tag + ".md": {"kind": "file", "release": "1", "source": "public", "body_sha256": tag * 64}}
def die(*a, **k):
    os.kill(os.getpid(), signal.SIGKILL)
if step.startswith("kill-fsync-"):
    nth, calls, real_fsync = int(step.rsplit("-", 1)[1]), [0], state.os.fsync
    def counting(fd):
        calls[0] += 1
        if calls[0] == nth:
            die()
        return real_fsync(fd)
    state.os.fsync = counting
elif step == "kill-write":
    state.os.write = die
elif step == "kill-rename":
    state.os.rename = die
elif step == "kill-dirsync":
    state._fsync_dir = die
if step == "hold":
    with state.Store(target, "project"):
        print("held", flush=True)
        sys.stdin.readline()
    sys.exit(0)
if step == "race":
    ok = locked = 0
    for _ in range(40):
        try:
            state.save(target, "project", entries)
            ok += 1
        except state.StateLocked:
            locked += 1
    print(ok, locked)
    sys.exit(0)
try:
    state.save(target, "project", entries)
except state.StateLocked:
    sys.exit(3)
except state.StateError:
    sys.exit(4)
"""


def scripts_dir():
    return os.environ.get("DOIA_STATE_SCRIPTS") or str(ROOT / "scripts")


def child_args(target, step, tag):
    return [sys.executable, "-I", "-c", CHILD, scripts_dir(), str(target), step, tag]


class Errors(StateCase):
    """Every I/O failure surfaces as StateError; a raw OSError never escapes (callers catch one type)."""

    def saved(self):
        state.save(self.target, LAYOUT, {"old.md": entry()})
        return self.file.read_bytes()

    def assertTypedAndIntact(self, old, *, exc=state.StateError):
        self.assertEqual(self.file.read_bytes(), old)
        self.assertEqual(self.listing(), [self.file.name])  # no temp file left

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory modes")
    def test_unwritable_state_directory_on_save(self):
        old = self.saved()
        os.chmod(self.sdir, 0o500)  # passes the owner and mode checks, cannot take a new file
        self.addCleanup(os.chmod, self.sdir, 0o700)
        with self.assertRaises(state.StateError) as cm:
            state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertNotIsInstance(cm.exception, OSError)
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["old.md"])
        os.chmod(self.sdir, 0o700)
        self.assertTypedAndIntact(old)

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory modes")
    def test_unwritable_state_directory_on_first_save(self):
        self.mkdir_state(0o500)
        self.addCleanup(os.chmod, self.sdir, 0o700)
        with self.assertRaises(state.StateError):
            state.save(self.target, LAYOUT, {"new.md": entry()})

    def test_write_failures_each_step(self):
        old = self.saved()
        steps = [
            ("write", state.os, "write", OSError(28, "No space left on device")),
            ("fsync", state.os, "fsync", OSError(5, "Input/output error")),
            ("rename", state.os, "rename", OSError(18, "Invalid cross-device link")),
            ("fchmod", state.os, "fchmod", OSError(1, "Operation not permitted")),
        ]
        for name, owner, attr, err in steps:
            with self.subTest(name), mock.patch.object(owner, attr, side_effect=err):
                with self.assertRaises(state.StateError) as cm:
                    state.save(self.target, LAYOUT, {"new.md": entry()})
                self.assertNotIsInstance(cm.exception, OSError)
            self.assertTypedAndIntact(old)

    def test_temp_file_open_failure(self):
        old = self.saved()
        real_open = os.open

        def failing(path, flags, *a, **kw):
            if str(path).endswith(".tmp"):
                raise OSError(28, "No space left on device")
            return real_open(path, flags, *a, **kw)

        with mock.patch.object(state.os, "open", side_effect=failing):
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertTypedAndIntact(old)

    def test_read_failures(self):
        self.saved()
        for attr, err in (("read", OSError(5, "Input/output error")), ("fstat", OSError(5, "Input/output error"))):
            with self.subTest(attr), mock.patch.object(state.os, attr, side_effect=err):
                with self.assertRaises(state.StateError) as cm:
                    state.load(self.target, LAYOUT)
                self.assertNotIsInstance(cm.exception, OSError)

    def test_first_open_failure(self):
        with mock.patch.object(state.os, "open", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(state.StateError):
                state.load(self.target, LAYOUT)
            with self.assertRaises(state.StateError):
                state.save(self.target, LAYOUT, {})

    def test_messages_carry_only_escaped_text(self):
        self.saved()
        with mock.patch.object(state.os, "write", side_effect=OSError(5, "bad\x1b[31m\nthing")):
            with self.assertRaises(state.StateError) as cm:
                state.save(self.target, LAYOUT, {"new.md": entry()})
        self.assertTrue(all(" " <= c <= "~" for c in str(cm.exception)), repr(str(cm.exception)))


class Sweep(StateCase):
    def tmp_name(self, stem=None, uid="0123456789abcdef0123456789abcdef"):
        return f".{stem or self.stem}.{uid}.tmp"

    def leftover(self, name, age=60, data=b"x"):
        self.mkdir_state()
        path = self.sdir / name
        path.write_bytes(data)
        os.chmod(path, 0o600)
        t = path.stat().st_mtime - age
        os.utime(path, (t, t))
        return path

    def test_old_leftover_temp_files_are_removed_by_the_next_save(self):
        gone = [self.leftover(self.tmp_name(uid=f"{i:032x}")) for i in range(3)]
        state.save(self.target, LAYOUT, {"a.md": entry()})
        for path in gone:
            self.assertFalse(path.exists(), path)
        self.assertEqual(self.listing(), [self.file.name])

    def test_a_fresh_temp_file_is_left_alone(self):
        fresh = self.leftover(self.tmp_name(), age=0)
        state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertTrue(fresh.exists())

    def test_nothing_else_is_ever_touched(self):
        self.mkdir_state()
        outside = self.tmp / "outside.txt"
        outside.write_text("keep")
        os.utime(outside, (outside.stat().st_mtime - 60,) * 2)  # old enough to be reaped if it were followed
        link = self.sdir / self.tmp_name(uid="1" * 32)
        os.symlink(outside, link)
        adir = self.sdir / self.tmp_name(uid="2" * 32)
        adir.mkdir()
        (adir / "inner").write_text("keep")
        keep = [
            self.leftover(self.tmp_name(stem="f" * 32)),  # another target's temp file
            self.leftover(self.tmp_name(uid="3" * 31)),  # wrong uid length
            self.leftover(self.tmp_name(uid="G" * 32)),  # not hex
            self.leftover("notes.txt"),
            self.leftover(self.tmp_name() + ".bak"),
            self.leftover(self.tmp_name()[1:]),  # no leading dot
        ]
        fifo = self.sdir / self.tmp_name(uid="4" * 32)
        os.mkfifo(fifo, 0o600)
        for odd in (link, adir, fifo):  # old enough to be reaped if their type were not checked
            old = os.lstat(odd).st_mtime - 60
            os.utime(odd, (old, old), follow_symlinks=False)
        state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertTrue(link.is_symlink() and outside.read_text() == "keep")
        self.assertTrue(adir.is_dir() and (adir / "inner").exists())
        self.assertTrue(fifo.exists())
        for path in keep:
            self.assertTrue(path.exists(), path)

    def test_a_temp_file_owned_by_someone_else_is_left_alone(self):
        path = self.leftover(self.tmp_name())
        real_euid = os.geteuid()
        with mock.patch.object(state, "_euid", return_value=real_euid + 1):
            # only the sweep sees the changed identity: call it directly on a directory descriptor
            dfd = os.open(self.sdir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                state._sweep(dfd, self.stem)
            finally:
                os.close(dfd)
        self.assertTrue(path.exists())

    def test_sweep_failure_does_not_fail_the_save(self):
        path = self.leftover(self.tmp_name())
        real_unlink = os.unlink

        def failing(name, *a, **kw):
            if str(name).endswith(".tmp") and str(name).startswith("."):
                raise OSError(1, "nope")
            return real_unlink(name, *a, **kw)

        with mock.patch.object(state.os, "unlink", side_effect=failing):
            state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertTrue(path.exists())
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["a.md"])


class ClosedStore(StateCase):
    def test_every_method_refuses_after_close(self):
        store = state.Store(self.target, LAYOUT)
        store.save({"a.md": entry()})
        store.close()
        store.close()  # idempotent
        with self.assertRaisesRegex(state.StateError, "closed"):
            store.load()
        with self.assertRaisesRegex(state.StateError, "closed"):
            store.save({})
        self.assertEqual(list(state.load(self.target, LAYOUT)), ["a.md"])

    def test_context_manager_closes(self):
        with state.Store(self.target, LAYOUT) as store:
            pass
        with self.assertRaises(state.StateError):
            store.load()

    def test_no_resolution_against_the_current_directory(self):
        store = state.Store(self.target, LAYOUT)
        store.close()
        stray = Path.cwd() / (self.stem + ".json")
        self.assertFalse(stray.exists())
        with self.assertRaises(state.StateError):
            store.load()


class Containment(StateCase):
    def test_state_directory_that_contains_the_target(self):
        for d in (self.tmp, self.tmp.parent):
            with self.subTest(d=d.name), mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(d)}):
                with self.assertRaisesRegex(state.StateError, "contains the target"):
                    state.save(self.target, LAYOUT, {})
                with self.assertRaisesRegex(state.StateError, "contains the target"):
                    state.load(self.target, LAYOUT)

    def test_state_directory_that_contains_this_repository(self):
        for d in (state._REPO_ROOT.parent,):
            with self.subTest(d=d.name), mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(d)}):
                with self.assertRaisesRegex(state.StateError, "contains this repository"):
                    state.load(self.target, LAYOUT)

    def test_containing_by_symlink_alias(self):
        alias = self.tmp.parent / ("doia-alias-" + self.tmp.name)
        os.symlink(self.tmp, alias)
        self.addCleanup(os.unlink, alias)
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(alias)}):
            with self.assertRaisesRegex(state.StateError, "contains the target"):
                state.load(self.target, LAYOUT)

    def test_siblings_are_fine(self):
        other = self.tmp / "sibling-state"
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": str(other)}):
            state.save(self.target, LAYOUT, {})
            self.assertEqual(state.load(self.target, LAYOUT), {})


class BytesSub(bytes):
    pass


class ByteArraySub(bytearray):
    pass


class IntSub(int):
    pass


class Spoof:
    """isinstance(Spoof(), str) is True through __class__, type(Spoof()) is Spoof."""

    @property
    def __class__(self):
        return str


class ExactTypes(unittest.TestCase):
    """_is_one_of and its users test the exact type: subclasses, spoofed __class__ and bool-for-int are all refused."""

    def test_is_one_of(self):
        cases = [
            (BytesSub(b"x"), (bytes,), False),
            (ByteArraySub(b"x"), (bytearray,), False),
            (StrSub("x"), (str,), False),
            (IntSub(1), (int,), False),
            (DictSub(), (dict,), False),
            (ListSub(), (list,), False),
            (SetSub(), (set,), False),
            (Spoof(), (str,), False),
            (True, (int,), False),
            (True, (bool,), True),
            (5, (int,), True),
            (b"", (), False),
            (b"", (bytes, bytearray), True),
            (bytearray(), (bytes, bytearray), True),
            ({}, (dict,), True),
        ]
        for obj, types, expected in cases:
            with self.subTest(type(obj).__name__, types=[t.__name__ for t in types]):
                self.assertIs(state._is_one_of(obj, types), expected)
        self.assertIs(state._is_one_of(memoryview(b"x"), (memoryview,)), True)


class Api(StateCase):
    """The helpers the installer needs, so it never re-implements hashing, probing or KeyError handling."""

    def test_body_sha256(self):
        import hashlib

        self.assertEqual(state.body_sha256(b"abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(state.body_sha256(bytearray(b"abc")), hashlib.sha256(b"abc").hexdigest())
        import array

        for bad in ("abc", None, 5, ["a"], array.array("B", b"abc")):  # only bytes-like types, not any buffer
            with self.subTest(bad=repr(bad)), self.assertRaises(state.StateError):
                state.body_sha256(bad)
        self.assertRegex(state.body_sha256(b""), r"^[0-9a-f]{64}$")

    def test_body_sha256_rejects_bytes_subclasses_and_spoofs(self):
        for bad in (BytesSub(b"abc"), ByteArraySub(b"abc"), Spoof(), True, IntSub(3), StrSub("abc")):
            with self.subTest(type(bad).__name__), self.assertRaises(state.StateError):
                state.body_sha256(bad)
        self.assertEqual(state.body_sha256(b"abc"), state.body_sha256(bytearray(b"abc")))
        self.assertEqual(state.body_sha256(b"abc"), state.body_sha256(memoryview(b"abc")))

    def test_state_path(self):
        self.assertEqual(state.state_path(self.target, LAYOUT), self.file)
        self.assertFalse(self.sdir.exists())  # pure: nothing created
        self.assertNotEqual(state.state_path(self.target, "user"), self.file)

    def test_error_classes(self):
        self.assertTrue(issubclass(state.StateLocked, state.StateError))
        self.assertTrue(issubclass(state.StateCorrupt, state.StateError))
        self.assertFalse(issubclass(state.StateLocked, state.StateCorrupt))
        self.assertFalse(issubclass(state.StateCorrupt, state.StateLocked))

    def test_locked_is_distinguishable_from_corrupt(self):
        with state.Store(self.target, LAYOUT):
            with self.assertRaises(state.StateLocked):
                state.save(self.target, LAYOUT, {})
        self.plant(b"not json")
        with self.assertRaises(state.StateCorrupt):
            state.load(self.target, LAYOUT)
        with self.assertRaises(state.StateCorrupt):
            state.save(self.target, LAYOUT, {})
        for raw, mode in (
            (json.dumps(self.doc()).encode(), 0o644),
            (b"", 0o600),
            (b" " * (state.MAX_BYTES + 1), 0o600),
        ):
            self.plant(raw, mode)
            with self.subTest(mode=oct(mode), size=len(raw)), self.assertRaises(state.StateCorrupt):
                state.load(self.target, LAYOUT)
        self.plant_doc(target="/elsewhere")
        with self.assertRaises(state.StateCorrupt):
            state.load(self.target, LAYOUT)

    def test_environment_and_location_errors_are_neither(self):
        with mock.patch.dict(os.environ, {"DOIA_STATE_DIR": "relative"}):
            with self.assertRaises(state.StateError) as cm:
                state.load(self.target, LAYOUT)
        self.assertNotIsInstance(cm.exception, (state.StateLocked, state.StateCorrupt))
        self.mkdir_state(0o775)
        with self.assertRaises(state.StateError) as cm:
            state.load(self.target, LAYOUT)
        self.assertNotIsInstance(cm.exception, (state.StateLocked, state.StateCorrupt))

    def test_lock_held_probe_does_not_create_anything(self):
        self.assertFalse(state.lock_held(self.target, LAYOUT))
        self.assertFalse(self.sdir.exists())
        self.mkdir_state()
        self.assertFalse(state.lock_held(self.target, LAYOUT))
        self.assertEqual(os.listdir(self.sdir), [])  # no lock file created either

    def test_lock_held_probe_sees_a_holder_and_lets_go(self):
        with state.Store(self.target, LAYOUT):
            self.assertTrue(state.lock_held(self.target, LAYOUT))
            self.assertTrue(state.lock_held(self.target, LAYOUT))  # probing twice changes nothing
        self.assertFalse(state.lock_held(self.target, LAYOUT))
        state.save(self.target, LAYOUT, {})  # the probe left no lock behind

    def test_lock_held_other_targets_are_independent(self):
        other = self.tmp / "other"
        other.mkdir()
        with state.Store(other, LAYOUT):
            self.assertFalse(state.lock_held(self.target, LAYOUT))
            self.assertTrue(state.lock_held(other, LAYOUT))

    def test_lock_held_refuses_a_bad_directory(self):
        self.mkdir_state(0o775)
        with self.assertRaises(state.StateError):
            state.lock_held(self.target, LAYOUT)

    def test_lock_held_does_not_follow_a_symlinked_lock_file(self):
        self.mkdir_state()
        decoy = self.tmp / "decoy"
        decoy.write_text("")
        os.symlink(decoy, self.sdir / (self.stem + ".lock"))
        with self.assertRaises(state.StateError):
            state.lock_held(self.target, LAYOUT)

    def test_with_entry_and_without_entry_copy_and_validate(self):
        base = {"a.md": entry()}
        added = state.with_entry(base, "b/c.md", entry(kind="lead"))
        self.assertEqual(sorted(added), ["a.md", "b/c.md"])
        self.assertEqual(sorted(base), ["a.md"])  # the input is untouched
        added["a.md"]["release"] = "9"
        self.assertEqual(base["a.md"]["release"], "0.4.0")  # and not shared
        replaced = state.with_entry(base, "a.md", entry(body_sha256=D2))
        self.assertEqual(replaced["a.md"]["body_sha256"], D2)
        for rel, e in (("../x", entry()), ("a.md", {"kind": "file"}), ("A.MD", entry()), ("x.md", entry(extra=1))):
            with self.subTest(rel=rel), self.assertRaises(state.StateError):
                state.with_entry(base, rel, e)
        dropped = state.without_entry(added, "b/c.md")
        self.assertEqual(sorted(dropped), ["a.md"])
        self.assertEqual(sorted(added), ["a.md", "b/c.md"])
        with self.assertRaisesRegex(state.StateError, "no entry"):
            state.without_entry(base, "zzz.md")

    def test_verdict(self):
        e = {
            "a.md": entry(pending_sha256=D2),
            "n.md": {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2},
        }
        self.assertEqual(state.verdict(e, "a.md", D1), "owned")
        self.assertEqual(state.verdict(e, "a.md", D2), "owned")
        self.assertEqual(state.verdict(e, "n.md", D2), "unverified")  # pending-only: never owned
        self.assertEqual(state.verdict(e, "n.md", D1), "unverified")
        self.assertEqual(state.verdict(e, "a.md", "c" * 64), "unverified")
        self.assertEqual(state.verdict(e, "missing.md", D1), "unverified")  # no KeyError by construction
        self.assertEqual(state.verdict(None, "a.md", D1), "unverified")  # a missing state file
        self.assertEqual(state.verdict({}, "a.md", D1), "unverified")
        for bad in (None, "", b"x" * 64, 7, "A" * 64, "a" * 63):
            with self.subTest(digest=repr(bad)[:12]):
                self.assertEqual(state.verdict(e, "a.md", bad), "unverified")
        for rel in (None, 5, b"a.md", ["a.md"]):
            with self.subTest(rel=repr(rel)):
                self.assertEqual(state.verdict(e, rel, D1), "unverified")

    def test_load_or_empty(self):
        self.assertEqual(state.load_or_empty(self.target, LAYOUT), {})
        self.assertFalse(self.sdir.exists())
        state.save(self.target, LAYOUT, {"a.md": entry()})
        self.assertEqual(list(state.load_or_empty(self.target, LAYOUT)), ["a.md"])
        self.plant(b"not json")
        with self.assertRaises(state.StateCorrupt):
            state.load_or_empty(self.target, LAYOUT)


class StrSub(str):
    pass


class EvilEq(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class DictSub(dict):
    pass


class ListSub(list):
    pass


class SetSub(set):
    pass


class HostileDict(dict):
    def _boom(self, *a, **k):
        raise RuntimeError("hostile dict touched")

    get = __getitem__ = __iter__ = __contains__ = items = keys = values = __len__ = copy = _boom


class HostileStr:
    def __str__(self):
        raise RuntimeError("hostile __str__")

    __repr__ = __str__


def other_user_inside(*names):
    """A _euid replacement that reports a different user only when called from one of the named functions."""
    real = os.geteuid()

    def euid():
        f = sys._getframe(1)
        while f:
            if f.f_code.co_name in names:
                return real + 1
            f = f.f_back
        return real

    return euid


class Hostile(StateCase):
    """Hostile input never leaks a raw exception: mutators raise StateError, predicates fail closed."""

    def test_accepts_and_verdict_are_total(self):
        entries_variants = [
            None,
            5,
            "x",
            [],
            b"",
            HostileDict(),
            DictSub({"a.md": entry()}),
            {"a.md": DictSub(entry())},
            {"a.md": None},
            {"a.md": []},
            {"a.md": HostileDict()},
            {"a.md": {"body_sha256": EvilEq(D1)}},
        ]
        rels = ["a.md", StrSub("a.md"), None, 5, b"a.md", ["a.md"], HostileStr()]
        digests = [D1, EvilEq(D1), StrSub(D1), None, 5, b"x", [D1], HostileStr()]
        for entries in entries_variants:
            for rel in rels:
                for digest in digests:
                    with self.subTest(
                        entries=type(entries).__name__, rel=type(rel).__name__, digest=type(digest).__name__
                    ):
                        self.assertEqual(state.verdict(entries, rel, digest), "unverified")
        self.assertEqual(state.verdict({"a.md": entry()}, "a.md", D1), "owned")  # the positive control
        for entry_value in (None, 5, "x", [], b"", HostileDict(), DictSub(entry()), {"body_sha256": EvilEq(D1)}):
            for digest in digests:
                with self.subTest(entry=type(entry_value).__name__, digest=type(digest).__name__):
                    self.assertIs(state.accepts(entry_value, digest), False)

    def test_accepts_does_not_trust_a_digest_object_with_its_own_equality(self):
        self.assertIs(state.accepts(entry(), EvilEq("x")), False)
        self.assertIs(state.accepts(entry(), StrSub(D1)), False)
        self.assertEqual(state.verdict({"a.md": entry()}, "a.md", EvilEq(D1)), "unverified")

    def test_verdict_does_not_touch_a_dict_subclass(self):
        self.assertEqual(state.verdict(HostileDict(), "a.md", D1), "unverified")
        self.assertEqual(state.verdict(DictSub({"a.md": entry()}), "a.md", D1), "unverified")

    def test_mutators_raise_state_error_on_hostile_arguments(self):
        calls = {
            "with_pending(entries=None)": lambda: state.with_pending(None, {}),
            "with_pending(list)": lambda: state.with_pending({}, []),
            "with_pending(None)": lambda: state.with_pending({}, None),
            "with_pending(DictSub)": lambda: state.with_pending({}, DictSub()),
            "with_pending(Hostile)": lambda: state.with_pending({}, HostileDict()),
            "with_pending(StrSub key)": lambda: state.with_pending({"a.md": entry()}, {StrSub("a.md"): D2}),
            "with_pending(new=list)": lambda: state.with_pending({}, {}, new=[]),
            "with_pending(new=DictSub)": lambda: state.with_pending({}, {}, new=DictSub()),
            "with_pending(new=Hostile)": lambda: state.with_pending({}, {}, new=HostileDict()),
            "with_pending(new spec Hostile)": lambda: state.with_pending({}, {}, new={"n.md": HostileDict()}),
            "with_pending(new StrSub key)": lambda: state.with_pending(
                {}, {}, new={StrSub("n"): dict(NEW, pending_sha256=D1)}
            ),
            "with_pending(Hostile entries)": lambda: state.with_pending(HostileDict(), {}),
            "settle(list in list)": lambda: state.settle({}, [[]]),
            "settle(None)": lambda: state.settle({}, None),
            "settle(str)": lambda: state.settle({}, "abc"),
            "settle(int)": lambda: state.settle({}, 5),
            "settle(bytes)": lambda: state.settle({}, b"abc"),
            "settle(dict)": lambda: state.settle({}, {"a.md": 1}),
            "settle(generator)": lambda: state.settle({}, (x for x in ())),
            "settle(StrSub member)": lambda: state.settle({"a.md": entry()}, {StrSub("a.md")}),
            "settle(None member)": lambda: state.settle({}, {None}),
            "settle(DictSub entries)": lambda: state.settle(DictSub(), set()),
            "settle(Hostile entries)": lambda: state.settle(HostileDict(), set()),
            "with_entry(StrSub rel)": lambda: state.with_entry({}, StrSub("a.md"), entry()),
            "with_entry(Hostile entries)": lambda: state.with_entry(HostileDict(), "a.md", entry()),
            "with_entry(DictSub entry)": lambda: state.with_entry({}, "a.md", DictSub(entry())),
            "with_entry(Hostile entry)": lambda: state.with_entry({}, "a.md", HostileDict()),
            "with_entry(None entry)": lambda: state.with_entry({}, "a.md", None),
            "without_entry(Hostile)": lambda: state.without_entry(HostileDict(), "a.md"),
            "without_entry(None rel)": lambda: state.without_entry({}, None),
            "without_entry(HostileStr rel)": lambda: state.without_entry({}, HostileStr()),
            "check_entries(DictSub)": lambda: state.check_entries(DictSub()),
            "check_entries(StrSub key)": lambda: state.check_entries({StrSub("a.md"): entry()}),
            "check_rel_path(HostileStr)": lambda: state.check_rel_path(HostileStr()),
            "body_sha256(HostileStr)": lambda: state.body_sha256(HostileStr()),
        }
        for name, call in calls.items():
            with self.subTest(name), self.assertRaises(state.StateError):
                call()

    def test_settle_rejects_hostile_containers_but_accepts_the_documented_ones(self):
        base = state.with_pending({"a.md": entry()}, {"a.md": D2})
        for ok in (set(), frozenset({"a.md"}), ["a.md"], ("a.md",), {"a.md"}):
            with self.subTest(type(ok).__name__):
                self.assertEqual(state.settle(base, ok)["a.md"]["body_sha256"], D2 if ok else D1)

    def test_settle_rejects_container_subclasses(self):
        for container in (SetSub(), ListSub(), type("FS", (frozenset,), {})(), type("T", (tuple,), {})(), DictSub()):
            with self.subTest(type(container).__name__), self.assertRaises(state.StateError):
                state.settle({}, container)

    def test_nul_and_non_text_targets_raise_state_error_not_value_error(self):
        for target in ("x\0y", "/tmp/a\0", None, 5, b"/tmp", ["/tmp"], StrSub("/tmp"), HostileStr()):
            with self.subTest(target=type(target).__name__):
                for call in (
                    lambda t=target: state.state_key(t, LAYOUT),
                    lambda t=target: state.state_path(t, LAYOUT),
                    lambda t=target: state.load(t, LAYOUT),
                    lambda t=target: state.load_or_empty(t, LAYOUT),
                    lambda t=target: state.save(t, LAYOUT, {}),
                    lambda t=target: state.Store(t, LAYOUT),
                    lambda t=target: state.lock_held(t, LAYOUT),
                    lambda t=target: state._check_location(t),
                ):
                    with self.assertRaises(state.StateError):
                        call()
        self.assertFalse(self.sdir.exists())

    def test_a_path_like_target_is_fine(self):
        self.assertEqual(state.state_key(self.target, LAYOUT)[0], os.path.realpath(self.target))

    def test_layout_must_be_a_plain_str(self):
        for layout in (StrSub(LAYOUT), None, 5, b"project", HostileStr()):
            with self.subTest(type(layout).__name__), self.assertRaises(state.StateError):
                state.state_key(self.target, layout)


def _raiser(name):
    def raiser(self, *args, **kwargs):
        raise RuntimeError(f"hostile {name} touched")

    return raiser


def _boom(name):
    """A class whose one named dunder raises; the other behavior is plain (and hashable, so it can be a dict key)."""
    return type("Boom" + name.strip("_"), (), {name: _raiser(name), "__hash__": object.__hash__})


class BoomHash:
    """Unhashable and its hash raises: can only ever be a value."""

    __hash__ = _raiser("__hash__")


class BoomAll:
    __eq__ = __ne__ = __len__ = __iter__ = __getitem__ = __bool__ = __str__ = __repr__ = __contains__ = _raiser("any")
    __lt__ = __le__ = __gt__ = __ge__ = __index__ = __int__ = __float__ = __bytes__ = __fspath__ = _raiser("any")
    __hash__ = object.__hash__


FIRED = []  # every foreign dunder of the Recording objects appends here; it must stay empty


def _recording(name, result):
    def method(self, *args):
        FIRED.append(name)
        return result

    return method


class Recording:
    """Inert, but notes every conversion or path hook that is run on it."""

    __str__ = _recording("__str__", "x")
    __repr__ = _recording("__repr__", "x")
    __format__ = _recording("__format__", "x")
    __fspath__ = _recording("__fspath__", "/tmp")
    __bytes__ = _recording("__bytes__", b"x")
    __index__ = _recording("__index__", 0)
    __int__ = _recording("__int__", 0)


def _escaping(exc_type):
    def raiser(self, *args):
        raise exc_type("escaped")

    return type(
        "Escaping" + exc_type.__name__,
        (),
        {n: raiser for n in ("__str__", "__repr__", "__format__", "__fspath__", "__bytes__", "__int__", "__index__")},
    )


EscapingSystemExit = _escaping(SystemExit)
EscapingKeyboardInterrupt = _escaping(KeyboardInterrupt)

_DUNDERS = ("__eq__", "__ne__", "__len__", "__iter__", "__getitem__", "__bool__", "__str__", "__repr__", "__contains__")
KEY_OK_HOSTILE = [_boom(n)() for n in _DUNDERS] + [
    BoomAll(),
    Recording(),
    EscapingSystemExit(),
    EscapingKeyboardInterrupt(),
]
VALUE_ONLY_HOSTILE = [BoomHash()]
# (object, may it be a dict key?) -- never use `in` on these lists: that would call the hostile __eq__
ALL_HOSTILE = [(h, True) for h in KEY_OK_HOSTILE] + [(h, False) for h in VALUE_ONLY_HOSTILE]


class HostileEverywhere(StateCase):
    """No foreign __eq__, __hash__, __len__, __iter__, __getitem__, __bool__, __str__ or __repr__ is ever invoked:
    every value is type-checked (`type(x) is str`) before any comparison, membership, regex or hashing."""

    SPEC = {"kind": "file", "release": "1", "source": "public", "pending_sha256": D2}

    def mutator_calls(self, h, key_ok):
        e = entry
        spec = self.SPEC
        calls = {}
        for field in ("kind", "source", "release", "body_sha256", "pending_sha256"):
            bad = {"a.md": e(**{field: h})}
            calls[f"check_entries({field})"] = lambda bad=bad: state.check_entries(bad)
            calls[f"with_entry({field})"] = lambda bad=bad: state.with_entry({}, "b.md", bad["a.md"])
            calls[f"with_pending(entries {field})"] = lambda bad=bad: state.with_pending(bad, {})
            calls[f"settle(entries {field})"] = lambda bad=bad: state.settle(bad, set())
            calls[f"without_entry(entries {field})"] = lambda bad=bad: state.without_entry(bad, "a.md")
            calls[f"_serialize({field})"] = lambda bad=bad: state._serialize("/x", LAYOUT, bad)
            calls[f"save({field})"] = lambda bad=bad: state.save(self.target, LAYOUT, bad)
            calls[f"new spec {field}"] = lambda field=field: state.with_pending(
                {}, {}, new={"n.md": {**spec, field: h}}
            )
        calls["check_entries(entry value)"] = lambda: state.check_entries({"a.md": h})
        calls["check_entries(container)"] = lambda: state.check_entries(h)
        calls["with_entry(entries)"] = lambda: state.with_entry(h, "a.md", e())
        calls["with_entry(rel)"] = lambda: state.with_entry({}, h, e())
        calls["with_entry(entry)"] = lambda: state.with_entry({}, "a.md", h)
        calls["without_entry(entries)"] = lambda: state.without_entry(h, "a.md")
        calls["without_entry(rel)"] = lambda: state.without_entry({}, h)
        calls["with_pending(entries)"] = lambda: state.with_pending(h, {})
        calls["with_pending(pending)"] = lambda: state.with_pending({"a.md": e()}, h)
        calls["with_pending(pending value)"] = lambda: state.with_pending({"a.md": e()}, {"a.md": h})
        calls["with_pending(new)"] = lambda: state.with_pending({}, {}, new=h)
        calls["with_pending(new value)"] = lambda: state.with_pending({}, {}, new={"n.md": h})
        calls["settle(entries)"] = lambda: state.settle(h, set())
        calls["settle(written)"] = lambda: state.settle({}, h)
        calls["settle(list member)"] = lambda: state.settle({"a.md": e()}, [h])
        calls["settle(tuple member)"] = lambda: state.settle({"a.md": e()}, (h,))
        calls["check_rel_path"] = lambda: state.check_rel_path(h)
        calls["body_sha256"] = lambda: state.body_sha256(h)
        calls["state_key(target)"] = lambda: state.state_key(h, LAYOUT)
        calls["state_key(layout)"] = lambda: state.state_key(self.target, h)
        calls["state_path"] = lambda: state.state_path(h, LAYOUT)
        calls["load"] = lambda: state.load(h, LAYOUT)
        calls["save(target)"] = lambda: state.save(h, LAYOUT, {})
        calls["save(entries)"] = lambda: state.save(self.target, LAYOUT, h)
        calls["lock_held"] = lambda: state.lock_held(h, LAYOUT)
        calls["Store"] = lambda: state.Store(h, LAYOUT)
        calls["_check_location"] = lambda: state._check_location(h)
        if key_ok:
            calls["check_entries(rel key)"] = lambda: state.check_entries({h: e()})
            calls["check_entries(entry key)"] = lambda: state.check_entries({"a.md": {**e(), h: 1}})
            calls["with_pending(pending key)"] = lambda: state.with_pending({"a.md": e()}, {h: D1})
            calls["with_pending(new key)"] = lambda: state.with_pending({}, {}, new={h: dict(spec)})
            calls["with_pending(new spec key)"] = lambda: state.with_pending({}, {}, new={"n.md": {**spec, h: 1}})
            calls["settle(set member)"] = lambda: state.settle({"a.md": e()}, {h})
            calls["settle(frozenset member)"] = lambda: state.settle({"a.md": e()}, frozenset({h}))
            calls["without_entry(entries key)"] = lambda: state.without_entry({h: e()}, "a.md")
            calls["settle(entries key)"] = lambda: state.settle({h: e()}, set())
        return calls

    def test_every_mutator_gives_state_error_and_never_runs_hostile_code(self):
        for h, key_ok in ALL_HOSTILE:
            for name, call in self.mutator_calls(h, key_ok).items():
                with self.subTest(hostile=type(h).__name__, call=name), self.assertRaises(state.StateError):
                    call()

    def test_accepts_and_verdict_stay_total_and_fail_closed(self):
        for h, key_ok in ALL_HOSTILE:
            who = type(h).__name__
            with self.subTest(hostile=who):
                self.assertIs(state.accepts(h, D1), False)
                self.assertIs(state.accepts(entry(), h), False)
                self.assertIs(state.accepts(entry(body_sha256=h), D1), False)
                self.assertIs(state.accepts(entry(pending_sha256=h), D2), False)
                self.assertIs(state.accepts({"body_sha256": D1, h: 1} if key_ok else {}, D2), False)
                self.assertEqual(state.verdict(h, "a.md", D1), "unverified")
                self.assertEqual(state.verdict({"a.md": entry()}, h, D1), "unverified")
                self.assertEqual(state.verdict({"a.md": h}, "a.md", D1), "unverified")
                self.assertEqual(state.verdict({"a.md": entry()}, "a.md", h), "unverified")
                self.assertEqual(state.verdict({"a.md": entry(body_sha256=h)}, "a.md", D1), "unverified")
                self.assertEqual(state.verdict({"a.md": entry(pending_sha256=h)}, "a.md", D2), "unverified")
        self.assertEqual(state.verdict({"a.md": entry()}, "a.md", D1), "owned")  # the positive control

    def test_foreign_str_repr_format_and_fspath_never_run(self):
        """_shown renders only an exact str or int; nothing else is converted, formatted or turned into a path."""
        del FIRED[:]
        self.addCleanup(FIRED.clear)
        for h in (Recording(), EscapingSystemExit(), EscapingKeyboardInterrupt()):
            for name, call in self.mutator_calls(h, True).items():
                with self.subTest(hostile=type(h).__name__, call=name), self.assertRaises(state.StateError):
                    call()
            self.assertEqual(state.verdict({"a.md": entry()}, h, D1), "unverified")
            self.assertEqual(state.verdict({"a.md": entry()}, "a.md", h), "unverified")
            self.assertIs(state.accepts(entry(), h), False)
        self.assertEqual(FIRED, [])  # the recorder never fired

    def test_the_review_repros(self):
        class X:
            def __str__(self):
                raise SystemExit("x")

        for call in (
            lambda: state.state_key("/tmp", X()),
            lambda: state.without_entry({}, X()),
            lambda: state.settle({}, [X()]),
            lambda: state.with_pending({"a.md": entry()}, {X(): D2}),
            lambda: state.with_pending({}, {}, new={X(): dict(self.SPEC)}),
            lambda: state.save("/tmp", X(), {}),
        ):
            with self.assertRaises(state.StateError):
                call()

    def test_oserr_renders_only_an_exact_str_strerror(self):
        class Loud(str):
            def __str__(self):
                FIRED.append("str")
                raise SystemExit("escaped")

            __repr__ = __format__ = __str__

        self.addCleanup(FIRED.clear)
        del FIRED[:]
        self.assertEqual(state._oserr(OSError(5, "real\x1b text")), "real? text")
        for foreign in (Loud("secret"), None, 5, b"bytes", ["a"], Recording(), EscapingSystemExit()):
            exc = OSError(5, "x")
            exc.strerror = foreign
            with self.subTest(type(foreign).__name__):
                self.assertEqual(state._oserr(exc), "OSError")
        exc = PermissionError(13, "x")
        exc.strerror = None
        self.assertEqual(state._oserr(exc), "PermissionError")
        self.assertEqual(FIRED, [])

    def test_shown_renders_only_exact_str_and_int(self):
        self.assertEqual(state._shown("a\x1b[0mb"), "a?[0mb")
        self.assertEqual(state._shown(5), "5")
        self.assertEqual(state._shown("x" * 100, 10), "x" * 10 + "...")
        for foreign in (Recording(), StrSub("secret"), True, 1.5, b"bytes", None, ["a"], {"a": 1}, object()):
            with self.subTest(type(foreign).__name__):
                self.assertEqual(state._shown(foreign), "?")
        self.assertEqual(FIRED, [])

    def test_only_exact_path_types_are_accepted_as_targets(self):
        class OnlyFspath:
            def __fspath__(self):
                FIRED.append("fspath")
                return str(self_target)

        self_target = self.target
        self.addCleanup(FIRED.clear)
        del FIRED[:]
        for bad in (OnlyFspath(), Recording(), EscapingSystemExit(), StrSub(str(self.target))):
            with self.subTest(type(bad).__name__), self.assertRaises(state.StateError):
                state.state_key(bad, LAYOUT)
        self.assertEqual(FIRED, [])  # a foreign __fspath__ is never run
        subclass = type("MyPath", (type(self.target),), {})
        with self.assertRaises(state.StateError):
            state.state_key(subclass(self.target), LAYOUT)
        import pathlib

        for good in (str(self.target), self.target, pathlib.PurePosixPath(self.target)):
            self.assertEqual(state.state_key(good, LAYOUT)[0], os.path.realpath(self.target))

    def test_membership_is_never_tested_before_the_type(self):
        """The exact review repro: a kind whose __eq__ raises."""

        class Boom:
            def __eq__(self, other):
                raise RuntimeError("eq")

            __hash__ = object.__hash__

        e = {"kind": Boom(), "release": "1", "source": "public", "body_sha256": "a" * 64}
        for call in (
            lambda: state.check_entries({"a.md": e}),
            lambda: state.with_pending({"a.md": e}, {}),
            lambda: state.settle({"a.md": e}, set()),
            lambda: state.with_entry({}, "a.md", e),
            lambda: state.without_entry({"a.md": e}, "a.md"),
            lambda: state._serialize("/x", LAYOUT, {"a.md": e}),
            lambda: state.save(self.target, LAYOUT, {"a.md": e}),
        ):
            with self.assertRaises(state.StateError):
                call()
        e["kind"], e["source"] = "file", Boom()
        with self.assertRaises(state.StateError):
            state.check_entries({"a.md": e})

    def test_hash_colliding_keys_are_never_compared(self):
        """A key hashing like a real one would make lookups call its __eq__; the key type is checked first."""

        class Collide:
            armed = False

            def __init__(self, word):
                self.word = word

            def __hash__(self):
                return hash(self.word)

            def __eq__(self, other):
                if Collide.armed:
                    raise RuntimeError("hostile __eq__ touched")
                return self is other

        self.addCleanup(setattr, Collide, "armed", False)
        entry_key = Collide("kind")
        e = {**entry(), entry_key: 1}
        spec_key = Collide("kind")
        spec = {**HostileEverywhere.SPEC, spec_key: 1}
        rel_key = Collide("a.md")
        pending_key = Collide("a.md")
        written_key = Collide("a.md")
        Collide.armed = True
        calls = [
            lambda: state.check_entries({"a.md": e}),
            lambda: state.with_pending({}, {}, new={"n.md": spec}),
            lambda: state.check_entries({rel_key: entry()}),
            lambda: state.with_pending({"a.md": entry()}, {pending_key: D2}),
            lambda: state.settle({"a.md": entry()}, {written_key}),
            lambda: state.without_entry({"a.md": entry()}, written_key),
        ]
        for n, call in enumerate(calls):
            with self.subTest(n), self.assertRaises(state.StateError):
                call()
        self.assertEqual(
            state.verdict({"a.md": e}, "a.md", D1), "owned"
        )  # accepts reads only exact keys of a plain dict

    def test_body_sha256_never_leaks_a_buffer_error(self):
        released = memoryview(bytearray(b"abc"))
        released.release()
        strided = memoryview(bytes(range(16)))[::2]
        multi = memoryview(bytearray(8)).cast("B", (2, 4))
        for name, data in (("released", released), ("strided", strided)):
            with self.subTest(name), self.assertRaises(state.StateError):
                state.body_sha256(data)
        self.assertRegex(state.body_sha256(multi), r"^[0-9a-f]{64}$")  # C-contiguous: hashable, or StateError
        self.assertRegex(state.body_sha256(bytes(8 * 1024 * 1024)), r"^[0-9a-f]{64}$")  # large input
        # (a bytearray mutated by another thread while it is hashed is out of scope)


class LockFile(StateCase):
    """The lock file gets the state file's discipline: owner, type, link count and mode, checked before any fix."""

    def lock_path(self):
        return self.sdir / (self.stem + ".lock")

    def make_lock(self, mode=0o600):
        self.mkdir_state()
        path = self.lock_path()
        path.write_bytes(b"")
        os.chmod(path, mode)
        return path

    def test_hard_linked_lock_file_with_a_loose_mode_is_corrupt(self):
        path = self.make_lock(0o644)
        os.link(path, self.tmp / "second-name")
        with self.assertRaises(state.StateCorrupt):
            state.lock_held(self.target, LAYOUT)
        with self.assertRaises(state.StateCorrupt):
            state.Store(self.target, LAYOUT)

    def test_hard_linked_lock_file_alone_is_corrupt(self):
        path = self.make_lock(0o600)
        os.link(path, self.tmp / "second-name")
        with self.assertRaisesRegex(state.StateCorrupt, "link"):
            state.lock_held(self.target, LAYOUT)
        with self.assertRaisesRegex(state.StateCorrupt, "link"):
            state.Store(self.target, LAYOUT)

    def test_loose_mode_alone_is_corrupt_and_is_not_repaired(self):
        path = self.make_lock(0o644)
        with self.assertRaisesRegex(state.StateCorrupt, "mode"):
            state.lock_held(self.target, LAYOUT)
        with self.assertRaisesRegex(state.StateCorrupt, "mode"):
            state.Store(self.target, LAYOUT)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)  # validated before any fchmod, not fixed up

    def test_lock_file_owned_by_someone_else_is_corrupt(self):
        self.make_lock()
        with mock.patch.object(state, "_euid", other_user_inside("_check_lock_file")):
            with self.assertRaisesRegex(state.StateCorrupt, "owned"):
                state.lock_held(self.target, LAYOUT)
            with self.assertRaisesRegex(state.StateCorrupt, "owned"):
                state.Store(self.target, LAYOUT)

    def test_lock_file_that_is_not_regular_is_refused(self):
        self.mkdir_state()
        os.mkfifo(self.lock_path(), 0o600)
        with self.assertRaises(state.StateError):
            state.lock_held(self.target, LAYOUT)
        with self.assertRaises(state.StateError):
            state.Store(self.target, LAYOUT)

    def test_a_clean_lock_file_still_works_and_a_fresh_one_is_created_0600(self):
        with state.Store(self.target, LAYOUT):
            self.assertEqual(stat.S_IMODE(self.lock_path().stat().st_mode), 0o600)
        with state.Store(self.target, LAYOUT):
            self.assertTrue(state.lock_held(self.target, LAYOUT))

    def test_a_symlinked_lock_file_is_refused_by_the_writer_too(self):
        self.mkdir_state()
        decoy = self.tmp / "decoy"
        decoy.write_text("")
        os.symlink(decoy, self.lock_path())
        with self.assertRaises(state.StateError):
            state.Store(self.target, LAYOUT)


class StandInInstaller(StateCase):
    """Phase 1, real atomic writes through install._write_one, `written` recorded only after each success, phase 2."""

    def run_install(self, files, fail_on=None):
        import install as installer

        written = set()
        with state.Store(self.target, LAYOUT) as store:
            entries = store.load() or {}
            new = {
                rel: dict(NEW, pending_sha256=state.body_sha256(data))
                for rel, data in files.items()
                if rel not in entries
            }
            pending = {rel: state.body_sha256(files[rel]) for rel in files if rel in entries}
            store.save(state.with_pending(entries, pending, new=new))  # phase 1
            try:
                for rel, data in files.items():
                    if rel == fail_on:
                        raise ValueError("injected failure")
                    installer._write_one(self.target / rel, "create", data, None, False)  # the real write helper
                    written.add(rel)  # recorded only after the write returned
            except ValueError:
                pass  # the run stops here; phase 2 still settles what really happened
            store.save(state.settle(store.load(), written))  # phase 2
        return written

    def verdicts(self, files):
        got = state.load(self.target, LAYOUT) or {}
        out = {}
        for rel in files:
            on_disk = (self.target / rel).read_bytes() if (self.target / rel).is_file() else None
            out[rel] = state.verdict(got, rel, state.body_sha256(on_disk) if on_disk is not None else None)
        return out

    def test_files_the_installer_wrote_are_owned_after_phase_2(self):
        files = {"a.md": b"alpha\n", "d/b.md": b"beta\n"}
        written = self.run_install(files)
        self.assertEqual(written, set(files))  # derived from the writes, not asserted by hand
        self.assertEqual(self.verdicts(files), {"a.md": "owned", "d/b.md": "owned"})

    def test_a_failed_write_is_not_promoted_even_if_the_bytes_match(self):
        files = {"a.md": b"alpha\n", "b.md": b"beta\n", "c.md": b"gamma\n"}
        (self.target / "b.md").write_bytes(files["b.md"])  # the attacker got there first with the planned bytes
        written = self.run_install(files)
        self.assertEqual(written, {"a.md"})  # create refuses to overwrite: the real helper failed on b.md
        got = state.load(self.target, LAYOUT)
        self.assertEqual(sorted(got), ["a.md"])  # b.md and c.md left no ownership claim
        verdicts = self.verdicts({"a.md": 0, "b.md": 0})
        self.assertEqual(verdicts, {"a.md": "owned", "b.md": "unverified"})

    def test_an_injected_failure_stops_the_run_and_only_earlier_writes_are_owned(self):
        files = {"a.md": b"alpha\n", "b.md": b"beta\n", "c.md": b"gamma\n"}
        written = self.run_install(files, fail_on="b.md")
        self.assertEqual(written, {"a.md"})
        self.assertEqual(sorted(state.load(self.target, LAYOUT)), ["a.md"])
        self.assertFalse((self.target / "b.md").exists())

    def test_a_crash_before_phase_2_leaves_every_new_path_unverified(self):
        import install as installer

        files = {"a.md": b"alpha\n"}
        with state.Store(self.target, LAYOUT) as store:
            store.save(
                state.with_pending({}, {}, new={"a.md": dict(NEW, pending_sha256=state.body_sha256(files["a.md"]))})
            )
            installer._write_one(self.target / "a.md", "create", files["a.md"], None, False)
            # the process dies here, after a real write but before phase 2
        self.assertEqual(self.verdicts(files), {"a.md": "unverified"})


class Processes(StateCase):
    """Real processes: lock contention and kills at each step of a save."""

    def run_child(self, step, tag, **kw):
        return subprocess.run(child_args(self.target, step, tag), capture_output=True, text=True, timeout=60, **kw)

    def test_a_held_lock_makes_every_other_process_lose_with_statelocked(self):
        holder = subprocess.Popen(
            child_args(self.target, "hold", "1"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertTrue(state.lock_held(self.target, LAYOUT))
            for tag in "234":
                self.assertEqual(self.run_child("save", tag).returncode, 3, tag)
            self.assertIsNone(state.load(self.target, LAYOUT))  # nobody wrote anything
        finally:
            holder.stdin.close()
            holder.wait(timeout=30)
        self.assertFalse(state.lock_held(self.target, LAYOUT))
        self.assertEqual(self.run_child("save", "5").returncode, 0)

    def test_four_processes_racing_never_corrupt_the_file(self):
        procs = [
            subprocess.Popen(child_args(self.target, "race", tag), stdout=subprocess.PIPE, text=True) for tag in "abcd"
        ]
        results = []
        for proc in procs:
            out, _ = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0)
            results.append(tuple(int(x) for x in out.split()))
        self.assertGreaterEqual(sum(ok for ok, _ in results), 4)
        self.assertEqual(sum(ok + locked for ok, locked in results), 4 * 40)  # nothing but ok or StateLocked
        got = state.load(self.target, LAYOUT)  # a clean, valid file written by exactly one of them
        self.assertEqual(len(got), 1)
        self.assertIn(list(got)[0], {f"f-{t}.md" for t in "abcd"})
        self.assertEqual(self.listing(), [self.file.name])

    def test_a_kill_at_any_step_leaves_old_or_new_state_and_the_next_run_converges(self):
        for step in ("kill-write", "kill-fsync-1", "kill-fsync-2", "kill-rename", "kill-dirsync"):
            with self.subTest(step):
                if self.sdir.exists():
                    shutil.rmtree(self.sdir)
                state.save(self.target, LAYOUT, {"old.md": entry()})
                proc = self.run_child(step, "9")
                self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr)
                got = state.load(self.target, LAYOUT)  # never a torn or corrupt file
                # before the rename (write, file fsync, the rename itself) the new state must NOT be visible;
                # after it (directory fsync, in either form) it is
                expected = {
                    "kill-write": ["old.md"],
                    "kill-fsync-1": ["old.md"],
                    "kill-rename": ["old.md"],
                    "kill-fsync-2": ["f-9.md"],
                    "kill-dirsync": ["f-9.md"],
                }[step]
                self.assertEqual(list(got), expected)
                self.assertFalse(state.lock_held(self.target, LAYOUT))  # the kernel released the lock
                for name in os.listdir(self.sdir):
                    if name.endswith(".tmp"):
                        path = self.sdir / name
                        os.utime(path, (path.stat().st_mtime - 60,) * 2)
                state.save(self.target, LAYOUT, {"final.md": entry()})
                self.assertEqual(list(state.load(self.target, LAYOUT)), ["final.md"])
                self.assertEqual(self.listing(), [self.file.name])  # the orphan was reaped

    def test_attacker_planted_bytes_for_a_new_path_stay_unverified_before_and_after_a_crash(self):
        planted = b"bytes the installer planned to write\n"
        digest = state.body_sha256(planted)
        (self.target / "n.md").write_bytes(planted)  # the attacker controls the target
        base = {"a.md": entry()}
        phase1 = state.with_pending(base, {}, new={"n.md": dict(NEW, pending_sha256=digest)})
        self.assertEqual(
            state.verdict(phase1, "n.md", state.body_sha256((self.target / "n.md").read_bytes())), "unverified"
        )
        state.save(self.target, LAYOUT, phase1)  # phase 1 on disk, then the installer dies
        crashed = state.load(self.target, LAYOUT)
        self.assertEqual(state.verdict(crashed, "n.md", digest), "unverified")
        again = state.settle(crashed, set())  # the next run reports nothing written
        self.assertEqual(state.verdict(again, "n.md", digest), "unverified")
        self.assertNotIn("n.md", again)
        # (the positive case, where an install really writes the path, is in the stand-in installer tests)

    def test_a_kill_between_the_phases_of_an_update_converges(self):
        state.save(self.target, LAYOUT, {"a.md": entry()})
        with state.Store(self.target, LAYOUT) as store:
            store.save(state.with_pending(store.load(), {"a.md": D2}, new={"n.md": dict(NEW, pending_sha256=D2)}))
        # the process dies here: the existing path accepts both digests, the new path is unverified
        got = state.load(self.target, LAYOUT)
        self.assertEqual(state.verdict(got, "a.md", D1), "owned")
        self.assertEqual(state.verdict(got, "a.md", D2), "owned")
        self.assertEqual(state.verdict(got, "n.md", D2), "unverified")
        self.assertEqual(state.verdict(got, "n.md", D1), "unverified")
        with state.Store(self.target, LAYOUT) as store:  # the re-run writes only a.md this time
            store.save(state.settle(store.load(), {"a.md"}))
        done = state.load(self.target, LAYOUT)
        self.assertEqual(sorted(done), ["a.md"])
        self.assertEqual(done["a.md"]["body_sha256"], D2)


NEW = {"kind": "file", "release": "0.5.0", "source": "public"}


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
        '        if st.st_uid != _euid():\n            raise StateCorrupt(f"state file',
        '        if False:\n            raise StateCorrupt(f"state file',
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
        "        os.fchmod(fd, stat.S_IRWXU)  # 0700: a tight umask",
        "        pass  # 0700: a tight umask",
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
        'for label, base in (("the target", Path(_real_path(target))), ',
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
        "            os.unlink(tmp, dir_fd=self._dfd)",
        "            pass",
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
    (
        "write errors typed",
        "        except OSError as exc:\n            self._discard(tmp)",
        "        except ZeroDivisionError as exc:\n            self._discard(tmp)",
        "Errors.test_write_failures_each_step",
    ),
    (
        "read errors typed",
        '    except OSError as exc:\n        raise StateError(f"state file {name}: read failed',
        '    except ZeroDivisionError as exc:\n        raise StateError(f"state file {name}: read failed',
        "Errors.test_read_failures",
    ),
    (
        "sweep pattern",
        "        if not pattern.fullmatch(name):",
        "        if False:",
        "Sweep.test_nothing_else_is_ever_touched",
    ),
    (
        "sweep no-follow",
        "follow_symlinks=False)\n            if not stat.S_ISREG",
        "follow_symlinks=True)\n            if not stat.S_ISREG",
        "Sweep.test_nothing_else_is_ever_touched",
    ),
    (
        "sweep regular only",
        "if not stat.S_ISREG(st.st_mode) or st.st_uid != _euid() or now",
        "if False or st.st_uid != _euid() or now",
        "Sweep.test_nothing_else_is_ever_touched",
    ),
    ("sweep age", " or now - st.st_mtime < MIN_TMP_AGE:", " or False:", "Sweep.test_a_fresh_temp_file_is_left_alone"),
    (
        "sweep owner",
        "st.st_uid != _euid() or now - st.st_mtime",
        "False or now - st.st_mtime",
        "Sweep.test_a_temp_file_owned_by_someone_else_is_left_alone",
    ),
    (
        "sweep is called",
        "        _sweep(self._dfd, self.stem)\n",
        "        pass\n",
        "Sweep.test_old_leftover_temp_files_are_removed_by_the_next_save",
    ),
    (
        "sweep called (kills)",
        "        _sweep(self._dfd, self.stem)\n",
        "        pass\n",
        "Processes.test_a_kill_at_any_step_leaves_old_or_new_state_and_the_next_run_converges",
    ),
    (
        "closed store",
        '        if self._dfd is None:\n            raise StateError("the state store is closed")',
        '        if False:\n            raise StateError("the state store is closed")',
        "ClosedStore.test_every_method_refuses_after_close",
    ),
    (
        "contains",
        '            if _inside(base, cand):\n                raise StateError(f"the state directory contains',
        '            if False:\n                raise StateError(f"the state directory contains',
        "Containment.test_state_directory_that_contains_the_target",
    ),
    (
        "contains repo",
        "            if _inside(base, cand):\n",
        "            if _inside(base, cand) and label == 'the target':\n",
        "Containment.test_state_directory_that_contains_this_repository",
    ),
    (
        "StateLocked",
        'raise StateLocked("another install is running")',
        'raise StateError("another install is running")',
        "Api.test_locked_is_distinguishable_from_corrupt",
    ),
    (
        "StateCorrupt",
        "raise StateCorrupt(str(exc)) from None",
        "raise StateError(str(exc)) from None",
        "Api.test_locked_is_distinguishable_from_corrupt",
    ),
    (
        "lock_held shared nonblocking",
        "fcntl.LOCK_SH | fcntl.LOCK_NB",
        "fcntl.LOCK_SH",
        "Api.test_lock_held_probe_sees_a_holder_and_lets_go",
    ),
    (
        "lock_held holder",
        "            except BlockingIOError:\n                return True",
        "            except BlockingIOError:\n                return False",
        "Api.test_lock_held_probe_sees_a_holder_and_lets_go",
    ),
    (
        "verdict entries",
        "    if type(entries) is not dict or type(rel) is not str:",
        "    if False:",
        "Api.test_verdict",
    ),
    (
        "with_entry validates",
        "    out[rel] = dict(entry) if type(entry) is dict else entry\n    return check_entries(out)",
        "    out[rel] = entry\n    return out",
        "Api.test_with_entry_and_without_entry_copy_and_validate",
    ),
    (
        "without_entry unknown",
        '    if type(rel) is not str or rel not in out:\n        raise StateError(f"no entry to remove',
        '    if False:\n        raise StateError(f"no entry to remove',
        "Api.test_with_entry_and_without_entry_copy_and_validate",
    ),
    (
        "body_sha256 type",
        "    if not _is_one_of(data, (bytes, bytearray, memoryview)):",
        "    if False:",
        "Api.test_body_sha256",
    ),
    ("load_or_empty", "    return {} if got is None else got", "    return got", "Api.test_load_or_empty"),
    ("state_path", '    return state_dir() / (stem + ".json")', "    return state_dir() / stem", "Api.test_state_path"),
    (
        "race lock",
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        "fcntl.LOCK_SH | fcntl.LOCK_NB",
        "Processes.test_a_held_lock_makes_every_other_process_lose_with_statelocked",
    ),
    (
        "pending-only never owned",
        '    body = entry.get("body_sha256")\n    if type(body) is not str:\n        return False',
        '    body = entry.get("body_sha256")\n    if False:\n        return False',
        "Accepts.test_pending_only_entry_is_never_owned",
    ),
    (
        "pending-only repro",
        '    body = entry.get("body_sha256")\n    if type(body) is not str:\n        return False',
        '    body = entry.get("body_sha256")\n    body = body or entry.get("pending_sha256")',
        "Accepts.test_exact_repro_of_the_review_finding",
    ),
    (
        "pending-only attacker bytes",
        '    body = entry.get("body_sha256")\n    if type(body) is not str:\n        return False',
        '    body = entry.get("body_sha256")\n    body = body or entry.get("pending_sha256")',
        "Processes.test_attacker_planted_bytes_for_a_new_path_stay_unverified_before_and_after_a_crash",
    ),
    (
        "pending next to body",
        "    return any(d == digest for d in stored)",
        "    return digest == body",
        "Accepts.test_existing_owned_path_still_accepts_its_pending_digest",
    ),
    (
        "accepts plain entry",
        "    if type(entry) is not dict or type(digest) is not str:\n        return False",
        "    if type(digest) is not str:\n        return False",
        "Hostile.test_accepts_and_verdict_are_total",
    ),
    (
        "accepts plain digest",
        "    if type(entry) is not dict or type(digest) is not str:\n        return False",
        "    if type(entry) is not dict:\n        return False",
        "Hostile.test_accepts_does_not_trust_a_digest_object_with_its_own_equality",
    ),
    (
        "plain dict verdict",
        "    if type(entries) is not dict or type(rel) is not str:",
        "    if not isinstance(entries, dict) or type(rel) is not str:",
        "Hostile.test_verdict_does_not_touch_a_dict_subclass",
    ),
    (
        "verdict plain rel",
        "    if type(entries) is not dict or type(rel) is not str:",
        "    if type(entries) is not dict:",
        "Hostile.test_accepts_and_verdict_are_total",
    ),
    (
        "NUL target wrapped",
        "except (OSError, ValueError)",
        "except OSError",
        "Hostile.test_nul_and_non_text_targets_raise_state_error_not_value_error",
    ),
    (
        "layout plain str",
        "if type(layout) is not str or not _LAYOUT.fullmatch(layout):",
        "if not isinstance(layout, str) or not _LAYOUT.fullmatch(layout):",
        "Hostile.test_layout_must_be_a_plain_str",
    ),
    (
        "settle container",
        "    if not _is_one_of(written, (set, frozenset, list, tuple)):",
        "    if False:",
        "Hostile.test_mutators_raise_state_error_on_hostile_arguments",
    ),
    (
        "settle members",
        "    for rel in written:\n        if type(rel) is not str:",
        "    for rel in []:\n        if type(rel) is not str:",
        "Hostile.test_mutators_raise_state_error_on_hostile_arguments",
    ),
    (
        "settle accepted containers",
        "    if not _is_one_of(written, (set, frozenset, list, tuple)):",
        "    if not _is_one_of(written, (set,)):",
        "Hostile.test_settle_rejects_hostile_containers_but_accepts_the_documented_ones",
    ),
    (
        "with_pending container",
        "    if type(pending) is not dict:",
        "    if False:",
        "Hostile.test_mutators_raise_state_error_on_hostile_arguments",
    ),
    (
        "with_pending new container",
        "    if new is not None and type(new) is not dict:",
        "    if False:",
        "Hostile.test_mutators_raise_state_error_on_hostile_arguments",
    ),
    (
        "plain str rel",
        "    if type(rel) is not str or not _text_ok(rel) or not rel:",
        "    if not _text_ok(rel) or not rel:",
        "Hostile.test_mutators_raise_state_error_on_hostile_arguments",
    ),
    (
        "lock owner",
        '    if st.st_uid != _euid():\n        raise StateCorrupt("state lock',
        '    if False:\n        raise StateCorrupt("state lock',
        "LockFile.test_lock_file_owned_by_someone_else_is_corrupt",
    ),
    (
        "lock link count",
        '    if st.st_nlink != 1:\n        raise StateCorrupt("state lock',
        '    if False:\n        raise StateCorrupt("state lock',
        "LockFile.test_hard_linked_lock_file_alone_is_corrupt",
    ),
    (
        "lock mode",
        "    elif stat.S_IMODE(st.st_mode) != 0o600:",
        "    elif False:",
        "LockFile.test_loose_mode_alone_is_corrupt_and_is_not_repaired",
    ),
    (
        "lock validated before fix",
        "    if created:\n        os.fchmod",
        "    if True:\n        os.fchmod",
        "LockFile.test_loose_mode_alone_is_corrupt_and_is_not_repaired",
    ),
    (
        "lock regular",
        '    if not stat.S_ISREG(st.st_mode):\n        raise StateCorrupt("state lock',
        '    if False:\n        raise StateCorrupt("state lock',
        "LockFile.test_lock_file_that_is_not_regular_is_refused",
    ),
    (
        "lock_held validates",
        "            _check_lock_file(lfd, created=False)",
        "            pass",
        "LockFile.test_hard_linked_lock_file_with_a_loose_mode_is_corrupt",
    ),
    (
        "writer creates exclusively",
        "_LOCK_FLAGS | os.O_EXCL",
        "_LOCK_FLAGS",
        "LockFile.test_loose_mode_alone_is_corrupt_and_is_not_repaired",
    ),
    (
        "state file link count",
        '        if st.st_nlink != 1:\n            raise StateCorrupt(f"state file',
        '        if False:\n            raise StateCorrupt(f"state file',
        "Confinement.test_hard_linked_state_file",
    ),
    (
        "O_NONBLOCK read open",
        "_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC",
        "_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC",
        "Confinement.test_file_not_regular",
    ),
    (
        "kind type before membership",
        "if type(kind) is not str or kind not in KINDS:",
        "if kind not in KINDS or type(kind) is not str:",
        "HostileEverywhere.test_membership_is_never_tested_before_the_type",
    ),
    (
        "source type before membership",
        "if type(source) is not str or source not in SOURCES:",
        "if source not in SOURCES or type(source) is not str:",
        "HostileEverywhere.test_membership_is_never_tested_before_the_type",
    ),
    (
        "kind type before membership (matrix)",
        "if type(kind) is not str or kind not in KINDS:",
        "if kind not in KINDS or type(kind) is not str:",
        "HostileEverywhere.test_every_mutator_gives_state_error_and_never_runs_hostile_code",
    ),
    (
        "entry keys typed",
        "    if any(type(k) is not str for k in entry):",
        "    if False:",
        "HostileEverywhere.test_hash_colliding_keys_are_never_compared",
    ),
    (
        "new spec keys typed",
        "            or any(type(k) is not str for k in spec)\n",
        "",
        "HostileEverywhere.test_hash_colliding_keys_are_never_compared",
    ),
    (
        "body_sha256 buffer errors",
        "    except (ValueError, TypeError, BufferError):",
        "    except TypeError:",
        "HostileEverywhere.test_body_sha256_never_leaks_a_buffer_error",
    ),
    (
        "failed write not promoted",
        '        if rel in written and "pending_sha256" in entry:',
        '        if "pending_sha256" in entry:',
        "StandInInstaller.test_a_failed_write_is_not_promoted_even_if_the_bytes_match",
    ),
    (
        "injected failure not promoted",
        '        if rel in written and "pending_sha256" in entry:',
        '        if "pending_sha256" in entry:',
        "StandInInstaller.test_an_injected_failure_stops_the_run_and_only_earlier_writes_are_owned",
    ),
    (
        "crash before phase 2 unverified",
        "    if type(body) is not str:\n        return False",
        '    body = body or entry.get("pending_sha256")\n    if type(body) is not str:\n        return False',
        "StandInInstaller.test_a_crash_before_phase_2_leaves_every_new_path_unverified",
    ),
    (
        "written paths owned",
        '        if rel in written and "pending_sha256" in entry:',
        "        if False:",
        "StandInInstaller.test_files_the_installer_wrote_are_owned_after_phase_2",
    ),
    (
        "shown exact type",
        '    elif type(text) is not str:\n        text = "?"',
        '    elif False:\n        text = "?"',
        "HostileEverywhere.test_foreign_str_repr_format_and_fspath_never_run",
    ),
    (
        "shown exact type (repro)",
        '    elif type(text) is not str:\n        text = "?"',
        '    elif False:\n        text = "?"',
        "HostileEverywhere.test_the_review_repros",
    ),
    (
        "shown exact type (unit)",
        '    elif type(text) is not str:\n        text = "?"',
        '    elif False:\n        text = "?"',
        "HostileEverywhere.test_shown_renders_only_exact_str_and_int",
    ),
    (
        "shown int exact",
        "    if type(text) is int:",
        "    if isinstance(text, int):",
        "HostileEverywhere.test_shown_renders_only_exact_str_and_int",
    ),
    (
        "path types exact",
        "    if not _is_one_of(target, _PATH_TYPES):",
        "    if False:",
        "HostileEverywhere.test_only_exact_path_types_are_accepted_as_targets",
    ),
    (
        "is_one_of exact",
        "    return any(t is c for c in types)",
        "    return isinstance(obj, tuple(types))",
        "ExactTypes.test_is_one_of",
    ),
    (
        "body_sha256 exact",
        "    if not _is_one_of(data, (bytes, bytearray, memoryview)):",
        "    if not isinstance(data, (bytes, bytearray, memoryview)):",
        "Api.test_body_sha256_rejects_bytes_subclasses_and_spoofs",
    ),
    (
        "settle container exact",
        "    if not _is_one_of(written, (set, frozenset, list, tuple)):",
        "    if not isinstance(written, (set, frozenset, list, tuple)):",
        "Hostile.test_settle_rejects_container_subclasses",
    ),
    (
        "oserr exact str",
        "    return _shown(exc.strerror if type(exc.strerror) is str else type(exc).__name__)",
        "    return _shown(exc.strerror)",
        "HostileEverywhere.test_oserr_renders_only_an_exact_str_strerror",
    ),
]


# Rows whose test can only notice the weakened guard by blocking (the 10 s alarm in StateCase turns the hang into a
# failure). Every other row must be caught by an assertion; a hang there would mean the test proves nothing useful.
HANG_ROWS = {"lock nonblocking", "lock_held shared nonblocking", "O_NONBLOCK read open"}


EXPECTED_ROWS = 97


class GuardTable(unittest.TestCase):
    def run_one(self, scripts_dir, test_id):
        """'green', 'red' (a test failed by assertion or error) or 'hang' (it blocked until the alarm or timeout)."""
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
            return "hang"
        if r.returncode == 0:
            return "green"
        return "hang" if "test hung" in r.stderr else "red"

    def test_every_listed_guard_goes_red_when_weakened_and_green_when_intact(self):
        # Exact on purpose: adding or dropping a row must be a deliberate edit of this number, so the table cannot
        # shrink unnoticed.
        self.assertEqual(len(MUTANTS), EXPECTED_ROWS)
        source = (ROOT / "scripts" / "state.py").read_text()
        tmp = Path(tempfile.mkdtemp(prefix="doia-mut-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        # <copy>/scripts/state.py: the module derives the repository root from its own location (parents[1]), which
        # must not swallow the temp directories the tests create
        intact = tmp / "intact" / "scripts"
        intact.mkdir(parents=True)
        (intact / "state.py").write_text(source)
        survivors, bad_hangs = [], []
        for n, (name, old, new, test_id) in enumerate(MUTANTS):
            with self.subTest(name):
                self.assertTrue(old in source, f"stale mutant row: {name}")
                cls_name, _, method = test_id.partition(".")
                cls = getattr(sys.modules[__name__], cls_name, None)
                self.assertTrue(callable(getattr(cls, method, None)), f"row {name}: no test {test_id}")
                self.assertEqual(self.run_one(intact, test_id), "green", f"{test_id} is not green on the intact copy")
                mutant = tmp / f"m{n}" / "scripts"
                mutant.mkdir(parents=True)
                (mutant / "state.py").write_text(source.replace(old, new, 1))
                status = self.run_one(mutant, test_id)
                if status == "green":
                    survivors.append(name)
                elif status == "hang" and name not in HANG_ROWS:
                    bad_hangs.append(name)
        self.assertEqual(survivors, [], "weakened guards whose test stayed green")
        self.assertEqual(bad_hangs, [], "guards caught only by a hang although they are not blocking guards")

    def test_the_runner_itself_detects_a_survivor(self):
        """Negative case: a no-op 'mutation' leaves the test green, and a real one is red."""
        tmp = Path(tempfile.mkdtemp(prefix="doia-mut-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = tmp / "x" / "scripts"
        copy.mkdir(parents=True)
        (copy / "state.py").write_text((ROOT / "scripts" / "state.py").read_text())
        self.assertEqual(self.run_one(copy, "StrictParse.test_duplicate_key"), "green")
        (copy / "state.py").write_text(
            (ROOT / "scripts" / "state.py")
            .read_text()
            .replace('            raise StateError(f"duplicate key: {_shown(key)}")', "            pass", 1)
        )
        self.assertEqual(self.run_one(copy, "StrictParse.test_duplicate_key"), "red")

    def test_a_blocking_guard_is_reported_as_a_hang_not_an_assertion(self):
        tmp = Path(tempfile.mkdtemp(prefix="doia-mut-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = tmp / "x" / "scripts"
        copy.mkdir(parents=True)
        src = (ROOT / "scripts" / "state.py").read_text()
        (copy / "state.py").write_text(src.replace("fcntl.LOCK_EX | fcntl.LOCK_NB", "fcntl.LOCK_EX", 1))
        test_id = "WriteSafety.test_second_save_while_lock_held_fails_and_first_file_unchanged"
        self.assertEqual(self.run_one(copy, test_id), "hang")

    def test_every_row_names_a_real_test_method(self):
        for name, _old, _new, test_id in MUTANTS:
            cls_name, _, method = test_id.partition(".")
            cls = getattr(sys.modules[__name__], cls_name, None)
            self.assertTrue(callable(getattr(cls, method, None)), f"row {name}: no test {test_id}")
        self.assertTrue(HANG_ROWS <= {row[0] for row in MUTANTS})


if __name__ == "__main__":
    unittest.main()
