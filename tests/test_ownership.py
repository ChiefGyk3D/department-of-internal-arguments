"""Ownership bound to content (marker hash), forced updates, and stricter rules for overriding a public persona."""

import contextlib
import hashlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import install as installer  # noqa: E402  (path set up above)
import lib  # noqa: E402
import overlay as overlay_mod  # noqa: E402
from install import install, list_managed, plan  # noqa: E402
from lib import GENERATED_MARK, version  # noqa: E402
from test_user_layout import make_overlay, role_text  # noqa: E402

LEGACY_MARKER = f"<!-- {GENERATED_MARK} (release 0.2.0) from roles/reviewer.md. Do not edit; run the generator. -->"
EDITED_LABEL = "edited (refused; --force-edited discards your changes)"


def legacy_text(public_rel, marker=LEGACY_MARKER):
    """The public generated file with its hash-bearing marker swapped for a 0.2.0-style one."""
    lines = (ROOT / public_rel).read_text().split("\n")
    i = next(k for k, ln in enumerate(lines) if GENERATED_MARK in ln)
    lines[i] = marker
    return "\n".join(lines)


def treat_as_020(*public_rels):
    """Patch: the current body of each file counts as a known 0.2.0 body.

    The real hash set describes the bytes release 0.2.0 shipped; once a source changes (the shared header
    gained a line in 0.3.x) the current file no longer matches, so these tests supply the hash they need."""
    extra = {installer.body_sha256(lib.split_marker(legacy_text(rel))[1]) for rel in public_rels}
    return mock.patch.object(installer, "LEGACY_BODY_SHA256", lib.LEGACY_BODY_SHA256 | extra)


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


class MarkerHash(unittest.TestCase):
    def test_every_generated_file_carries_a_matching_body_hash(self):
        files = [p for base in (".claude", ".github/agents") for p in (ROOT / base).rglob("*.md")]
        self.assertEqual(len(files), 15)
        for p in files:
            text = p.read_text()
            m, body = lib.split_marker(text)
            self.assertIsNotNone(m.group(3), p.name)
            self.assertEqual(m.group(3), hashlib.sha256(body.encode()).hexdigest(), p.name)
            self.assertNotIn(GENERATED_MARK, body)

    def test_marker_line_is_strict(self):
        self.assertIsNone(lib.split_marker(f"x\n<!-- {GENERATED_MARK} (release forged) -->\n"))
        self.assertIsNone(lib.split_marker(f"{LEGACY_MARKER}\n{LEGACY_MARKER}\n"))  # two markers: never well-formed
        self.assertIsNotNone(lib.split_marker(f"{LEGACY_MARKER}\nbody\n"))


