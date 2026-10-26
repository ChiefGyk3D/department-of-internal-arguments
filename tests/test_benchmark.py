"""Tests for the benchmark corpus, the rubric and the scorer (scripts/benchmark.py). Synthetic records only; no model
is ever run. The git tests build a throwaway repository in a temporary directory."""

import contextlib
import copy
import datetime
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


def finding(
    fid="f1", cls="input-validation", rnd=1, paths=("src/a.py",), evidence=("unchecked", "value"), quotes=None, **extra
):
    f = {
        "id": fid,
        "class": cls,
        "severity": "important",
        "round": rnd,
        "scope": "in_diff",
        "summary": "A synthetic finding.",
        "source": "synthetic",
        "paths": list(paths),
        "evidence_any": list(evidence),
        "fixed": None,
        "at_base": "not-observed",
        "quotes": list(quotes) if quotes is not None else ["unchecked value"],
        "touched": ["unchecked"],
        "repro": {"kind": "pattern", "path": "src/a.py", "regex": "unchecked", "present": True},
    }
    f.update(extra)
    return f


def pre_existing(fid, **kw):
    f = finding(fid, **kw)
    f["scope"] = "pre_existing"
    del f["touched"]
    return f


def brief_finding(fid="g1", **extra):
    f = finding(fid, cls="brief-gap", paths=("brief",), evidence=("race", "rename"))
    f["scope"] = "brief"
    f["repro"] = {"kind": "ref", "finding": "t1/f1"}
    f["absent_from_input"] = ["lost update"]
    del f["quotes"], f["touched"]
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
        "date": "2026-10-01",
        "commit": "a" * 40,
        "tools_enabled": ["read"],
        "outcome": "completed",
        "findings": [
            {"class": "input-validation", "path": "src/a.py", "evidence": "the unchecked value is never validated"}
        ],
        "wall_clock_s": 12.5,
        "tokens": {"input": 1000, "output": 200},
        "approval_ref": "synthetic",
        "corpus_hidden": True,
        "refusals": [],
    }
    r.update(over)
    return r


GRANDCHILD = (
    "import subprocess, sys, time\n"
    'late = \'import time, sys; time.sleep(2.5); open(sys.argv[1], "w").write("x")\'\n'
    "subprocess.Popen([sys.executable, '-c', late, %r])\n"
    "time.sleep(%s)\n"
)


def grandchild_code(marker, stay):
    """A child that starts a grandchild which writes `marker` after 2.5 s, then sleeps `stay` seconds (0: exits)."""
    return GRANDCHILD % (str(marker), stay)


def parse(rec, c=None, repo=None):
    return benchmark.parse_result(json.dumps(rec), 1, c or corpus(), repo)


