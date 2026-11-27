"""Read a security-review output file and print one verdict line plus a findings table.

    python3 scripts/review_verdict.py REVIEW.txt

Verdicts (pack rule 3 in packs/cyber-tier-review: a review that cannot show its answer is held, never clean):

    CLEAN     exit 0  a findings section is present and the review states there are no Critical or High findings
    FINDINGS  exit 1  one or more findings; counts per severity, then one row per unique finding
    HELD      exit 3  no answer to read: empty output, no findings section, a cutoff, an authentication error, a
                      scan or tool that failed to start, or a bare status=ok wrapper marker with none of the above
    usage or unreadable input: exit 2

A finding is a heading such as `### High - title` (hyphen, en dash, em dash or colon). Reviews often repeat the
findings block, so findings are deduplicated on severity and normalized title; the first line number is kept. Held
wins over everything else: a held review is listed with any findings it did show but is never clean.

The input is read as untrusted data: one regular file, no symlink, bounded size, invalid UTF-8 replaced. Output never
echoes a finding body or the text that triggered HELD; only fixed reason strings and titles capped at 200 characters
with control characters removed. Standard library only."""

import os
import re
import stat
import sys
import unicodedata

MAX_BYTES = 1024 * 1024
TITLE_MAX = 200
SEVERITIES = ("critical", "high", "medium", "low", "info")
EXIT_CLEAN, EXIT_FINDINGS, EXIT_USAGE, EXIT_HELD = 0, 1, 2, 3

# A separator after the severity: hyphen, en dash, em dash or colon.
FINDING_RE = re.compile(
    r"^[ \t]{0,3}#{2,6}[ \t]*(Critical|High|Medium|Low|Info(?:rmational)?)[ \t]*[-–—:]+[ \t]*(.*)$", re.I
)
SECTION_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*(?:\*\*)?(?:findings|verdict|summary of findings)\b", re.I)
CLEAN_RE = re.compile(
    r"\bno[ \t]+(?:critical|high)(?:[ \t]*(?:,|/|or|and|&)[ \t]*(?:critical|high))?(?:[ \t]+severity)?[ \t]+"
    r"(?:findings?|issues?|vulnerabilit(?:y|ies))\b",
    re.I,
)
# Fixed reason -> pattern. The reason is printed; the matched text never is.
HELD_PATTERNS = (
    ("cutoff: flagged for possible risk", re.compile(r"cybersecurity[ \t]+risk|flagged[ \t]+for[ \t]+possible", re.I)),
    (
        "authentication error",
        re.compile(
            r"hardware[ \t]+key|not[ \t]+authenticated|re-?authenticate|authentication[ \t]+(?:error|failed|required)",
            re.I,
        ),
    ),
    (
        "scan or tool failed to start",
        re.compile(
            r"did[ \t]+not[ \t]+start|could[ \t]+not[ \t]+start|failed[ \t]+to[ \t]+start|terminal[ \t]+failure"
            r"|is[ \t]+not[ \t]+a[ \t]+function",
            re.I,
        ),
    ),
)


def clean_title(raw):
    """Strip markdown emphasis and control, format and line-separator characters, collapse space, cap the length."""
    text = "".join(
        " " if unicodedata.category(c) in ("Zl", "Zp") else c
        for c in raw
        if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn") or c in "\t"
    )
    text = re.sub(r"\s+", " ", text.replace("\t", " ")).strip().strip("*_`# ").strip()
    return text[:TITLE_MAX]


def parse(text):
    """Return (findings, section_present, clean_statement, held_reasons). Findings: [(severity, title, line)]."""
    findings, seen = [], set()
    section = clean_statement = False
    for number, line in enumerate(text.splitlines(), 1):
        m = FINDING_RE.match(line)
        if m:
            sev = m.group(1).lower()
            sev = "info" if sev.startswith("info") else sev
            title = clean_title(m.group(2))
            key = (sev, re.sub(r"\W+", " ", title).strip().lower())
            if key not in seen:
                seen.add(key)
                findings.append((sev, title, number))
            section = True
            continue
        if SECTION_RE.match(line):
            section = True
        if CLEAN_RE.search(line):
            clean_statement = True
    held = [reason for reason, pat in HELD_PATTERNS if pat.search(text)]
    return findings, section, clean_statement, held


def read_review(path):
    """Read one regular, non-symlink file of at most MAX_BYTES. Raises OSError or ValueError otherwise."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise ValueError("file too large")
    return data.decode("utf-8", errors="replace")


def verdict(text):
    """Return (name, exit code, reason or None, findings)."""
    findings, section, clean_statement, held = parse(text)
    if not text.strip():
        return "HELD", EXIT_HELD, "empty output", findings
    if held:
        return "HELD", EXIT_HELD, held[0], findings
    if findings:
        return "FINDINGS", EXIT_FINDINGS, None, findings
    if section and clean_statement:
        return "CLEAN", EXIT_CLEAN, None, findings
    if not section:
        return "HELD", EXIT_HELD, "no findings section", findings
    return "HELD", EXIT_HELD, "findings section without a no-Critical-or-High statement", findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: review_verdict.py REVIEW_FILE", file=sys.stderr)
        return EXIT_USAGE
    try:
        text = read_review(argv[0])
    except (OSError, ValueError) as e:
        print(f"review_verdict: cannot read input: {type(e).__name__}", file=sys.stderr)
        return EXIT_USAGE
    name, code, reason, findings = verdict(text)
    counts = {s: sum(1 for f in findings if f[0] == s) for s in SEVERITIES}
    tally = " ".join(f"{s}={n}" for s, n in counts.items())
    if name == "CLEAN":
        print("VERDICT: CLEAN no Critical or High findings stated")
    elif name == "FINDINGS":
        print(f"VERDICT: FINDINGS {tally} (unique {len(findings)})")
    else:
        print(f"VERDICT: HELD {reason} (never a clean review) {tally}")
    if findings:
        print(f"{'SEVERITY':<9} {'LINE':>5}  TITLE")
        for sev, title, number in findings:
            print(f"{sev:<9} {number:>5}  {title}")
    return code


if __name__ == "__main__":
    sys.exit(main())