class MarkerStrictness(unittest.TestCase):
    def test_text_around_a_valid_marker_line_is_not_well_formed(self):
        for line in (f"junk {LEGACY_MARKER}", f"{LEGACY_MARKER} junk", f"<!--  {LEGACY_MARKER[5:]}"):
            self.assertIsNone(lib.split_marker(f"{line}\nbody\n"), line)

    def test_generator_refuses_a_body_that_quotes_the_marker(self):
        import generate

        pack = {"dir": "x", "description": "d", "body": f"quoted: {LEGACY_MARKER}", "updated": "2026-10-08"}
        with self.assertRaisesRegex(ValueError, "marker"):
            generate.render_skill(pack, "0.0.0")

    def test_oversized_marker_file_is_edited_not_owned(self):
        with tempfile.TemporaryDirectory() as d:
            big = Path(d) / "big.md"
            big.write_text((ROOT / ".claude/agents/reviewer.md").read_text() + "x" * installer.MAX_MANAGED_BYTES)
            self.assertEqual(installer.inspect(big)[0], "edited")

    def test_list_managed_flags_reject_force_edited(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run_main("--target", d, "--list-managed", "--force-edited")
            self.assertEqual(code, 1)
            self.assertIn("read-only", err)


class EditedFiles(unittest.TestCase):
    def setup_installed(self, d):
        install(d, "claude", True, layout="user")
        return Path(d) / "agents/reviewer.md"

    def test_hand_edit_is_refused_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            edited = agent.read_text() + "\nmy local rule\n"
            agent.write_text(edited)
            with self.assertRaisesRegex(ValueError, "edited"):
                install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_text(), edited)

    def test_forged_marker_is_not_ownership(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            forged = f"hand written\n<!-- {GENERATED_MARK} (release forged) -->\n"
            agent.write_text(forged)
            with self.assertRaises(ValueError):
                install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_text(), forged)
            self.assertFalse(installer.is_generated(agent))

    def test_force_edited_discards_local_edits(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            agent.write_text(agent.read_text() + "\nmy local rule\n")
            install(d, "claude", True, update=True, layout="user", force_edited=True)
            self.assertEqual(agent.read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes())

    def test_force_edited_never_takes_an_unmarked_file(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            agent.write_text("mine, no marker\n")
            with self.assertRaises(ValueError):
                install(d, "claude", True, update=True, layout="user", force_edited=True)
            self.assertEqual(agent.read_text(), "mine, no marker\n")

    def test_force_edited_requires_update(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, "--update"):
            install(d, "claude", layout="user", force_edited=True)

    def test_marker_only_changes_do_not_count_as_edits(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            agent.write_text(agent.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
            install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes())

    def test_plan_labels(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            self.assertEqual({a for _, _, a in plan(d, "claude", update=True, layout="user")}, {"unchanged"})
            agent.write_text(agent.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
            actions = {dest.name: a for _, dest, a in plan(d, "claude", update=True, layout="user")}
            self.assertEqual(actions["reviewer.md"], "update")
            self.assertEqual(actions["implementer.md"], "unchanged")
            (Path(d) / "agents/implementer.md").write_text("x\n" + agent.read_text())
            self.assertEqual(installer._plan(d, "claude", update=True, layout="user")[1][2], "edited")

    def test_unchanged_files_are_not_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            before = agent.stat().st_mtime_ns
            os.utime(agent, ns=(before - 10**9, before - 10**9))
            old = agent.stat().st_mtime_ns
            install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.stat().st_mtime_ns, old)

    def test_cli_labels_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            run_main("--target", d, "--layout", "user", "--apply")
            agent = Path(d) / "agents/reviewer.md"
            code, out, _ = run_main("--target", d, "--layout", "user", "--update")
            self.assertEqual(code, 0)
            self.assertIn("unchanged: agents/reviewer.md", out)
            agent.write_text(agent.read_text() + "\nQZX-LOCAL-EDIT\n")
            code, out, err = run_main("--target", d, "--layout", "user", "--update")
            self.assertEqual(code, 1)
            self.assertIn(f"{EDITED_LABEL}: agents/reviewer.md", out)
            self.assertIn("unchanged: agents/implementer.md", out)
            code, out, _ = run_main("--target", d, "--layout", "user", "--update", "--force-edited", "--apply")
            self.assertEqual(code, 0)
            self.assertIn("discards local edits", out)
            self.assertNotIn("QZX-LOCAL-EDIT", agent.read_text())

    def test_cli_force_edited_without_update_refused(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run_main("--target", d, "--layout", "user", "--force-edited")
            self.assertEqual(code, 1)
            self.assertIn("--update", err)

    def test_changed_between_preflight_and_write(self):
        with tempfile.TemporaryDirectory() as d:
            agent = self.setup_installed(d)
            agent.write_text(agent.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
            items = installer._plan(d, "claude", update=True, layout="user")
            self.assertIn("update", [i[2] for i in items])
            agent.write_text("replaced after the preflight\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                installer._apply(items)
            self.assertEqual(agent.read_text(), "replaced after the preflight\n")
            self.assertEqual(list(Path(d).rglob("*.doia-tmp")), [])


class LegacyMarkers(unittest.TestCase):
    def test_legacy_file_with_known_body_is_owned(self):
        with tempfile.TemporaryDirectory() as d, treat_as_020(".claude/agents/reviewer.md"):
            agent = Path(d) / "agents/reviewer.md"
            agent.parent.mkdir()
            agent.write_text(legacy_text(".claude/agents/reviewer.md"))
            self.assertEqual(installer.inspect(agent)[0], "legacy")
            self.assertTrue(installer.is_generated(agent))
            install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes())

    def test_legacy_marker_with_unknown_body_is_unverified_and_needs_force(self):
        with tempfile.TemporaryDirectory() as d:
            agent = Path(d) / "agents/reviewer.md"
            agent.parent.mkdir()
            text = legacy_text(".claude/agents/reviewer.md") + "\nsomething added\n"
            agent.write_text(text)
            self.assertEqual(installer.inspect(agent)[0], "legacy-unverified")
            with self.assertRaises(ValueError):
                install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_text(), text)
            install(d, "claude", True, update=True, layout="user", force_edited=True)
            self.assertEqual(agent.read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes())

    def test_known_legacy_hashes_cover_every_020_file(self):
        self.assertEqual(len(lib.LEGACY_BODY_SHA256), 14)
        for h in lib.LEGACY_BODY_SHA256:
            self.assertRegex(h, r"^[0-9a-f]{64}$")

    def test_list_managed_reports_edited_and_legacy(self):
        with tempfile.TemporaryDirectory() as d, treat_as_020(".claude/agents/implementer.md"):
            install(d, "claude", True, layout="user")
            rev = Path(d) / "agents/reviewer.md"
            rev.write_text(rev.read_text() + "\nedit\n")
            imp = Path(d) / "agents/implementer.md"
            imp.write_text(legacy_text(".claude/agents/implementer.md"))
            tra = Path(d) / "agents/transcriber.md"
            tra.write_text(legacy_text(".claude/agents/transcriber.md") + "\nmore\n")
            rows = {r["path"]: r["status"] for r in list_managed(d, layout="user")}
            self.assertEqual(rows["agents/reviewer.md"], "edited")
            self.assertEqual(rows["agents/implementer.md"], "stale")  # verified 0.2.0 file, newer release
            self.assertEqual(rows["agents/transcriber.md"], "legacy-unverified")
            self.assertEqual(rows["agents/adversarial-reviewer.md"], "current")


class OverlayMessages(unittest.TestCase):
    def test_group_writable_error_names_the_remedy(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            (ov / "roles/auditor.md").chmod(0o664)
            with self.assertRaises(ValueError) as cm:
                overlay_mod.load_overlay(ov)
            self.assertIn("chmod -R g-w,o-w", str(cm.exception))
            self.assertIn(str(ov), str(cm.exception))

    def test_nested_frontmatter_gets_a_depth_error(self):
        for depth in (30, 400, 5000):
            nested = "[" * depth + "]" * depth
            with tempfile.TemporaryDirectory() as d:
                text = re.sub(r"^description: .*$", f"description: {nested}", role_text("auditor"), flags=re.M)
                ov = make_overlay(d, roles={"auditor": text})
                with self.assertRaisesRegex(ValueError, "nested"):
                    overlay_mod.load_overlay(ov)

    def test_modest_nesting_in_a_list_is_still_fine(self):
        with mock.patch.object(overlay_mod, "MAX_NESTING", 2):
            self.assertEqual(overlay_mod.nesting_depth([["x"]]), 2)
            self.assertEqual(overlay_mod.nesting_depth("x"), 0)


if __name__ == "__main__":
    unittest.main()
