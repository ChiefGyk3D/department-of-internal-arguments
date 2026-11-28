"""scripts/review_verdict.py (issue #139): a review output is CLEAN, FINDINGS or HELD; a review that cannot show its
answer is HELD, never clean. All fixtures are synthetic text."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_verdict.py"
sys.path.insert(0, str(ROOT / "scripts"))

import review_verdict as rv  # noqa: E402

DASH = "—"
FINDINGS_BLOCK = (
    "## Findings\n\n"
    f"### High {DASH} Synthetic widget trusts its input\nBody text that must never be echoed.\n\n"
    f"### Medium {DASH} Synthetic cache never expires\nMore body.\n\n"
    f"### Low {DASH} Synthetic typo in a label\n"
)
CLEAN = "## Findings\n\nNo Critical or High findings. Attacked: the synthetic parser, the synthetic loader.\n"


def run(text, name="review.txt"):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = rv.main([str(p)])
    return code, out.getvalue()


class Clean(unittest.TestCase):
    def test_explicit_statement_with_findings_section_is_clean(self):
        code, out = run(CLEAN)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("VERDICT: CLEAN"))

    def test_statement_without_findings_section_is_held(self):
        code, out = run("No Critical or High findings.\n")
        self.assertEqual(code, 3)
        self.assertIn("HELD", out.splitlines()[0])

    def test_findings_section_without_statement_is_held(self):
        code, _ = run("## Findings\n\nLooked around.\n")
        self.assertEqual(code, 3)

    def test_a_high_finding_beats_a_clean_statement(self):
        code, _ = run(CLEAN + f"\n### High {DASH} Synthetic late find\n")
        self.assertEqual(code, 1)


class Findings(unittest.TestCase):
    def test_counts_and_table(self):
        code, out = run(FINDINGS_BLOCK)
        self.assertEqual(code, 1)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith("VERDICT: FINDINGS"))
        for part in ("critical=0", "high=1", "medium=1", "low=1"):
            self.assertIn(part, first)
        self.assertIn("Synthetic widget trusts its input", out)
        self.assertNotIn("must never be echoed", out)

    def test_repeated_block_is_deduplicated_and_keeps_first_line(self):
        code, out = run(FINDINGS_BLOCK + "\n" + FINDINGS_BLOCK)
        self.assertEqual(code, 1)
        self.assertIn("high=1", out.splitlines()[0])
        rows = [ln for ln in out.splitlines() if "Synthetic widget" in ln]
        self.assertEqual(len(rows), 1)
        self.assertRegex(rows[0], r"\b3\b")

    def test_medium_only_is_findings(self):
        code, out = run(f"### Medium {DASH} Synthetic only\n")
        self.assertEqual(code, 1)
        self.assertIn("medium=1", out)

    def test_ascii_hyphen_and_colon_separators(self):
        code, out = run("### High - Synthetic one\n### Critical: Synthetic two\n")
        self.assertEqual(code, 1)
        self.assertIn("critical=1", out)
        self.assertIn("high=1", out)

    def test_title_is_capped_and_control_characters_stripped(self):
        title = "A" * 300 + chr(27) + "[31mred" + chr(7) + chr(0x202E) + "B"
        code, out = run(f"### High {DASH} {title}\n")
        self.assertEqual(code, 1)
        for bad in (chr(27), chr(7), chr(0x202E)):
            self.assertNotIn(bad, out)
        self.assertNotIn("A" * 201, out)
        self.assertIn("A" * 200, out)


class Held(unittest.TestCase):
    def check_held(self, text):
        code, out = run(text)
        self.assertEqual(code, 3, out)
        self.assertTrue(out.startswith("VERDICT: HELD"))
        return out

    def test_empty_and_whitespace(self):
        self.assertIn("empty output", self.check_held(""))
        self.assertIn("empty output", self.check_held("\n  \n"))

    def test_no_findings_section(self):
        self.check_held("I read the code and it looked fine to me.\n")

    def test_cutoff_text(self):
        self.check_held("This request was flagged for possible cybersecurity risk.\n" + CLEAN)

    def test_authentication_error(self):
        self.check_held(CLEAN + "\nThis session was not authenticated with one of your authorized hardware keys.\n")

    def test_scan_failed_to_start_phrases(self):
        for phrase in ("the scan did not start", "scan could not start", "terminal failure", "x is not a function"):
            with self.subTest(phrase=phrase):
                self.check_held(f"{CLEAN}\n{phrase}\n")

    def test_status_ok_marker_alone_is_not_an_answer(self):
        self.check_held("status=ok\n")

    def test_status_ok_with_a_real_answer_is_not_held(self):
        code, _ = run("status=ok\n" + CLEAN)
        self.assertEqual(code, 0)

    def test_findings_beside_a_failed_scan_note_are_findings_with_a_note(self):
        final = FINDINGS_BLOCK + "\nNote: the optional deep-scan tool did not start; I ran my own probes instead.\n"
        code, out = run(real_shape("prompt", "commentary", final))
        self.assertEqual(code, 1, out)
        self.assertIn("note: scan or tool failed to start mentioned", out.splitlines()[0])
        self.assertIn("high=1", out.splitlines()[0])
        self.assertIn("Synthetic widget", out)

    def test_findings_beside_a_cutoff_or_auth_marker_are_findings_with_a_note(self):
        for marker in ("flagged for possible cybersecurity risk", "not authenticated with a hardware key"):
            with self.subTest(marker=marker):
                code, out = run(FINDINGS_BLOCK + f"\n{marker}\n")
                self.assertEqual(code, 1, out)
                self.assertIn("note:", out.splitlines()[0])

    def test_findings_without_a_marker_have_no_note(self):
        _, out = run(FINDINGS_BLOCK)
        self.assertNotIn("note:", out)

    def test_clean_statement_beside_a_failed_scan_note_is_held(self):
        code, out = run(CLEAN + "\nNote: the deep-scan tool did not start.\n")
        self.assertEqual(code, 3, out)
        self.assertIn("scan or tool failed to start", out)

    def test_held_does_not_hide_the_marker_text(self):
        code, out = run(FINDINGS_BLOCK.replace("###", "NOHEADING") + "\nthe scan did not start\n")
        self.assertEqual(code, 3)

    def test_held_output_does_not_echo_the_matched_text(self):
        out = self.check_held("flagged for possible cybersecurity risk: SECRETBODY\n")
        self.assertNotIn("SECRETBODY", out)

    def test_invalid_utf8_is_handled(self):
        code, _ = run(b"\xff\xfe\x80 garbage\n")
        self.assertEqual(code, 3)
        code, _ = run(b"## Findings\n\nNo Critical or High findings. \xff\n")
        self.assertEqual(code, 0)


def real_shape(prompt, transcript, final, wrapper="exit=0 status=ok"):
    """Synthetic output shaped like a real run: echoed prompt, transcript, final message twice around `tokens used`."""
    return f"{prompt}\ncodex\nexec\n{transcript}\n{final}\ntokens used\n12,345\n{final}\n{wrapper}\n"


MARKER_PROMPT = (
    "Review this parser. If the scan did not start, or you see a flagged for possible cybersecurity risk cutoff,\n"
    "or a hardware key error, or x is not a function, say so. Also mention terminal failure.\n"
)


class RealShape(unittest.TestCase):
    def test_valid_review_whose_prompt_and_transcript_hold_marker_words_is_findings(self):
        text = real_shape(
            MARKER_PROMPT, "thinking: the tool is not a function here, did not start earlier", FINDINGS_BLOCK
        )
        code, out = run(text)
        self.assertEqual(code, 1, out)
        first = out.splitlines()[0]
        self.assertIn("high=1", first)
        self.assertIn("medium=1", first)
        self.assertIn("low=1", first)

    def test_valid_clean_review_with_marker_words_in_the_prompt_is_clean(self):
        code, out = run(real_shape(MARKER_PROMPT, "commentary", CLEAN))
        self.assertEqual(code, 0, out)

    def test_duplicated_final_message_is_counted_once(self):
        code, out = run(real_shape("prompt", "commentary", FINDINGS_BLOCK))
        self.assertEqual(code, 1)
        self.assertIn("(unique 3)", out)
        rows = [ln for ln in out.splitlines() if "Synthetic widget" in ln]
        self.assertEqual(len(rows), 1)

    def test_held_review_whose_final_message_says_the_scan_could_not_start(self):
        final = "The scan could not start, so the review stopped. No result."
        code, out = run(real_shape("prompt", "commentary", final))
        self.assertEqual(code, 3, out)
        self.assertIn("failed to start", out)

    def test_held_even_when_the_transcript_had_findings_headings_only(self):
        final = "The scan could not start, so the review stopped."
        code, _ = run(real_shape("prompt", FINDINGS_BLOCK, final))
        self.assertEqual(code, 3)

    def test_findings_in_transcript_only_fall_back_when_final_message_has_none(self):
        code, out = run(real_shape("prompt", FINDINGS_BLOCK, "All done."))
        self.assertEqual(code, 1, out)

    def test_clean_statement_in_the_transcript_alone_is_not_clean(self):
        code, _ = run(real_shape("prompt", CLEAN, "Done."))
        self.assertEqual(code, 3)

    def test_tokens_used_with_no_final_message_is_held(self):
        code, out = run("prompt\ncodex\ntokens used\n12,345\nexit=0 status=ok\n")
        self.assertEqual(code, 3)
        self.assertIn("no final message", out)

    def test_wrapper_marker_is_not_part_of_the_final_message(self):
        code, out = run("prompt\ntokens used\n9\nexit=0 status=ok\n")
        self.assertEqual(code, 3)
        self.assertIn("no final message", out)

    def test_markers_in_the_last_8_kib_fallback_without_tokens_line(self):
        pad = "filler line of synthetic text\n" * 2000
        code, _ = run("did not start\n" + pad + CLEAN)
        self.assertEqual(code, 0)
        code, _ = run(pad + CLEAN + "\nthe scan did not start\n")
        self.assertEqual(code, 3)


class CleanIsStrict(unittest.TestCase):
    def test_no_high_alone_or_no_critical_alone_is_held(self):
        for stmt in ("No High findings.", "No Critical findings.", "There are no high issues."):
            with self.subTest(stmt=stmt):
                code, _ = run(f"## Findings\n\n{stmt}\n")
                self.assertEqual(code, 3)

    def test_accepted_clean_phrasings(self):
        for stmt in (
            "No Critical or High findings.",
            "No Critical and no High issues found.",
            "no Critical, no High",
            "No High or Critical findings were found in the synthetic parser.",
            "No **Critical** or **High** findings.",
        ):
            with self.subTest(stmt=stmt):
                code, out = run(f"## Findings\n\n{stmt}\n")
                self.assertEqual(code, 0, out)

    def test_high_in_a_table_row_with_a_no_critical_statement_is_held(self):
        text = "## Findings\n\nNo Critical findings.\n\n| Severity | Title |\n| --- | --- |\n| High | Synthetic gap |\n"
        code, _ = run(text)
        self.assertEqual(code, 3)

    def test_unrecognized_severity_positions_hold_a_clean_statement(self):
        for line in (
            "**High**: synthetic thing",
            "[Critical] synthetic thing",
            "Severity: High",
            "- High: synthetic thing",
            "1. Critical synthetic thing",
            "| Critical | synthetic |",
            "High — synthetic thing",
        ):
            with self.subTest(line=line):
                code, out = run(f"{CLEAN}\n{line}\n")
                self.assertEqual(code, 3, out)
                self.assertRegex(out, "outside a recognized finding heading|after the clean statement")

    def test_plain_prose_with_the_word_high_stays_clean(self):
        code, _ = run(CLEAN + "\nThe loader has a high degree of reuse. Critical path reviewed.\n")
        self.assertEqual(code, 0)


class HeadingVariants(unittest.TestCase):
    def test_variants_are_findings(self):
        for head in (
            f"### **High** {DASH} Synthetic bold",
            "### **High:** Synthetic bold colon",
            "### [High] Synthetic bracket",
            "### 1. High - Synthetic numbered",
            "## 2) Medium – Synthetic en dash",
            "#### Critical: Synthetic colon",
            "### (Low) Synthetic paren",
        ):
            with self.subTest(head=head):
                code, out = run(f"## Findings\n\nNo Critical or High findings.\n\n{head}\n")
                self.assertEqual(code, 1, out)
                self.assertIn("Synthetic", out)

    def test_bold_heading_high_is_findings_with_a_clean_looking_statement(self):
        code, out = run(CLEAN + f"\n### **High** {DASH} Synthetic late\n")
        self.assertEqual(code, 1)
        self.assertIn("high=1", out)

    def test_a_bare_heading_that_only_starts_with_a_severity_word_is_not_a_finding(self):
        code, out = run("## Findings\n\n### High level overview\n\nNo Critical or High findings.\n")
        self.assertEqual(code, 0, out)


class LargeFile(unittest.TestCase):
    def test_file_over_the_cap_is_read_from_its_end_and_noted(self):
        pad = ("synthetic transcript line\n" * 60000)[: rv.MAX_BYTES + 5000]
        text = "did not start\n" + pad + "\ntokens used\n9\n" + FINDINGS_BLOCK
        self.assertGreater(len(text.encode()), rv.MAX_BYTES)
        code, out = run(text)
        self.assertEqual(code, 1, out)
        self.assertIn("truncated", out.splitlines()[0])
        self.assertIn("high=1", out.splitlines()[0])

    def test_small_file_has_no_truncation_note(self):
        _, out = run(FINDINGS_BLOCK)
        self.assertNotIn("truncated", out)


class NeverStartedWording(unittest.TestCase):
    def test_variants_hold_a_clean_statement(self):
        for phrase in (
            "the scan never started",
            "the scan has not started",
            "the scan was not started",
            "the scan never got started",
            "the scan did not run",
            "the scan could not run",
            "the scan never ran",
            "the scan failed to run",
            "unable to start the scan",
            "the scan couldn't start",
        ):
            with self.subTest(phrase=phrase):
                code, out = run(f"{CLEAN}\n{phrase}\n")
                self.assertEqual(code, 3, out)
                self.assertIn("failed to start", out)

    def test_variant_beside_findings_is_a_note(self):
        code, out = run(FINDINGS_BLOCK + "\nThe scan never started.\n")
        self.assertEqual(code, 1)
        self.assertIn("note:", out.splitlines()[0])


class CleanOnlyAfterTheHeading(unittest.TestCase):
    def test_statement_before_the_findings_heading_is_not_clean(self):
        code, out = run("No Critical or High findings.\n\n## Findings\n\nLooked around.\n")
        self.assertEqual(code, 3)
        self.assertIn("without a no-Critical-or-High statement", out)

    def test_a_later_heading_after_the_clean_block_is_held(self):
        code, out = run(CLEAN + "\n## More\n\nAnything at all.\n")
        self.assertEqual(code, 3, out)
        self.assertIn("after the clean statement", out)

    def test_a_later_finding_heading_after_the_clean_block_is_not_clean(self):
        code, _ = run(CLEAN + f"\n### Medium {DASH} Synthetic late\n")
        self.assertEqual(code, 1)

    def test_later_severity_mentions_are_held(self):
        for tail in ("High", "**Critical** thing", "Severity: Critical", "high severity gap", "- High: x", "| High |"):
            with self.subTest(tail=tail):
                code, out = run(f"{CLEAN}\n{tail}\n")
                self.assertEqual(code, 3, out)

    def test_truncated_final_block_with_an_open_code_fence_is_held(self):
        code, out = run(CLEAN + "\n```\nstack trace cut off here\n")
        self.assertEqual(code, 3, out)
        self.assertIn("truncated", out)

    def test_balanced_fence_and_plain_text_after_clean_stay_clean(self):
        code, out = run(CLEAN + "\nNot covered: the synthetic installer.\n```\nok\n```\n")
        self.assertEqual(code, 0, out)

    def test_a_clean_block_then_a_second_clean_statement_is_still_clean(self):
        code, _ = run(CLEAN + "\nNo Critical or High findings, confirmed.\n")
        self.assertEqual(code, 0)


class PathComponents(unittest.TestCase):
    def test_symlinked_parent_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "real").mkdir()
            (d / "real" / "r.txt").write_text(CLEAN)
            (d / "link").symlink_to(d / "real")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rv.main([str(d / "link" / "r.txt")]), 2)
                self.assertEqual(rv.main([str(d / "real" / "r.txt")]), 0)

    def test_dotdot_components_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "sub").mkdir()
            (d / "r.txt").write_text(CLEAN)
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rv.main([str(d / "sub" / ".." / "r.txt")]), 2)

    def test_relative_path_in_a_real_directory_works(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "r.txt").write_text(CLEAN)
            old = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(rv.main(["r.txt"]), 0)
            finally:
                os.chdir(old)


class Unreadable(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rv.main([str(Path(d) / "nope")]), 2)

    def test_directory_symlink_fifo_and_unreadable_oversize_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            real = d / "real.txt"
            real.write_text(CLEAN)
            (d / "link.txt").symlink_to(real)
            os.mkfifo(d / "fifo")
            big = d / "big.txt"
            big.write_bytes(b"x" * (rv.MAX_BYTES + 1))
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rv.main([str(d)]), 2)
                self.assertEqual(rv.main([str(d / "link.txt")]), 2)
                self.assertEqual(rv.main([str(d / "fifo")]), 2)
                self.assertEqual(rv.main([str(big)]), 3)
                self.assertEqual(rv.main([str(real)]), 0)

    def test_usage_errors_exit_2(self):
        for argv in ([], ["a", "b"]):
            r = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True)
            self.assertEqual(r.returncode, 2)

    def test_exit_codes_through_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.txt"
            for text, want in ((CLEAN, 0), (FINDINGS_BLOCK, 1), ("", 3)):
                p.write_text(text)
                r = subprocess.run([sys.executable, str(SCRIPT), str(p)], capture_output=True)
                self.assertEqual(r.returncode, want)


class SectionLayout(unittest.TestCase):
    """Issue #143: `## High` with `### 1. Title` children. Fixtures are short anonymized heading blocks."""

    def fx(self, name):
        return (ROOT / "tests" / "fixtures" / name).read_text()

    def test_numbered_children_give_titles_and_section_counts(self):
        code, out = run(self.fx("review_layout_sections_numbered.txt"))
        self.assertEqual(code, 1)
        first = out.splitlines()[0]
        for part in ("critical=0", "high=2", "medium=1", "low=1", "unique 4"):
            self.assertIn(part, first)
        self.assertIn("Gate integrity does not cover repository code run by the workflow", out)
        self.assertIn("Typo in a log label", out)
        self.assertNotIn("must never be echoed", out)

    def test_no_finding_has_an_empty_title(self):
        findings = rv.parse_findings(self.fx("review_layout_sections_numbered.txt"))
        self.assertEqual(len(findings), 4)
        self.assertTrue(all(title for _, title, _ in findings))

    def test_findings_suffix_headings_and_mixed_children(self):
        code, out = run(self.fx("review_layout_sections_findings_suffix.txt"))
        self.assertEqual(code, 1)
        first = out.splitlines()[0]
        self.assertIn("high=1", first)
        self.assertIn("medium=1", first)
        self.assertIn("Required check can be spoofed by a status", out)
        self.assertIn("Typed values accept a trailing newline", out)

    def test_empty_severity_section_still_fails_closed(self):
        code, out = run("## High\n\nSomething was found but not itemized.\n")
        self.assertEqual(code, 1)
        self.assertIn("high=1", out.splitlines()[0])

    def test_unclassifiable_severity_heading_is_never_clean(self):
        code, out = run(self.fx("review_layout_unclassified.txt"))
        self.assertIn(code, (1, 3))
        self.assertNotIn("CLEAN", out.splitlines()[0])

    def test_ordinary_numbered_headings_do_not_hold_a_clean_review(self):
        code, _ = run("## Findings\n\n### 1. Scope reviewed\n\nNo Critical or High findings.\n")
        self.assertEqual(code, 0)

    def test_deeper_subheadings_are_not_findings(self):
        text = self.fx("review_layout_section_with_subheadings.txt")
        findings = rv.parse_findings(text)
        self.assertEqual(
            [(sev, title) for sev, title, _ in findings],
            [
                ("high", "Required check can be spoofed by a status"),
                ("medium", "Typed values accept a trailing newline"),
            ],
        )
        code, out = run(text)
        self.assertEqual(code, 1)
        self.assertIn("unique 2", out.splitlines()[0])

    def test_severity_in_a_findings_heading_never_gives_clean(self):
        code, out = run(self.fx("review_layout_findings_heading_with_severity.txt"))
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("VERDICT: HELD"))


if __name__ == "__main__":
    unittest.main()
