"""Behavioral-eval registry, result schema and matrix renderer (issue #45). No model call, no network."""

import contextlib
import copy
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evals_matrix as em  # noqa: E402  (path set up above)
import generate  # noqa: E402
from validate import privacy_hits, validate  # noqa: E402

HASH = "a" * 64
OTHER_HASH = "b" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def base_case(n, **over):
    case = {
        "id": n,
        "subject": "any",
        "roles": ["any"],
        "packs": [],
        "clients": list(em.CLIENTS),
        "setup": "none",
        "input": "An input",
        "expect": "An expected behavior",
        "grading": "mechanical",
        "grading_reason": "a string is present in the transcript",
        "cost_class": "prompt-only",
    }
    case.update(over)
    return case


CLIENT_FLAGS = {c: {"headless": c != "copilot"} for c in em.CLIENTS}


def registry_text(cases, clients=None):
    return json.dumps({"clients": clients or CLIENT_FLAGS, "cases": cases})


def mini_cases():
    return em.parse_registry(
        registry_text(
            [
                base_case(1, subject="reviewer", roles=["reviewer"]),
                base_case(2, grading="judged"),
                base_case(3, clients=["local"]),
            ]
        )
    )


def record(**over):
    row = {
        "case": 1,
        "client": "claude-code",
        "client_version": "2.1.4",
        "model": "claude-sonnet-5-5",
        "tools_enabled": ["read", "shell"],
        "commit": COMMIT,
        "persona_hash": HASH,
        "date": "2026-10-26",
        "outcome": "pass",
        "evidence": "transcript line 12 shows the gate run",
    }
    row.update(over)
    return row


def local_record(**over):
    fields = {
        "client": "local",
        "client_version": "0.12.3",
        "model": "gemma4:12b",
        "quantization": "Q4_K_M",
        "context": 8192,
    }
    fields.update(over)
    return record(**fields)


def line(row):
    return json.dumps(row)


def parse(row, cases=None):
    return em.parse_record(line(row), 1, cases or mini_cases())


