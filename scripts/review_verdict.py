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
only ever blocks a clean verdict: findings in the final message are always reported (FINDINGS, with a note when a
cutoff, authentication or failed-scan marker is also present); a clean statement beside such a marker is HELD.

Only the final message is judged for HELD markers, the findings section and the clean statement: the text after the
last `tokens used` line and its count line, up to a wrapper marker line (`exit=N status=ok`); without a `tokens used`
line, the last 8 KiB. The echoed prompt and the transcript never decide the verdict. Findings come from the final
message first and from the whole file only when it has none. No final message is HELD.

The input is read as untrusted data: one regular file, no symlink, invalid UTF-8 replaced, at most the last 1 MiB read
(a larger file is truncated from the front and the verdict line says so). Output never
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
SEV = r"(?:Critical|High|Medium|Low|Info(?:rmational)?)"
DASHES = "-–—:"
# A finding heading: optional numbering, bold or bracket markers, the severity, a separator (hyphen, en dash, em dash,
# colon, or a closing bracket or bold marker), then the title. A bare "### High level overview" is not a finding.
FINDING_RE = re.compile(
    r"^[ \t]{0,3}#{2,6}[ \t]*(?:\d+(?:\.\d+)*[.)]?[ \t]+)?(?:\*\*|__)?[\[(]?(" + SEV + r")\b"
    r"(?P<sep>[ \t]*[" + DASHES + r"]*(?:[\])]|\*\*|__)*[ \t]*[" + DASHES + r"]*[ \t]*)(?P<title>.*)$",
    re.I,
)
SECTION_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*(?:\*\*)?(?:findings|verdict|summary of findings)\b", re.I)
# One explicit statement that rules out BOTH Critical and High ("No Critical or High findings", "no Critical and no
# High", "no High or Critical"); "no High" alone or "no Critical" alone is not enough. Trailing text is allowed.
_JOIN = r"[ \t]*(?:,|/|&|\bor\b|\band\b)[ \t]*(?:no[ \t]+)?"
CLEAN_RE = re.compile(
    r"\bno[ \t]+(?:\*\*|__)?(?:critical(?:\*\*|__)?"
    + _JOIN
    + r"(?:\*\*|__)?high|high(?:\*\*|__)?"
    + _JOIN
    + r"(?:\*\*|__)?critical)(?![\w-])",
    re.I,
)
# A critical or high word in a severity position on a line that is not a recognized finding heading: bold or bracketed,
# `severity: high`, a table cell, a list item or a line that begins with the word and a separator.
SEVERITY_MENTION_RE = re.compile(
    r"(?:\*\*|__)[ \t]*(?:critical|high)\b|\[[ \t]*(?:critical|high)[ \t]*\]"
    r"|severity[ \t]*[:=][ \t]*\W{0,4}(?:critical|high)\b"
    r"|\|[ \t]*\W{0,4}(?:critical|high)\W{0,4}[ \t]*\|"
    r"|^[ \t>]*(?:[-*+]|\d+[.)])[ \t]+\W{0,4}(?:critical|high)\b"
    r"|^[ \t>#]*\W{0,4}(?:critical|high)\W{0,2}[ \t]*[" + DASHES + r"][ \t]",
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


TOKENS_RE = re.compile(r"^[ \t]*tokens[ \t]+used[ \t]*$", re.I)
COUNT_RE = re.compile(r"^[ \t]*[0-9][0-9,._ ]*[ \t]*$")
WRAPPER_RE = re.compile(r"^[ \t]*exit=\S*[ \t]+status=\S*[ \t]*$", re.I)
FALLBACK_BYTES = 8 * 1024


def final_segment(text):
    """Return (line offset, segment text): the final message only. A real output is the echoed prompt, the model
    transcript, a `tokens used` line with a count line, the final message and a wrapper marker line. Markers in the
    prompt or transcript must not decide the verdict, so only the text after the last `tokens used` line and its
    count line, up to the wrapper marker, is judged. Without that line, the last 8 KiB is used."""
    lines = text.splitlines()
    last = max((i for i, ln in enumerate(lines) if TOKENS_RE.match(ln)), default=None)
    if last is None:
        start = 0
        size = 0
        for i in range(len(lines) - 1, -1, -1):
            size += len(lines[i]) + 1
            if size > FALLBACK_BYTES:
                start = i + 1
                break
    else:
        start = last + 1
        if start < len(lines) and COUNT_RE.match(lines[start]):
            start += 1
    seg = [ln for ln in lines[start:] if not WRAPPER_RE.match(ln)]
    return start, "\n".join(seg)


def finding_match(line):
    m = FINDING_RE.match(line)
    if m and (m.group("sep").strip() or m.group("title") == ""):
        return m
    return None


def parse_findings(text, offset=0):
    """Findings [(severity, title, line)] deduplicated on severity and title, first line kept."""
    findings, seen = [], set()
    for number, line in enumerate(text.splitlines(), 1 + offset):
        m = finding_match(line)
        if m:
            sev = m.group(1).lower()
            sev = "info" if sev.startswith("info") else sev
            title = clean_title(m.group("title"))
            key = (sev, re.sub(r"\W+", " ", title).strip().lower())
            if key not in seen:
                seen.add(key)
                findings.append((sev, title, number))
    return findings


def parse(text):
    """Return (findings, section, clean_statement, held, has_final, stray, seg_has_findings). Findings come from the
    final-message segment, and from the whole file only when the segment has none; the section, the clean statement
    and the held markers are judged in the segment only."""
    offset, seg = final_segment(text)
    seg_findings = parse_findings(seg, offset)
    findings = seg_findings or parse_findings(text)
    section = any(finding_match(ln) or SECTION_RE.match(ln) for ln in seg.splitlines())
    clean_statement = any(CLEAN_RE.search(ln) for ln in seg.splitlines())
    held = [reason for reason, pat in HELD_PATTERNS if pat.search(seg)]
    stray = any(
        SEVERITY_MENTION_RE.search(ln) and not finding_match(ln) and not CLEAN_RE.search(ln) for ln in seg.splitlines()
    )
    return findings, section, clean_statement, held, bool(seg.strip()), stray, bool(seg_findings)


def read_review(path):
    """Read one regular, non-symlink file; a file over MAX_BYTES is read from its last MAX_BYTES (the final message
    is at the end). Returns (text, truncated). Raises OSError or ValueError otherwise."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        truncated = st.st_size > MAX_BYTES
        with os.fdopen(fd, "rb", closefd=False) as f:
            if truncated:
                f.seek(st.st_size - MAX_BYTES)
            data = f.read(MAX_BYTES)
    finally:
        os.close(fd)
    return data.decode("utf-8", errors="replace"), truncated


def verdict(text):
    """Return (name, exit code, reason or None, findings)."""
    findings, section, clean_statement, held, has_final, stray, seg_has_findings = parse(text)
    if not text.strip():
        return "HELD", EXIT_HELD, "empty output", findings
    if not has_final:
        return "HELD", EXIT_HELD, "no final message", findings
    if seg_has_findings:
        # HELD only ever blocks CLEAN: findings the final message shows are always reported, with a note.
        return "FINDINGS", EXIT_FINDINGS, f"note: {held[0]} mentioned" if held else None, findings
    if held:
        return "HELD", EXIT_HELD, held[0], findings
    if findings:
        return "FINDINGS", EXIT_FINDINGS, None, findings
    if section and clean_statement and stray:
        return "HELD", EXIT_HELD, "severity mention outside a recognized finding heading", findings
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
        text, truncated = read_review(argv[0])
    except (OSError, ValueError) as e:
        print(f"review_verdict: cannot read input: {type(e).__name__}", file=sys.stderr)
        return EXIT_USAGE
    name, code, reason, findings = verdict(text)
    counts = {s: sum(1 for f in findings if f[0] == s) for s in SEVERITIES}
    tally = " ".join(f"{s}={n}" for s, n in counts.items())
    note = " [input truncated to its last 1 MiB; line numbers count from the cut]" if truncated else ""
    if name == "CLEAN":
        print(f"VERDICT: CLEAN no Critical or High findings stated{note}")
    elif name == "FINDINGS":
        extra = f" {reason}" if reason else ""
        print(f"VERDICT: FINDINGS {tally} (unique {len(findings)}){extra}{note}")
    else:
        print(f"VERDICT: HELD {reason} (never a clean review) {tally}{note}")
    if findings:
        print(f"{'SEVERITY':<9} {'LINE':>5}  TITLE")
        for sev, title, number in findings:
            print(f"{sev:<9} {number:>5}  {title}")
    return code


if __name__ == "__main__":
    sys.exit(main())
