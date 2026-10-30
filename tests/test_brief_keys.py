"""The brief's machine-readable `role:` and `model:` lines (issue #55): template, export and validator agree.

`parse_brief` below is the reference reader: the contract a consumer (SAFO's compare mode) must satisfy. It is test
code and is not shipped. It fails closed: anything it cannot read as exactly one `role:` and one `model:` is None."""

import re
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_models as em  # noqa: E402  (path set up above)
import generate  # noqa: E402
import validate  # noqa: E402

TEMPLATE = "templates/brief.md"
KEY_LINE = re.compile(r"(role|model):")
ROLE_VALUE = re.compile(r"[a-z][a-z-]{0,39}")
MODEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}")
LOWER_KEY_LINE = re.compile(r"[a-z][a-z-]*:")


def parse_brief(text):
    """{'role': ..., 'model': ...} or None. Exactly one of each at line start, outside code fences, one plain value."""
    if "\r" in text or any(unicodedata.category(c) in ("Cf", "Cc") and c not in "\n\t" for c in text):
        return None  # CRLF, zero-width, bidi or control characters: ambiguous bytes, not read
    found, in_fence = {}, False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        m = KEY_LINE.match(line)
        if in_fence or m is None:
            continue
        key = m.group(1)
        if key in found:
            return None  # duplicated, even with the same value
        if not line.startswith(f"{key}: "):
            return None  # `role:` alone, `role:x` or two spaces: not the template's shape
        found[key] = line[len(key) + 2 :]
    if set(found) != {"role", "model"}:
        return None
    if not ROLE_VALUE.fullmatch(found["role"]) or not MODEL_VALUE.fullmatch(found["model"]):
        return None  # empty, `a | b`, `a, b`, trailing space and anything else with more than one token
    return found