class ModelId(unittest.TestCase):
    def test_exact_ids_pass(self):
        for m in (
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-sonnet-5",
            "gpt-5.1-codex",
            "gpt-4-turbo",
            "gemini-2.5-pro",
            "o3-mini",
            "gemma4:12b",
            "llama3.2:8b",
            "nomic-embed-text:v1.5",
            "hf.co/org/m:7b",
        ):
            self.assertTrue(benchmark.exact_model_id(m), m)

    def test_family_names_and_aliases_fail(self):
        for m in (
            "sonnet",
            "opus",
            "claude",
            "claude-sonnet",
            "claude-3",
            "claude-3-5",
            "gpt-5",
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

    def test_an_explicit_latest_tag_is_a_moving_alias(self):
        for m in ("gemma:latest", "llama3:latest", "llama3.2:latest", "hf.co/org/m:latest"):
            self.assertFalse(benchmark.exact_model_id(m), m)

    def test_a_local_tag_must_itself_be_a_valid_ollama_tag(self):
        # pins the local branch: it must defer to the tag rules, not accept anything with a colon
        for m in ("a:1b", "gemma4:", "gemma4:12b-", "../x:7b", "ollama:12b", "gemma4:big", ":7b", "x y:7b"):
            self.assertFalse(benchmark.exact_model_id(m), m)


class Matching(unittest.TestCase):
    def setUp(self):
        self.t = task(
            findings=[
                finding("f1", "input-validation", 1, ("src/a.py",), ("unchecked", "value"), ["unchecked value"]),
                pre_existing(
                    "f2",
                    cls="ownership-trust",
                    rnd=2,
                    paths=("src/b.py",),
                    evidence=("forg(ed|ery)", "marker"),
                    quotes=["forged marker"],
                ),
            ]
        )

    def m(self, reported, t=None):
        return benchmark.match_findings(t or self.t, reported)

    GOOD = {"class": "input-validation", "path": "src/a.py", "evidence": "the unchecked value is never validated"}

    def test_match_needs_class_path_quote_and_two_evidence_hits(self):
        self.assertEqual(self.m([self.GOOD])["matched"], ["f1"])
        for key, bad in (
            ("class", "ownership-trust"),
            ("path", "src/zzz.py"),
            ("evidence", "looks fine to me"),
            ("evidence", "this is unchecked"),  # one distinct pattern only
            ("evidence", "unchecked and the value"),  # two patterns but not the quoted code
        ):
            r = self.m([{**self.GOOD, key: bad}])
            self.assertEqual(r["matched"], [], (key, bad))
            self.assertEqual(r["false"], 1, (key, bad))

    def test_evidence_min_override_lowers_the_hit_count_for_one_finding(self):
        one_hit = {**self.GOOD, "evidence": "this unchecked thing"}  # hits one of the two patterns
        t = task(findings=[finding("f1", evidence=("unchecked", "zzz"), quotes=["unchecked thing"])])
        self.assertEqual(self.m([one_hit], t)["matched"], [])
        t = task(findings=[finding("f1", evidence=("unchecked", "zzz"), quotes=["unchecked thing"], evidence_min=1)])
        self.assertEqual(self.m([one_hit], t)["matched"], ["f1"])
        t = task(findings=[finding("f1", evidence=("unchecked", "zzz"), quotes=["quoted code"], evidence_min=1)])
        self.assertEqual(self.m([one_hit], t)["matched"], [])

    def test_a_brief_finding_needs_no_quote(self):
        b = task("b1", roles=("brief-writer",), kind="brief", findings=[brief_finding()])
        r = self.m([{"class": "brief-gap", "path": "brief", "evidence": "name the race before the rename"}], b)
        self.assertEqual(r["matched"], ["g1"])

    def test_duplicate_is_not_a_second_match_and_not_false(self):
        r = self.m([self.GOOD, self.GOOD])
        self.assertEqual((r["matched"], r["duplicates"], r["false"]), (["f1"], 1, 0))

    def test_unevidenced_and_malformed_findings_are_false(self):
        r = self.m(
            [
                {"class": "input-validation", "path": "src/a.py", "evidence": ""},
                {"class": "made-up-class", "path": "src/a.py", "evidence": "unchecked value"},
                {"class": "input-validation", "path": "src/a.py"},
            ]
        )
        self.assertEqual((r["matched"], r["false"], r["unevidenced"]), ([], 3, 3))

    def test_the_reviewers_three_report_case_gives_one_set_in_every_order(self):
        quotes = ["code zero", "code one", "code two", "code three"]
        t = task(
            findings=[finding(f"e{i}", "dead-code", 1, ("src/a.py",), ("alpha", "beta"), [quotes[i]]) for i in range(4)]
        )

        def report(*ids):
            text = "alpha beta " + " ".join(quotes[i] for i in ids)
            return {"class": "dead-code", "path": "src/a.py", "evidence": text}

        reports = [report(0, 2, 3), report(0, 1, 2, 3), report(2)]
        seen = {tuple(sorted(self.m(list(order), t)["matched"])) for order in itertools.permutations(reports)}
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(len(next(iter(seen))), 3)

    def test_fifty_copies_of_one_valid_finding_match_once_and_are_not_precise(self):
        r = self.m([self.GOOD] * 50)
        self.assertEqual((r["matched"], r["duplicates"], r["false"]), (["f1"], 49, 0))
        row = benchmark.summarize([parse(record(findings=[self.GOOD] * 50), corpus(self.t))], corpus(self.t))
        cells = [x.strip() for x in row.splitlines()[-1].strip("|").split("|")]
        self.assertEqual((cells[4], cells[7]), ("50.0", "2.0"))  # recall 1 of 2, precision 1 of 50 reported

    def test_the_quote_may_break_across_lines_in_the_report(self):
        r = self.m([{**self.GOOD, "evidence": "the unchecked\n  value is bad"}])
        self.assertEqual(r["matched"], ["f1"])

    def test_two_patterns_satisfied_by_the_same_words_count_once(self):
        t = task(findings=[finding("f1", evidence=(r"\.\.", r"\.\./x"), quotes=["unchecked value"])])
        r = self.m([{**self.GOOD, "evidence": "../x unchecked value"}], t)
        self.assertEqual(r["matched"], [])
        r = self.m([{**self.GOOD, "evidence": "a .. b and ../x unchecked value"}], t)
        self.assertEqual(r["matched"], ["f1"])

    def test_the_same_word_matched_twice_in_different_places_still_counts_once(self):
        t = task(findings=[finding("f1", evidence=(r"unch\w+", r"\bunchecked\b"), quotes=["unchecked value"])])
        r = self.m([{**self.GOOD, "evidence": "unchecked, and again unchecked value"}], t)
        self.assertEqual(r["matched"], [])
        t = task(findings=[finding("f1", evidence=(r"unch\w+", r"\bvalue\b"), quotes=["unchecked value"])])
        r = self.m([{**self.GOOD, "evidence": "unchecked, and again unchecked value"}], t)
        self.assertEqual(r["matched"], ["f1"])

    def overlapping(self):
        shared = ("alpha", "beta")
        return task(
            findings=[
                finding("e1", "dead-code", 1, ("src/a.py",), shared, ["code one", "code both"]),
                finding("e2", "dead-code", 1, ("src/a.py",), shared, ["code two", "code both"]),
            ]
        )

    def test_overlapping_candidates_are_assigned_by_maximum_matching_in_every_order(self):
        t = self.overlapping()
        both = {"class": "dead-code", "path": "src/a.py", "evidence": "aaa alpha beta, code one and code two"}
        only_one = {"class": "dead-code", "path": "src/a.py", "evidence": "zzz alpha beta, code one"}
        for order in itertools.permutations([both, only_one]):
            r = self.m(list(order), t)
            self.assertEqual(sorted(r["matched"]), ["e1", "e2"], order)
            self.assertEqual((r["false"], r["duplicates"]), (0, 0))

    def test_permutations_of_a_larger_overlapping_set_all_agree(self):
        t = self.overlapping()
        reports = [
            {"class": "dead-code", "path": "src/a.py", "evidence": "a alpha beta, code one and code two"},
            {"class": "dead-code", "path": "src/a.py", "evidence": "b alpha beta, code one"},
            {"class": "dead-code", "path": "src/a.py", "evidence": "c alpha beta, code both"},
            {"class": "dead-code", "path": "src/a.py", "evidence": "d unrelated"},
        ]
        seen = {
            (tuple(sorted(r["matched"])), r["false"], r["duplicates"])
            for r in (self.m(list(p), t) for p in itertools.permutations(reports))
        }
        self.assertEqual(seen, {(("e1", "e2"), 1, 1)})

    def test_rounds_avoided_counts_only_fully_matched_rounds(self):
        self.assertEqual(benchmark.rounds_avoided(self.t, ["f1"]), (1, 2))
        self.assertEqual(benchmark.rounds_avoided(self.t, ["f1", "f2"]), (2, 2))
        self.assertEqual(benchmark.rounds_avoided(self.t, []), (0, 2))
        both = task(findings=[finding("a", rnd=1), finding("b", rnd=1)])
        self.assertEqual(benchmark.rounds_avoided(both, ["a"]), (0, 1))


def first_word(pattern):
    """What a lazy reviewer would echo: the first literal word of a pattern."""
    plain = re.sub(r"\(\?i\)|\\[a-zA-Z]", "", pattern)
    words = re.findall(r"[A-Za-z_][A-Za-z_-]{2,}", plain)
    return words[0] if words else plain.replace("\\", "")


class RealCorpusMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = benchmark.load_corpus()

    def task(self, tid):
        return next(t for t in self.c["tasks"] if t["id"] == tid)

    def test_report_order_does_not_change_the_score_on_the_real_overlapping_findings(self):
        t = self.task("lead-install-first-head")
        a = {
            "class": "error-reporting",
            "path": "scripts/install.py",
            "evidence": "if dest.is_symlink() or not dest.is_file(): the directory is omitted, "
            "no row, return None in _lead_row",
        }
        b = {
            "class": "error-reporting",
            "path": "scripts/install.py",
            "evidence": "if len(raw) > MAX_MANAGED_BYTES: an oversized file is silently omitted "
            "by _lead_row, return None",
        }
        one = benchmark.match_findings(t, [a, b])
        two = benchmark.match_findings(t, [b, a])
        self.assertEqual(sorted(one["matched"]), ["lead-list-hides-oversized", "lead-list-hides-unsafe"])
        self.assertEqual(sorted(two["matched"]), sorted(one["matched"]))

    def test_echoing_the_label_scores_zero_recall(self):
        # Built the way the independent reviewer built it: class and path copied from the label, evidence the first
        # literal word of the first pattern. Without having seen the diff this must match nothing.
        for t in self.c["tasks"]:
            reports = [
                {"class": f["class"], "path": f["paths"][0], "evidence": first_word(f["evidence_any"][0])}
                for f in t["findings"]
            ]
            self.assertEqual(benchmark.match_findings(t, reports)["matched"], [], t["id"])

    def test_echoing_every_pattern_word_still_needs_the_quoted_code_on_diff_tasks(self):
        for t in self.c["tasks"]:
            if t["kind"] != "diff":
                continue
            reports = [
                {
                    "class": f["class"],
                    "path": f["paths"][0],
                    "evidence": " ".join(first_word(p) for p in f["evidence_any"]),
                }
                for f in t["findings"]
            ]
            self.assertEqual(benchmark.match_findings(t, reports)["matched"], [], t["id"])

    def satisfying_words(self, pattern):
        plain = re.sub(r"\(\?i\)|\\[a-zA-Z]", "", pattern)
        for w in re.findall(r"[A-Za-z_][A-Za-z_.-]+", plain):
            if re.search(pattern, w):
                return w
        return None

    def test_KNOWN_LIMIT_copying_the_public_quotes_and_patterns_scores_full_recall_on_diff_tasks(self):
        # Documented limit, not a defect to fix here: corpus.json is public, so a report built from its quotes and
        # pattern words matches with no code reviewed. This is why a scored run must never show the model the corpus
        # (the corpus_hidden field of a result record).
        total = hit = 0
        for t in self.c["tasks"]:
            if t["kind"] != "diff":
                continue
            reports = []
            for f in t["findings"]:
                words = [w for w in (self.satisfying_words(p) for p in f["evidence_any"]) if w]
                text = " ; ".join([*words, *f["quotes"]])
                reports.append({"class": f["class"], "path": f["paths"][0], "evidence": text})
            total += len(t["findings"])
            hit += len(benchmark.match_findings(t, reports)["matched"])
        self.assertEqual((hit, total), (16, 16))

    def test_KNOWN_LIMIT_the_brief_task_is_trivially_gameable_from_the_public_patterns(self):
        t = self.task("lead-persona-brief")
        filler = " lorem" * 40  # keeps the two matched phrases apart, so neither wide pattern window covers the other
        texts = {
            "brief-listing-never-hides": "unsafe row." + filler + " list-managed gives the file a row",
            "brief-concurrent-edit": "editor edit." + filler + " race then rename",
            "brief-fs-errors-are-rows": "error is a row." + filler + " unreadable row",
        }
        reports = [{"class": "brief-gap", "path": "brief", "evidence": v} for v in texts.values()]
        self.assertEqual(len(benchmark.match_findings(t, reports)["matched"]), 3)

    def test_a_report_with_the_label_words_and_the_real_line_does_match(self):
        t = self.task("codex-adapter-first-head")
        f = t["findings"][0]
        text = f"The filesystem root is accepted: {f['quotes'][0]} never refuses it; see _check_target"
        r = benchmark.match_findings(t, [{"class": f["class"], "path": f["paths"][0], "evidence": text}])
        self.assertEqual(r["matched"], [f["id"]])


class ResultRecords(unittest.TestCase):
    def rejects(self, rec, fragment, c=None, repo=None):
        with self.assertRaises(ValueError) as cm:
            parse(rec, c, repo)
        self.assertIn(fragment, str(cm.exception))

    def test_good_record_parses(self):
        self.assertEqual(parse(record())["model"], "claude-sonnet-5-5")

    def test_family_name_instead_of_exact_model_id_is_rejected(self):
        for m in ("sonnet", "claude-sonnet", "claude-3", "opus", "inherit", "gemma:latest"):
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

    def test_wall_clock_must_be_a_positive_bounded_number(self):
        for bad in (0, -1, "12", True, float("nan"), float("inf"), 10**9):
            self.rejects(record(wall_clock_s=bad), "wall_clock_s")

    def test_tokens_cost_and_run_are_bounded(self):
        self.rejects(record(tokens={"input": 10**400, "output": 1}), "tokens")
        self.rejects(record(tokens={"input": 1, "output": benchmark.MAX_TOKENS + 1}), "tokens")
        self.assertEqual(parse(record(tokens={"input": benchmark.MAX_TOKENS, "output": 0}))["tokens"]["output"], 0)
        self.rejects(record(cost_usd=float("inf")), "cost_usd")
        self.rejects(record(cost_usd=-1), "cost_usd")
        self.rejects(record(run=benchmark.MAX_RUN + 1), "run")

    def test_a_scored_run_must_have_hidden_the_corpus_from_the_model(self):
        self.assertEqual(parse(record(corpus_hidden=True))["corpus_hidden"], True)
        for bad in (False, "true", 1, None):
            self.rejects(record(corpus_hidden=bad), "corpus_hidden")
        r = record()
        del r["corpus_hidden"]
        self.rejects(r, "corpus_hidden")

    def test_a_date_more_than_a_day_ahead_is_refused(self):
        today = datetime.datetime.now(datetime.UTC).date()
        self.assertEqual(parse(record(date=(today + datetime.timedelta(days=1)).isoformat()))["run"], 1)
        self.rejects(record(date=(today + datetime.timedelta(days=2)).isoformat()), "future")

    def test_invisible_and_blank_looking_characters_are_rejected_in_text_fields(self):
        for ch in ("\ufe0f", "\u034f", "\u3164", "\u2800", "\u00a0", "\u3000", "\U000e0001", "\u180e", "\u0378"):
            for key in ("client", "client_version", "approval_ref", "notes"):
                self.rejects(record(**{key: "ok" + ch + "ok"}), key)
            self.rejects(record(findings=[{"class": "dead-code", "path": "a", "evidence": "x" + ch + "y"}]), "control")

    def test_commit_must_be_the_full_40_character_id(self):
        for bad in ("abcdef1", "a" * 39, "a" * 41, "A" * 40, "g" * 40, "xyz", 5, None):
            self.rejects(record(commit=bad), "40-character")
        self.assertEqual(parse(record(commit="0123456789abcdef0123456789abcdef01234567"))["run"], 1)

    def test_cost_has_an_upper_bound(self):
        self.assertEqual(parse(record(cost_usd=10_000_000))["cost_usd"], 10_000_000)
        self.rejects(record(cost_usd=10_000_001), "cost_usd")

    def test_the_date_must_be_a_real_calendar_date(self):
        for bad in ("2026-02-30", "2026-13-01", "2026-00-10", "2026-1-01", "20261101", "2026-11-31"):
            self.rejects(record(date=bad), "date")
        self.assertEqual(parse(record(date="2024-02-29"))["date"], "2024-02-29")

    def test_control_and_invisible_characters_are_rejected_in_text_fields(self):
        for ch in ("\x1b[31m", "\n", "\x00", "​", "‮", "\x7f", " "):
            for key in ("client", "client_version", "approval_ref", "notes"):
                self.rejects(record(**{key: "ok" + ch + "ok"}), key)
            self.rejects(record(tools_enabled=["read" + ch]), "tools_enabled")
            self.rejects(record(findings=[{"class": "dead-code", "path": "a" + ch, "evidence": "e"}]), "control")
        self.rejects(record(refusals=[{"kind": "refusal", "note": "x\x1b"}]), "refusal")
        self.rejects(record(model="gemma4:12b", quantization="q4\x1b", context=8192), "quantization")

    def test_evidence_may_hold_line_breaks_and_tabs_but_no_other_control(self):
        ok = record(findings=[{"class": "dead-code", "path": "a.py", "evidence": "line one\n\tline two\r\n"}])
        self.assertEqual(len(parse(ok)["findings"]), 1)
        self.rejects(record(findings=[{"class": "dead-code", "path": "a.py", "evidence": "x\x1b[2Jy"}]), "control")

    def test_local_models_carry_quantization_and_context(self):
        self.rejects(record(model="gemma4:12b"), "quantization")
        ok = record(model="gemma4:12b", quantization="q4_K_M", context=8192)
        self.assertEqual(parse(ok)["context"], 8192)
        self.rejects(record(model="gemma4:12b", quantization="q4_K_M", context=0), "context")
        self.rejects(record(model="gemma4:12b", quantization="q4_K_M", context=benchmark.MAX_CONTEXT + 1), "context")
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

    def test_at_most_max_findings_per_record(self):
        one = {"class": "dead-code", "path": "p", "evidence": "e"}
        self.assertEqual(len(parse(record(findings=[one] * 50))["findings"]), 50)
        self.rejects(record(findings=[one] * 51), "findings")

    def test_malformed_json_and_not_an_object(self):
        for line in ("{", "[]", '"x"', "1"):
            with self.assertRaises(ValueError):
                benchmark.parse_result(line, 1, corpus())

    def test_a_duplicate_json_key_is_rejected_not_last_wins(self):
        line = json.dumps(record()).replace('"run": 1', '"run": 1, "run": 2')
        self.assertIn('"run": 2', line)
        with self.assertRaises(ValueError) as cm:
            benchmark.parse_result(line, 1, corpus())
        self.assertIn("duplicate", str(cm.exception))

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

    def test_a_duplicate_key_in_the_corpus_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "corpus.json"
            p.write_text('{"version": 1, "version": 1, "tasks": [], "dropped": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                benchmark.load_corpus(p)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.c = corpus(
            task(
                findings=[
                    finding("f1", "input-validation", 1, ("src/a.py",), ("unchecked", "value"), ["unchecked value"]),
                    pre_existing(
                        "f2",
                        cls="ownership-trust",
                        rnd=2,
                        paths=("src/b.py",),
                        evidence=("forged", "marker"),
                        quotes=["forged marker"],
                    ),
                ]
            ),
            task(
                "t2",
                roles=("reviewer",),
                findings=[finding("g1", "dead-code", 1, ("src/c.py",), ("dead", "code"), ["dead code"])],
            ),
        )

    def test_metrics_for_one_record(self):
        rec = parse(
            record(
                findings=[
                    {"class": "input-validation", "path": "src/a.py", "evidence": "unchecked value"},
                    {"class": "dead-code", "path": "src/a.py", "evidence": "dead code"},
                ]
            ),
            self.c,
        )
        m = benchmark.score_record(rec, self.c)
        self.assertEqual((m["expected"], m["matched"], m["false"], m["reported"]), (2, 1, 1, 2))
        self.assertEqual((m["rounds_avoided"], m["rounds_total"]), (1, 2))
        self.assertEqual(m["by_scope"]["in_diff"], [1, 1])
        self.assertEqual(m["by_scope"]["pre_existing"], [1, 0])

    def recs(self):
        return [
            parse(record(), self.c),
            parse(
                record(
                    task="t2",
                    wall_clock_s=7.5,
                    tokens={"input": 500, "output": 100},
                    findings=[{"class": "dead-code", "path": "src/c.py", "evidence": "this dead code is unused"}],
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

    def test_table_for_synthetic_records_with_recall_per_scope(self):
        recs = self.recs()
        table = benchmark.summarize(recs, self.c)
        self.assertIn(
            "| Role | Model | Client | Tasks | Recall % | In-diff recall % | Pre-existing recall % | Precision % |",
            table,
        )
        self.assertIn(
            "| reviewer | claude-haiku-5-5 | claude-code 2.1.0 | 1 of 2 | 0.0 | 0.0 | 0.0 | n/a | 0 | 0 of 2 "
            "| 12.5 | 1000 | 200 | 1 |",
            table,
        )
        self.assertIn(
            "| reviewer | claude-sonnet-5-5 | claude-code 2.1.0 | 2 of 2 | 66.7 | 100.0 | 0.0 | 100.0 | 0 | 2 of 3 "
            "| 10.0 | 1500 | 300 | 0 |",
            table,
        )
        self.assertEqual(table, benchmark.summarize(list(reversed(recs)), self.c))

    def test_precision_is_matched_over_matched_plus_false(self):
        rec = parse(
            record(
                findings=[
                    {"class": "input-validation", "path": "src/a.py", "evidence": "the unchecked value"},
                    {"class": "dead-code", "path": "src/a.py", "evidence": "nothing"},
                    {"class": "dead-code", "path": "src/b.py", "evidence": "nothing"},
                ]
            ),
            self.c,
        )
        row = [ln for ln in benchmark.summarize([rec], self.c).splitlines() if "claude-sonnet" in ln][0]
        cells = [x.strip() for x in row.strip("|").split("|")]
        self.assertEqual((cells[4], cells[7], cells[8]), ("50.0", "33.3", "2"))

    def test_different_client_versions_are_different_rows(self):
        a = parse(record(), self.c)
        b = parse(record(client_version="2.2.0"), self.c)
        lines = [ln for ln in benchmark.summarize([a, b], self.c).splitlines() if "claude-sonnet" in ln]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("claude-code 2.1.0" in ln for ln in lines))
        self.assertTrue(any("claude-code 2.2.0" in ln for ln in lines))

    def test_cli_prints_table_fails_on_bad_record_and_changes_nothing(self):
        routing = ROOT / "policies" / "model-routing.md"
        before = hashlib.sha256(routing.read_bytes()).hexdigest()
        real = benchmark.load_corpus()
        first = real["tasks"][0]
        rec = record(task=first["id"], role=first["roles"][0], findings=[])
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "good.jsonl"
            good.write_text(json.dumps(rec) + "\n", encoding="utf-8")
            bad = Path(d) / "bad.jsonl"
            bad.write_text(json.dumps({**rec, "model": "sonnet"}) + "\n", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(benchmark.main(["--score", str(good), "--skip-commit-check"]), 0)
                self.assertEqual(benchmark.main(["--score", str(bad), "--skip-commit-check"]), 1)
            self.assertIn("| Role | Model |", out.getvalue())
            self.assertIn("commits: NOT VERIFIED", out.getvalue())
            self.assertIn("FAIL", err.getvalue())
            # with the commit check on (the default), a commit that is not in the repository is refused
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(benchmark.main(["--score", str(good)]), 1)
            self.assertIn("not in the given repository", err.getvalue())
            # and a real commit is verified and said to be
            head = git(ROOT, "rev-parse", "HEAD").strip()
            real = Path(d) / "real.jsonl"
            real.write_text(json.dumps({**rec, "commit": head}) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(benchmark.main(["--score", str(real)]), 0)
            self.assertIn("commits: verified", out.getvalue())
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

        def f0(c):
            return c["tasks"][0]["findings"][0]

        cases = {
            "duplicate task id": lambda c: c["tasks"].append(copy.deepcopy(c["tasks"][0])),
            "duplicate finding id": lambda c: c["tasks"][0]["findings"].append(copy.deepcopy(f0(c))),
            "unknown class": lambda c: f0(c).update({"class": "vibes"}),
            "bad regex": lambda c: f0(c).update({"evidence_any": ["(", "x"]}),
            "no evidence": lambda c: f0(c).update({"evidence_any": []}),
            "one pattern and no evidence_min": lambda c: f0(c).update({"evidence_any": ["unchecked"]}),
            "evidence_min too big": lambda c: f0(c).update({"evidence_min": 3}),
            "evidence_min zero": lambda c: f0(c).update({"evidence_min": 0}),
            "unknown role": lambda c: c["tasks"][0].update({"roles": ["implementer"]}),
            "no findings": lambda c: c["tasks"][0].update({"findings": []}),
            "bad sha": lambda c: c["tasks"][0].update({"base": "xyz"}),
            "bad digest": lambda c: c["tasks"][0].update({"diff_sha256": "abc"}),
            "bad pattern regex": lambda c: f0(c).update(
                {"repro": {"kind": "pattern", "path": "a", "regex": "(", "present": True}}
            ),
            "unknown repro": lambda c: f0(c).update({"repro": {"kind": "magic"}}),
            "missing key": lambda c: f0(c).pop("summary"),
            "private path": lambda c: f0(c).update({"summary": "see /ho" + "me/someone/x"}),
            "dropped without reason": lambda c: c["dropped"].append({"id": "d1"}),
            "unknown scope": lambda c: f0(c).update({"scope": "somewhere"}),
            "catastrophic evidence pattern": lambda c: f0(c).update({"evidence_any": ["(a+)+$", "x"]}),
            "catastrophic touched pattern": lambda c: f0(c).update({"touched": ["(a|aa)+$"]}),
            "absolute finding path": lambda c: f0(c).update({"paths": ["/etc/passwd"]}),
            "parent finding path": lambda c: f0(c).update({"paths": ["../x"]}),
            "pathspec-magic finding path": lambda c: f0(c).update({"paths": [":(glob)*"]}),
            "absolute probe": lambda c: f0(c).update({"repro": {"kind": "probe", "probe": "/x/p.py", "args": []}}),
            "parent probe": lambda c: f0(c).update({"repro": {"kind": "probe", "probe": "../p.py", "args": []}}),
            "nested probe": lambda c: f0(c).update({"repro": {"kind": "probe", "probe": "a/p.py", "args": []}}),
            "absolute mutation path": lambda c: f0(c).update(
                {"repro": {"kind": "mutation", "path": "/x", "old": "a", "new": "b", "tests_file": "t.py", "k": None}}
            ),
            "parent mutation path": lambda c: f0(c).update(
                {
                    "repro": {
                        "kind": "mutation",
                        "path": "a/../../x",
                        "old": "a",
                        "new": "b",
                        "tests_file": "t.py",
                        "k": None,
                    }
                }
            ),
            "hostile tests_file": lambda c: f0(c).update(
                {"repro": {"kind": "mutation", "path": "a", "old": "a", "new": "b", "tests_file": "../t.py", "k": None}}
            ),
            "hostile pattern path": lambda c: f0(c).update(
                {"repro": {"kind": "pattern", "path": "../x", "regex": "x", "present": True}}
            ),
            "in_diff without touched": lambda c: f0(c).pop("touched"),
            "in_diff with a bad touched regex": lambda c: f0(c).update({"touched": ["("]}),
            "pre_existing with touched": lambda c: f0(c).update({"scope": "pre_existing"}),
            "no quotes": lambda c: f0(c).pop("quotes"),
            "empty quotes": lambda c: f0(c).update({"quotes": []}),
            "quote too short": lambda c: f0(c).update({"quotes": ["x"]}),
            "quote too long": lambda c: f0(c).update({"quotes": ["x" * 300]}),
            "absence repro without proxy note": lambda c: f0(c).update(
                {"repro": {"kind": "pattern", "path": "a", "regex": "x", "present": False}}
            ),
            "proxy note on a direct repro": lambda c: f0(c).update({"proxy": "why"}),
            "bad mutation repro": lambda c: f0(c).update(
                {"repro": {"kind": "mutation", "path": "a", "old": "x", "new": "x", "tests_file": "t.py", "k": None}}
            ),
        }
        for name, fn in cases.items():
            self.assertTrue(mutate(fn), name)

    def test_proxy_and_mutation_repros_are_accepted_when_well_formed(self):
        c = corpus()
        f = c["tasks"][0]["findings"][0]
        f["repro"] = {"kind": "pattern", "path": "a", "regex": "x", "present": False}
        f["proxy"] = "stands in for the behavior"
        self.assertEqual(self.errors(c), [])
        del f["proxy"]
        f["repro"] = {"kind": "mutation", "path": "a", "old": "x", "new": "y", "tests_file": "t.py", "k": None}
        self.assertEqual(self.errors(c), [])

    def test_a_hostile_brief_input_file_is_refused(self):
        c = self.brief_corpus()
        for bad in ("/etc/hostname", "../x.md", "a/../../x.md", "a\\b.md", ""):
            broken = copy.deepcopy(c)
            broken["tasks"][1]["input_file"] = bad
            self.assertTrue(self.errors(broken), bad)

    def test_a_pre_existing_finding_needs_quotes_but_no_touched_line(self):
        c = corpus(task(findings=[pre_existing("f1")]))
        self.assertEqual(self.errors(c), [])

    def brief_corpus(self):
        brief = task(
            "b1",
            roles=("brief-writer",),
            kind="brief",
            findings=[brief_finding()],
            input_file="inputs/x.md",
            input_sha256="d" * 64,
        )
        for k in ("diff_sha256", "base", "head"):
            brief.pop(k)
        return corpus(task(), brief)

    def test_brief_task_needs_input_and_absent_from_input_patterns(self):
        c = self.brief_corpus()
        self.assertEqual(self.errors(c), [])
        broken = copy.deepcopy(c)
        broken["tasks"][1]["findings"][0]["repro"] = {"kind": "ref", "finding": "t1/zzz"}
        self.assertTrue(self.errors(broken))
        broken = copy.deepcopy(c)
        del broken["tasks"][1]["input_file"]
        self.assertTrue(self.errors(broken))
        broken = copy.deepcopy(c)
        del broken["tasks"][1]["findings"][0]["absent_from_input"]
        self.assertTrue(self.errors(broken))

    def test_a_brief_finding_is_about_the_brief_and_has_the_brief_scope(self):
        c = self.brief_corpus()
        for key, value in (("paths", ["scripts/install.py"]), ("scope", "in_diff")):
            broken = copy.deepcopy(c)
            broken["tasks"][1]["findings"][0][key] = value
            self.assertTrue(self.errors(broken), key)
        broken = copy.deepcopy(c)
        broken["tasks"][1]["findings"][0]["quotes"] = ["not for a brief"]
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

    def test_both_scopes_are_present_and_labeled(self):
        scopes = {f["scope"] for t in self.c["tasks"] for f in t["findings"]}
        self.assertEqual(scopes, {"in_diff", "pre_existing", "brief"})
        counts = {s: sum(f["scope"] == s for t in self.c["tasks"] for f in t["findings"]) for s in scopes}
        self.assertEqual(counts, {"in_diff": 10, "pre_existing": 6, "brief": 3})

    def test_every_absence_only_repro_says_it_is_a_proxy(self):
        for t in self.c["tasks"]:
            for f in t["findings"]:
                r = f["repro"]
                if r["kind"] == "pattern" and r["present"] is False:
                    self.assertTrue(f.get("proxy"), f["id"])

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
    """The resolve and reproduce machinery, against a synthetic repository.

    No test here depends on the local git configuration: GIT_ENV points the global and system configuration at the
    null device for the helper that builds the repository, and every call under test goes through benchmark._git,
    which pins the configuration itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "src").mkdir()
        self.long = [f"line {i}\n" if i % 4 else "\n" for i in range(1, 41)]
        self.shas = {}
        for name, text, tweak in (
            ("base", "ok\n", {}),
            ("defect", "unchecked value\n", {10: "changed ten\n", 14: "changed fourteen\n", 30: "changed thirty\n"}),
            ("fixed", "checked value\n", {}),
        ):
            (self.repo / "src" / "a.py").write_text(text, encoding="utf-8")
            if name == "defect":  # a non-ASCII file name, so core.quotePath changes the diff header
                (self.repo / "src" / "\u00e9.txt").write_text("accent\n", encoding="utf-8")
            lines = list(self.long)
            for n, new in tweak.items():
                lines[n - 1] = new
            (self.repo / "src" / "long.txt").write_text("".join(lines), encoding="utf-8")
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

    def run_reproduce(self, c, **kw):
        return benchmark.reproduce(self.repo, c, probes_dir=self.probes, **kw)

    def test_check_commits_ok_missing_and_no_clone(self):
        c = self.corpus_for(self.pattern_finding())
        self.assertEqual(benchmark.check_commits(self.repo, c), ("ok", []))
        bad = copy.deepcopy(c)
        bad["tasks"][0]["head"] = "f" * 40
        status, errs = benchmark.check_commits(self.repo, bad)
        self.assertEqual(status, "fail")
        self.assertTrue(any("f" * 12 in e for e in errs))
        with tempfile.TemporaryDirectory() as empty:
            status, errs = benchmark.check_commits(Path(empty), c)
            self.assertEqual((status, errs), ("not-run", []))

    def test_a_digest_mismatch_names_both_possible_causes(self):
        c = self.corpus_for(self.pattern_finding())
        c["tasks"][0]["diff_sha256"] = "0" * 64
        status, errs = benchmark.check_commits(self.repo, c)
        self.assertEqual(status, "fail")
        self.assertIn("rewritten", errs[0])
        self.assertIn("configuration", errs[0])
        self.assertIn("version", errs[0])

    def test_the_digest_does_not_depend_on_git_configuration(self):
        before = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])
        options = {
            "diff.algorithm": "histogram",
            "diff.context": "10",
            "diff.interHunkContext": "5",
            "diff.noprefix": "true",
            "diff.mnemonicPrefix": "true",
            "diff.suppressBlankEmpty": "true",
            "diff.renames": "copies",
            "diff.indentHeuristic": "false",
            "core.quotePath": "false",
        }
        # a plain diff with these options differs from the pinned one (the options are not inert in this repository)
        for k, v in options.items():
            git(self.repo, "config", k, v)
        plain = subprocess.run(
            ["git", "-C", str(self.repo), "diff", self.shas["base"], self.shas["defect"]],
            capture_output=True,
            env={**os.environ, **GIT_ENV},
        ).stdout
        self.assertNotEqual(hashlib.sha256(plain).hexdigest(), before)
        glob = Path(self._tmp.name) / "global.cfg"
        glob.write_text(
            "[diff]\n\tcontext = 12\n\talgorithm = patience\n\tsuppressBlankEmpty = true\n", encoding="utf-8"
        )
        saved = os.environ.get("GIT_CONFIG_GLOBAL")
        os.environ["GIT_CONFIG_GLOBAL"] = str(glob)
        try:
            after = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])
        finally:
            if saved is None:
                del os.environ["GIT_CONFIG_GLOBAL"]
            else:
                os.environ["GIT_CONFIG_GLOBAL"] = saved
        self.assertEqual(after, before)
        self.assertEqual(benchmark.check_commits(self.repo, self.corpus_for(self.pattern_finding()))[0], "ok")

    def test_git_environment_variables_of_the_caller_do_not_leak_in(self):
        env = benchmark._git_env()
        for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            self.assertEqual(env[k], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        saved = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = "/nonexistent-dir-for-the-test"
        try:
            self.assertNotIn("GIT_DIR", benchmark._git_env())
            self.assertEqual(benchmark.check_commits(self.repo, self.corpus_for(self.pattern_finding()))[0], "ok")
        finally:
            if saved is None:
                del os.environ["GIT_DIR"]
            else:
                os.environ["GIT_DIR"] = saved

    def test_scope_labels_are_checked_against_the_diff(self):
        ok = self.corpus_for(self.pattern_finding())
        self.assertEqual(benchmark.check_commits(self.repo, ok)[0], "ok")
        untouched = self.corpus_for(self.pattern_finding(touched=["never appears"]))
        status, errs = benchmark.check_commits(self.repo, untouched)
        self.assertEqual(status, "fail")
        self.assertTrue(any("in_diff" in e for e in errs))
        # a finding whose quote is a line the diff adds cannot be pre_existing unless it was already there at the base
        added = pre_existing("f1", quotes=["unchecked value"], fixed=self.shas["fixed"])
        status, errs = benchmark.check_commits(self.repo, self.corpus_for(added))
        self.assertEqual(status, "fail")
        self.assertTrue(any("pre_existing" in e for e in errs))
        already = pre_existing("f1", quotes=["unchecked value"], fixed=self.shas["fixed"], at_base="observed")
        status, errs = benchmark.check_commits(self.repo, self.corpus_for(already))
        self.assertEqual(status, "fail")  # an added line cannot be pre_existing, whatever the base shows
        self.assertTrue(any("pre_existing" in e for e in errs))
        absent = self.corpus_for(self.pattern_finding(quotes=["a line that is not in the code"]))
        status, errs = benchmark.check_commits(self.repo, absent)
        self.assertEqual(status, "fail")
        self.assertTrue(any("quote not found" in e for e in errs))

    # ---- the digest does not depend on attributes, drivers, config or the working tree

    def clean_digest(self):
        return benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])

    def poison(self, source):
        """Make `source` an attribute or driver source that would change a plain git diff. Returns (env, undo)."""
        repo = self.repo
        env = {}
        undo = []
        if source == "user-attributes":
            fake = Path(self._tmp.name) / "fakehome"
            (fake / ".config" / "git").mkdir(parents=True)
            (fake / ".config" / "git" / "attributes").write_text("*.py -diff\n", encoding="utf-8")
            env = {"HOME": str(fake), "XDG_CONFIG_HOME": str(fake / ".config")}
        elif source == "core.attributesFile":
            f = Path(self._tmp.name) / "attrs"
            f.write_text("* binary\n", encoding="utf-8")
            git(repo, "config", "core.attributesFile", str(f))
            undo.append(lambda: git(repo, "config", "--unset", "core.attributesFile"))
        elif source == "info/attributes":
            f = repo / ".git" / "info" / "attributes"
            f.parent.mkdir(exist_ok=True)
            f.write_text("*.py -diff\n", encoding="utf-8")
            undo.append(f.unlink)
        elif source == "worktree .gitattributes":
            f = repo / ".gitattributes"
            f.write_text("* binary\n", encoding="utf-8")
            undo.append(f.unlink)
        elif source == "index .gitattributes":
            f = repo / ".gitattributes"
            f.write_text("* binary\n", encoding="utf-8")
            git(repo, "add", ".gitattributes")
            undo.append(lambda: (git(repo, "rm", "-q", "--cached", ".gitattributes"), f.unlink()))
        elif source == "binary diff driver":
            f = repo / ".git" / "info" / "attributes"
            f.parent.mkdir(exist_ok=True)
            f.write_text("*.py diff=evil\n", encoding="utf-8")
            git(repo, "config", "diff.evil.binary", "true")
            undo.append(f.unlink)
            undo.append(lambda: git(repo, "config", "--unset", "diff.evil.binary"))
        else:
            raise AssertionError(source)
        return env, undo

    SOURCES = (
        "user-attributes",
        "core.attributesFile",
        "info/attributes",
        "worktree .gitattributes",
        "index .gitattributes",
        "binary diff driver",
    )

    def plain_diff(self, env):
        return subprocess.run(
            ["git", "-C", str(self.repo), "diff", "--full-index", self.shas["base"], self.shas["defect"]],
            capture_output=True,
            env={**os.environ, **GIT_ENV, **env},
        ).stdout

    def test_the_digest_ignores_every_attribute_source(self):
        clean = self.clean_digest()
        self.assertNotIn(b"Binary files", self.plain_diff({}))
        for source in self.SOURCES:
            with self.subTest(source):
                env, undo = self.poison(source)
                try:
                    # the poison is real: a plain diff now differs from the clean one
                    self.assertIn(b"Binary files", self.plain_diff(env), source)
                    saved = {k: os.environ.get(k) for k in env}
                    os.environ.update(env)
                    try:
                        self.assertEqual(self.clean_digest(), clean, source)
                        self.assertEqual(
                            benchmark.check_commits(self.repo, self.corpus_for(self.pattern_finding()))[0], "ok"
                        )
                    finally:
                        for k, v in saved.items():
                            if v is None:
                                os.environ.pop(k, None)
                            else:
                                os.environ[k] = v
                finally:
                    for fn in reversed(undo):
                        fn()

    def test_the_command_line_pins_alone_hold_for_the_attribute_files_they_can_reach(self):
        # _run_diff on the repository itself (no isolated view): only the pins defend here
        clean = benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"])
        for source in ("user-attributes", "core.attributesFile"):
            with self.subTest(source):
                env, undo = self.poison(source)
                saved = {k: os.environ.get(k) for k in env}
                os.environ.update(env)
                try:
                    self.assertEqual(benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"]), clean)
                finally:
                    for k, v in saved.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v
                    for fn in reversed(undo):
                        fn()

    def test_attribute_pins_are_in_the_environment_and_the_config(self):
        self.assertEqual(benchmark._git_env()["GIT_ATTR_NOSYSTEM"], "1")
        self.assertIn("core.attributesFile=" + os.devnull, benchmark.DIFF_CONFIG)

    def test_external_diff_and_textconv_programs_never_run(self):
        mark1, mark2 = Path(self._tmp.name) / "MARK-external", Path(self._tmp.name) / "MARK-textconv"
        script = Path(self._tmp.name) / "ext.sh"
        script.write_text(f"#!/bin/sh\ntouch {mark1}\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        git(self.repo, "config", "diff.external", str(script))
        git(self.repo, "config", "diff.evil.textconv", f"touch {mark2}; cat")
        info = self.repo / ".git" / "info" / "attributes"
        info.parent.mkdir(exist_ok=True)
        info.write_text("*.py diff=evil\n", encoding="utf-8")
        try:
            # the poison is real: a plain git diff runs both programs
            subprocess.run(
                ["git", "-C", str(self.repo), "diff", self.shas["base"], self.shas["defect"]],
                capture_output=True,
                env={**os.environ, **GIT_ENV},
            )
            self.assertTrue(mark1.exists())
            mark1.unlink()
            clean_text = benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"])
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "diff",
                    "--no-ext-diff",
                    "--textconv",
                    self.shas["base"],
                    self.shas["defect"],
                ],
                capture_output=True,
                env={**os.environ, **GIT_ENV},
            )
            self.assertTrue(mark2.exists())
            mark1.unlink(missing_ok=True)
            mark2.unlink()
            # the pins alone (no isolated view)
            benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"])
            self.assertFalse(mark1.exists())
            self.assertFalse(mark2.exists())
            self.assertTrue(clean_text)
            # and through the public function
            benchmark.diff_bytes(self.repo, self.shas["base"], self.shas["defect"])
            self.assertFalse(mark1.exists())
            self.assertFalse(mark2.exists())
        finally:
            info.unlink()

    def test_the_diff_uses_full_object_ids_and_the_pins_are_in_the_argument_list(self):
        text = benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"]).decode()
        self.assertRegex(text, r"(?m)^index [0-9a-f]{40}\.\.[0-9a-f]{40}")
        for pin in ("--full-index", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--no-color", "-U3"):
            self.assertIn(pin, benchmark.DIFF_ARGS)

    def test_core_quotepath_is_pinned(self):
        clean = benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"])
        self.assertIn(b"\\303\\251", clean)  # the accented name is escaped
        git(self.repo, "config", "core.quotePath", "false")
        self.assertEqual(benchmark._run_diff(self.repo, self.shas["base"], self.shas["defect"]), clean)

    def test_a_subdirectory_of_a_repository_is_not_a_clone(self):
        self.assertTrue(benchmark._has_clone(self.repo))
        self.assertFalse(benchmark._has_clone(self.repo / "src"))
        c = self.corpus_for(self.pattern_finding())
        self.assertEqual(benchmark.check_commits(self.repo / "src", c), ("not-run", []))

    def test_run_env_points_home_at_the_scratch_directory(self):
        env = benchmark._run_env(Path("/scratch/dir"))
        self.assertEqual(env["HOME"], "/scratch/dir")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertFalse([k for k in env if k.startswith("GIT_")])

    def test_cli_not_run_message_and_the_reproduce_exit_code_without_a_clone(self):
        c = self.corpus_for(self.pattern_finding())
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as empty:
            path = Path(d) / "corpus.json"
            path.write_text(json.dumps(c), encoding="utf-8")
            base = ["--corpus", str(path), "--repo", empty, "--probes", str(self.probes)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(benchmark.main([*base, "--check"]), 0)
                self.assertIn("not-run", out.getvalue())
                self.assertIn("no commit was checked", out.getvalue())
                self.assertEqual(benchmark.main([*base, "--reproduce"]), 2)

    # ---- hostile inputs: paths, processes, bounds

    def corpus_with_probe(self, name):
        probe = {"kind": "probe", "probe": name, "args": []}
        return self.corpus_for(finding(repro=probe, at_base="n/a"))

    def test_a_probe_named_by_an_absolute_or_parent_path_is_never_run(self):
        mark = Path(self._tmp.name) / "PAYLOAD-RAN"
        payload = Path(self._tmp.name) / "payload.py"
        payload.write_text(f"open({str(mark)!r}, 'w').write('x')\nprint('OBSERVED')\n", encoding="utf-8")
        for name in (str(payload), "../payload.py", "a/../../payload.py", "sub/payload.py"):
            res = self.run_reproduce(self.corpus_with_probe(name))
            self.assertFalse(res[0]["ok"], name)
            self.assertFalse(mark.exists(), name)

    def test_a_probe_that_is_a_symlink_is_never_run(self):
        mark = Path(self._tmp.name) / "LINKED-PAYLOAD-RAN"
        payload = Path(self._tmp.name) / "payload.py"
        payload.write_text(f"open({str(mark)!r}, 'w').write('x')\nprint('OBSERVED')\n", encoding="utf-8")
        (self.probes / "linked.py").symlink_to(payload)
        res = self.run_reproduce(self.corpus_with_probe("linked.py"))
        self.assertFalse(res[0]["ok"])
        self.assertIn("symlink", res[0]["notes"])
        self.assertFalse(mark.exists())

    def test_a_hostile_touched_pattern_is_cut_off_in_the_scope_check(self):
        long_a = "a" * 24 + "!"
        (self.repo / "src" / "a.py").write_text(long_a + "\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "hostile line")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        digest = benchmark.diff_digest(self.repo, self.shas["base"], sha)
        f = finding(
            quotes=[long_a],
            touched=["(a+)+$"],
            repro={"kind": "pattern", "path": "src/a.py", "regex": "a", "present": True},
        )
        c = corpus(task(base=self.shas["base"], head=sha, diff_sha256=digest, findings=[f]))
        with mock.patch.object(benchmark, "REGEX_BUDGET_S", 0.3):
            with self.assertRaises(benchmark.RegexBudget):
                benchmark.check_commits(self.repo, c)

    def test_a_hanging_git_archive_is_killed_with_its_children(self):
        mark = Path(self._tmp.name) / "ARCHIVE-GRANDCHILD-WROTE"
        real_git = shutil.which("git")
        fake = Path(self._tmp.name) / "fakearchive"
        fake.mkdir()
        script = fake / "git"
        script.write_text(
            "#!/bin/sh\n"
            'case "$*" in\n'
            f"  *archive*) (sleep 2.5; touch {mark}) & sleep 8 ;;\n"
            f'  *) exec {real_git} "$@" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        with (
            mock.patch.dict(os.environ, {"PATH": f"{fake}:{os.environ['PATH']}"}),
            mock.patch.object(benchmark, "GIT_TIMEOUT", 1),
        ):
            with tempfile.TemporaryDirectory() as d, self.assertRaises(RuntimeError):
                benchmark._extract(self.repo, self.shas["defect"], d)
        time.sleep(3.5)
        self.assertFalse(mark.exists())

    def test_a_brief_input_file_outside_the_bench_directory_is_never_read(self):
        bench = Path(self._tmp.name) / "bench"
        bench.mkdir()
        secret = Path(self._tmp.name) / "secret.txt"
        secret.write_text("unrelated words\n", encoding="utf-8")
        for name in (str(secret), "../secret.txt"):
            c = self.corpus_for(finding())
            b = task(
                "b1",
                roles=("brief-writer",),
                kind="brief",
                findings=[brief_finding(absent_from_input=["topic"])],
                input_file=name,
                input_sha256="d" * 64,
            )
            for k in ("diff_sha256", "base", "head"):
                b.pop(k)
            c["tasks"].append(b)
            res = {r["finding"]: r for r in self.run_reproduce(c, bench_dir=bench)}
            # read unconfined, "topic" is absent from the secret file and the finding would reproduce
            self.assertFalse(res["g1"]["ok"], name)
            self.assertIn("path", res["g1"]["notes"])

    def test_pathspecs_are_literal_so_a_glob_in_a_path_matches_nothing(self):
        base, head = self.shas["base"], self.shas["defect"]
        self.assertTrue(benchmark._run_diff(self.repo, base, head, "src/a.py"))
        self.assertEqual(benchmark._run_diff(self.repo, base, head, "src/*.py"), b"")

    def test_show_refuses_hostile_paths(self):
        for bad in ("../x", "/etc/passwd", ":(glob)*", "-x", "a/../b", "", "a\0b"):
            with self.assertRaises(ValueError, msg=bad):
                benchmark._show(self.repo, self.shas["defect"], bad)

    def test_a_grandchild_of_a_timed_out_probe_is_killed_too(self):
        mark = Path(self._tmp.name) / "GRANDCHILD-WROTE"
        (self.probes / "slow.py").write_text(grandchild_code(mark, 8), encoding="utf-8")
        with mock.patch.object(benchmark, "PROBE_TIMEOUT", 1):
            res = self.run_reproduce(self.corpus_with_probe("slow.py"))
        self.assertFalse(res[0]["ok"])
        self.assertIn("timed out", res[0]["notes"])
        time.sleep(3.5)
        self.assertFalse(mark.exists())

    def test_a_probe_that_floods_its_output_fails_instead_of_filling_memory(self):
        (self.probes / "flood.py").write_text("print('x' * 100000)\nprint('OBSERVED')\n", encoding="utf-8")
        with mock.patch.object(benchmark, "MAX_CHILD_OUTPUT", 1000):
            res = self.run_reproduce(self.corpus_with_probe("flood.py"))
        self.assertFalse(res[0]["ok"])
        self.assertIn("printed more than", res[0]["notes"])

    def test_a_git_that_hangs_is_killed_with_its_children(self):
        mark = Path(self._tmp.name) / "GIT-GRANDCHILD-WROTE"
        fake = Path(self._tmp.name) / "fakebin"
        fake.mkdir()
        script = fake / "git"
        script.write_text(f"#!/bin/sh\n(sleep 2.5; touch {mark}) &\nsleep 8\n", encoding="utf-8")
        script.chmod(0o755)
        with (
            mock.patch.dict(os.environ, {"PATH": f"{fake}:{os.environ['PATH']}"}),
            mock.patch.object(benchmark, "GIT_TIMEOUT", 1),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                benchmark._git(self.repo, "rev-parse", "HEAD")
        time.sleep(3.5)
        self.assertFalse(mark.exists())

    def test_git_output_over_the_cap_is_an_error_not_a_truncated_digest(self):
        with mock.patch.object(benchmark, "MAX_GIT_OUTPUT", 100):
            with self.assertRaises(RuntimeError) as cm:
                benchmark.diff_bytes(self.repo, self.shas["base"], self.shas["defect"])
        self.assertIn("more than", str(cm.exception))

    def test_archive_caps(self):
        sha = self.shas["defect"]
        with tempfile.TemporaryDirectory() as ok:
            benchmark._extract(self.repo, sha, ok)  # the normal case still works
            self.assertTrue((Path(ok) / "src" / "a.py").is_file())
        for name, value, fragment in (
            ("MAX_ARCHIVE_BYTES", 1000, "archive is over"),
            ("MAX_ARCHIVE_FILES", 2, "more than 2 files"),
            ("MAX_MEMBER_BYTES", 10, "is over 10 bytes"),
            ("MAX_EXPANDED_BYTES", 50, "expands to more than"),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                with mock.patch.object(benchmark, name, value):
                    with self.assertRaises(RuntimeError) as cm:
                        benchmark._extract(self.repo, sha, d)
                self.assertIn(fragment, str(cm.exception))

    def test_a_pattern_that_backtracks_forever_is_cut_off_where_the_corpus_is_applied(self):
        hostile = {"kind": "pattern", "path": "src/a.py", "regex": "(a+)+$", "present": True}
        long_a = "a" * 24 + "!"
        (self.repo / "src" / "a.py").write_text(long_a + "\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "hostile text")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        c = self.corpus_for(finding(repro=hostile, at_base="n/a"))
        c["tasks"][0]["head"] = sha
        with mock.patch.object(benchmark, "REGEX_BUDGET_S", 0.3):
            started = time.monotonic()
            res = self.run_reproduce(c)
            self.assertFalse(res[0]["ok"])
            self.assertIn("took more than", res[0]["notes"])
            # the brief input and a touched search are guarded the same way
            with tempfile.TemporaryDirectory() as bench:
                (Path(bench) / "in.md").write_text(long_a, encoding="utf-8")
                b = task(
                    "b1",
                    roles=("brief-writer",),
                    kind="brief",
                    findings=[brief_finding(absent_from_input=["(a+)+$"])],
                    input_file="in.md",
                    input_sha256="d" * 64,
                )
                for k in ("diff_sha256", "base", "head"):
                    b.pop(k)
                c2 = self.corpus_for(finding())
                c2["tasks"].append(b)
                res = {r["finding"]: r for r in self.run_reproduce(c2, bench_dir=Path(bench))}
                self.assertIn("took more than", res["g1"]["notes"])
        self.assertLess(time.monotonic() - started, 20)

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

    def test_a_commit_in_a_record_must_exist_in_the_given_repository(self):
        real = self.shas["defect"]
        self.assertEqual(parse(record(commit=real), repo=self.repo)["commit"], real)
        with self.assertRaises(ValueError) as cm:
            parse(record(commit="f" * 40), repo=self.repo)
        self.assertIn("not in the given repository", str(cm.exception))
        with self.assertRaises(ValueError):
            parse(record(commit=real[:10]), repo=self.repo)  # an abbreviation is never accepted
        with tempfile.TemporaryDirectory() as notrepo:
            with self.assertRaises(ValueError):
                parse(record(commit=real), repo=notrepo)
        # without a repository only the shape is checked
        self.assertEqual(parse(record(commit="f" * 40))["commit"], "f" * 40)

    def test_check_commits_needs_no_network(self):
        c = self.corpus_for(self.pattern_finding())
        saved = os.environ.copy()
        try:
            os.environ["GIT_ALLOW_PROTOCOL"] = "none"
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

    def test_an_absence_repro_observes_the_defect_when_the_pattern_is_missing(self):
        # defect: "unchecked value" has no line starting "checked"; the fix has one. present=False is the defect.
        repro = {"kind": "pattern", "path": "src/a.py", "regex": "^checked", "present": False}
        c = self.corpus_for(self.pattern_finding(repro=repro, proxy="stands in", at_base="n/a"))
        res = self.run_reproduce(c)
        self.assertTrue(res[0]["ok"], res)
        self.assertIn("proxy: stands in", res[0]["notes"])

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

    def test_brief_finding_must_be_absent_from_the_recorded_input(self):
        with tempfile.TemporaryDirectory() as bench:
            (Path(bench) / "in.md").write_text("The brief says nothing about the topic.\n", encoding="utf-8")
            c = self.corpus_for(finding())
            b = task(
                "b1",
                roles=("brief-writer",),
                kind="brief",
                findings=[brief_finding(absent_from_input=["topic"])],
                input_file="in.md",
                input_sha256="d" * 64,
            )
            for k in ("diff_sha256", "base", "head"):
                b.pop(k)
            c["tasks"].append(b)
            res = {r["finding"]: r for r in self.run_reproduce(c, bench_dir=Path(bench))}
            self.assertFalse(res["g1"]["ok"])  # "topic" is in the input
            self.assertIn("already covers", res["g1"]["notes"])
            b["findings"][0]["absent_from_input"] = ["unrelated words"]
            res = {r["finding"]: r for r in self.run_reproduce(c, bench_dir=Path(bench))}
            self.assertTrue(res["g1"]["ok"], res)

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
                self.assertIn("git: git version", out.getvalue())
                c["tasks"][0]["head"] = "f" * 40
                path.write_text(json.dumps(c), encoding="utf-8")
                self.assertEqual(benchmark.main([*base, "--check"]), 1)

    def test_diff_digest_is_stable_and_sensitive(self):
        a = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"])
        self.assertEqual(a, benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"]))
        self.assertNotEqual(a, benchmark.diff_digest(self.repo, self.shas["base"], self.shas["fixed"]))
        self.assertRegex(a, re.compile(r"[0-9a-f]{64}"))


class MutationRepro(unittest.TestCase):
    """A mutation repro: the defect is a survivor, a change the tests do not notice."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "src").mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.shas = {}
        states = (
            (
                "base",
                "VALUE = 1\n",
                "import unittest\n\n\nclass T(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertTrue(True)\n",
            ),
            (
                "defect",
                "VALUE = 1\n",
                "import unittest\n\n\nclass T(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertTrue(True)\n",
            ),
            (
                "fixed",
                "VALUE = 1\n",
                "import sys\nimport unittest\nfrom pathlib import Path\n\n\nclass T(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        src = Path(__file__).parents[1] / 'src' / 'a.py'\n"
                "        self.assertIn('VALUE = 1', src.read_text())\n",
            ),
        )
        for name, src, test in states:
            (self.repo / "src" / "a.py").write_text(src + f"# {name}\n", encoding="utf-8")
            (self.repo / "tests" / "test_a.py").write_text(test, encoding="utf-8")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", name)
            self.shas[name] = git(self.repo, "rev-parse", "HEAD").strip()

    def corpus_for(self, repro, fixed=True):
        t = task(
            base=self.shas["base"],
            head=self.shas["defect"],
            diff_sha256=benchmark.diff_digest(self.repo, self.shas["base"], self.shas["defect"]),
            findings=[finding(repro=repro, at_base="n/a", fixed=self.shas["fixed"] if fixed else None)],
        )
        return corpus(t)

    def repro(self, **kw):
        r = {
            "kind": "mutation",
            "path": "src/a.py",
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "tests_file": "test_a.py",
            "k": None,
        }
        r.update(kw)
        return r

    def test_a_surviving_mutation_is_the_defect_and_a_killed_one_is_the_fix(self):
        res = benchmark.reproduce(self.repo, self.corpus_for(self.repro()))
        self.assertTrue(res[0]["ok"], res)

    def test_a_mutation_path_outside_the_tree_is_never_read_or_written(self):
        outside = Path(self._tmp.name) / "outside.txt"
        outside.write_text("VALUE = 1\n", encoding="utf-8")
        for path in (str(outside), "../outside.txt", "src/../../x"):
            res = benchmark.reproduce(self.repo, self.corpus_for(self.repro(path=path), fixed=False))
            self.assertFalse(res[0]["ok"], path)
            self.assertEqual(outside.read_text(encoding="utf-8"), "VALUE = 1\n", path)

    def test_a_hostile_tests_file_is_refused_before_anything_runs(self):
        res = benchmark.reproduce(self.repo, self.corpus_for(self.repro(tests_file="../test_a.py"), fixed=False))
        self.assertFalse(res[0]["ok"])
        self.assertIn("plain file name", res[0]["notes"])

    def test_a_grandchild_of_timed_out_unit_tests_is_killed_too(self):
        mark = Path(self._tmp.name) / "TEST-GRANDCHILD-WROTE"
        code = grandchild_code(mark, 8)
        (self.repo / "tests" / "test_a.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n"
            + "".join("        " + ln + "\n" for ln in code.splitlines()),
            encoding="utf-8",
        )
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "slow tests")
        slow = git(self.repo, "rev-parse", "HEAD").strip()
        c = self.corpus_for(self.repro(), fixed=False)
        c["tasks"][0]["head"] = slow
        c["tasks"][0]["diff_sha256"] = benchmark.diff_digest(self.repo, self.shas["base"], slow)
        with mock.patch.object(benchmark, "TEST_TIMEOUT", 1):
            res = benchmark.reproduce(self.repo, c)
        self.assertFalse(res[0]["ok"])
        time.sleep(3.5)
        self.assertFalse(mark.exists())

    def test_a_mutation_that_the_tests_catch_at_the_head_is_not_a_reproduction(self):
        c = self.corpus_for(self.repro(), fixed=False)
        c["tasks"][0]["head"] = self.shas["fixed"]
        c["tasks"][0]["diff_sha256"] = benchmark.diff_digest(self.repo, self.shas["base"], self.shas["fixed"])
        self.assertFalse(benchmark.reproduce(self.repo, c)[0]["ok"])

    def test_code_that_is_no_longer_there_means_the_defect_is_gone(self):
        gone = self.repro(old="VALUE = 99", new="VALUE = 98")
        res = benchmark.reproduce(self.repo, self.corpus_for(gone, fixed=False))  # only the head is checked
        self.assertFalse(res[0]["ok"])

    def test_red_tests_on_the_unmutated_tree_is_an_error_not_a_pass(self):
        (self.repo / "tests" / "test_a.py").write_text(
            "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.fail('red')\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "red")
        red = git(self.repo, "rev-parse", "HEAD").strip()
        c = self.corpus_for(self.repro(), fixed=False)
        c["tasks"][0]["head"] = red
        c["tasks"][0]["diff_sha256"] = benchmark.diff_digest(self.repo, self.shas["base"], red)
        res = benchmark.reproduce(self.repo, c)
        self.assertFalse(res[0]["ok"])
        self.assertIn("not green", res[0]["notes"])


class Extraction(unittest.TestCase):
    def archive(self, name):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name)
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
        return buf.getvalue()

    def test_members_that_escape_the_destination_are_refused(self):
        for name in ("../evil", "a/../../evil", "/abs/evil"):
            with tarfile.open(fileobj=io.BytesIO(self.archive(name))) as tf:
                with self.assertRaises(RuntimeError):
                    list(benchmark._safe_members(tf))
        with tarfile.open(fileobj=io.BytesIO(self.archive("ok/file.txt"))) as tf:
            self.assertEqual([m.name for m in benchmark._safe_members(tf)], ["ok/file.txt"])

    def test_links_and_devices_are_not_extracted(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            tf.addfile(link)
        with tarfile.open(fileobj=io.BytesIO(buf.getvalue())) as tf:
            self.assertEqual(list(benchmark._safe_members(tf)), [])


class Confinement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "root"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "sub" / "f.txt").write_text("x", encoding="utf-8")
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")

    def test_plain_relative_paths_are_confined_to_the_root(self):
        real = Path(os.path.realpath(self.root))
        self.assertEqual(benchmark.confine(self.root, "sub/f.txt"), real / "sub" / "f.txt")
        self.assertEqual(benchmark.confine(self.root, "missing/new.txt"), real / "missing" / "new.txt")

    def test_hostile_paths_are_refused(self):
        for bad in (
            "/etc/passwd",
            str(self.outside / "secret.txt"),
            "../outside/secret.txt",
            "sub/../../outside/secret.txt",
            "sub//f.txt",
            "./sub/f.txt",
            "sub/./f.txt",
            "sub/f.txt/",
            "",
            "a\0b",
            "a\\b",
            "-rf",
            ":(top)x",
            "a" * 400,
            "a\x1bb",
            5,
            None,
        ):
            with self.assertRaises(ValueError, msg=repr(bad)):
                benchmark.confine(self.root, bad)

    def test_a_symlink_in_any_component_is_refused_even_when_it_points_inside(self):
        (self.root / "link").symlink_to(self.outside)
        (self.root / "inner").symlink_to(self.root / "sub")
        (self.root / "sub" / "filelink").symlink_to(self.outside / "secret.txt")
        for bad in ("link/secret.txt", "link", "inner/f.txt", "sub/filelink"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                benchmark.confine(self.root, bad)
            self.assertIn("symlink", str(cm.exception))

    def test_a_root_that_is_itself_reached_through_a_symlink_is_fine(self):
        alias = Path(self._tmp.name) / "alias"
        alias.symlink_to(self.root)
        self.assertEqual(benchmark.confine(alias, "sub/f.txt").name, "f.txt")


class Processes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.mark = Path(self._tmp.name) / "MARK"

    def run_python(self, code, **kw):
        return benchmark._run([sys.executable, "-c", code], env=dict(os.environ), **kw)

    def test_a_timeout_kills_the_whole_process_group(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_python(grandchild_code(self.mark, 8), timeout=1)
        time.sleep(3.5)
        self.assertFalse(self.mark.exists())

    def test_a_grandchild_left_behind_by_a_child_that_exited_is_killed(self):
        (done, cut) = self.run_python(grandchild_code(self.mark, 0), timeout=20)
        self.assertEqual((done.returncode, cut), (0, False))
        time.sleep(3.5)
        self.assertFalse(self.mark.exists())

    def test_an_interrupt_kills_the_group_too(self):
        real = benchmark.selectors.DefaultSelector

        class Boom(real):
            def select(self, timeout=None):
                time.sleep(0.5)  # let the child start its grandchild
                raise KeyboardInterrupt

        with mock.patch.object(benchmark.selectors, "DefaultSelector", Boom):
            with self.assertRaises(KeyboardInterrupt):
                self.run_python(grandchild_code(self.mark, 8), timeout=20)
        time.sleep(3.5)
        self.assertFalse(self.mark.exists())

    def test_output_is_capped_and_the_flooding_child_is_killed(self):
        code = "import sys, time\nsys.stdout.write('x' * 5000000)\nsys.stdout.flush()\ntime.sleep(30)\n"
        started = time.monotonic()
        done, cut = self.run_python(code, timeout=25, max_output=1000)
        self.assertTrue(cut)
        self.assertLess(time.monotonic() - started, 15)  # not the 30 s the child wanted
        self.assertLess(len(done.stdout), 1200)
        self.assertIn(b"[output truncated at 1000 bytes]", done.stdout)
        done, cut = self.run_python("import sys\nsys.stderr.write('e' * 100000)\n", timeout=20, max_output=500)
        self.assertTrue(cut)
        self.assertLess(len(done.stderr), 700)

    def test_a_normal_child_returns_its_output_and_exit_status(self):
        done, cut = self.run_python("import sys\nprint('out')\nsys.stderr.write('err')\nsys.exit(3)", timeout=20)
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr, cut), (3, b"out", b"err", False))


class Bounds(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_corpus_over_the_byte_cap_is_refused_before_parsing(self):
        path = self.dir / "corpus.json"
        path.write_text('{"version": 1}' + " " * 90, encoding="utf-8")  # 104 bytes
        with mock.patch.object(benchmark, "MAX_CORPUS_BYTES", 103):
            with self.assertRaises(ValueError) as cm:
                benchmark.load_corpus(path)
        self.assertIn("is over 103 bytes", str(cm.exception))
        with mock.patch.object(benchmark, "MAX_CORPUS_BYTES", 104):
            self.assertEqual(benchmark.load_corpus(path), {"version": 1})

    def test_hostile_nesting_is_an_ordinary_error(self):
        path = self.dir / "corpus.json"
        path.write_text("[" * 200000, encoding="utf-8")
        with self.assertRaises(ValueError):
            benchmark.load_corpus(path)
        with self.assertRaises(ValueError) as cm:
            benchmark.parse_result("[" * 200000, 1, corpus())
        self.assertIn("not JSON", str(cm.exception))

    def test_a_result_line_and_a_results_file_have_byte_caps(self):
        line = json.dumps(record())
        path = self.dir / "r.jsonl"
        path.write_text(line + "\n", encoding="utf-8")
        with mock.patch.object(benchmark, "MAX_RECORD_BYTES", len(line) - 1):
            with self.assertRaises(ValueError) as cm:
                benchmark.load_results(path, corpus())
            self.assertIn("over", str(cm.exception))
            with self.assertRaises(ValueError):
                benchmark.parse_result(line, 1, corpus())
        with mock.patch.object(benchmark, "MAX_RECORD_BYTES", len(line) + 1):
            self.assertEqual(len(benchmark.load_results(path, corpus())), 1)
        lines = [json.dumps(record(run=i)) for i in range(1, 4)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(benchmark, "MAX_RESULTS_BYTES", len(lines[0]) * 2):
            with self.assertRaises(ValueError) as cm:
                benchmark.load_results(path, corpus())
            self.assertIn("results file is over", str(cm.exception))


class MultibyteBounds(unittest.TestCase):
    def test_the_line_cap_counts_bytes_not_characters(self):
        line = json.dumps(record(notes="\u00e9" * 100), ensure_ascii=False)
        self.assertLess(len(line), len(line.encode("utf-8")))
        cap = (len(line) + len(line.encode("utf-8"))) // 2  # above the character count, below the byte count
        with tempfile.TemporaryDirectory() as d, mock.patch.object(benchmark, "MAX_RECORD_BYTES", cap):
            path = Path(d) / "r.jsonl"
            path.write_text(line + "\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                benchmark.load_results(path, corpus())
            self.assertIn("over", str(cm.exception))


class Patterns(unittest.TestCase):
    def test_exponential_patterns_are_refused_at_load(self):
        for bad in (
            "(a+)+$",
            "(a*)*b",
            "(.*)*",
            "(a|aa)+$",
            "(a|a)*b",
            "(?:a{1,100}){1,100}",
            "(a)\\1",
            "a{0,5000}",
            "x" * 401,
        ):
            self.assertTrue(benchmark.pattern_errors(bad), bad)

    def test_the_patterns_the_corpus_uses_are_accepted(self):
        for ok in (
            r"\bfilesystem root\b",
            r"(?i)list-managed.{0,200}(\brow\b|every state)",
            r"\btarget\b[^.\n]{0,60}(/|\broot\b)",
            r"\bwrap(ped|s)?\b",
            r"\bnomic(-embed-text)?\b",
            r"^\"\"\"Summaris",
            r"a+",
            r"(ab)?c*",
        ):
            self.assertEqual(benchmark.pattern_errors(ok), [], ok)

    def test_the_matcher_has_a_hard_time_budget(self):
        started = time.monotonic()
        with self.assertRaises(benchmark.RegexBudget):
            benchmark._search("(a+)+$", "a" * 24 + "!", budget=0.3)
        with self.assertRaises(benchmark.RegexBudget):
            benchmark._spans("(a+)+$", "a" * 24 + "!", budget=0.3)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(benchmark._search("a+", "caab", budget=0.3))

    def test_matching_a_hostile_pattern_that_got_past_validation_is_cut_off(self):
        t = task(findings=[finding("f1", evidence=("(a+)+$", "(b+)+$"), quotes=["unchecked value"])])
        report = {"class": "input-validation", "path": "src/a.py", "evidence": "a" * 24 + "! unchecked value"}
        with mock.patch.object(benchmark, "REGEX_BUDGET_S", 0.3):
            with self.assertRaises(benchmark.RegexBudget):
                benchmark.match_findings(t, [report])

    def test_the_budget_timer_is_always_cleared(self):
        import signal

        with benchmark._regex_budget(5):
            pass
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0.0)


if __name__ == "__main__":
    unittest.main()
