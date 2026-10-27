"""The tier-to-model export (issue #53): generation, drift check, refusal of floating aliases, strict reader."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_models as em  # noqa: E402  (path set up above)
import generate  # noqa: E402
import validate  # noqa: E402
from lib import TIER_MODEL  # noqa: E402

EXPORT = "export/model-pins.json"
BAD_IDS = {
    "alias": "sonnet",
    "latest": "latest",
    "alias with latest": "claude-sonnet-latest",
    "empty": "",
    "trailing newline": "claude-sonnet-5-5\n",
    "unicode lookalike": "claude-ѕonnet-5-5",  # Cyrillic dze in place of s
    "uppercase": "Claude-sonnet-5-5",
    "family only": "claude-sonnet",
    "trailing dash": "claude-sonnet-5-",
    "leading space": " claude-sonnet-5-5",
    "fullwidth digit": "claude-sonnet-５-5",
    "not a string": 5,
}


def copy_tree(dst):
    """A scratch copy of the repository sources (no .git, no caches) so a test can edit one file and run the CLI."""
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".venv")
    shutil.copytree(ROOT, dst, ignore=ignore, symlinks=True, dirs_exist_ok=True)
    return Path(dst)


def run(root, *args):
    return subprocess.run(
        [sys.executable, str(Path(root) / "scripts/export_models.py"), *args],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=60,
    )


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = copy_tree(Path(self._tmp.name) / "repo")

    def edit(self, rel, old, new):
        p = self.root / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1), encoding="utf-8")


class Content(unittest.TestCase):
    def test_committed_export_is_current_and_reads_strictly(self):
        self.assertEqual(em.drift(ROOT), [])
        data = em.read_pins(ROOT / EXPORT)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["release"], (ROOT / "VERSION").read_text().strip())

    def test_tiers_are_the_pins_with_family_and_roles(self):
        data = em.read_pins(ROOT / EXPORT)
        self.assertEqual({t: v["model"] for t, v in data["tiers"].items()}, TIER_MODEL)
        self.assertEqual(data["tiers"]["standard"]["family"], "sonnet")
        self.assertEqual(data["tiers"]["cheap"]["family"], "haiku")
        self.assertEqual(data["tiers"]["cheap"]["roles"], ["transcriber"])
        self.assertEqual(data["tiers"]["standard"]["roles"], ["adversarial-reviewer", "implementer", "reviewer"])
        self.assertEqual(data["roles"]["transcriber"], "cheap")

    def test_report_header_order(self):
        self.assertEqual(
            em.read_pins(ROOT / EXPORT)["report_header"],
            [
                "ROLE",
                "MODEL",
                "HEAD",
                "BASE",
                "GATE",
                "OTHER GATES",
                "PACKS LOADED",
                "TREE",
                "NOT VERIFIED",
                "LOCAL-LLM",
                "VERIFIED-AGAINST",
                "DISCREPANCIES",
            ],
        )

    def test_bytes_are_deterministic_ascii_sorted_with_trailing_newline(self):
        raw = (ROOT / EXPORT).read_bytes()
        raw.decode("ascii")
        self.assertTrue(raw.endswith(b"}\n") and not raw.endswith(b"\n\n"))
        self.assertEqual(em.generate_text(ROOT).encode("ascii"), raw)
        self.assertEqual(em.generate_text(ROOT), em.generate_text(ROOT))
        self.assertIn('\n  "release"', raw.decode())
        top = list(json.loads(raw))
        self.assertEqual(top, sorted(top))

    def test_no_private_data(self):
        self.assertEqual([h for h in validate.privacy_hits(ROOT) if h[0].startswith("export/")], [])
        text = (ROOT / EXPORT).read_text()
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn("/ho" + "me/", text)  # built in pieces: the sweep would flag the literal


class Drift(Scratch):
    def test_check_green_on_clean_copy(self):
        r = run(self.root, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def assert_stale(self):
        r = run(self.root, "--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(EXPORT, r.stdout + r.stderr)

    def test_edited_tier_model_is_stale(self):
        self.edit("scripts/lib.py", '"claude-haiku-5-5"', '"claude-haiku-5-6"')
        self.assert_stale()

    def test_edited_role_tier_is_stale(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', 'tier: "cheap"')
        self.assert_stale()

    def test_edited_report_header_is_stale(self):
        self.edit("fragments/report-header.md", "PACKS LOADED:", "PACKS USED:")
        self.assert_stale()

    def test_missing_file_is_stale(self):
        (self.root / EXPORT).unlink()
        self.assert_stale()

    def test_write_repairs_and_check_goes_green(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', 'tier: "cheap"')
        self.assertEqual(run(self.root, "--write").returncode, 0)
        self.assertEqual(run(self.root, "--check").returncode, 0)

    def test_generate_check_names_the_stale_file(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', 'tier: "cheap"')
        # roles drift too, so only assert the export is among the named files
        self.assertTrue(any(EXPORT in p for p in generate.drift(self.root)))

    def test_generate_write_writes_the_export(self):
        (self.root / EXPORT).unlink()
        generate.write(self.root)
        self.assertEqual((self.root / EXPORT).read_bytes(), (ROOT / EXPORT).read_bytes())

    def test_validate_flags_a_tampered_export(self):
        p = self.root / EXPORT
        p.write_text(p.read_text().replace("claude-haiku-5-5", "haiku"), encoding="utf-8")
        errors = validate.validate(self.root)
        self.assertTrue(any(EXPORT in e for e in errors), errors)


class Refusals(Scratch):
    def test_floating_aliases_refused_by_builder(self):
        for label, bad in BAD_IDS.items():
            with self.subTest(label):
                with self.assertRaises(em.ModelPinsError):
                    em.build(ROOT, tier_model={"standard": bad, "cheap": "claude-haiku-5-5"})

    def test_floating_alias_refused_by_cli_writes_nothing_exit_1(self):
        before = (self.root / EXPORT).read_bytes()
        for label, bad in BAD_IDS.items():
            if not isinstance(bad, str) or "\n" in bad or "\\" in bad:
                continue  # these reach the CLI through the builder tests; the CLI test edits source text
            with self.subTest(label):
                self.edit("scripts/lib.py", '"claude-sonnet-5-5"', json.dumps(bad))
                r = run(self.root, "--write")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual((self.root / EXPORT).read_bytes(), before)
                self.assertEqual(sorted(p.name for p in (self.root / "export").iterdir()), ["model-pins.json"])
                shutil.copy(ROOT / "scripts/lib.py", self.root / "scripts/lib.py")

    def test_trailing_newline_pin_refused_by_cli(self):
        self.edit("scripts/lib.py", '"claude-sonnet-5-5"', '"claude-sonnet-5-5\\n"')
        before = (self.root / EXPORT).read_bytes()
        r = run(self.root, "--write")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual((self.root / EXPORT).read_bytes(), before)

    def test_tier_with_no_pin(self):
        pins = {"standard": "claude-sonnet-5-5", "cheap": None}
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(ROOT, tier_model=pins)
        self.assertIn("no model pin", str(cm.exception))
        # a tier that a role uses but TIER_MODEL lacks is refused too
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(ROOT, tier_model={"standard": "claude-sonnet-5-5"})
        self.assertIn("is not a tier", str(cm.exception))

    def test_role_with_a_tier_that_is_not_a_tier(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', 'tier: "premium"')
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(self.root)
        self.assertIn("is not a tier", str(cm.exception))
        r = run(self.root, "--write")
        self.assertEqual(r.returncode, 1)

    def test_duplicate_role_name(self):
        shutil.copy(self.root / "roles/reviewer.md", self.root / "roles/reviewer-copy.md")
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(self.root)
        self.assertIn("duplicate role name", str(cm.exception))
        self.assertEqual(run(self.root, "--check").returncode, 1)

    def test_unreadable_sources_fail_closed(self):
        (self.root / "fragments/report-header.md").unlink()
        self.assertEqual(run(self.root, "--write").returncode, 1)
        (self.root / "VERSION").unlink()
        self.assertEqual(run(self.root, "--write").returncode, 1)

    def test_malformed_role_frontmatter(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', "tier: 5")
        with self.assertRaises(em.ModelPinsError):
            em.build(self.root)

    def test_bad_role_name_and_bad_version(self):
        self.edit("roles/reviewer.md", 'name: "reviewer"', 'name: "Re viewer"')
        with self.assertRaises(em.ModelPinsError):
            em.build(self.root)
        (self.root / "VERSION").write_text("v1\n")
        with self.assertRaises(em.ModelPinsError):
            em.build(ROOT, version_text="v1")

    def test_report_header_malformed(self):
        for bad in ("", "no field here\n", "ROLE: a\nROLE: b\n"):
            with self.subTest(bad=bad):
                (self.root / "fragments/report-header.md").write_text(bad)
                with self.assertRaises(em.ModelPinsError):
                    em.build(self.root)

    def test_echoed_text_is_escaped(self):
        self.edit("roles/reviewer.md", 'tier: "standard"', 'tier: "x\\u001b[31my"')
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(self.root)
        msg = str(cm.exception)
        self.assertNotIn("\x1b", msg)
        self.assertTrue(msg.isascii())
        with self.assertRaises(em.ModelPinsError) as cm:
            em.build(ROOT, tier_model={"standard": "claude-‮sonnet\x1b", "cheap": "claude-haiku-5-5"})
        self.assertTrue(str(cm.exception).isascii())
        self.assertNotIn("\x1b", str(cm.exception))

    def test_write_is_atomic_temp_then_rename(self):
        src = (ROOT / "scripts/export_models.py").read_text()
        self.assertIn("os.replace", src)
        self.assertIn("mkstemp", src)


class Reader(Scratch):
    def good(self):
        return json.loads((ROOT / EXPORT).read_text())

    def put(self, data=None, text=None):
        p = self.root / "model-pins-test.json"
        p.write_text(text if text is not None else em.render(data), encoding="utf-8")
        return p

    def test_round_trip(self):
        self.assertEqual(em.read_pins(self.put(self.good())), self.good())

    def test_unknown_keys_refused_at_every_level(self):
        for path in ((), ("tiers",), ("tiers", "standard")):
            with self.subTest(path=path):
                d = self.good()
                node = d
                for k in path:
                    node = node[k]
                node["extra"] = 1
                with self.assertRaises(em.ModelPinsError):
                    em.read_pins(self.put(d))

    def test_missing_keys_refused(self):
        for key in ("schema", "release", "tiers", "roles", "report_header"):
            with self.subTest(key=key):
                d = self.good()
                del d[key]
                with self.assertRaises(em.ModelPinsError):
                    em.read_pins(self.put(d))

    def test_wrong_types_and_values(self):
        cases = {
            "schema bool": lambda d: d.update(schema=True),
            "schema 2": lambda d: d.update(schema=2),
            "schema string": lambda d: d.update(schema="1"),
            "release": lambda d: d.update(release="latest"),
            "model alias": lambda d: d["tiers"]["standard"].update(model="sonnet"),
            "model latest": lambda d: d["tiers"]["standard"].update(model="claude-sonnet-latest"),
            "family mismatch": lambda d: d["tiers"]["standard"].update(family="haiku"),
            "role unknown tier": lambda d: d["roles"].update(reviewer="premium"),
            "roles inconsistent": lambda d: d["tiers"]["cheap"].update(roles=[]),
            "roles unsorted": lambda d: d["tiers"]["standard"].update(roles=["reviewer", "implementer"]),
            "header duplicate": lambda d: d.update(report_header=["ROLE", "ROLE"]),
            "header empty": lambda d: d.update(report_header=[]),
            "header not list": lambda d: d.update(report_header="ROLE"),
            "tiers empty": lambda d: d.update(tiers={}),
            "tiers null": lambda d: d.update(tiers=None),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                d = self.good()
                mutate(d)
                with self.assertRaises(em.ModelPinsError):
                    em.read_pins(self.put(d))

    def test_malformed_json(self):
        good = em.render(self.good())
        for label, text in {
            "duplicate key": good.replace('"schema": 1,', '"schema": 1,\n  "schema": 1,', 1),
            "NaN": good.replace('"schema": 1', '"schema": NaN', 1),
            "trailing garbage": good + "x",
            "not an object": "[]\n",
            "empty": "",
            "non canonical": json.dumps(json.loads(good)) + "\n",
            "no trailing newline": good.rstrip("\n"),
            "non ascii": good.replace('"cheap"', '"chéap"', 1),
            "crlf": good.replace("\n", "\r\n"),
        }.items():
            with self.subTest(label):
                with self.assertRaises(em.ModelPinsError) as cm:
                    em.read_pins(self.put(text=text))
                reason = {"duplicate key": "duplicate key", "NaN": "non-finite"}.get(label)
                if reason:  # pin the specific layer, not just "some check failed"
                    self.assertIn(reason, str(cm.exception))

    def test_file_level_refusals(self):
        with self.assertRaises(em.ModelPinsError):
            em.read_pins(self.root / "absent.json")
        target = self.put(self.good())
        link = self.root / "link.json"
        os.symlink(target, link)
        with self.assertRaises(em.ModelPinsError):
            em.read_pins(link)
        with self.assertRaises(em.ModelPinsError):
            em.read_pins(self.root)
        big = self.root / "big.json"
        big.write_text(" " * (em.MAX_BYTES + 1))
        with self.assertRaises(em.ModelPinsError) as cm:
            em.read_pins(big)
        self.assertIn("is over", str(cm.exception))
        bad = self.root / "bad.json"
        bad.write_bytes(b"\xff\xfe")
        with self.assertRaises(em.ModelPinsError):
            em.read_pins(bad)

    def test_reader_error_escapes_echoed_text(self):
        d = self.good()
        d["tiers"]["standard"]["model"] = "claude-\x1b[31m"
        with self.assertRaises(em.ModelPinsError) as cm:
            em.read_pins(self.put(d))
        self.assertNotIn("\x1b", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
