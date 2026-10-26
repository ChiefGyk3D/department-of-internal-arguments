"""Codex adapter: custom agents (.codex/agents/*.toml) and skills (.agents/skills/*/SKILL.md). Stdlib only."""

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import generate  # noqa: E402  (path set up above)
import install as installer  # noqa: E402
import lib  # noqa: E402
import state_fixture  # noqa: E402  (path set up above)
from install import install, list_managed, plan  # noqa: E402
from lib import GENERATED_MARK, PACK_NAMES, ROLE_NAMES  # noqa: E402
from validate import validate  # noqa: E402

# Every test in this module runs against a private state directory (issue #40): no write reaches the home.
setUpModule = state_fixture.set_up_module
tearDownModule = state_fixture.tear_down_module

AGENTS = ".codex/agents"
SKILLS = ".agents/skills"


def load_toml(path):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


def copy_fixture(base):
    out = Path(base) / "fixture"
    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return out


class Generated(unittest.TestCase):
    def test_every_role_and_pack_is_generated_for_codex(self):
        agents = sorted((ROOT / AGENTS).glob("*.toml"))
        self.assertTrue(agents)  # loose collection, strict assertion
        self.assertEqual([a.stem for a in agents], sorted(ROLE_NAMES))
        skills = sorted((ROOT / SKILLS).glob("pack-*/SKILL.md"))
        self.assertEqual(len(skills), len(PACK_NAMES))

    def test_agent_files_are_valid_toml_with_only_the_documented_required_keys(self):
        for name in ROLE_NAMES:
            data = load_toml(ROOT / AGENTS / f"{name}.toml")
            # Never a model, sandbox or approval key: Codex ignores sandbox/approval in an agent file, and a
            # tier-to-OpenAI-model mapping is not verifiable here. The model is inherited and named in MODEL:.
            self.assertEqual(set(data), {"name", "description", "developer_instructions"}, name)
            self.assertEqual(data["name"], name)

    def test_instructions_carry_header_local_llm_line_and_every_policy(self):
        for name in ROLE_NAMES:
            text = load_toml(ROOT / AGENTS / f"{name}.toml")["developer_instructions"]
            for field in ("ROLE:", "MODEL:", "GATE:", "TREE:", "NOT VERIFIED:", "LOCAL-LLM:", "VERIFIED-AGAINST:"):
                self.assertIn(field, text, name)
            for title in ("Human approvals", "Quality gates", "Security boundaries", "Model routing"):
                self.assertIn(f"### {title}", text, name)
            self.assertNotIn("{{fragment:", text)

    def test_instructions_are_the_same_text_as_the_other_adapters(self):
        claude_body = lib.split_marker((ROOT / ".claude/agents/reviewer.md").read_text())[1].split("---\n", 2)[2]
        codex = load_toml(ROOT / AGENTS / "reviewer.toml")["developer_instructions"]
        for chunk in claude_body.strip().split("\n\n"):
            self.assertIn(chunk, codex)

    def test_capability_notes_say_what_codex_cannot_honor(self):
        notes = {n: load_toml(ROOT / AGENTS / f"{n}.toml")["developer_instructions"] for n in ROLE_NAMES}
        for n in ("reviewer", "adversarial-reviewer"):  # role has no edit capability
            self.assertIn("cannot remove", notes[n], n)
            self.assertIn("read-only", notes[n], n)
        self.assertNotIn("cannot remove", notes["implementer"])
        self.assertIn("no skills capability", notes["transcriber"])  # transcriber has no skills capability
        self.assertNotIn("no skills capability", notes["implementer"])
        for n in ROLE_NAMES:
            self.assertIn("UNVERIFIED", notes[n])  # nothing here has been run in Codex yet

    def test_marker_is_inside_the_instructions_and_owns_the_file(self):
        for name in ROLE_NAMES:
            f = ROOT / AGENTS / f"{name}.toml"
            parts = lib.split_marker(f.read_text())
            self.assertIsNotNone(parts, name)
            m, body = parts
            self.assertEqual(m.group(3), hashlib.sha256(body.encode()).hexdigest(), name)
            self.assertIn(GENERATED_MARK, load_toml(f)["developer_instructions"])
            self.assertEqual(installer.inspect(f)[0], "owned")

    def test_skills_match_the_claude_skill_bytes(self):
        for d in (ROOT / SKILLS).glob("pack-*"):
            self.assertEqual(
                (d / "SKILL.md").read_bytes(), (ROOT / ".claude/skills" / d.name / "SKILL.md").read_bytes()
            )

    def test_generator_has_no_drift_and_validate_passes(self):
        self.assertEqual(generate.drift(ROOT), [])
        self.assertEqual(validate(ROOT), [])

    def test_a_body_that_would_end_the_toml_string_is_refused(self):
        role = dict(lib.load_roles(ROOT)[0])
        role["body"] += "\nx ''' y\n"
        with self.assertRaisesRegex(ValueError, "three single quotes"):
            generate.render_codex_agent(role, generate.policies_block(ROOT), "0.0.0")

    def test_drift_is_reported_for_a_tampered_or_stray_codex_file(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_fixture(d)
            (r / AGENTS / "reviewer.toml").write_text("tampered\n")
            (r / SKILLS / "pack-threat-model" / "extra.md").write_text("x\n")
            problems = generate.drift(r)
            self.assertTrue(any("reviewer.toml" in p for p in problems))
            self.assertTrue(any("stray" in p and "extra.md" in p for p in problems))
            self.assertNotEqual(validate(r), [])


class Docs(unittest.TestCase):
    def test_eval_row_and_unverified_marking_exist(self):
        evals = (ROOT / "evals/behavioral-evals.md").read_text(encoding="utf-8")
        rows = [ln for ln in evals.splitlines() if ln.startswith("| ") and "Codex adapter" in ln]
        self.assertEqual(len(rows), 1)
        self.assertIn("LOCAL-LLM:", rows[0])
        install_doc = (ROOT / "docs/install.md").read_text(encoding="utf-8")
        self.assertIn("UNVERIFIED", install_doc)
        self.assertIn("https://learn.chatgpt.com/docs/agent-configuration/agents-md", install_doc)
        self.assertIn("retrieved 2026-10-26", install_doc)

    def test_changelog_has_a_codex_entry_after_0_3_1(self):
        # Everything newer than 0.3.1 (Unreleased plus the 0.4.0 section, which is
        # where Unreleased moves at release time); older sections do not count.
        log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        newer = log.split("## [Unreleased]", 1)[1].split("\n## [0.3.1]", 1)[0]
        self.assertIn("Codex adapter", newer)


class Installer(unittest.TestCase):
    def test_preview_lists_codex_files_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            rows = plan(d, "codex")
            dests = sorted(str(r[1].relative_to(d)) for r in rows)
            want = sorted(
                [f"{AGENTS}/{n}.toml" for n in ROLE_NAMES] + [f"{SKILLS}/pack-{n}/SKILL.md" for n in PACK_NAMES]
            )
            self.assertEqual(dests, want)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_apply_is_byte_identical_and_update_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "codex", True)
            for n in ROLE_NAMES:
                self.assertEqual(
                    (Path(d) / AGENTS / f"{n}.toml").read_bytes(), (ROOT / AGENTS / f"{n}.toml").read_bytes()
                )
            actions = {a for _, _, a in plan(d, "codex", update=True)}
            self.assertEqual(actions, {"unchanged"})

    def test_pack_selection_limits_skills(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "codex", True, packs=["threat-model"])
            self.assertEqual(sum("SKILL.md" in f for f in files), 1)

    def test_whole_install_refused_on_any_collision_and_nothing_written(self):
        with tempfile.TemporaryDirectory() as d:
            mine = Path(d) / AGENTS / "reviewer.toml"
            mine.parent.mkdir(parents=True)
            mine.write_text("mine\n")
            with self.assertRaisesRegex(ValueError, "collision"):
                install(d, "codex", True)
            self.assertEqual(mine.read_text(), "mine\n")
            self.assertEqual(sorted(p.name for p in mine.parent.iterdir()), ["reviewer.toml"])
            self.assertFalse((Path(d) / SKILLS).exists())

    def test_unmarked_file_is_a_collision_even_with_update_and_force(self):
        with tempfile.TemporaryDirectory() as d:
            mine = Path(d) / AGENTS / "reviewer.toml"
            mine.parent.mkdir(parents=True)
            mine.write_text("mine\n")
            for kw in ({}, {"force_edited": True}):
                with self.assertRaisesRegex(ValueError, "not a generated file"):
                    install(d, "codex", True, update=True, **kw)
            self.assertEqual(mine.read_text(), "mine\n")

    def test_hash_ownership_edited_file_refused_then_forced(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "codex", True)
            f = Path(d) / AGENTS / "implementer.toml"
            edited = f.read_text().replace("You implement", "You freely implement", 1)
            self.assertNotEqual(edited, f.read_text())
            f.write_text(edited)
            with self.assertRaisesRegex(ValueError, "edited"):
                install(d, "codex", True, update=True)
            self.assertEqual(f.read_text(), edited)
            install(d, "codex", True, update=True, force_edited=True)
            self.assertEqual(f.read_bytes(), (ROOT / AGENTS / "implementer.toml").read_bytes())

    def test_forged_marker_is_not_ownership(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "codex", True)
            f = Path(d) / AGENTS / "reviewer.toml"
            forged = f"x = 1\n# <!-- {GENERATED_MARK} (release forged) -->\n"
            f.write_text(forged)
            with self.assertRaises(ValueError):
                install(d, "codex", True, update=True)
            self.assertEqual(f.read_text(), forged)

    def test_symlink_at_a_destination_is_a_collision_and_its_target_is_untouched(self):
        # Proves the collision logic only (it refuses before ensure_safe is reached); ensure_safe is proven below.
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / "outside.toml"
            outside.write_text("keep\n")
            t = Path(d) / "t"
            (t / AGENTS).mkdir(parents=True)
            os.symlink(outside, t / AGENTS / "reviewer.toml")
            for update in (False, True):
                with self.assertRaises(ValueError):
                    install(t, "codex", True, update=update)
            self.assertEqual(outside.read_text(), "keep\n")

    def test_dangling_symlink_as_a_directory_component_is_refused_by_ensure_safe(self):
        # The destination itself does not exist, so no collision rule applies: only ensure_safe can say "symlink".
        for sub in (".codex", ".agents", ".codex/agents"):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d) / "t"
                t.mkdir()
                link = t / sub
                link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(Path(d) / "does-not-exist", link)
                for apply in (False, True):
                    with self.assertRaisesRegex(ValueError, "symlink destination refused", msg=sub):
                        install(t, "codex", apply)
                self.assertFalse((Path(d) / "does-not-exist").exists())

    def test_symlinked_parent_directory_refused(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            t = Path(d) / "t"
            t.mkdir()
            os.symlink(real, t / ".codex")
            with self.assertRaisesRegex(ValueError, "symlink"):
                install(t, "codex", True)
            self.assertEqual(list(real.iterdir()), [])

    def test_symlinked_target_and_repo_target_refused(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real"
            real.mkdir()
            link = Path(d) / "link"
            os.symlink(real, link)
            with self.assertRaisesRegex(ValueError, "symlink"):
                install(link, "codex", True)
            with self.assertRaisesRegex(ValueError, "repository"):
                install(ROOT, "codex", True)
            with self.assertRaisesRegex(ValueError, "repository"):
                install(ROOT / "docs", "codex", True)

    def test_user_layout_is_accepted_for_codex_and_uses_home_relative_paths(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "codex", True, layout="user")
            self.assertIn(f"{AGENTS}/reviewer.toml", files)
            self.assertIn(f"{SKILLS}/pack-threat-model/SKILL.md", files)
            self.assertEqual({a for _, _, a in plan(d, "codex", update=True, layout="user")}, {"unchanged"})

    def test_user_layout_still_refuses_copilot(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(ValueError, "copilot"):
            install(d, "copilot", layout="user")

    def test_existing_adapters_are_unchanged_by_the_new_one(self):
        with tempfile.TemporaryDirectory() as d:
            files = install(d, "both")
            self.assertFalse(any(f.startswith((".codex", ".agents")) for f in files))

    def test_cli_adapter_choice_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = run_main("--target", d, "--adapter", "codex")
            self.assertEqual(code, 0)
            self.assertIn("Preview only", out)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual(run_main("--target", d, "--adapter", "codex", "--apply")[0], 0)
            self.assertEqual(run_main("--target", d, "--adapter", "codex", "--apply")[0], 1)  # collision

    def test_list_managed_sees_codex_files_current_and_drift(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "codex", True)
            rows = {r["path"]: r["status"] for r in list_managed(d)}
            self.assertEqual(len(rows), len(ROLE_NAMES) + len(PACK_NAMES))
            self.assertEqual(set(rows.values()), {"current"})
            f = Path(d) / AGENTS / "reviewer.toml"
            f.write_text(f.read_text() + "# mine\n")
            rows = {r["path"]: r["status"] for r in list_managed(d)}
            self.assertEqual(rows[f"{AGENTS}/reviewer.toml"], "edited")
            self.assertEqual(run_main("--target", d, "--list-managed")[0], 3)

    def test_list_managed_user_layout_sees_codex_files(self):
        with tempfile.TemporaryDirectory() as d:
            install(d, "codex", True, layout="user")
            rows = list_managed(d, layout="user")
            self.assertEqual(len(rows), len(ROLE_NAMES) + len(PACK_NAMES))
            self.assertEqual({r["status"] for r in rows}, {"current"})


class Overlay(unittest.TestCase):
    def make(self, base, roles=None, packs=None):
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

    def test_public_name_is_append_only_for_roles_and_packs(self):
        with tempfile.TemporaryDirectory() as d:
            ov = self.make(
                d,
                roles={"reviewer": '---\nname: "reviewer"\n---\n\nPRIVATE-ROLE-LINE\n'},
                packs={"threat-model": '---\nname: "threat-model"\n---\n\nPRIVATE-PACK-LINE\n'},
            )
            t = Path(d) / "t"
            t.mkdir()
            install(t, "codex", True, overlay=ov)
            agent = load_toml(t / AGENTS / "reviewer.toml")
            public = load_toml(ROOT / AGENTS / "reviewer.toml")
            self.assertEqual(set(agent), {"name", "description", "developer_instructions"})
            self.assertEqual(agent["description"], public["description"])
            self.assertIn("PRIVATE-ROLE-LINE", agent["developer_instructions"])
            self.assertIn("## Maintainer additions (private overlay)", agent["developer_instructions"])
            self.assertIn("overlay", agent["developer_instructions"].splitlines()[0])
            pub_instr = public["developer_instructions"].splitlines()[1:]
            self.assertEqual(agent["developer_instructions"].splitlines()[1 : len(pub_instr) + 1], pub_instr)
            self.assertIn("PRIVATE-PACK-LINE", (t / SKILLS / "pack-threat-model/SKILL.md").read_text())
            self.assertEqual((t / AGENTS / "reviewer.toml").stat().st_mode & 0o777, 0o600)  # overlay text is private
            self.assertEqual({r["status"] for r in list_managed(t, overlay=ov)}, {"current"})

    def test_new_overlay_role_is_installed_whole(self):
        text = (
            (ROOT / "roles/reviewer.md").read_text(encoding="utf-8").replace('name: "reviewer"', 'name: "auditor"', 1)
        )
        with tempfile.TemporaryDirectory() as d:
            ov = self.make(d, roles={"auditor": text})
            t = Path(d) / "t"
            t.mkdir()
            files = install(t, "codex", True, overlay=ov)
            self.assertIn(f"{AGENTS}/auditor.toml", files)
            self.assertEqual(load_toml(t / AGENTS / "auditor.toml")["name"], "auditor")


if __name__ == "__main__":
    unittest.main()