def comment_free(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


class ReferenceParser(unittest.TestCase):
    GOOD = "# Work brief\n\nrole: reviewer\nmodel: sonnet\n\n- Task / owner:\n"

    def test_accepts_a_filled_brief(self):
        self.assertEqual(parse_brief(self.GOOD), {"role": "reviewer", "model": "sonnet"})
        full = self.GOOD.replace("sonnet", "claude-sonnet-5-5")
        self.assertEqual(parse_brief(full)["model"], "claude-sonnet-5-5")

    def test_accepts_the_template_with_its_values(self):
        got = parse_brief(comment_free((ROOT / TEMPLATE).read_text(encoding="utf-8")))
        self.assertIsNotNone(got)

    def test_rejects_ambiguous_shapes(self):
        g = self.GOOD
        cases = {
            "two roles, different values": g + "role: implementer\n",
            "two roles, same value": g + "role: reviewer\n",
            "two models": g + "model: haiku\n",
            "role lists alternatives": g.replace("role: reviewer", "role: reviewer | implementer"),
            "model with a comma": g.replace("model: sonnet", "model: sonnet, haiku"),
            "model with a pipe": g.replace("model: sonnet", "model: sonnet|haiku"),
            "empty role": g.replace("role: reviewer", "role: "),
            "empty model": g.replace("model: sonnet", "model:"),
            "missing role": g.replace("role: reviewer\n", ""),
            "missing model": g.replace("model: sonnet\n", ""),
            "key inside a code fence": "```\nrole: reviewer\n```\nmodel: sonnet\n",
            "both keys only in a fence": "# x\n```\nrole: reviewer\nmodel: sonnet\n```\n",
            "key with leading spaces": g.replace("role: reviewer", "  role: reviewer"),
            "key with a leading tab": g.replace("role: reviewer", "\trole: reviewer"),
            "list item": g.replace("role: reviewer", "- role: reviewer"),
            "CRLF": g.replace("\n", "\r\n"),
            "zero-width space in the value": g.replace("reviewer", "review​er"),
            "zero-width space before the key": g.replace("role:", "​role:"),
            "bidi override": g.replace("sonnet", "‮sonnet"),
            "BOM": "﻿" + g,
            "uppercase key": g.replace("role:", "Role:"),
            "trailing space": g.replace("sonnet", "sonnet "),
            "two spaces after the colon": g.replace("role: reviewer", "role:  reviewer"),
            "inner space": g.replace("sonnet", "son net"),
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertIsNone(parse_brief(text))

    def test_uppercase_key_is_not_a_key(self):
        # `Role:` is not read as a key, so the brief has no role and is rejected (above); lowercase is the contract.
        self.assertIsNone(KEY_LINE.match("Role: reviewer"))


class TemplateLines(unittest.TestCase):
    def setUp(self):
        self.text = comment_free((ROOT / TEMPLATE).read_text(encoding="utf-8"))

    def test_exactly_one_role_and_one_model_line_at_line_start(self):
        lines = self.text.split("\n")
        self.assertEqual(sum(ln.startswith("role: ") for ln in lines), 1)
        self.assertEqual(sum(ln.startswith("model: ") for ln in lines), 1)

    def test_lines_sit_at_the_top_of_the_body(self):
        lines = [ln for ln in self.text.split("\n") if ln.strip()]
        self.assertEqual(lines[0], "# Work brief")
        self.assertTrue(lines[1].startswith("role: "))
        self.assertTrue(lines[2].startswith("model: "))

    def test_values_come_from_the_export(self):
        got = parse_brief(self.text)
        data = em.read_pins(ROOT / em.EXPORT_PATH)
        self.assertIn(got["role"], data["roles"])
        pins = {t["model"] for t in data["tiers"].values()} | {t["family"] for t in data["tiers"].values()}
        self.assertIn(got["model"], pins)

    def test_old_prose_line_stays(self):
        self.assertIn("Role agent and model:", self.text)

    def test_export_brief_keys_equal_template_keys_both_directions(self):
        data = em.read_pins(ROOT / em.EXPORT_PATH)
        template_keys = {ln.split(":")[0] for ln in self.text.split("\n") if LOWER_KEY_LINE.match(ln)}
        # `spend:` is a separate policy line (issue #66) with its own validator; it is not a machine-read brief key.
        self.assertEqual(template_keys - {"spend"}, set(data["brief_keys"]))
        self.assertEqual(data["brief_keys"], ["role", "model"])
        self.assertEqual(data["brief_keys"], list(em.BRIEF_KEYS))


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        ignore = shutil.ignore_patterns(".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".venv")
        self.root = Path(self._tmp.name) / "repo"
        shutil.copytree(ROOT, self.root, ignore=ignore, symlinks=True)

    def edit(self, rel, old, new):
        p = self.root / rel
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1), encoding="utf-8")

    def brief_problems(self):
        return [e for e in validate.validate(self.root) if TEMPLATE in e]


class Validator(Scratch):
    def test_clean_copy_has_no_brief_problem(self):
        self.assertEqual(self.brief_problems(), [])

    def test_missing_duplicated_or_alternative_lines_fail(self):
        cases = {
            "role missing": ("role: reviewer\n", ""),
            "model missing": ("model: sonnet\n", ""),
            "role duplicated": ("role: reviewer\n", "role: reviewer\nrole: implementer\n"),
            "model duplicated": ("model: sonnet\n", "model: sonnet\nmodel: haiku\n"),
            "role lists alternatives": ("role: reviewer", "role: reviewer | implementer"),
            "model has a comma": ("model: sonnet", "model: sonnet, haiku"),
            "model empty": ("model: sonnet", "model: "),
            "role is not a role": ("role: reviewer", "role: wizard"),
            "model is not a pin or family": ("model: sonnet", "model: gpt-9"),
            "key indented": ("role: reviewer", " role: reviewer"),
            "extra machine key": ("model: sonnet\n", "model: sonnet\ntier: standard\n"),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.edit(TEMPLATE, old, new)
                self.assertTrue(self.brief_problems(), label)

    def test_exact_model_id_is_accepted(self):
        self.edit(TEMPLATE, "model: sonnet", "model: claude-sonnet-5-5")
        self.assertEqual(self.brief_problems(), [])

    def test_template_only_in_a_comment_does_not_count(self):
        self.edit(TEMPLATE, "role: reviewer\nmodel: sonnet\n", "<!-- role: reviewer\nmodel: sonnet -->\n")
        self.assertTrue(self.brief_problems())


class ExportDrift(Scratch):
    def test_export_without_brief_keys_is_stale_and_unreadable(self):
        p = self.root / em.EXPORT_PATH
        p.write_text(p.read_text(encoding="utf-8").replace('  "brief_keys": [\n    "role",\n    "model"\n  ],\n', ""))
        self.assertTrue(generate.drift(self.root))
        with self.assertRaises(em.ModelPinsError):
            em.read_pins(p)

    def test_template_key_the_export_does_not_name_fails_validation(self):
        self.edit(TEMPLATE, "model: sonnet\n", "model: sonnet\ntier: standard\n")
        self.assertTrue(self.brief_problems())

    def test_reader_refuses_wrong_brief_keys(self):
        good = em.build()
        for label, bad in {
            "not a list": "role",
            "empty": [],
            "duplicate": ["role", "role"],
            "uppercase": ["Role", "model"],
            "not strings": [1, 2],
            "unknown key": ["role", "model", "tier"],
            "reordered": ["model", "role"],
        }.items():
            with self.subTest(label):
                d = dict(good, brief_keys=bad)
                with self.assertRaises(em.ModelPinsError):
                    em.validate_data(d)


if __name__ == "__main__":
    unittest.main()
