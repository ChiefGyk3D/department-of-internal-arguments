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

    def test_held_still_lists_findings_but_never_counts_as_clean(self):
        code, out = run(FINDINGS_BLOCK + "\nthe scan did not start\n")
        self.assertEqual(code, 3)
        self.assertIn("Synthetic widget", out)

    def test_held_output_does_not_echo_the_matched_text(self):
        out = self.check_held("flagged for possible cybersecurity risk: SECRETBODY\n")
        self.assertNotIn("SECRETBODY", out)

    def test_invalid_utf8_is_handled(self):
        code, _ = run(b"\xff\xfe\x80 garbage\n")
        self.assertEqual(code, 3)
        code, _ = run(b"## Findings\n\nNo Critical or High findings. \xff\n")
        self.assertEqual(code, 0)


class Unreadable(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rv.main([str(Path(d) / "nope")]), 2)

    def test_directory_symlink_fifo_and_oversize(self):
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
                self.assertEqual(rv.main([str(big)]), 2)
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


if __name__ == "__main__":
    unittest.main()
