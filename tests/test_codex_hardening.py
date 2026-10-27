"""Round-2 hardening of the Codex adapter and installer: filesystem root, invalid TOML, validator mutations,
preview provenance, and the honest trust-model wording. Stdlib only."""

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate  # noqa: E402  (path set up above)
import install as installer  # noqa: E402
import lib  # noqa: E402
import validate as validate_mod  # noqa: E402
from lib import PACK_NAMES  # noqa: E402

ADAPTERS = installer.ADAPTERS


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = installer.main(list(args))
    return code, out.getvalue(), err.getvalue()


def copy_fixture(base):
    out = Path(base) / "fixture"
    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return out


class FilesystemRoot(unittest.TestCase):
    def test_check_target_rejects_the_root(self):
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            installer._check_target("/", ROOT)

    def test_every_adapter_and_layout_rejects_the_root(self):
        for adapter in ADAPTERS:
            for layout in ("project", "user"):
                if layout == "user" and adapter in ("copilot", "both"):
                    continue  # refused earlier for another reason
                with self.assertRaisesRegex(ValueError, "filesystem root", msg=f"{adapter}/{layout}"):
                    installer.plan("/", adapter, layout=layout)

    def test_cli_refuses_the_root_with_exit_1(self):
        code, _, err = run_main("--target", "/", "--adapter", "codex")
        self.assertEqual(code, 1)
        self.assertIn("filesystem root", err)

    def test_dotdot_that_resolves_to_the_root_is_rejected(self):
        sneaky = os.path.join(tempfile.gettempdir(), "..")
        self.assertEqual(os.path.realpath(sneaky), os.path.realpath("/"))
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            installer._check_target(sneaky, ROOT)

    def test_a_symlink_to_the_root_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "rootlink"
            os.symlink("/", link)
            with self.assertRaisesRegex(ValueError, "symlink"):
                installer._check_target(link, ROOT)

    def test_a_normal_directory_and_a_home_like_directory_pass(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home-like"
            home.mkdir()
            self.assertEqual(installer._check_target(d, ROOT), Path(d).absolute())
            self.assertEqual(installer._check_target(home, ROOT), home.absolute())
            self.assertTrue(installer.plan(home, "codex", layout="user"))


class InvalidToml(unittest.TestCase):
    CONTROLS = [chr(c) for c in range(0x20) if chr(c) not in "\t\n"] + ["\x7f"]

    def render(self, **changes):
        role = dict(lib.load_roles(ROOT)[0])
        for k, v in changes.items():
            role[k] = role[k] + v
        return generate.render_codex_agent(role, generate.policies_block(ROOT), "0.0.0"), role

    def test_every_forbidden_control_character_is_a_typed_error_naming_the_source(self):
        self.assertGreaterEqual(len(self.CONTROLS), 30)  # loose collection, strict assertion
        for c in self.CONTROLS:
            with self.subTest(code=hex(ord(c))):
                with self.assertRaises(generate.CodexTomlError) as cm:
                    self.render(body=f"\nx{c}y\n")
                self.assertIn("adversarial-reviewer.md", str(cm.exception))

    def test_a_control_character_in_a_policy_or_description_is_refused_too(self):
        role = dict(lib.load_roles(ROOT)[0])
        with self.assertRaises(generate.CodexTomlError):
            generate.render_codex_agent(role, "## Shared policies\n\nbad \x01 policy", "0.0.0")
        role["description"] += "\x01"  # json.dumps escapes it, so it must still parse: no error
        text = generate.render_codex_agent(role, generate.policies_block(ROOT), "0.0.0")
        self.assertIn("\\u0001", text)

    def test_tab_newline_and_unicode_line_separators_are_fine(self):
        text, _ = self.render(body="\nta\tb c\n")
        self.assertIn("ta\tb c", text)

    def test_an_overlay_addition_with_a_control_character_is_refused(self):
        role = dict(lib.load_roles(ROOT)[0])
        with self.assertRaises(generate.CodexTomlError):
            generate.render_codex_agent(
                role, generate.policies_block(ROOT), "0.0.0", source="overlay additions role x.md", additions="a\x02b"
            )


class CheckCodexToml(unittest.TestCase):
    """_check_codex_toml is unreachable through render_codex_agent (the header is generated), so call it directly."""

    GOOD = "name = \"r\"\ndescription = \"d\"\ndeveloper_instructions = '''\nx\n'''\n"

    def check(self, text, name="r"):
        generate._check_codex_toml(text, name, "roles/r.md")

    def test_a_well_formed_file_passes(self):
        self.check(self.GOOD)

    def test_an_extra_key_is_refused(self):
        with self.assertRaisesRegex(generate.CodexTomlError, "roles/r.md"):
            self.check('model = "x"\n' + self.GOOD)

    def test_a_missing_key_is_refused(self):
        with self.assertRaisesRegex(generate.CodexTomlError, "exactly"):
            self.check('name = "r"\ndescription = "d"\n')

    def test_a_wrong_name_is_refused(self):
        with self.assertRaisesRegex(generate.CodexTomlError, "exactly"):
            self.check(self.GOOD, name="other")

    def test_malformed_toml_is_refused(self):
        with self.assertRaisesRegex(generate.CodexTomlError, "invalid TOML"):
            self.check("name = \n")


class ValidatorIsolation(unittest.TestCase):
    """drift() is patched to report nothing, so only the validator's own TOML checks can fail the run."""

    def run_validate(self, new_text):
        with tempfile.TemporaryDirectory() as d:
            r = copy_fixture(d)
            (r / ".codex/agents/reviewer.toml").write_text(new_text, encoding="utf-8")
            with mock.patch.object(validate_mod, "drift", return_value=[]):
                return validate_mod.validate(r)

    def test_malformed_toml_is_reported(self):
        errors = self.run_validate("name = \n")
        self.assertTrue(any("invalid TOML" in e for e in errors), errors)

    def test_valid_toml_with_an_extra_key_is_reported(self):
        good = (ROOT / ".codex/agents/reviewer.toml").read_text(encoding="utf-8")
        errors = self.run_validate('model = "x"\n' + good)
        self.assertTrue(any("unreviewed Codex fields" in e for e in errors), errors)

    def test_a_name_that_differs_from_the_file_is_reported(self):
        good = (ROOT / ".codex/agents/reviewer.toml").read_text(encoding="utf-8")
        errors = self.run_validate(good.replace('name = "reviewer"', 'name = "other"', 1))
        self.assertTrue(any("unreviewed Codex fields" in e for e in errors), errors)

    def test_the_unmodified_tree_is_clean_under_the_same_patch(self):
        with mock.patch.object(validate_mod, "drift", return_value=[]):
            self.assertEqual(validate_mod.validate(ROOT), [])


class PreviewProvenance(unittest.TestCase):
    def test_preview_shows_source_and_sha256_of_every_file(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = run_main("--target", d, "--adapter", "codex")
            self.assertEqual(code, 0)
            for name in PACK_NAMES:
                rel = f".agents/skills/pack-{name}/SKILL.md"
                digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
                line = next(ln for ln in out.splitlines() if rel in ln)
                self.assertIn("public", line)
                self.assertIn(f"sha256 {digest}", line)

    def test_overlay_sourced_files_are_labeled_overlay_with_their_digest(self):
        with tempfile.TemporaryDirectory() as d:
            ov = Path(d) / "ov"
            (ov / "roles").mkdir(parents=True)
            (ov / "packs/threat-model").mkdir(parents=True)
            (ov / "packs/threat-model/PACK.md").write_text(
                '---\nname: "threat-model"\n---\n\nPRIVATE-PACK-LINE\n', encoding="utf-8"
            )
            for p in [ov, *ov.rglob("*")]:
                p.chmod(0o755 if p.is_dir() else 0o644)
            t = Path(d) / "t"
            t.mkdir()
            code, out, _ = run_main("--target", str(t), "--adapter", "codex", "--overlay", str(ov))
            self.assertEqual(code, 0, out)
            rel = ".agents/skills/pack-threat-model/SKILL.md"
            line = next(ln for ln in out.splitlines() if rel in ln)
            self.assertIn("overlay", line)
            text = generate.render_skill(
                next(p for p in lib.load_packs(ROOT) if p["dir"] == "threat-model"),
                lib.version(ROOT),
                source="overlay additions pack threat-model",
                additions="PRIVATE-PACK-LINE",
            )
            self.assertIn(f"sha256 {hashlib.sha256(text.encode()).hexdigest()}", line)
            public_line = next(ln for ln in out.splitlines() if "pack-linux-platform" in ln)
            self.assertIn("public", public_line)


class TrustModelWording(unittest.TestCase):
    def read(self, rel):
        return " ".join((ROOT / rel).read_text(encoding="utf-8").split())

    def test_overlay_page_states_the_overlay_is_fully_trusted(self):
        text = self.read("docs/overlay-and-ownership.md")
        for needle in (
            "fully trusted",
            "do not stop a hostile overlay",
            "must not be able to write",
            "unreviewed unless you review them",
        ):
            self.assertIn(needle, text)

    def test_install_page_repeats_it_for_every_adapter_and_names_the_preview_as_the_control(self):
        text = self.read("docs/install.md")
        for needle in ("fully trusted", "every adapter", "sha256 of each file", "preview"):
            self.assertIn(needle, text)

    OLD_CLAIMS = (
        "never overwrites a file it did not generate",
        "never overwrite a file it did not generate",
        "only files it can prove it generated",
        "can prove it generated",
    )
    WORDING = "self-identify as generated may be overwritten"

    def test_the_old_ownership_claim_is_absent_from_every_location(self):
        pages = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "scripts/install.py"]
        pages += sorted((ROOT / "docs").glob("*.md"))
        self.assertGreaterEqual(len(pages), 13)  # loose collection, strict assertion
        for page in pages:
            text = " ".join(page.read_text(encoding="utf-8").split())
            for claim in self.OLD_CLAIMS:
                self.assertNotIn(claim, text, f"{page.name}: {claim}")

    def paragraph(self, rel, anchor):
        paras = [" ".join(p.split()) for p in (ROOT / rel).read_text(encoding="utf-8").split("\n\n")]
        hits = [p for p in paras if anchor in p]
        self.assertEqual(len(hits), 1, f"{rel}: anchor {anchor!r}")
        return hits[0]

    def test_every_paragraph_that_discusses_ownership_carries_the_honest_wording(self):
        places = (
            ("docs/install.md", "`--adapter` is `claude`"),
            ("docs/install.md", "The project install writes"),
            ("docs/install.md", "It writes `<target>/.codex/agents"),
            ("docs/overlay-and-ownership.md", "The marker line carries"),
        )
        for rel, anchor in places:
            self.assertIn(self.WORDING, self.paragraph(rel, anchor), f"{rel}: {anchor}")
        lines = [
            ln
            for ln in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
            if "hash is not a signature" in ln
        ]
        self.assertEqual(len(lines), 1)  # the one bullet that states the limit must carry the wording
        self.assertIn(self.WORDING, " ".join(lines[0].split()))


if __name__ == "__main__":
    unittest.main()
