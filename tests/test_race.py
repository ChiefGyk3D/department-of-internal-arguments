"""Writes go through no-follow directory descriptors: a path component swapped for a symlink after the hash check
must not redirect a write, and the run must fail."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install as installer  # noqa: E402  (path set up above)
from install import install  # noqa: E402
from lib import version  # noqa: E402


def stale_install(d):
    """A user-layout install whose reviewer.md is behind (so the next --update replaces it)."""
    t = Path(d) / "dot"
    t.mkdir()
    install(t, "claude", True, layout="user")
    a = t / "agents/reviewer.md"
    a.write_text(a.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
    return t, a


class DirectorySwap(unittest.TestCase):
    def test_parent_swapped_for_a_symlink_after_the_hash_check(self):
        with tempfile.TemporaryDirectory() as d:
            t, agent = stale_install(d)
            victim_dir = Path(d) / "victim"
            victim_dir.mkdir()
            victim = victim_dir / "reviewer.md"
            victim.write_text("VICTIM\n")
            items = installer._plan(t, "claude", update=True, layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "reviewer.md":
                    (t / "agents").rename(t / "agents.moved")
                    (t / "agents").symlink_to(victim_dir, target_is_directory=True)

            with mock.patch.object(installer, "_race_seam", side_effect=swap), self.assertRaises(ValueError):
                installer._apply(items)
            self.assertEqual(victim.read_text(), "VICTIM\n")
            self.assertEqual(sorted(p.name for p in victim_dir.iterdir()), ["reviewer.md"])
            self.assertEqual(list(Path(d).rglob("*.doia-tmp")), [])

    def test_parent_swapped_after_the_write_still_fails_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            t, agent = stale_install(d)
            victim_dir = Path(d) / "victim"
            victim_dir.mkdir()
            items = installer._plan(t, "claude", update=True, layout="user")

            def swap(stage, dest):
                if stage == "written" and dest.name == "reviewer.md":
                    (t / "agents").rename(t / "agents.moved")
                    (t / "agents").symlink_to(victim_dir, target_is_directory=True)

            with (
                mock.patch.object(installer, "_race_seam", side_effect=swap),
                self.assertRaisesRegex(ValueError, "replaced"),
            ):
                installer._apply(items)
            self.assertEqual(list(victim_dir.iterdir()), [])

    def test_new_file_into_a_directory_swapped_before_the_write(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            (t / "agents").mkdir()
            victim_dir = Path(d) / "victim"
            victim_dir.mkdir()
            items = installer._plan(t, "claude", layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "adversarial-reviewer.md":
                    (t / "agents").rename(t / "agents.moved")
                    (t / "agents").symlink_to(victim_dir, target_is_directory=True)

            with mock.patch.object(installer, "_race_seam", side_effect=swap), self.assertRaises(ValueError):
                installer._apply(items)
            self.assertEqual(list(victim_dir.iterdir()), [])

    def test_missing_parents_are_created_without_following_links(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            items = installer._plan(t, "claude", layout="user")
            installer._apply(items)
            self.assertTrue((t / "skills/pack-rf-emcomm/SKILL.md").is_file())

    def test_ancestor_symlink_is_refused_by_the_descriptor_walk_itself(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            (real / "sub").mkdir()
            link = Path(d) / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError):
                os.close(installer._open_dir_chain(link / "sub", create=False))
            os.close(installer._open_dir_chain(real / "sub", create=False))


class FileSwap(unittest.TestCase):
    def test_destination_swapped_for_a_symlink_after_the_hash_check(self):
        with tempfile.TemporaryDirectory() as d:
            t, agent = stale_install(d)
            victim = Path(d) / "victim.md"
            victim.write_text("VICTIM\n")
            items = installer._plan(t, "claude", update=True, layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "reviewer.md":
                    dest.unlink()
                    dest.symlink_to(victim)

            with (
                mock.patch.object(installer, "_race_seam", side_effect=swap),
                self.assertRaisesRegex(ValueError, "changed"),
            ):
                installer._apply(items)
            self.assertEqual(victim.read_text(), "VICTIM\n")
            self.assertTrue(agent.is_symlink())
            self.assertEqual(list(Path(d).rglob("*.doia-tmp")), [])

    def test_destination_replaced_by_another_file_is_not_clobbered(self):
        with tempfile.TemporaryDirectory() as d:
            t, agent = stale_install(d)
            items = installer._plan(t, "claude", update=True, layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "reviewer.md":
                    dest.unlink()
                    dest.write_text("someone else's file\n")

            with (
                mock.patch.object(installer, "_race_seam", side_effect=swap),
                self.assertRaisesRegex(ValueError, "changed"),
            ):
                installer._apply(items)
            self.assertEqual(agent.read_text(), "someone else's file\n")

    def test_new_destination_planted_as_a_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            (t / "agents").mkdir(parents=True)
            victim = Path(d) / "victim.md"
            victim.write_text("VICTIM\n")
            items = installer._plan(t, "claude", layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "reviewer.md":
                    dest.symlink_to(victim)

            with mock.patch.object(installer, "_race_seam", side_effect=swap), self.assertRaises(ValueError):
                installer._apply(items)
            self.assertEqual(victim.read_text(), "VICTIM\n")

    def test_swap_of_a_file_for_a_directory_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            t, agent = stale_install(d)
            items = installer._plan(t, "claude", update=True, layout="user")

            def swap(stage, dest):
                if stage == "checked" and dest.name == "reviewer.md":
                    dest.unlink()
                    dest.mkdir()

            with mock.patch.object(installer, "_race_seam", side_effect=swap), self.assertRaises(ValueError):
                installer._apply(items)


if __name__ == "__main__":
    unittest.main()
