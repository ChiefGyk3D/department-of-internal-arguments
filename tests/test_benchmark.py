"""Tests for the benchmark corpus, the rubric and the scorer (scripts/benchmark.py). Synthetic records only; no model
is ever run. The git tests build a throwaway repository in a temporary directory."""

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark  # noqa: E402  (path set up above)
from validate import PRIVACY_RE  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(repo, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def finding(fid="f1", cls="input-validation", rnd=1, paths=("src/a.py",), evidence=("unchecked",), **extra):
    f = {
        "id": fid,
        "class": cls,
        "severity": "important",
        "round": rnd,
        "summary": "A synthetic finding.",
        "source": "synthetic",
        "paths": list(paths),
        "evidence_any": list(evidence),
        "fixed": None,
        "at_base": "not-observed",
        "repro": {"kind": "pattern", "path": "src/a.py", "regex": "unchecked", "present": True},
    }
    f.update(extra)
    return f


def task(tid="t1", roles=("reviewer", "adversarial-reviewer"), findings=None, **extra):
    t = {
        "id": tid,
        "roles": list(roles),
        "kind": "diff",
        "title": "A synthetic task",
        "base": "a" * 40,
        "head": "b" * 40,
        "diff_sha256": "c" * 64,
        "findings": findings if findings is not None else [finding()],
    }
    t.update(extra)
    return t


def corpus(*tasks):
    return {"version": 1, "tasks": list(tasks) or [task()], "dropped": []}


def record(**over):
    r = {
        "task": "t1",
        "role": "reviewer",
        "run": 1,
        "client": "claude-code",
        "client_version": "2.1.0",
        "model": "claude-sonnet-5-5",
        "date": "2026-11-01",
        "commit": "abcdef1",
        "tools_enabled": ["read"],
        "outcome": "completed",
        "findings": [{"class": "input-validation", "path": "src/a.py", "evidence": "the value is unchecked here"}],
        "wall_clock_s": 12.5,
        "tokens": {"input": 1000, "output": 200},
        "approval_ref": "synthetic",
        "refusals": [],
    }
    r.update(over)
    return r


def parse(rec, c=None):
    return benchmark.parse_result(json.dumps(rec), 1, c or corpus())


class ModelId(unittest.TestCase):
    def test_exact_ids_pass(self):
        for m in ("claude-sonnet-5-5", "claude-haiku-5-5", "gpt-5.1-codex", "gemma4:12b", "llama3.2:latest"):
            self.assertTrue(benchmark.exact_model_id(m), m)

    def test_family_names_and_aliases_fail(self):
        for m in (
            "sonnet",
            "opus",
            "claude",
            "claude-sonnet",
            "claude-opus-latest",
            "claude-sonnet-5-latest",
            "gpt-5-inherit",
            "gpt-daybreak-blue-latest",
            "inherit",
            "default",
            "gemma4",
            "",
            " claude-sonnet-5-5",
            "Claude-Sonnet-5-5",
            None,
            5,
        ):
            self.assertFalse(benchmark.exact_model_id(m), repr(m))


class Matching(unittest.TestCase):
    def setUp(self):
        self.t = task(
            findings=[
                finding("f1", "input-validation", 1, ("src/a.py",), ("unchecked",)),
                finding("f2", "ownership-trust", 2, ("src/b.py",), ("forg(ed|ery)",)),
            ]
        )

    def m(self, reported):
        return benchmark.match_findings(self.t, reported)

    def test_match_needs_class_path_and_evidence(self):
        good = {"class": "input-validation", "path": "src/a.py", "evidence": "x is unchecked"}
        self.assertEqual(self.m([good])["matched"], ["f1"])
        for key, bad in (("class", "ownership-trust"), ("path", "src/zzz.py"), ("evidence", "looks fine to me")):
            r = self.m([{**good, key: bad}])
            self.assertEqual(r["matched"], [], key)
            self.assertEqual(r["false"], 1, key)

    def test_duplicate_is_not_a_second_match_and_not_false(self):
        good = {"class": "input-validation", "path": "src/a.py", "evidence": "unchecked"}
        r = self.m([good, good])
        self.assertEqual((r["matched"], r["duplicates"], r["false"]), (["f1"], 1, 0))

    def test_unevidenced_and_malformed_findings_are_false(self):
        r = self.m(
            [
                {"class": "input-validation", "path": "src/a.py", "evidence": ""},
                {"class": "made-up-class", "path": "src/a.py", "evidence": "unchecked"},
                {"class": "input-validation", "path": "src/a.py"},
            ]
        )
        self.assertEqual((r["matched"], r["false"], r["unevidenced"]), ([], 3, 3))

    def test_result_does_not_depend_on_report_order(self):
        a = {"class": "input-validation", "path": "src/a.py", "evidence": "unchecked"}
        b = {"class": "ownership-trust", "path": "src/b.py", "evidence": "a forged marker"}
        junk = {"class": "dead-code", "path": "src/a.py", "evidence": "nothing"}
        one = self.m([a, junk, b])
        two = self.m([b, a, junk])
        self.assertEqual(sorted(one["matched"]), sorted(two["matched"]))
        self.assertEqual((one["false"], one["duplicates"]), (two["false"], two["duplicates"]))

    def test_rounds_avoided_counts_only_fully_matched_rounds(self):
        self.assertEqual(benchmark.rounds_avoided(self.t, ["f1"]), (1, 2))
        self.assertEqual(benchmark.rounds_avoided(self.t, ["f1", "f2"]), (2, 2))
        self.assertEqual(benchmark.rounds_avoided(self.t, []), (0, 2))
        both = task(findings=[finding("a", rnd=1), finding("b", rnd=1)])
        self.assertEqual(benchmark.rounds_avoided(both, ["a"]), (0, 1))


class ResultRecords(unittest.TestCase):
    def rejects(self, rec, fragment, c=None):
        with self.assertRaises(ValueError) as cm:
            parse(rec, c)
        self.assertIn(fragment, str(cm.exception))

    def test_good_record_parses(self):
        self.assertEqual(parse(record())["model"], "claude-sonnet-5-5")

    def test_family_name_instead_of_exact_model_id_is_rejected(self):
        for m in ("sonnet", "claude-sonnet", "opus", "inherit"):
            self.rejects(record(model=m), "model")

    def test_missing_model_version_or_cost_is_rejected(self):
        for key, fragment in (("model", "model"), ("client_version", "client_version"), ("tokens", "tokens")):
            r = record()
            del r[key]
            self.rejects(r, fragment)
        self.rejects(record(client_version=""), "client_version")
        self.rejects(record(tokens={"input": 5}), "tokens")
        self.rejects(record(tokens={"input": -1, "output": 2}), "tokens")
        self.rejects(record(tokens={"input": True, "output": 2}), "tokens")

    def test_other_required_fields_and_extras(self):
        for key in ("task", "role", "run", "client", "date", "commit", "outcome", "findings", "wall_clock_s"):
            r = record()
            del r[key]
            self.rejects(r, key)
        self.rejects(record(surprise=1), "surprise")
        self.rejects(record(approval_ref=""), "approval_ref")

    def test_task_and_role_must_exist_and_fit(self):
        self.rejects(record(task="nope"), "unknown task")
        self.rejects(record(role="implementer"), "role")
        c = corpus(task(roles=("reviewer",)))
        self.rejects(record(role="adversarial-reviewer"), "not scored for role", c)

    def test_wall_clock_must_be_a_positive_number(self):
        for bad in (0, -1, "12", True, float("nan")):
            self.rejects(record(wall_clock_s=bad), "wall_clock_s")

    def test_local_models_carry_quantization_and_context(self):
        self.rejects(record(model="gemma4:12b"), "quantization")
        ok = record(model="gemma4:12b", quantization="q4_K_M", context=8192)
        self.assertEqual(parse(ok)["context"], 8192)
        self.rejects(record(model="gemma4:12b", quantization="q4_K_M", context=0), "context")
        self.rejects(record(quantization="q4_K_M", context=8192), "only local")

    def test_refusals_are_counted_and_must_match_the_outcome(self):
        self.rejects(record(outcome="refused"), "refusal")
        self.rejects(record(outcome="filter-cutoff", refusals=[]), "refusal")
        self.rejects(record(refusals=[{"kind": "banana", "note": "x"}]), "kind")
        r = parse(record(outcome="refused", refusals=[{"kind": "refusal", "note": "declined"}], findings=[]))
        self.assertEqual(len(r["refusals"]), 1)

    def test_findings_shape_and_bounds(self):
        self.rejects(record(findings=[{"class": "x", "path": "p"}]), "findings")
        self.rejects(record(findings=[{"class": "x", "path": "p", "evidence": "e", "extra": 1}]), "findings")
        self.rejects(record(findings=[{"class": "input-validation", "path": "p", "evidence": "e" * 3000}]), "evidence")

    def test_malformed_json_and_not_an_object(self):
        for line in ("{", "[]", '"x"', "1"):
            with self.assertRaises(ValueError):
                benchmark.parse_result(line, 1, corpus())

    def test_duplicate_run_is_rejected(self):
        line = json.dumps(record())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.jsonl"
            p.write_text(line + "\n" + line + "\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                benchmark.load_results(p, corpus())
            self.assertIn("duplicate", str(cm.exception))
            p.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                benchmark.load_results(p, corpus())


class Scoring(unittest.TestCase):
    def setUp(self):
        self.c = corpus(
            task(
                findings=[
                    finding("f1", "input-validation", 1, ("src/a.py",), ("unchecked",)),
                    finding("f2", "ownership-trust", 2, ("src/b.py",), ("forged",)),
                ]
            ),
            task("t2", roles=("reviewer",), findings=[finding("g1", "dead-code", 1, ("src/c.py",), ("dead",))]),
        )

    def test_metrics_for_one_record(self):
        rec = parse(
            record(
                findings=[
                    {"class": "input-validation", "path": "src/a.py", "evidence": "unchecked"},
                    {"class": "dead-code", "path": "src/a.py", "evidence": "dead"},
                ]
            ),
            self.c,
        )
        m = benchmark.score_record(rec, self.c)
        self.assertEqual((m["expected"], m["matched"], m["false"], m["reported"]), (2, 1, 1, 2))
        self.assertEqual((m["rounds_avoided"], m["rounds_total"]), (1, 2))

    def test_table_for_synthetic_records(self):
        recs = [
            parse(record(), self.c),
            parse(
                record(
                    task="t2",
                    wall_clock_s=7.5,
                    tokens={"input": 500, "output": 100},
                    findings=[{"class": "dead-code", "path": "src/c.py", "evidence": "is dead"}],
                ),
                self.c,
            ),
            parse(
                record(
                    model="claude-haiku-5-5",
                    outcome="refused",
                    findings=[],
                    refusals=[{"kind": "refusal", "note": "declined"}],
                ),
                self.c,
            ),
        ]
        table = benchmark.summarize(recs, self.c)
        self.assertIn("| Role | Model | Tasks | Recall % | Precision % | False findings |", table)
        self.assertIn(
            "| reviewer | claude-haiku-5-5 | 1 of 2 | 0.0 | n/a | 0 | 0 of 2 | 12.5 | 1000 | 200 | 1 |", table
        )
        self.assertIn(
            "| reviewer | claude-sonnet-5-5 | 2 of 2 | 66.7 | 100.0 | 0 | 2 of 3 | 10.0 | 1500 | 300 | 0 |", table
        )
        self.assertEqual(table, benchmark.summarize(list(reversed(recs)), self.c))

    def test_cli_prints_table_fails_on_bad_record_and_changes_nothing(self):
        routing = ROOT / "policies" / "model-routing.md"
        before = hashlib.sha256(routing.read_bytes()).hexdigest()
        real = benchmark.load_corpus()
        first = real["tasks"][0]
        rec = record(
            task=first["id"],
            role=first["roles"][0],
            findings=[],
        )
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.jsonl"
            good.write_text(json.dumps(rec) + "\n", encoding="utf-8")
            bad = Path(d) / "bad.jsonl"
            bad.write_text(json.dumps({**rec, "model": "sonnet"}) + "\n", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(benchmark.main(["--score", str(good)]), 0)
                self.assertEqual(benchmark.main(["--score", str(bad)]), 1)
            self.assertIn("| Role | Model |", out.getvalue())
            self.assertIn("FAIL", err.getvalue())
        self.assertEqual(hashlib.sha256(routing.read_bytes()).hexdigest(), before)


class CorpusStructure(unittest.TestCase):
    def errors(self, c):
        return benchmark.corpus_errors(c)

    def test_minimal_synthetic_corpus_is_clean(self):
        self.assertEqual(self.errors(corpus()), [])

    def test_each_structural_fault_is_reported(self):
        def mutate(fn):
            c = copy.deepcopy(corpus())
            fn(c)
            return self.errors(c)

        cases = {
            "duplicate task id": lambda c: c["tasks"].append(copy.deepcopy(c["tasks"][0])),
            "duplicate finding id": lambda c: c["tasks"][0]["findings"].append(
                copy.deepcopy(c["tasks"][0]["findings"][0])
            ),
            "unknown class": lambda c: c["tasks"][0]["findings"][0].update({"class": "vibes"}),
            "bad regex": lambda c: c["tasks"][0]["findings"][0].update({"evidence_any": ["("]}),
            "no evidence": lambda c: c["tasks"][0]["findings"][0].update({"evidence_any": []}),
            "unknown role": lambda c: c["tasks"][0].update({"roles": ["implementer"]}),
            "no findings": lambda c: c["tasks"][0].update({"findings": []}),
            "bad sha": lambda c: c["tasks"][0].update({"base": "xyz"}),
            "bad digest": lambda c: c["tasks"][0].update({"diff_sha256": "abc"}),
            "bad pattern regex": lambda c: c["tasks"][0]["findings"][0].update(
                {"repro": {"kind": "pattern", "path": "a", "regex": "(", "present": True}}
            ),
            "unknown repro": lambda c: c["tasks"][0]["findings"][0].update({"repro": {"kind": "magic"}}),
            "missing key": lambda c: c["tasks"][0]["findings"][0].pop("summary"),
            "private path": lambda c: c["tasks"][0]["findings"][0].update({"summary": "see /ho" + "me/someone/x"}),
            "dropped without reason": lambda c: c["dropped"].append({"id": "d1"}),
        }
        for name, fn in cases.items():
            self.assertTrue(mutate(fn), name)

    def test_brief_task_needs_input_and_absent_from_input_patterns(self):
        brief = task(
            "b1",
            roles=("brief-writer",),
            kind="brief",
            findings=[
                finding(
                    "g1",
                    paths=("brief",),
                    repro={"kind": "ref", "finding": "t1/f1"},
                    absent_from_input=["race"],
                )
            ],
            input_file="inputs/x.md",
            input_sha256="d" * 64,
        )
        brief.pop("diff_sha256")
        brief.pop("base")
        brief.pop("head")
        c = corpus(task(), brief)
        self.assertEqual(self.errors(c), [])
        broken = copy.deepcopy(c)
        broken["tasks"][1]["findings"][0]["repro"] = {"kind": "ref", "finding": "t1/zzz"}
        self.assertTrue(self.errors(broken))
        broken = copy.deepcopy(c)
        del broken["tasks"][1]["input_file"]
        self.assertTrue(self.errors(broken))


class RealCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = benchmark.load_corpus()

    def test_real_corpus_is_structurally_clean(self):
        self.assertEqual(benchmark.corpus_errors(self.c, check_files=True), [])

    def test_at_least_six_seeds_and_the_brief_column(self):
        self.assertGreaterEqual(len(self.c["tasks"]), 6)
        roles = {r for t in self.c["tasks"] for r in t["roles"]}
        self.assertTrue({"reviewer", "adversarial-reviewer", "brief-writer"} <= roles)

    def test_dropped_seeds_are_recorded_with_a_reason(self):
        for d in self.c["dropped"]:
            self.assertTrue(d["reason"].strip())

    def test_every_probe_is_used_and_every_used_probe_exists(self):
        used = {f["repro"]["probe"] for t in self.c["tasks"] for f in t["findings"] if f["repro"]["kind"] == "probe"}
        on_disk = {p.name for p in benchmark.PROBES.glob("*.py")}
        self.assertEqual(used, on_disk)

    def test_no_private_identifier_in_the_corpus_files(self):
        files = [benchmark.CORPUS, *benchmark.PROBES.glob("*.py"), *benchmark.INPUTS.glob("*")]
        self.assertGreaterEqual(len(files), 3)
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(PRIVACY_RE.search(line), f"{f.name}:{n}")

    def test_corpus_names_no_private_repository(self):
        text = benchmark.CORPUS.read_text(encoding="utf-8")
        for word in ("Plan" + " A", "SA" + "FO", "scrum-" + "around", "/ho" + "me/", "~/"):
            self.assertNotIn(word, text)

    def test_privacy_scan_bites(self):
        self.assertIsNotNone(PRIVACY_RE.search("contact me at " + "192" + ".168" + ".1.20"))


class GitBacked(unittest.TestCase):
    """The resolve and reproduce machinery, against a synthetic repository."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "src").mkdir()
        self.shas = {}
        for name, text in (("base", "ok\n"), ("defect", "unchecked value\n"), ("fixed", "checked value\n")):
            (self.repo / "src" / "a.py").write_text(text, encoding="utf-8")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", name)
            self.shas[name] = git(self.repo, "rev-parse", "HEAD").strip()
        self.probes = Path(self._tmp.name) / "probes"
        self.probes.mkdir()
        (self.probes / "p.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "t = (Path(sys.argv[1]) / 'src' / 'a.py').read_text()\n"
            "print('OBSERVED' if 'unchecked' in t else 'NOT-OBSERVED')\n",
            encoding="utf-8",
        )

    def corpus_for(self, fnd):
        digest = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])
        t = task(base=self.shas["base"], head=self.shas["defect"], diff_sha256=digest, findings=[fnd])
        return corpus(t)

    def pattern_finding(self, **kw):
        return finding(fixed=self.shas["fixed"], **kw)

    def run_reproduce(self, c):
        return benchmark.reproduce(self.repo, c, probes_dir=self.probes)

    def test_check_commits_ok_missing_and_no_clone(self):
        c = self.corpus_for(self.pattern_finding())
        self.assertEqual(benchmark.check_commits(self.repo, c), ("ok", []))
        bad = copy.deepcopy(c)
        bad["tasks"][0]["head"] = "f" * 40
        status, errs = benchmark.check_commits(self.repo, bad)
        self.assertEqual(status, "fail")
        self.assertTrue(any("f" * 12 in e for e in errs))
        bad = copy.deepcopy(c)
        bad["tasks"][0]["diff_sha256"] = "0" * 64
        self.assertEqual(benchmark.check_commits(self.repo, bad)[0], "fail")
        with tempfile.TemporaryDirectory() as empty:
            status, errs = benchmark.check_commits(Path(empty), c)
            self.assertEqual((status, errs), ("not-run", []))

    def test_a_shallow_clone_is_not_a_clone_for_the_check(self):
        shallow = Path(self._tmp.name) / "shallow"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "file://" + str(self.repo), str(shallow)],
            check=True,
            capture_output=True,
            env={**os.environ, **GIT_ENV},
        )
        c = self.corpus_for(self.pattern_finding())
        self.assertEqual(benchmark.check_commits(shallow, c), ("not-run", []))

    def test_check_commits_needs_no_network(self):
        c = self.corpus_for(self.pattern_finding())
        env = {**os.environ, "GIT_ALLOW_PROTOCOL": "none"}
        saved = os.environ.copy()
        try:
            os.environ.update(env)
            self.assertEqual(benchmark.check_commits(self.repo, c)[0], "ok")
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_pattern_seed_reproduces(self):
        res = self.run_reproduce(self.corpus_for(self.pattern_finding()))
        self.assertEqual([r["ok"] for r in res], [True], res)

    def test_pattern_seed_that_is_also_present_after_the_fix_is_not_reproduced(self):
        c = self.corpus_for(
            self.pattern_finding(repro={"kind": "pattern", "path": "src/a.py", "regex": "value", "present": True})
        )
        self.assertFalse(self.run_reproduce(c)[0]["ok"])

    def test_pattern_seed_absent_at_the_defect_is_not_reproduced(self):
        c = self.corpus_for(
            self.pattern_finding(repro={"kind": "pattern", "path": "src/a.py", "regex": "zzz", "present": True})
        )
        self.assertFalse(self.run_reproduce(c)[0]["ok"])

    def test_base_expectation_is_enforced(self):
        c = self.corpus_for(self.pattern_finding(at_base="observed"))
        self.assertFalse(self.run_reproduce(c)[0]["ok"])

    def test_probe_seed_reproduces_and_a_wrong_probe_does_not(self):
        probe = {"kind": "probe", "probe": "p.py", "args": []}
        self.assertTrue(self.run_reproduce(self.corpus_for(self.pattern_finding(repro=probe)))[0]["ok"])
        (self.probes / "p.py").write_text("print('NOT-OBSERVED')\n", encoding="utf-8")
        self.assertFalse(self.run_reproduce(self.corpus_for(self.pattern_finding(repro=probe)))[0]["ok"])
        # a probe that prints OBSERVED and then crashes must fail, even where only the defect state is checked
        (self.probes / "p.py").write_text("print('OBSERVED')\nraise SystemExit(3)\n", encoding="utf-8")
        alone = finding(repro=probe, at_base="n/a")
        self.assertFalse(self.run_reproduce(self.corpus_for(alone))[0]["ok"])

    def test_unfixed_finding_needs_only_the_defect_state(self):
        c = self.corpus_for(finding())
        self.assertTrue(self.run_reproduce(c)[0]["ok"])

    def test_cli_check_and_reproduce_exit_codes(self):
        c = self.corpus_for(self.pattern_finding())
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corpus.json"
            path.write_text(json.dumps(c), encoding="utf-8")
            base = ["--corpus", str(path), "--repo", str(self.repo), "--probes", str(self.probes)]
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(benchmark.main([*base, "--check"]), 0)
                self.assertEqual(benchmark.main([*base, "--reproduce"]), 0)
                c["tasks"][0]["head"] = "f" * 40
                path.write_text(json.dumps(c), encoding="utf-8")
                self.assertEqual(benchmark.main([*base, "--check"]), 1)

    def test_diff_digest_is_stable_and_sensitive(self):
        a = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])
        self.assertEqual(a, benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"]))
        self.assertNotEqual(a, benchmark.diff_digest(self.repo, self.shas["base"], self.shas["fixed"]))
        self.assertRegex(a, re.compile(r"[0-9a-f]{64}"))


if __name__ == "__main__":
    unittest.main()
