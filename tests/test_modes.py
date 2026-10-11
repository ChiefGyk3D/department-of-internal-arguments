"""File modes: an update never widens a destination's permissions, and overlay-sourced files are never group/world
accessible."""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import install as installer  # noqa: E402  (path set up above)
from install import install  # noqa: E402
from lib import version  # noqa: E402
from test_user_layout import make_overlay, role_text  # noqa: E402


def perms(text):
    """'rw-r-----' -> permission bits, so a fixture mode is not a literal mask in a chmod call."""
    return sum(1 << (8 - i) for i, ch in enumerate(text) if ch != "-")


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def staled(path):
    path.write_text(path.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))


class UpdateKeepsModes(unittest.TestCase):
    def test_a_0600_file_stays_0600_and_a_0640_stays_0640(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user")
            a, b = t / "agents/reviewer.md", t / "agents/implementer.md"
            for p, m in ((a, perms("rw-------")), (b, perms("rw-r-----"))):
                staled(p)
                os.chmod(p, m)
            install(t, "claude", True, update=True, layout="user")
            self.assertEqual(mode(a), 0o600)
            self.assertEqual(mode(b), 0o640)
            self.assertIn(f"(release {version()})", a.read_text())

    def test_a_plain_update_does_not_change_a_0644_file(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user")
            a = t / "agents/reviewer.md"
            staled(a)
            os.chmod(a, perms("rw-r--r--"))
            install(t, "claude", True, update=True, layout="user")
            self.assertEqual(mode(a), 0o644)

    def test_the_temp_file_is_created_0600(self):
        seen = []
        real = os.open

        def spy(path, flags, mode=0o600, **kw):
            seen.append((str(path), mode))
            return real(path, flags, mode, **kw)

        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user")
            staled(t / "agents/reviewer.md")
            with mock.patch.object(installer.os, "open", side_effect=spy):
                install(t, "claude", True, update=True, layout="user")
        tmp = [m for p, m in seen if p.endswith(".doia-tmp")]
        self.assertEqual(tmp, [0o600])

    def test_a_failed_update_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user")
            a = t / "agents/reviewer.md"
            staled(a)
            before = a.read_text()
            with mock.patch.object(installer.os, "rename", side_effect=OSError("boom")), self.assertRaises(OSError):
                install(t, "claude", True, update=True, layout="user")
            self.assertEqual(list(Path(d).rglob("*.doia-tmp")), [])
            self.assertEqual(a.read_text(), before)


class NewPublicFiles(unittest.TestCase):
    def test_a_new_public_file_is_never_group_or_world_writable_even_with_umask_zero(self):
        old = os.umask(0)
        try:
            with tempfile.TemporaryDirectory() as d:
                install(d, "claude", True, layout="user")
                self.assertEqual(mode(Path(d) / "agents/reviewer.md"), 0o644)
        finally:
            os.umask(old)


class OverlayFilesArePrivate(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.d = Path(self._d.name)
        self.ov = make_overlay(
            self.d, roles={"reviewer": "---\nname: reviewer\n---\n\nPrivate.\n", "auditor": role_text("auditor")}
        )
        self.t = self.d / "dot"
        self.t.mkdir()
        self.old = os.umask(0o022)

    def tearDown(self):
        os.umask(self.old)
        self._d.cleanup()

    def test_new_overlay_files_are_0600_and_public_files_default(self):
        install(self.t, "claude", True, layout="user", overlay=self.ov)
        self.assertEqual(mode(self.t / "agents/reviewer.md"), 0o600)  # public persona with additions
        self.assertEqual(mode(self.t / "agents/auditor.md"), 0o600)  # whole overlay persona
        self.assertEqual(mode(self.t / "agents/implementer.md"), 0o644)  # public, default

    def test_updating_a_wide_file_to_one_with_additions_narrows_it(self):
        install(self.t, "claude", True, layout="user")
        a = self.t / "agents/reviewer.md"
        os.chmod(a, perms("rw-r--r--"))
        install(self.t, "claude", True, update=True, layout="user", overlay=self.ov)
        self.assertEqual(mode(a), 0o600)
        self.assertIn("Private.", a.read_text())

    def test_update_of_an_overlay_file_keeps_0600_and_never_widens_a_0640_one(self):
        install(self.t, "claude", True, layout="user", overlay=self.ov)
        a = self.t / "agents/auditor.md"
        os.chmod(a, perms("rw-r-----"))
        staled(a)
        install(self.t, "claude", True, update=True, layout="user", overlay=self.ov)
        self.assertEqual(mode(a), 0o600)

    def test_group_umask_does_not_widen_a_new_overlay_file(self):
        os.umask(0o002)
        install(self.t, "claude", True, layout="user", overlay=self.ov)
        self.assertEqual(mode(self.t / "agents/reviewer.md"), 0o600)


if __name__ == "__main__":
    unittest.main()
