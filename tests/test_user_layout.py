"""User layout, private overlay and --list-managed. Stdlib only."""

import contextlib
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate  # noqa: E402  (path set up above)
import install as installer  # noqa: E402
import overlay as overlay_mod  # noqa: E402
from install import install, is_generated, list_managed, plan  # noqa: E402
from lib import GENERATED_MARK, PACK_NAMES, ROLE_NAMES, version  # noqa: E402
from validate import validate  # noqa: E402

PUBLIC_REVIEWER = (ROOT / "roles/reviewer.md").read_text(encoding="utf-8")
PUBLIC_PACK = (ROOT / "packs/threat-model/PACK.md").read_text(encoding="utf-8")


def make_overlay(base, roles=None, packs=None):
    """A well-formed private overlay: owned by us, mode 0755, files 0644."""
    ov = Path(base) / "private-overlay"
    (ov / "roles").mkdir(parents=True)
    (ov / "packs").mkdir()
    for name, text in (roles or {}).items():
        (ov / "roles" / f"{name}.md").write_text(text, encoding="utf-8")
    for name, text in (packs or {}).items():
        (ov / "packs" / name).mkdir()
        (ov / "packs" / name / "PACK.md").write_text(text, encoding="utf-8")
    for p in [ov, *ov.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return ov


def role_text(name, description="Overlay persona for a private task.", base=PUBLIC_REVIEWER):
    text = re.sub(r"^name: .*$", f"name: {name}", base, count=1, flags=re.M)
    return re.sub(r"^description: .*$", f'description: "{description}"', text, count=1, flags=re.M)


def pack_text(name, base=PUBLIC_PACK):
    return re.sub(r"^name: .*$", f"name: {name}", base, count=1, flags=re.M)


def copy_fixture(base):
    out = Path(base) / "fixture"
    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return out


def all_files(d):
    return sorted(str(p.relative_to(d)) for p in Path(d).rglob("*") if p.is_file())


class ProjectLayoutUnchanged(unittest.TestCase):
    def test_golden_project_output_is_the_generated_tree(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "both", True)
            self.assertEqual(sorted(files), all_files(d))
            want = sorted(
                str(p.relative_to(ROOT))
                for base in (".claude/agents", ".claude/skills", ".github/agents")
                for p in (ROOT / base).rglob("*")
                if p.is_file()
            )
            self.assertEqual(all_files(d), want)
            for rel in want:
                self.assertEqual((Path(d) / rel).read_bytes(), (ROOT / rel).read_bytes(), rel)

    def test_explicit_project_layout_equals_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(install(d, "both"), install(d, "both", layout="project"))

    def test_default_order_is_agents_then_packs_then_copilot(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "both")
            agents = sorted(f"{'.claude/agents/' + r}.md" for r in ROLE_NAMES)
            packs = [f".claude/skills/pack-{p}/SKILL.md" for p in PACK_NAMES]
            copilot = sorted(f".github/agents/{r}.agent.md" for r in ROLE_NAMES)
            self.assertEqual(files, agents + packs + copilot)


class UserLayout(unittest.TestCase):
    def test_paths_and_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "claude", True, layout="user")
            self.assertEqual(
                sorted(files),
                sorted([f"agents/{r}.md" for r in ROLE_NAMES] + [f"skills/pack-{p}/SKILL.md" for p in PACK_NAMES]),
            )
            self.assertEqual(all_files(d), sorted(files))
            self.assertFalse((Path(d) / ".claude").exists())
            self.assertEqual(
                (Path(d) / "agents/reviewer.md").read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes()
            )
            self.assertEqual(
                (Path(d) / "skills/pack-rf-emcomm/SKILL.md").read_bytes(),
                (ROOT / ".claude/skills/pack-rf-emcomm/SKILL.md").read_bytes(),
            )

    def test_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", layout="user")
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_copilot_has_no_user_layout(self):
        with tempfile.TemporaryDirectory() as d:
            for adapter in ("copilot", "both"):
                with self.assertRaisesRegex(ValueError, "copilot"):
                    install(d, adapter, layout="user")

    def test_unknown_layout_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            install(d, "claude", layout="global")

    def test_pack_filter(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "claude", packs=["threat-model"], layout="user")
            self.assertEqual([f for f in files if f.startswith("skills/")], ["skills/pack-threat-model/SKILL.md"])
            with self.assertRaises(ValueError):
                install(d, "claude", packs=["../x"], layout="user")

    def test_collision_refuses_whole_install(self):
        with tempfile.TemporaryDirectory() as d:
            existing = Path(d) / "agents/reviewer.md"
            existing.parent.mkdir()
            existing.write_text("my own reviewer")
            with self.assertRaises(ValueError):
                install(d, "claude", True, layout="user")
            self.assertEqual(existing.read_text(), "my own reviewer")
            self.assertFalse((Path(d) / "skills").exists())

    def test_update_replaces_marker_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            agent = Path(d) / "agents/reviewer.md"
            agent.write_text(agent.read_text().replace("(release ", "(release 0.0.0-", 1))
            with self.assertRaises(ValueError):
                install(d, "claude", True, layout="user")  # still a collision without --update
            install(d, "claude", True, update=True, layout="user")
            self.assertEqual(agent.read_bytes(), (ROOT / ".claude/agents/reviewer.md").read_bytes())
            mine = Path(d) / "skills/pack-threat-model/SKILL.md"
            mine.write_text("hand written\n")
            with self.assertRaises(ValueError):
                install(d, "claude", True, update=True, layout="user")
            self.assertEqual(mine.read_text(), "hand written\n")

    def test_symlinked_directory_and_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "dotfiles"
            project.mkdir()
            elsewhere = Path(d) / "elsewhere"
            elsewhere.mkdir()
            (project / "agents").symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(ValueError):
                install(project, "claude", True, layout="user")
            self.assertEqual(list(elsewhere.iterdir()), [])
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            agent = Path(d) / "agents/reviewer.md"
            other = Path(d) / "other.md"
            other.write_text(agent.read_text())
            agent.unlink()
            agent.symlink_to(other)
            with self.assertRaises(ValueError):
                install(d, "claude", True, update=True, layout="user")

    def test_symlinked_target_refused(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            link = Path(d) / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                install(link, "claude", layout="user")

    def test_target_inside_repository_refused(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            sub = fx / "scratch"
            sub.mkdir()
            for layout in ("user", "project"):
                with self.assertRaisesRegex(ValueError, "target cannot be"):
                    install(sub, "claude", True, root=fx, layout=layout)
                with self.assertRaisesRegex(ValueError, "target cannot be"):
                    install(fx, "claude", root=fx, layout=layout)
            self.assertEqual(list(sub.iterdir()), [])

    def test_write_guard_refuses_a_destination_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            with self.assertRaises(ValueError):
                installer.assert_outside_repo(fx / "agents/x.md", fx)
            installer.assert_outside_repo(Path(d) / "agents/x.md", fx)


class OverlayLoading(unittest.TestCase):
    def test_additions_and_new_names(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(
                d,
                roles={
                    "reviewer": "---\nname: reviewer\n---\n\nPrivate reviewer text.\n",
                    "auditor": role_text("auditor"),
                },
                packs={"private-ops": pack_text("private-ops")},
            )
            t = Path(d) / "dot"
            t.mkdir()
            files = install(t, "claude", True, layout="user", overlay=ov)
            self.assertIn("agents/auditor.md", files)
            self.assertIn("skills/pack-private-ops/SKILL.md", files)
            self.assertEqual(len(files), len(ROLE_NAMES) + 1 + len(PACK_NAMES) + 1)
            self.assertIn("Private reviewer text.", (t / "agents/reviewer.md").read_text())
            self.assertNotIn("Private reviewer text.", (t / "agents/implementer.md").read_text())
            self.assertEqual(
                (t / "agents/implementer.md").read_bytes(), (ROOT / ".claude/agents/implementer.md").read_bytes()
            )
            for rel in ("agents/reviewer.md", "agents/auditor.md", "skills/pack-private-ops/SKILL.md"):
                self.assertTrue(is_generated(t / rel), rel)
                self.assertIn(f"(release {version()})", (t / rel).read_text())

    def test_overlay_pack_additions_follow_the_public_pack(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, packs={"threat-model": "---\nname: threat-model\n---\n\nPrivate status note.\n"})
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user", overlay=ov)
            text = (t / "skills/pack-threat-model/SKILL.md").read_text()
            self.assertIn("Private status note.", text)
            self.assertTrue(text.index("Status:") < text.index("Private status note."))

    def test_marker_names_overlay_and_file_but_never_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(
                d, roles={"auditor": role_text("auditor")}, packs={"private-ops": pack_text("private-ops")}
            )
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user", overlay=ov)
            for rel, needle in (
                ("agents/auditor.md", "auditor.md"),
                ("skills/pack-private-ops/SKILL.md", "private-ops"),
            ):
                text = (t / rel).read_text()
                marker = next(line for line in text.splitlines() if GENERATED_MARK in line)
                self.assertIn("overlay", marker)
                self.assertIn(needle, marker)
                self.assertNotIn(str(ov), text)
                self.assertNotIn(str(ov.parent), text)
                self.assertNotIn(d, text)
                self.assertNotIn("private-overlay", text)
                self.assertNotIn("/tmp", marker)

    def test_apply_rechecks_symlinks_after_planning(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "dot"
            t.mkdir()
            elsewhere = Path(d) / "elsewhere"
            elsewhere.mkdir()
            items = installer._plan(t, "claude", layout="user")
            (t / "agents").symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(ValueError):
                installer._apply(items)
            self.assertEqual(list(elsewhere.iterdir()), [])

    def test_overlay_works_in_project_layout_with_copilot(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            t = Path(d) / "proj"
            t.mkdir()
            files = install(t, "both", True, overlay=ov)
            self.assertIn(".claude/agents/auditor.md", files)
            self.assertIn(".github/agents/auditor.agent.md", files)
            self.assertIn("overlay", (t / ".github/agents/auditor.agent.md").read_text())

    def test_pack_filter_knows_overlay_packs(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, packs={"private-ops": pack_text("private-ops")})
            files = install(Path(d), "claude", packs=["private-ops"], layout="user", overlay=ov)
            self.assertEqual([f for f in files if f.startswith("skills/")], ["skills/pack-private-ops/SKILL.md"])
            with self.assertRaises(ValueError):
                install(Path(d), "claude", packs=["nope"], layout="user", overlay=ov)

    def test_without_overlay_a_private_pack_name_is_unknown(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            install(d, "claude", packs=["private-ops"], layout="user")

    def test_privacy_sweep_does_not_apply_to_the_overlay(self):
        private = "Reach the box at 10.20." + "30.40 and read /ho" + "me/someone/notes."
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor") + "\n" + private + "\n"})
            files = install(Path(d), "claude", layout="user", overlay=ov)
            self.assertIn("agents/auditor.md", files)
            # The same text in the public tree is a validator failure.
            fx = copy_fixture(d)
            (fx / "roles/reviewer.md").write_text(PUBLIC_REVIEWER + "\n" + private + "\n")
            self.assertTrue(any("private-identifier" in e for e in validate(fx)))

    def test_overlay_rules_still_apply(self):
        bad = {
            "unknown tier": role_text("auditor").replace('tier: "standard"', 'tier: "top"'),
            "no report header": role_text("auditor").replace("{{fragment:report-header}}", "x"),
            "extra field": role_text("auditor").replace('tier: "standard"', 'tier: "standard"\nmodel: inherit'),
            "unknown fragment": role_text("auditor") + "\n{{fragment:nope}}\n",
            "name mismatch": role_text("other"),
            "bad name": role_text("../auditor"),
            "edit on new role": role_text("auditor"),
        }
        bad["edit on new role"] = re.sub(
            r"^tools: .*$", 'tools: ["read", "edit", "shell"]', bad["edit on new role"], flags=re.M
        )
        for label, text in bad.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                ov = make_overlay(d)
                (ov / "roles/auditor.md").write_text(text)
                (ov / "roles/auditor.md").chmod(0o644)
                with self.assertRaises(ValueError):
                    install(Path(d), "claude", layout="user", overlay=ov)

    def test_overlay_pack_rules_still_apply(self):
        bad = {
            "no verify section": pack_text("private-ops").replace("## How to verify", "## Other"),
            "bad date": pack_text("private-ops").replace('updated: "2026-10-08"', 'updated: "26"', 1),
            "name mismatch": pack_text("different"),
        }
        for label, text in bad.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                ov = make_overlay(d, packs={"private-ops": text})
                with self.assertRaises(ValueError):
                    install(Path(d), "claude", layout="user", overlay=ov)

    def test_overlay_must_be_outside_repository(self):
        with tempfile.TemporaryDirectory() as d:
            fx = copy_fixture(d)
            ov = make_overlay(fx, roles={"auditor": role_text("auditor")})
            t = Path(d) / "dot"
            t.mkdir()
            with self.assertRaisesRegex(ValueError, "repository"):
                install(t, "claude", root=fx, layout="user", overlay=ov)
        # An overlay that contains the repository is refused too.
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            fx = ov / "fixture"
            shutil.copytree(ROOT, fx, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            with self.assertRaisesRegex(ValueError, "repository"):
                overlay_mod.load_overlay(ov, root=fx)

    def test_empty_or_missing_overlay_refused(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "empty"
            empty.mkdir()
            empty.chmod(0o755)
            for p in (empty, Path(d) / "missing"):
                with self.assertRaises(ValueError):
                    overlay_mod.load_overlay(p)


class OverlayHostile(unittest.TestCase):
    def load(self, ov):
        return overlay_mod.load_overlay(ov)

    def read_direct(self, d, name):
        """_read_file is the last line of defense after the directory scan; exercise it on its own."""
        fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return overlay_mod._read_file(fd, name, "x")
        finally:
            os.close(fd)

    def test_read_file_alone_refuses_symlink_fifo_and_directory(self):
        import signal

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "real.md").write_text("x")
            (Path(d) / "real.md").chmod(0o644)
            (Path(d) / "link.md").symlink_to(Path(d) / "real.md")
            os.mkfifo(Path(d) / "pipe.md", 0o644)
            (Path(d) / "sub").mkdir()
            signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(AssertionError("blocked on a FIFO")))
            signal.alarm(5)
            try:
                for name in ("link.md", "pipe.md", "sub"):
                    with self.subTest(name), self.assertRaises(ValueError):
                        self.read_direct(d, name)
            finally:
                signal.alarm(0)
            self.assertEqual(self.read_direct(d, "real.md"), "x")

    def test_read_file_alone_bounds_a_file_that_lies_about_its_size(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "big.md").write_bytes(b"x" * (overlay_mod.MAX_FILE_BYTES + 10))
            (Path(d) / "big.md").chmod(0o644)
            real = os.fstat

            def lying(fd):
                st = real(fd)
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, 0, 0, 0, 0))

            with (
                mock.patch.object(overlay_mod.os, "fstat", side_effect=lying),
                self.assertRaisesRegex(ValueError, "large"),
            ):
                self.read_direct(d, "big.md")

    def test_non_utf8_body_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d)
            (ov / "roles/auditor.md").write_bytes(role_text("auditor").encode() + b"\n\xff\xfe bad\n")
            (ov / "roles/auditor.md").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                self.load(ov)

    def test_names_must_be_lowercase_words(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"Auditor": role_text("Auditor")})
            with self.assertRaises(ValueError):
                self.load(ov)
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, packs={"Private_Ops": pack_text("Private_Ops")})
            with self.assertRaises(ValueError):
                self.load(ov)

    def test_group_or_world_writable_refused(self):
        for target, mode in (("", 0o775), ("roles", 0o757), ("roles/auditor.md", 0o664), ("roles/auditor.md", 0o646)):
            with self.subTest(target=target, mode=oct(mode)), tempfile.TemporaryDirectory() as d:
                ov = make_overlay(d, roles={"auditor": role_text("auditor")})
                (ov / target).chmod(mode)
                with self.assertRaisesRegex(ValueError, "writable"):
                    self.load(ov)

    def test_pack_directory_permissions_checked(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, packs={"private-ops": pack_text("private-ops")})
            (ov / "packs/private-ops").chmod(0o777)
            with self.assertRaisesRegex(ValueError, "writable"):
                self.load(ov)

    def test_other_owner_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            with (
                mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1),
                self.assertRaisesRegex(ValueError, "owned"),
            ):
                self.load(ov)

    def test_symlinked_role_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            real = Path(d) / "real.md"
            real.write_text(role_text("linked"))
            (ov / "roles/linked.md").symlink_to(real)
            with self.assertRaisesRegex(ValueError, "symlink|regular"):
                self.load(ov)

    def test_symlinked_pack_file_and_dir_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d)
            real = Path(d) / "realpack"
            real.mkdir()
            (real / "PACK.md").write_text(pack_text("linked"))
            (ov / "packs/linked").symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.load(ov)
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, packs={"linked": pack_text("linked")})
            (ov / "packs/linked/PACK.md").unlink()
            other = Path(d) / "p.md"
            other.write_text(pack_text("linked"))
            (ov / "packs/linked/PACK.md").symlink_to(other)
            with self.assertRaises(ValueError):
                self.load(ov)

    def test_symlinked_overlay_root_or_ancestor_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            link = Path(d) / "ovlink"
            link.symlink_to(ov, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.load(link)
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.load(link / "..")  # resolves through the link component
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            (ov / "roles").rename(Path(d) / "elsewhere")
            (ov / "roles").symlink_to(Path(d) / "elsewhere", target_is_directory=True)
            with self.assertRaises(ValueError):
                self.load(ov)

    def test_fifo_refused_without_hanging(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            os.mkfifo(ov / "roles/pipe.md", 0o644)
            with self.assertRaisesRegex(ValueError, "regular"):
                self.load(ov)

    def test_oversized_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor") + "x" * (overlay_mod.MAX_FILE_BYTES + 1)})
            with self.assertRaisesRegex(ValueError, "large"):
                self.load(ov)

    def test_file_at_the_cap_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            base = role_text("auditor")
            pad = overlay_mod.MAX_FILE_BYTES - len(base.encode()) - 1
            ov = make_overlay(d, roles={"auditor": base + "\n" + "x" * pad})
            self.assertEqual((ov / "roles/auditor.md").stat().st_size, overlay_mod.MAX_FILE_BYTES)
            self.assertEqual([r["name"] for r in self.load(ov).roles], ["auditor"])

    def test_duplicate_frontmatter_key_refused(self):
        with tempfile.TemporaryDirectory() as d:
            text = role_text("auditor").replace('tier: "standard"', 'tier: "standard"\ntier: "cheap"', 1)
            ov = make_overlay(d, roles={"auditor": text})
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.load(ov)

    def test_deeply_nested_json_value_is_a_typed_error(self):
        with tempfile.TemporaryDirectory() as d:
            text = role_text("auditor").replace('tier: "standard"', "tier: " + "[" * 5000 + "]" * 5000, 1)
            ov = make_overlay(d, roles={"auditor": text})
            with self.assertRaises(ValueError):
                self.load(ov)

    def test_non_utf8_and_missing_frontmatter_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d)
            (ov / "roles/auditor.md").write_bytes(b"---\nname: auditor\n\xff\xfe\n---\n")
            (ov / "roles/auditor.md").chmod(0o644)
            with self.assertRaises(ValueError):
                self.load(ov)
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": "no frontmatter here\n"})
            with self.assertRaises(ValueError):
                self.load(ov)

    def test_unexpected_entries_refused(self):
        cases = {
            "top-level file": lambda ov: (ov / "notes.txt").write_text("x"),
            "non-md role": lambda ov: (ov / "roles/readme.txt").write_text("x"),
            "bad role file name": lambda ov: (ov / "roles/Bad Name.md").write_text("x"),
            "dir in roles": lambda ov: (ov / "roles/sub").mkdir(),
            "file in packs": lambda ov: (ov / "packs/stray.md").write_text("x"),
            "extra file in pack dir": lambda ov: (
                (ov / "packs/private-ops").mkdir(),
                (ov / "packs/private-ops/PACK.md").write_text(pack_text("private-ops")),
                (ov / "packs/private-ops/extra.md").write_text("x"),
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                ov = make_overlay(d, roles={"auditor": role_text("auditor")})
                mutate(ov)
                for p in ov.rglob("*"):
                    if not p.is_symlink():
                        p.chmod(0o755 if p.is_dir() else 0o644)
                with self.assertRaises(ValueError):
                    self.load(ov)

    def test_too_many_entries_refused(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d)
            for i in range(overlay_mod.MAX_ENTRIES + 1):
                (ov / f"roles/r{i}.md").write_text("x")
                (ov / f"roles/r{i}.md").chmod(0o644)
            with self.assertRaisesRegex(ValueError, "too many"):
                self.load(ov)

    def test_failed_overlay_install_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            os.mkfifo(ov / "roles/pipe.md", 0o644)
            t = Path(d) / "dot"
            t.mkdir()
            with self.assertRaises(ValueError):
                install(t, "claude", True, layout="user", overlay=ov)
            self.assertEqual(list(t.iterdir()), [])


class ListManaged(unittest.TestCase):
    def rows(self, t, **kw):
        return {r["path"]: r for r in list_managed(t, **kw)}

    def test_current_after_install(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            rows = self.rows(d, layout="user")
            self.assertEqual(len(rows), len(ROLE_NAMES) + len(PACK_NAMES))
            self.assertEqual({r["status"] for r in rows.values()}, {"current"})
            self.assertEqual({r["release"] for r in rows.values()}, {version()})
            self.assertEqual({r["source"] for r in rows.values()}, {"public"})

    def test_project_layout_lists_all_three_locations(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "both", True)
            rows = self.rows(d, layout="project")
            self.assertEqual(len(rows), len(ROLE_NAMES) * 2 + len(PACK_NAMES))
            self.assertIn(".github/agents/reviewer.agent.md", rows)

    def test_stale_differs_and_orphan(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            old = Path(d) / "agents/reviewer.md"
            old.write_text(old.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
            edited = Path(d) / "agents/implementer.md"
            edited.write_text(edited.read_text() + "\nlocal edit\n")
            orphan = Path(d) / "agents/gone.md"
            orphan.write_text(generate._seal("---\nname: gone\n---\n", "body\n", "roles/gone.md", version()))
            rows = self.rows(d, layout="user")
            self.assertEqual(rows["agents/reviewer.md"]["status"], "stale")
            self.assertEqual(rows["agents/reviewer.md"]["release"], "0.0.1")
            self.assertEqual(rows["agents/implementer.md"]["status"], "edited")
            self.assertEqual(rows["agents/gone.md"]["status"], "orphan")
            self.assertEqual(rows["agents/transcriber.md"]["status"], "current")

    def test_unmarked_files_and_symlinks_are_not_listed(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            (Path(d) / "agents/mine.md").write_text("mine\n")
            link = Path(d) / "agents/linked.md"
            link.symlink_to(Path(d) / "agents/reviewer.md")
            rows = self.rows(d, layout="user")
            self.assertNotIn("agents/mine.md", rows)
            self.assertNotIn("agents/linked.md", rows)

    def test_symlinked_scan_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / "other"
            install(other.mkdir() or other, "claude", True, layout="user")
            t = Path(d) / "dot"
            t.mkdir()
            (t / "agents").symlink_to(other / "agents", target_is_directory=True)
            self.assertEqual(list_managed(t, layout="user"), [])

    def test_hostile_release_text_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            agent = Path(d) / "agents/reviewer.md"
            agent.write_text(agent.read_text().replace(f"(release {version()})", "(release \x1b[31mEVIL)", 1))
            self.assertEqual(self.rows(d, layout="user")["agents/reviewer.md"]["release"], "unknown")

    def test_overlay_states(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(
                d, roles={"auditor": role_text("auditor"), "reviewer": "---\nname: reviewer\n---\n\nPrivate.\n"}
            )
            t = Path(d) / "dot"
            t.mkdir()
            install(t, "claude", True, layout="user", overlay=ov)
            with_ov = self.rows(t, layout="user", overlay=ov)
            self.assertEqual({r["status"] for r in with_ov.values()}, {"current"})
            self.assertEqual(with_ov["agents/auditor.md"]["source"], "overlay")
            self.assertEqual(with_ov["agents/implementer.md"]["source"], "public")
            without = self.rows(t, layout="user")
            self.assertEqual(without["agents/auditor.md"]["status"], "unchecked")
            self.assertEqual(without["agents/reviewer.md"]["status"], "unchecked")
            self.assertEqual(without["agents/implementer.md"]["status"], "current")
            # The overlay moves on: the installed copy is now behind it.
            (ov / "roles/auditor.md").write_text(role_text("auditor", "Changed."))
            self.assertEqual(self.rows(t, layout="user", overlay=ov)["agents/auditor.md"]["status"], "differs")
            # An overlay file that was removed leaves an orphan.
            (ov / "roles/auditor.md").unlink()
            self.assertEqual(self.rows(t, layout="user", overlay=ov)["agents/auditor.md"]["status"], "orphan")

    def test_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "claude", True, layout="user")
            before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(d).rglob("*") if p.is_file()}
            list_managed(d, layout="user")
            after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(d).rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(sorted(p.name for p in Path(d).rglob("*.doia-tmp")), [])

    def test_symlinked_target_refused_and_missing_target(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            link = Path(d) / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(ValueError):
                list_managed(link, layout="user")
            with self.assertRaises(ValueError):
                list_managed(Path(d) / "missing", layout="user")


class Cli(unittest.TestCase):
    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = installer.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_user_layout_preview_then_apply(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = self.run_main("--target", d, "--layout", "user")
            self.assertEqual(code, 0)
            self.assertIn("new: agents/reviewer.md", out)
            self.assertIn("Preview only", out)
            self.assertEqual(list(Path(d).iterdir()), [])
            code, out, _ = self.run_main("--target", d, "--layout", "user", "--apply")
            self.assertEqual(code, 0)
            self.assertTrue((Path(d) / "agents/reviewer.md").is_file())

    def test_copilot_user_layout_refused(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = self.run_main("--target", d, "--layout", "user", "--adapter", "both")
            self.assertEqual(code, 1)
            self.assertIn("copilot", err)

    def test_list_managed_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_main("--target", d, "--layout", "user", "--apply")
            code, out, _ = self.run_main("--target", d, "--layout", "user", "--list-managed")
            self.assertEqual(code, 0)
            self.assertIn("current", out)
            self.assertIn(version(), out)
            agent = Path(d) / "agents/reviewer.md"
            agent.write_text(agent.read_text().replace(f"(release {version()})", "(release 0.0.1)", 1))
            code, out, _ = self.run_main("--target", d, "--layout", "user", "--list-managed")
            self.assertEqual(code, 3)
            self.assertIn("stale", out)
            # Still read-only: nothing was repaired.
            self.assertIn("(release 0.0.1)", agent.read_text())

    def test_list_managed_rejects_write_flags(self):
        with tempfile.TemporaryDirectory() as d:
            for flag in ("--apply", "--update"):
                code, _, err = self.run_main("--target", d, "--list-managed", flag)
                self.assertEqual(code, 1)
                self.assertIn("read-only", err)

    def test_overlay_flag_end_to_end_and_marker_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            t = Path(d) / "dot"
            t.mkdir()
            code, out, _ = self.run_main("--target", str(t), "--layout", "user", "--overlay", str(ov), "--apply")
            self.assertEqual(code, 0)
            self.assertNotIn(str(ov), out)
            self.assertTrue((t / "agents/auditor.md").is_file())
            code, out, _ = self.run_main("--target", str(t), "--layout", "user", "--overlay", str(ov), "--list-managed")
            self.assertEqual(code, 0)
            self.assertIn("overlay", out)
            self.assertNotIn(str(ov), out)

    def test_plan_output_never_contains_overlay_path(self):
        with tempfile.TemporaryDirectory() as d:
            ov = make_overlay(d, roles={"auditor": role_text("auditor")})
            for _, dest, _ in plan(Path(d), "claude", layout="user", overlay=ov):
                self.assertNotIn(str(ov), str(dest))
            self.assertTrue(stat.S_ISDIR(ov.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