def copy_tree(base):
    out = Path(base) / "fixture"
    shutil.copytree(ROOT, out, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return out


class RegistryReading(unittest.TestCase):
    def test_real_registry_has_the_28_exercises(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        self.assertEqual([c["id"] for c in cases], list(range(1, 29)))
        self.assertTrue(any(c["grading"] == "judged" for c in cases))
        self.assertTrue(any(c["grading"] == "mechanical" for c in cases))
        self.assertEqual(cases[25]["clients"], ["codex"])

    def test_every_row_of_the_doc_is_a_registry_case(self):
        doc = (ROOT / "evals/behavioral-evals.md").read_text(encoding="utf-8")
        rows = [ln for ln in doc.splitlines() if re.match(r"\| \d+ \|", ln)]
        self.assertEqual(len(rows), 28)

    def test_a_valid_mini_registry_loads(self):
        self.assertEqual([c["id"] for c in mini_cases()], [1, 2, 3])

    def test_strict_reading_rejects_each_of_these(self):
        def drop(key):
            c = base_case(1)
            del c[key]
            return [c]

        bad = {
            "not an object": "[]",
            "not json": "{",
            "no cases key": "{}",
            "empty cases": registry_text([]),
            "extra top key": json.dumps({"clients": CLIENT_FLAGS, "cases": [base_case(1)], "x": 1}),
            "extra case key": registry_text([base_case(1, extra="x")]),
            "missing key": registry_text(drop("expect")),
            "null field": registry_text([base_case(1, expect=None)]),
            "null list": registry_text([base_case(1, packs=None)]),
            "string id": registry_text([base_case("1")]),
            "bool id": registry_text([base_case(True)]),
            "float id": registry_text([base_case(1.0)]),
            "gap in ids": registry_text([base_case(1), base_case(3)]),
            "duplicate id": registry_text([base_case(1), base_case(1)]),
            "ids out of order": registry_text([base_case(2), base_case(1)]),
            "unknown role": registry_text([base_case(1, roles=["boss"])]),
            "empty roles": registry_text([base_case(1, roles=[])]),
            "duplicate role": registry_text([base_case(1, roles=["any", "any"])]),
            "unknown pack": registry_text([base_case(1, packs=["no-such-pack"])]),
            "unknown client": registry_text([base_case(1, clients=["chatgpt"])]),
            "empty clients": registry_text([base_case(1, clients=[])]),
            "duplicate client": registry_text([base_case(1, clients=["local", "local"])]),
            "unknown grading": registry_text([base_case(1, grading="auto")]),
            "unknown cost class": registry_text([base_case(1, cost_class="free")]),
            "empty text": registry_text([base_case(1, input="")]),
            "newline in text": registry_text([base_case(1, input="a\nb")]),
            "control character": registry_text([base_case(1, expect="a\x07b")]),
            "bad setup name": registry_text([base_case(1, setup="Has Spaces")]),
            "role missing from subject": registry_text([base_case(1, subject="implementer", roles=["reviewer"])]),
            "pack missing from subject": registry_text([base_case(1, subject="any", packs=["rf-emcomm"])]),
            "duplicate json key": '{"cases": [], "cases": []}',
            "NaN constant": '{"cases": [], "x": NaN}',
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                em.parse_registry(text)

    def test_duplicate_key_inside_a_case_is_refused(self):
        text = registry_text([base_case(1)]).replace('"id": 1,', '"id": 1, "id": 1,')
        with self.assertRaises(ValueError):
            em.parse_registry(text)


class DocFromRegistry(unittest.TestCase):
    def test_checked_in_doc_is_current(self):
        self.assertEqual(em.doc_drift(ROOT), [])

    def test_generate_check_covers_the_doc(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            self.assertEqual(generate.drift(r), [])
            path = r / "evals/behavioral-evals.md"
            path.write_text(path.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
            self.assertTrue(any("behavioral-evals.md" in p for p in generate.drift(r)))

    def test_registry_edit_without_regenerating_is_drift(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/cases.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cases"][0]["expect"] = "Something else entirely"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(any("behavioral-evals.md" in p for p in em.doc_drift(r)))
            self.assertNotEqual(validate(r), [])

    def test_a_row_dropped_from_the_doc_is_drift(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/behavioral-evals.md"
            kept = [ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.startswith("| 7 |")]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            self.assertTrue(em.doc_drift(r))
            self.assertNotEqual(validate(r), [])

    def test_write_doc_repairs_drift_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/behavioral-evals.md"
            good = path.read_text(encoding="utf-8")
            path.write_text("stale\n", encoding="utf-8")
            em.write_doc(r)
            self.assertEqual(path.read_text(encoding="utf-8"), good)
            em.write_doc(r)
            self.assertEqual(path.read_text(encoding="utf-8"), good)

    def test_pipes_in_registry_text_are_escaped_in_the_table(self):
        cases = em.parse_registry(registry_text([base_case(1, input="use `a || b` here")]))
        row = next(ln for ln in em.render_cases_doc(cases).splitlines() if ln.startswith("| 1 |"))
        self.assertIn(r"`a \|\| b`", row)
        self.assertEqual(len(re.split(r"(?<!\\)\|", row)), 7)  # five cells between the outer bars

    def test_a_missing_registry_is_drift_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            (r / "evals/cases.json").unlink()
            self.assertTrue(em.doc_drift(r))
            self.assertNotEqual(validate(r), [])


class RecordReading(unittest.TestCase):
    def test_good_records_parse(self):
        for row in (
            record(),
            local_record(),
            record(outcome="fail", cost=1200),
            record(case=2, outcome="needs-human"),
            record(client="copilot", client_version="unspecified", model="unspecified", outcome="unsupported"),
            record(outcome="not-run", client_version="unspecified", model="unspecified"),
            record(evidence="synthetic operator N0CALL in grid FN31pr"),
        ):
            with self.subTest(row=row):
                self.assertEqual(parse(row)["case"], row["case"])

    def test_each_rejection_has_a_message_naming_the_line(self):
        cases = mini_cases()

        def without(key):
            r = record()
            del r[key]
            return r

        bad = {
            "not json": "{",
            "not an object": "[]",
            "duplicate key": '{"case": 1, "case": 1}',
            "NaN cost": line(record()).replace("}", ', "cost": NaN}'),
            "too long": line(record(evidence="x" * 5000)),
            "padded past the line cap": line(record()) + " " * 5000,
            "null cost": line(record(cost=None)),
            "null quantization": line(local_record(quantization=None)),
            "null context": line(local_record(context=None)),
            "local without quantization and context": line(
                {k: v for k, v in local_record().items() if k not in ("quantization", "context")}
            ),
            "extra field": line(record(note="x")),
            **{f"missing {k}": line(without(k)) for k in em.REQUIRED_FIELDS},
            **{f"null {k}": line(record(**{k: None})) for k in em.REQUIRED_FIELDS},
            "string case": line(record(case="1")),
            "bool case": line(record(case=True)),
            "unknown case": line(record(case=99)),
            "unknown client": line(record(client="chatgpt")),
            "client not applicable": line(record(case=3)),
            "unknown outcome": line(record(outcome="passed")),
            "non-list tools": line(record(tools_enabled="read")),
            "unknown tool": line(record(tools_enabled=["read", "teleport"])),
            "duplicate tool": line(record(tools_enabled=["read", "read"])),
            "bad date": line(record(date="2026-13-40")),
            "date with time": line(record(date="2026-10-26T10:00")),
            "short commit": line(record(commit="abc1234")),
            "upper commit": line(record(commit=COMMIT.upper())),
            "short hash": line(record(persona_hash="a" * 63)),
            "negative cost": line(record(cost=-1)),
            "bool cost": line(record(cost=True)),
            "string cost": line(record(cost="12")),
            "empty evidence": line(record(evidence="")),
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(ValueError) as cm:
                em.parse_record(text, 7, cases)
            self.assertTrue(str(cm.exception).startswith("line 7:"), str(cm.exception))

    def test_pass_on_a_judged_case_is_refused(self):
        with self.assertRaisesRegex(ValueError, "judged"):
            parse(record(case=2, outcome="pass"))
        for outcome in ("fail", "needs-human", "refused", "error", "not-run"):
            parse(record(case=2, outcome=outcome))

    def test_pass_needs_an_exact_model_version_and_commit(self):
        for name, over in {
            "family name": {"model": "sonnet"},
            "family with vendor": {"model": "claude-sonnet"},
            "inherit": {"model": "inherit"},
            "latest": {"model": "latest"},
            "empty model": {"model": ""},
            "unspecified model": {"model": "unspecified"},
            "model with pipe": {"model": "a|b-5"},
            "empty version": {"client_version": ""},
            "unspecified version": {"client_version": "unspecified"},
            "version without digit": {"client_version": "latest"},
            "no commit": {"commit": ""},
        }.items():
            for outcome in ("pass", "fail", "error"):
                with self.subTest(name, outcome=outcome), self.assertRaises(ValueError):
                    parse(record(outcome=outcome, **over))

    def test_unsupported_and_not_run_may_leave_model_and_version_unspecified(self):
        for outcome, client in (("unsupported", "copilot"), ("not-run", "claude-code")):
            parse(record(client=client, outcome=outcome, model="unspecified", client_version="unspecified"))
            with self.assertRaises(ValueError):  # unspecified, not blank and not a family name
                parse(record(client=client, outcome=outcome, model="sonnet"))

    def test_local_model_needs_a_tag_quantization_and_context(self):
        for name, over in {
            "family name": {"model": "gemma4"},
            "no size": {"model": "gemma4:abc"},
            "missing quantization": {"quantization": None},
            "missing context": {"context": None},
            "zero context": {"context": 0},
            "bool context": {"context": True},
            "string context": {"context": "8192"},
            "bad quantization": {"quantization": "q4 k m"},
        }.items():
            row = local_record()
            row.update(over)
            row = {k: v for k, v in row.items() if v is not None or name.startswith(("family", "no size"))}
            if name.startswith("missing"):
                row = {k: v for k, v in local_record().items() if k != over and k not in over}
            with self.subTest(name), self.assertRaises(ValueError):
                parse(row)

    def test_remote_clients_may_not_carry_local_fields(self):
        with self.assertRaises(ValueError):
            parse(record(quantization="Q4_K_M"))
        with self.assertRaises(ValueError):
            parse(record(context=8192))

    def test_evidence_is_a_short_sanitized_pointer(self):
        for name, text in {
            "newline": "line one\nline two",
            "pipe": "a | b",
            "backtick": "run `id`",
            "angle bracket": "<script>",
            "non-ascii": "café",
            "hostname": "fetched from build-box.corp.example.com",
            "short hostname": "ssh to lab.internal",
            "ipv4": "saw 10.1." + "2.3 in the log",
            "home path": "see /ho" + "me/someone/x",
            "callsign": "operator AB1CDE keyed up",
            "grid square": "grid EM15fd",
            "long token": "key " + "A1b2C3d4" * 5,
            "over the cap": ("ab " * 70)[: em.MAX_EVIDENCE + 1],
        }.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse(record(evidence=text))

    def test_evidence_allows_file_pointers_and_placeholders(self):
        for text in (
            "transcript.txt line 12",
            "see evals/cases.json case 3",
            "N0CALL and N0TST and FN31pr only",
            "commit " + COMMIT,
            ("ab " * 70)[: em.MAX_EVIDENCE - 1] + "z",
        ):
            with self.subTest(text=text):
                parse(record(evidence=text))

    def test_privacy_checks_run_on_every_record_field_that_holds_text(self):
        with self.assertRaises(ValueError):
            parse(record(client_version="9.9.9 build-box.corp.example.com"))
        with self.assertRaises(ValueError):
            parse(local_record(quantization="AB1CDE"))


class MatrixRendering(unittest.TestCase):
    def render(self, rows, cases=None):
        records = [em.parse_record(line(r), i, cases or mini_cases()) for i, r in enumerate(rows, 1)]
        return em.render_matrix(records, cases or mini_cases(), HASH)

    def test_sample_records_render_the_expected_matrix(self):
        out = self.render(
            [
                record(),
                record(case=2, outcome="needs-human"),
                local_record(outcome="fail", persona_hash=OTHER_HASH),
                local_record(case=3),
                record(client="copilot", client_version="unspecified", model="unspecified", outcome="unsupported"),
            ]
        )
        want = "\n".join(
            [
                "| # | Case | Grading | claude-code / claude-sonnet-5-5 | codex | copilot / unspecified |"
                " local / gemma4:12b (Q4\\_K\\_M, 8192) |",
                "| --- | --- | --- | --- | --- | --- | --- |",
                "| 1 | reviewer | mechanical | pass | not run | unsupported | fail (stale) |",
                "| 2 | any | judged | needs human | not run | not run | not run |",
                "| 3 | any | mechanical | n/a | n/a | n/a | pass |",
                "",
                "Latest record per cell. `not run` means no record exists; `n/a` means the case does not apply to"
                " that client; `stale` means the persona sources changed after the result was recorded, so it"
                " proves nothing about the current text. Only `pass` on a mechanical case is a pass.",
                "",
                "Stale cells: 1.",
                "",
            ]
        )
        self.assertEqual(out, want)

    def test_no_records_shows_every_cell_as_not_run_never_blank_or_pass(self):
        out = em.render_matrix([], mini_cases(), HASH)
        body = [ln for ln in out.splitlines() if re.match(r"\| \d+ \|", ln)]
        self.assertEqual(len(body), 3)
        for ln in body:
            cells = [c.strip() for c in ln.strip("|").split("|")][3:]
            self.assertTrue(cells)
            self.assertTrue(all(c in ("not run", "n/a") for c in cells), ln)
            self.assertNotIn("pass", ln)

    def test_unsupported_is_shown_plainly_and_not_marked_stale(self):
        out = self.render(
            [
                record(
                    client="copilot",
                    client_version="unspecified",
                    model="unspecified",
                    outcome="unsupported",
                    persona_hash=OTHER_HASH,
                )
            ]
        )
        self.assertIn("| unsupported |", out)
        self.assertNotIn("unsupported (stale)", out)
        self.assertIn("Stale cells: 0.", out)

    def test_not_run_record_renders_as_not_run(self):
        out = self.render([record(outcome="not-run", model="unspecified", client_version="unspecified")])
        self.assertIn("claude-code / unspecified", out)
        self.assertNotIn("| pass", out)
        self.assertIn("| 1 | reviewer | mechanical | not run |", out)

    def test_latest_record_wins_and_a_later_line_breaks_a_date_tie(self):
        out = self.render([record(date="2026-10-26", outcome="fail"), record(date="2026-10-25", outcome="pass")])
        self.assertIn("mechanical | fail |", out)
        out = self.render([record(outcome="fail"), record(outcome="pass")])
        self.assertIn("mechanical | pass |", out)

    def test_stale_is_a_hash_mismatch_for_every_measured_outcome(self):
        for outcome in ("pass", "fail", "refused", "error", "needs-human"):
            with self.subTest(outcome):
                self.assertIn("(stale)", self.render([record(outcome=outcome, persona_hash=OTHER_HASH)]))
        self.assertNotIn("(stale)", self.render([record()]))

    def test_output_is_deterministic_and_independent_of_line_order(self):
        rows = [record(), record(case=2, outcome="fail"), local_record(), record(client="codex", model="gpt-5.4")]
        a = self.render(rows)
        b = self.render(list(reversed(rows)))
        self.assertEqual(a, b)
        self.assertEqual(a, self.render(rows))

    def test_echoed_text_is_escaped(self):
        self.assertEqual(em.esc("a|b"), r"a\|b")
        self.assertEqual(em.esc("<b>&"), "&lt;b&gt;&amp;")
        self.assertEqual(em.esc("`x`[y]*z*_w_"), r"\`x\`\[y\]\*z\*\_w\_")
        cases = em.parse_registry(registry_text([base_case(1, subject="any <b>|x</b>")]))
        out = em.render_matrix([], cases, HASH)
        self.assertNotIn("<b>", out)
        self.assertIn(r"any &lt;b&gt;\|x&lt;/b&gt;", out)

    def test_a_record_for_an_unknown_case_cannot_reach_the_renderer(self):
        with self.assertRaises(ValueError):
            parse(record(case=42))


class Files(unittest.TestCase):
    def write(self, d, rows):
        p = Path(d) / "results.jsonl"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return p

    def test_load_results_reads_a_file_and_skips_blank_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, [line(record()), "", line(record(case=2, outcome="fail"))])
            self.assertEqual(len(em.load_results(p, mini_cases())), 2)
            p = self.write(d, [line(record()), "not json", line(record())])
            with self.assertRaisesRegex(ValueError, "line 2"):
                em.load_results(p, mini_cases())

    def test_an_empty_file_is_an_error_not_an_empty_matrix(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_text("\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                em.load_results(p, mini_cases())

    def test_non_utf8_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_bytes(b"\xff\xfe\n")
            with self.assertRaises(ValueError):
                em.load_results(p, mini_cases())

    def test_example_results_parse_against_the_real_registry(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        rows = em.load_results(ROOT / "evals/example-results.jsonl", cases)
        self.assertGreaterEqual(len(rows), 4)
        outcomes = {r["outcome"] for r in rows}
        self.assertIn("unsupported", outcomes)
        self.assertIn("needs-human", outcomes)

    def test_cli_prints_the_matrix_and_exits_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = em.main([str(ROOT / "evals/example-results.jsonl")])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("| # | Case | Grading |", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_cli_exits_one_and_prints_no_matrix_on_a_bad_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, [line(record(case=1)), line(record(case=1, outcome="pass", model="sonnet"))])
            real = ROOT / "evals/example-results.jsonl"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(em.main([str(real)]), 0)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = em.main([str(p)])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("FAIL:", err.getvalue())

    def test_cli_check_mode_matches_generate_check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(em.main(["--check"]), 0)
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            (r / "evals/behavioral-evals.md").write_text("x\n", encoding="utf-8")
            with contextlib.redirect_stdout(out):
                self.assertEqual(em.main(["--check", "--root", str(r)]), 1)

    def test_cli_rejects_check_with_a_results_file(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            em.main(["--check", "results.jsonl"])

    def test_cli_marks_stale_against_the_checkout_hash(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        cur = em.persona_hash(ROOT)
        with tempfile.TemporaryDirectory() as d:
            fresh = record(persona_hash=cur)
            p = self.write(d, [line(fresh), line(record(case=2, outcome="fail", persona_hash=OTHER_HASH))])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(em.main([str(p)]), 0)
            self.assertIn("| pass |", out.getvalue())
            self.assertIn("fail (stale)", out.getvalue())
        self.assertTrue(cases)


class PersonaHash(unittest.TestCase):
    def test_hash_covers_roles_policies_packs_and_fragments_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            base = em.persona_hash(r)
            self.assertRegex(base, r"^[0-9a-f]{64}$")
            self.assertEqual(em.persona_hash(r), base)
            for rel in (
                "roles/reviewer.md",
                "policies/quality-gates.md",
                "packs/threat-model/PACK.md",
                "fragments/report-header.md",
            ):
                path = r / rel
                original = path.read_bytes()
                path.write_bytes(original + b"\nchange\n")
                with self.subTest(rel):
                    self.assertNotEqual(em.persona_hash(r), base)
                path.write_bytes(original)
                self.assertEqual(em.persona_hash(r), base)
            for rel in ("README.md", "evals/cases.json", "CHANGELOG.md"):
                path = r / rel
                path.write_bytes(path.read_bytes() + b"\nchange\n")
                with self.subTest(rel):
                    self.assertEqual(em.persona_hash(r), base)

    def test_a_missing_source_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            shutil.rmtree(r / "fragments")
            with self.assertRaises(ValueError):
                em.persona_hash(r)

    def test_cli_prints_the_hash(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(em.main(["--persona-hash"]), 0)
        self.assertEqual(out.getvalue().strip(), em.persona_hash(ROOT))


class Privacy(unittest.TestCase):
    def test_registry_and_example_results_hold_only_synthetic_identifiers(self):
        for rel in ("evals/cases.json", "evals/example-results.jsonl", "evals/behavioral-evals.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(em.identifier_problems(text, hostnames=False), [], rel)
        self.assertEqual([h for h in privacy_hits(ROOT) if h[0].startswith("evals/")], [])

    def test_the_sweep_bites(self):
        for text in ("build-box.corp.example.com", "10.1." + "2.3", "AB1CDE", "EM15fd", "/ho" + "me/someone/x"):
            with self.subTest(text):
                self.assertTrue(em.identifier_problems(f"seen {text} here"))
        for text in ("N0CALL", "N0TST", "FN31pr", "scripts/export_prompt.py", "evals/cases.json", "v1.2.3"):
            with self.subTest(text):
                self.assertEqual(em.identifier_problems(f"seen {text} here"), [])

    def test_validator_rejects_a_private_looking_string_in_the_example_results(self):
        with tempfile.TemporaryDirectory() as d:
            r = copy_tree(d)
            path = r / "evals/example-results.jsonl"
            rows = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(rows[0])
            first["evidence"] = "from build-box.corp.example.com"
            rows[0] = json.dumps(first)
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            self.assertTrue(any("example-results" in e for e in validate(r)))

    def test_validator_accepts_the_checked_in_tree(self):
        self.assertEqual([e for e in validate(ROOT) if "evals" in e], [])


class NoNetworkNoModel(unittest.TestCase):
    FORBIDDEN = re.compile(
        r"^\s*(?:import|from)\s+(?:socket|ssl|urllib|http|ftplib|smtplib|requests|subprocess|asyncio|"
        r"multiprocessing|ctypes|os)\b",
        re.M,
    )

    def test_the_module_imports_no_network_process_or_os_module(self):
        source = (ROOT / "scripts/evals_matrix.py").read_text(encoding="utf-8")
        self.assertEqual(self.FORBIDDEN.findall(source), [])

    def test_the_guard_bites(self):
        for bad in ("import socket", "from urllib import request", "import subprocess", "  import os"):
            self.assertTrue(self.FORBIDDEN.search(bad), bad)
        self.assertIsNone(self.FORBIDDEN.search("import json\nfrom pathlib import Path"))


class Classification(unittest.TestCase):
    def test_every_case_states_why_it_is_mechanical_or_judged(self):
        for c in em.load_registry(ROOT / "evals/cases.json"):
            self.assertGreater(len(c["grading_reason"]), 20, c["id"])

    def test_guard_cases_are_not_mutated_by_loading(self):
        cases = em.load_registry(ROOT / "evals/cases.json")
        before = copy.deepcopy(cases)
        em.render_cases_doc(cases)
        em.render_matrix([], cases, HASH)
        self.assertEqual(cases, before)


if __name__ == "__main__":
    unittest.main()
