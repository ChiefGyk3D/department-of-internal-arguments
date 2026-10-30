"""Round 2 hardening for the behavioral-eval registry and matrix (issue #45): each test pins one review finding."""

import datetime
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import evals_matrix as em  # noqa: E402  (path set up above)
import generate  # noqa: E402
from test_evals_matrix import (  # noqa: E402
    HASH,
    base_case,
    copy_tree,
    line,
    local_record,
    mini_cases,
    parse,
    record,
    registry_text,
)
from validate import validate  # noqa: E402

JUDGED = {3, 6, 7, 11, 14, 15, 16, 26}


class GradingIsPinned(unittest.TestCase):
    def test_the_exact_judged_set(self):
        # Reclassifying a case is a decision, so it is a deliberate edit of this set and of the CHANGELOG.
        cases = em.load_registry(ROOT / "evals/cases.json")
        self.assertEqual({c["id"] for c in cases if c["grading"] == "judged"}, JUDGED)
        self.assertEqual({c["id"] for c in cases if c["grading"] == "mechanical"}, set(range(1, 34)) - JUDGED)

    def test_mechanical_reasons_state_the_negative_condition(self):
        by_id = {c["id"]: c["grading_reason"] for c in em.load_registry(ROOT / "evals/cases.json")}
        want = {
            2: ("reports the injection", "absent", "upload"),
            4: ("no git push", "unchanged", "refusal"),
            12: ("no new file", "redirect"),
            24: ("DISCREPANCIES: 1", "not applied or quoted"),
        }
        for n, parts in want.items():
            for part in parts:
                self.assertIn(part, by_id[n], f"case {n}")

    def test_judged_cases_cannot_pass_in_the_real_registry(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        for n in sorted(JUDGED):
            client = cases[n - 1]["clients"][0]
            row = record(case=n, client=client, outcome="pass")
            if client == "local":
                row = local_record(case=n, outcome="pass")
            with self.subTest(case=n), self.assertRaisesRegex(ValueError, "judged"):
                em.parse_record(line(row), 1, cases)

    def test_schema_doc_lists_the_judged_set(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("3, 6, 7, 11, 14, 15, 16 and 26", doc)


class GenerateWritesTheDoc(unittest.TestCase):
    def test_generate_write_regenerates_the_doc_from_the_registry(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/cases.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cases"][0]["expect"] = "A new expected behavior for the first row"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(em.doc_drift(r))
            generate.write(r)
            self.assertIn("A new expected behavior for the first row", (r / "evals/behavioral-evals.md").read_text())
            self.assertEqual(generate.drift(r), [])


class ExactModelGrammar(unittest.TestCase):
    def accepts(self, client, model):
        try:
            if client == "local":
                parse(local_record(model=model))
            else:  # copilot has no headless interface yet, so the only record it may hold is a hand run
                parse(record(client=client, model=model, outcome="needs-human" if client == "copilot" else "pass"))
        except ValueError:
            return False
        return True

    def test_documented_shapes_are_accepted(self):
        for client, model in (
            ("claude-code", "claude-sonnet-5-5"),
            ("claude-code", "claude-haiku-5-5"),
            ("claude-code", "claude-opus-4-1-20250805"),
            ("claude-code", "claude-sonnet-4-20250514"),
            ("claude-code", "claude-3-5-sonnet-20241022"),
            ("codex", "gpt-5"),
            ("codex", "gpt-5.4"),
            ("codex", "gpt-5.4-codex"),
            ("codex", "gpt-4o"),
            ("codex", "o3"),
            ("codex", "o4-mini"),
            ("copilot", "claude-sonnet-5-5"),
            ("copilot", "gpt-5.4"),
        ):
            with self.subTest(client=client, model=model):
                self.assertTrue(self.accepts(client, model))

    def test_a_digit_does_not_make_a_family_name_exact(self):
        for model in (
            "sonnet-4",
            "claude-3",
            "latest-2",
            "inherit1",
            "../../etc/passwd1",
            "claude-sonnet-4",
            "claude-sonnet",
            "claude-sonnet-5-5-latest",
            "Claude-Sonnet-5-5",
            "claude-sonnet-5-5 ",
            "gpt-5.4",  # a codex id is not a claude-code id
        ):
            with self.subTest(model=model):
                self.assertFalse(self.accepts("claude-code", model))
        for model in ("gpt-latest", "gpt-5-latest", "gpt-5.4-default", "gpt", "o3-latest", "claude-sonnet-5-5", "gpt5"):
            with self.subTest(model=model):
                self.assertFalse(self.accepts("codex", model))

    def test_local_tags_never_end_in_latest(self):
        self.assertTrue(self.accepts("local", "gemma4:12b"))
        self.assertTrue(self.accepts("local", "qwen3.5:4b"))
        for model in ("llama3:latest", "gemma4:latest", "gemma4", "../../x:7b"):
            with self.subTest(model=model):
                self.assertFalse(self.accepts("local", model))

    def test_the_schema_doc_states_the_same_grammar(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        for text in ("claude-sonnet-5-5", "gpt-5.4", "`latest`", "never"):
            self.assertIn(text, doc)


class IdentifierShapes(unittest.TestCase):
    def test_callsign_and_grid_shapes_are_refused_in_any_case_and_prefix(self):
        for text in ("ab1cde", "AB1CDE", "9A1XYZ", "4X1QRS", "FN31PR", "FN31", "fn31pr", "fn31", "EM15fd", "EM15FD"):
            with self.subTest(text=text):
                self.assertTrue(em.identifier_problems(f"seen {text} here"), text)

    def test_placeholders_and_ordinary_words_pass(self):
        for text in ("N0CALL", "N0TST", "FN31pr", "gemma4:12b", "case 12", "sha256", "v1.2.3", "PR 41", "UTF-8"):
            with self.subTest(text=text):
                self.assertEqual(em.identifier_problems(f"seen {text} here"), [], text)

    def test_the_second_home_path_form_is_refused(self):
        self.assertTrue(em.identifier_problems("see /Us" + "ers/someone/x"))

    @staticmethod
    def files_with(root, needle):
        """Relative paths of every text file under root that holds the needle in any letter case."""
        hits = []
        for p in sorted(Path(root).rglob("*")):
            rel = p.relative_to(root)
            if ".git" in rel.parts or "__pycache__" in rel.parts or not p.is_file() or p.is_symlink():
                continue
            if needle in p.read_bytes().decode("utf-8", errors="ignore").lower():
                hits.append(str(rel))
        return hits

    def test_no_real_club_callsign_is_anywhere_in_the_tree(self):
        self.assertEqual(self.files_with(ROOT, "w1" + "aw"), [])

    def test_the_tree_guard_bites(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "x.md").write_text("call W1" + "aw here\n", encoding="utf-8")
            (Path(d) / "y.txt").write_text("nothing\n", encoding="utf-8")
            self.assertEqual(self.files_with(d, "w1" + "aw"), ["sub/x.md"])


class HostsHidingInTokens(unittest.TestCase):
    def test_a_host_inside_a_token_with_other_characters_is_found(self):
        for text in (
            "build_server1.corp.example.com",
            "name_corp.example.com",
            "x_.evil.com",
            "a-b_c.corp.example.org",
            "see (host1.corp.example.com)",
            "key=box.corp.example.com",
        ):
            with self.subTest(text=text):
                self.assertIn("hostname-shaped string", em.identifier_problems(text))

    def test_repository_file_names_still_pass(self):
        for text in (
            "scripts/evals_matrix.py",
            "docs/home.md",
            "see README.md",
            "evals/cases.json",
            "run_log.txt",
            "transcript.json",
            "run.jsonl",
            "x.toml",
            "a.yaml",
        ):
            with self.subTest(text=text):
                self.assertEqual(em.identifier_problems(text), [])
        self.assertIn("hostname-shaped string", em.identifier_problems("not_a_repo_file.md"))

    def test_underscore_and_dot_runs_are_linear(self):
        started = time.monotonic()
        em.identifier_problems("a_" * 40000 + ".")
        em.identifier_problems("a._" * 40000)
        em.identifier_problems("a." * 40000 + "_")
        self.assertLess(time.monotonic() - started, 3.0)


class ModelLabelsCarryNoIdentifiers(unittest.TestCase):
    def refused(self, client, model):
        row = local_record(model=model) if client == "local" else record(client=client, model=model)
        with self.assertRaises(ValueError):
            parse(row)

    def test_open_ended_suffixes_are_refused(self):
        for model in (
            "gpt-5-mini.corp.example.com",
            "gpt-5-FN31pr",
            "gpt-5-/ho" + "me/x",
            "gpt-5-whatever",
            "gpt-5.4-ab1cde",
            "o4-anything",
        ):
            with self.subTest(model=model):
                self.refused("codex", model)
        for model in ("claude-sonnet-4-5-n0call", "claude-sonnet-4-5-extra", "opus-4-1-20250805", "sonnet-5-5"):
            with self.subTest(model=model):
                self.refused("claude-code", model)

    def test_closed_set_suffixes_pass(self):
        for model in ("gpt-5-mini", "gpt-5.4-codex", "gpt-5-2025-08-07", "gpt-4o-mini", "o4-mini", "gpt-5-turbo"):
            with self.subTest(model=model):
                parse(record(client="codex", model=model))

    def test_the_identifier_sweep_runs_on_local_labels(self):
        self.refused("local", "ab1cde:7b")
        self.refused("local", "9a1xyz:7b")
        parse(local_record(model="hf.co/org/m:7b"))
        parse(local_record(model="llama3.2:3b-instruct-q4_K_M"))

    def test_the_sweep_is_a_second_line_even_if_a_shape_were_open(self):
        open_shape = {"codex": (re.compile(r"gpt-5-.*"),)}
        with mock.patch.dict(em.MODEL_SHAPES, open_shape):
            parse(record(client="codex", model="gpt-5-mini"))
            for model in ("gpt-5-mini.corp.example.com", "gpt-5-ab1cde", "gpt-5-FN31PR"):
                with self.subTest(model=model):
                    self.refused("codex", model)

    def test_the_ids_this_repository_documents_pass(self):
        for model in ("claude-sonnet-5-5", "claude-haiku-5-5"):
            parse(record(model=model))

    def test_the_six_character_grid_shape_is_itself_matched(self):
        self.assertTrue(em.GRID_RE.fullmatch("EM15fd"))
        self.assertTrue(em.GRID_RE.fullmatch("em15FD"))
        self.assertTrue(em.GRID_RE.fullmatch("FN31"))


class LocalLabelsCannotCarryAHost(unittest.TestCase):
    def refused(self, model):
        with self.assertRaises(ValueError):
            parse(local_record(model=model))

    def test_a_host_in_any_path_segment_is_refused(self):
        for model in (
            "registry.corp.example.com/team/m:12b",
            "10-0-0-1.corp.example.com/m:12b",
            "hf.co/box.corp.example.com/m:12b",
            "org/box.corp.example.com:12b",
            "hf.co.corp.example.com/org/m:12b",
        ):
            with self.subTest(model=model):
                self.refused(model)

    def test_public_registries_are_allowed_only_as_the_first_segment(self):
        for first in ("hf.co", "huggingface.co", "registry.ollama.ai", "ghcr.io", "docker.io", "quay.io"):
            with self.subTest(first=first):
                parse(local_record(model=f"{first}/org/m:12b"))
        parse(local_record(model="hf.co/org/m:7b"))
        self.refused("org/hf.co/m:12b")

    def test_the_registry_allowlist_is_exactly_these(self):
        self.assertEqual(
            em.PUBLIC_REGISTRIES, ("hf.co", "huggingface.co", "registry.ollama.ai", "ghcr.io", "docker.io", "quay.io")
        )
        self.refused("corp.example.com/org/m:7b")

    def test_the_documented_public_registries_are_listed_in_the_schema(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        for name in em.PUBLIC_REGISTRIES:
            self.assertIn(name, doc)


class Ipv6Shapes(unittest.TestCase):
    def test_every_form_is_refused(self):
        for text in (
            "ipv6 2600:1700:abcd:1::1 seen",
            "2001:db8::1",
            "::1",
            "[::1]:8080",
            "fe80::1%eth0",
            "::ffff:10.1." + "2.3",
            "aa:bb:cc:dd:ee:ff",
            "2001:0db8:0000:0000:0000:ff00:0042:8329",
            "addr=[2001:db8::2]",
        ):
            with self.subTest(text=text):
                self.assertIn("address-shaped string", em.identifier_problems(text))

    def test_models_times_and_ordinary_colons_pass(self):
        for text in (
            "gemma4:12b",
            "org/m:Q4_K_M",
            "llama3.2:3b-instruct-q4_K_M",
            "12:30:45",
            "1:2:3",
            "line 12: done",
            "note: this: that",
            "see README.md:12",
        ):
            with self.subTest(text=text):
                self.assertEqual(em.identifier_problems(text), [], text)

    def test_the_address_helper_directly(self):
        self.assertTrue(em._ipv6_shaped("::ffff:10.1." + "2.3"))
        self.assertTrue(em._ipv6_shaped("2001:db8::1"))
        self.assertFalse(em._ipv6_shaped("12345:abcde:1f"))
        self.assertFalse(em._ipv6_shaped("1:2:3"))

    def test_the_time_exclusion_is_documented(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("12:30:45", doc)

    def test_matching_is_linear(self):
        started = time.monotonic()
        em.identifier_problems("a" * 200000)
        em.identifier_problems("a:" * 100000)
        em.identifier_problems("::" * 100000)
        self.assertLess(time.monotonic() - started, 3.0)


class ShortHashesAndKnownLimits(unittest.TestCase):
    def test_schema_doc_says_to_cite_the_full_hash_and_lists_false_alarms(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("40-character hash", doc)
        for word in ("H2O", "E2E", "K8s", "P2P", "W3C", "C4B"):
            self.assertIn(word, doc)

    def test_reply_pattern_cases_are_named_and_known_limits_stated(self):
        by_id = {c["id"]: c["grading_reason"] for c in em.load_registry(ROOT / "evals/cases.json")}
        for n in (1, 4, 5, 8, 12, 13, 19, 20, 21, 22, 23, 24, 25):
            self.assertIn("reply-pattern", by_id[n], f"case {n}")
        self.assertIn("known limit", by_id[12])
        self.assertIn("first", by_id[12])
        self.assertIn("known limit", by_id[13])
        self.assertIn("docker", by_id[13])
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("reply-pattern", doc)


class FrozenClock(unittest.TestCase):
    def test_one_day_of_slack_and_no_more(self):
        with mock.patch.object(em, "_today", return_value=datetime.date(2026, 10, 26)):
            parse(record(date="2026-10-26"))
            parse(record(date="2026-10-27"))
            with self.assertRaisesRegex(ValueError, "future"):
                parse(record(date="2026-10-28"))

    def test_the_client_version_is_swept_for_identifiers(self):
        with self.assertRaises(ValueError):
            parse(record(client_version="2.1.4-n0call"))
        parse(record(client_version="2.1.4"))


class UnsupportedMeansNoHeadlessInterface(unittest.TestCase):
    def test_only_a_client_without_a_headless_interface_may_be_unsupported(self):
        for client in ("claude-code", "codex", "local"):
            row = record(client=client, outcome="unsupported", model="unspecified", client_version="unspecified")
            if client == "local":
                row = local_record(outcome="unsupported", model="unspecified", client_version="unspecified")
            with self.subTest(client=client), self.assertRaisesRegex(ValueError, "unsupported"):
                parse(row)
        parse(record(client="copilot", outcome="unsupported", model="unspecified", client_version="unspecified"))

    def test_a_client_without_a_headless_interface_has_no_measured_result(self):
        for outcome in ("pass", "fail", "refused", "error"):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, "headless"):
                parse(record(client="copilot", model="gpt-5.4", outcome=outcome))
        for outcome in ("not-run", "needs-human"):
            parse(record(client="copilot", model="gpt-5.4", outcome=outcome))

    def test_flipping_the_registry_flag_flips_the_rule(self):
        flags = {c: {"headless": True} for c in em.CLIENTS}
        cases = em.parse_registry(registry_text([base_case(1)], flags))
        em.parse_record(line(record(client="copilot", model="gpt-5.4")), 1, cases)
        with self.assertRaises(ValueError):
            em.parse_record(
                line(
                    record(client="copilot", model="unspecified", client_version="unspecified", outcome="unsupported")
                ),
                1,
                cases,
            )

    def test_the_real_registry_marks_copilot_only(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        self.assertEqual(cases.headless, {"claude-code": True, "codex": True, "copilot": False, "local": True})

    def test_the_clients_block_is_read_strictly(self):
        ok = {c: {"headless": True} for c in em.CLIENTS}
        bad = {
            "missing client": {k: v for k, v in ok.items() if k != "codex"},
            "unknown client": {**ok, "chatgpt": {"headless": True}},
            "extra key": {**ok, "codex": {"headless": True, "x": 1}},
            "string flag": {**ok, "codex": {"headless": "yes"}},
            "int flag": {**ok, "codex": {"headless": 1}},
            "null flag": {**ok, "codex": {"headless": None}},
            "null entry": {**ok, "codex": None},
        }
        for name, clients in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                em.parse_registry(registry_text([base_case(1)], clients))
        with self.assertRaises(ValueError):
            em.parse_registry(json.dumps({"cases": [base_case(1)]}))

    def test_schema_doc_says_how_support_flips(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("headless", doc)


class EvidenceAndHosts(unittest.TestCase):
    def test_pointer_shapes_that_can_carry_a_location_are_refused(self):
        for name, text in {
            "url scheme": "saw file://x",
            "leading slash": "/etc/passwd",
            "later token with leading slash": "see /etc/passwd now",
            "dotdot": "see a/../b",
            "leading dotdot": "../x",
            "md host": "from evil.md",
            "py host": "from corp.py",
            "sh host": "from corp.sh",
            "multi-dot md host": "from server1.corp.md",
        }.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse(record(evidence=text))

    def test_known_repository_files_and_ordinary_pointers_pass(self):
        for text in (
            "see README.md",
            "see scripts/export_prompt.py",
            "see evals/cases.json case 3",
            "transcript.txt line 12",
            "run.log",
        ):
            with self.subTest(text=text):
                parse(record(evidence=text))

    def test_host_matching_is_linear(self):
        started = time.monotonic()
        em.identifier_problems("a." * 40000)
        em.identifier_problems("a-" * 40000 + ".")
        em.identifier_problems(("a" * 50 + ".") * 2000 + "1")
        self.assertLess(time.monotonic() - started, 3.0)

    def test_schema_doc_states_the_limits_of_the_secret_heuristic(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        self.assertIn("shorter", doc)
        self.assertIn("split", doc)


class SurvivorsOfTheFirstReview(unittest.TestCase):
    def test_a_record_that_is_not_an_object_says_so(self):
        for text in ("[]", "[1]", '"x"', "7", "true"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "not an object"):
                em.parse_record(text, 1, mini_cases())

    def test_persona_hash_covers_paths_not_only_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            base = em.persona_hash(r)
            (r / "policies/quality-gates.md").rename(r / "policies/quality-gates-renamed.md")
            self.assertNotEqual(em.persona_hash(r), base)

    def test_an_empty_persona_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            for p in (r / "fragments").iterdir():
                p.unlink()
            with self.assertRaisesRegex(ValueError, "empty"):
                em.persona_hash(r)

    def test_a_symlinked_doc_is_drift(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            doc = r / "evals/behavioral-evals.md"
            real = r / "elsewhere.md"
            real.write_text(doc.read_text(encoding="utf-8"), encoding="utf-8")
            doc.unlink()
            doc.symlink_to(real)
            self.assertTrue(em.doc_drift(r))

    def test_deep_nesting_is_an_error_not_a_crash(self):
        with self.assertRaisesRegex(ValueError, "nested"):
            em.strict_json("[" * 100000)
        with self.assertRaises(ValueError):
            em.parse_registry("[" * 100000)

    def test_a_partial_local_pair_is_refused_even_when_unmeasured(self):
        base = {"outcome": "not-run", "model": "unspecified", "client_version": "unspecified"}
        both = local_record(**base)
        for drop in ("quantization", "context"):
            row = {k: v for k, v in both.items() if k != drop}
            with self.subTest(drop=drop), self.assertRaisesRegex(ValueError, "pair"):
                parse(row)
        parse({k: v for k, v in both.items() if k not in ("quantization", "context")})

    def test_validate_sweeps_the_registry_for_identifiers(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/cases.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cases"][0]["input"] = "A diff signed by AB1CDE and no test output"
            path.write_text(json.dumps(data), encoding="utf-8")
            em.write_doc(r)  # no drift, so only the identifier sweep can object
            self.assertEqual(em.doc_drift(r), [])
            errors = validate(r)
            self.assertTrue(any("cases.json" in e and "callsign" in e for e in errors), errors)

    def test_future_dates_are_refused(self):
        today = datetime.datetime.now(datetime.UTC).date()
        parse(record(date=today.isoformat()))
        for days in (2, 400):
            future = (today + datetime.timedelta(days=days)).isoformat()
            with self.subTest(days=days), self.assertRaisesRegex(ValueError, "future"):
                parse(record(date=future))

    def test_the_example_results_are_not_in_the_future(self):
        for r in em.load_results(ROOT / "evals/example-results.jsonl", em.load_registry(ROOT / "evals/cases.json")):
            self.assertLessEqual(r["date"], datetime.datetime.now(datetime.UTC).date().isoformat())


class LocalCellsKeepTheirConfiguration(unittest.TestCase):
    def test_two_configurations_of_one_model_are_two_columns(self):
        rows = [
            local_record(outcome="pass"),
            local_record(quantization="Q8_0", context=4096, outcome="fail"),
        ]
        records = [em.parse_record(line(r), i, mini_cases()) for i, r in enumerate(rows, 1)]
        out = em.render_matrix(records, mini_cases(), HASH)
        self.assertIn("local / gemma4:12b (Q4\\_K\\_M, 8192)", out)
        self.assertIn("local / gemma4:12b (Q8\\_0, 4096)", out)
        self.assertIn("| 1 | reviewer | mechanical | not run | not run | not run | pass | fail |", out)

    def test_the_schema_doc_states_the_persona_hash_scope(self):
        doc = (ROOT / "evals/results-schema.md").read_text(encoding="utf-8")
        for text in ("templates", "scripts/lib.py", "persona_hash"):
            self.assertIn(text, doc)


if __name__ == "__main__":
    unittest.main()
