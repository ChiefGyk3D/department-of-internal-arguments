"""Offline structural checks. Not a model-behavior or sandbox certification."""

import re
import sys
import tomllib
from pathlib import Path

from benchmark import corpus_errors, load_corpus
from generate import ADDITIONS_HEADING, drift
from lib import (
    CAPABILITY_ORDER,
    CODEX_AGENTS_DIR,
    CODEX_SKILLS_DIR,
    FRAGMENT_NAMES,
    FRAGMENT_RE,
    GENERATED_MARK,
    PACK_NAMES,
    POLICIES,
    ROLE_NAMES,
    ROOT,
    TIER_MODEL,
    fragment_text,
    frontmatter,
    load_packs,
    load_roles,
)

EDITORS = {"implementer", "transcriber"}  # the only roles that may carry the edit capability
RUNNERS = {"implementer", "reviewer", "adversarial-reviewer", "transcriber"}  # all must be able to run commands

# Built from pieces so this file does not trip the sweep it implements. Word-bounded so a
# commit SHA or a four-part version string cannot trip it.
PRIVACY_PATTERNS = [
    r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])",
    r"\b" + "192" + r"\.168" + r"\b",
    "/ho" + "me/",
    "chiefgyk3d" + "/dotfiles",
    r"\b" + "llm" + "-local" + r"\b",
    r"\b" + "dot" + "sync" + r"\b",
    r"\b" + "agents" + "-status" + r"\b",
    r"\b" + "50" + "60" + r"\b",
    r"\b" + "30" + "50" + r"\b",
    r"\b" + "114" + "35" + r"\b",
]
REPORT_FIELDS = (
    "ROLE:",
    "MODEL:",
    "HEAD:",
    "BASE:",
    "GATE:",
    "OTHER GATES:",
    "PACKS LOADED:",
    "TREE:",
    "NOT VERIFIED:",
    "LOCAL-LLM:",
    "VERIFIED-AGAINST:",
    "DISCREPANCIES:",
)
LOCAL_LLM_TOKENS = ("LOCAL-LLM:", "VERIFIED-AGAINST:", "DISCREPANCIES:")
TEMPLATES = ("adr", "brief", "copilot-instructions", "handoff", "lead")
# One phrase per orchestrator rule in templates/lead.md (issue #33); the template is injected into a controller session,
# so a rule that silently disappears from it must fail the gate.
LEAD_RULES = {
    "waits": "You do not wait.",
    "tracking": "`issue: owner/repo#N`",
    "working directory": "same command as the worktree dispatch",
    "look first": "## Before you tell the human to do something",
    "access": "ask once for the union",
    "done": "## Done",
    "approval token": "approval token",
    "public weakness": "unfixed weakness",
    "rejected option": "Re-suggest an option the human has rejected.",
}
PRIVACY_RE = re.compile("|".join(f"(?:{p})" for p in PRIVACY_PATTERNS))
FORBIDDEN_SUFFIXES = {".vtt", ".pem", ".key", ".p12", ".pfx"}
SKIP_PARTS = {".git", "__pycache__"}


def tree_files(root):
    for f in sorted(Path(root).rglob("*")):
        if SKIP_PARTS.intersection(f.relative_to(root).parts):
            continue
        if f.is_file() or f.is_symlink():
            yield f


def privacy_hits(root=ROOT):
    """(relative path, line number) for every line matching the private-identifier sweep."""
    hits = []
    for f in tree_files(root):
        if f.is_symlink():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PRIVACY_RE.search(line):
                hits.append((str(f.relative_to(root)), n))
    return hits


def workflow_errors(root):
    errors = []
    wf_dir = Path(root) / ".github/workflows"
    shas = set()
    files = sorted(wf_dir.glob("*.yml")) if wf_dir.is_dir() else []
    if not files:
        errors.append("no workflow files found")
    for f in files:
        text = f.read_text(encoding="utf-8")
        name = f.name
        if "pull_request_target" in text:
            errors.append(f"{name}: privileged PR trigger")
        if "continue-on-error" in text:
            errors.append(f"{name}: continue-on-error is not allowed")
        if not re.search(r"^permissions:", text, re.M):
            errors.append(f"{name}: no top-level permissions block")
        uses = re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", text, re.M)
        if not uses:
            errors.append(f"{name}: no uses: lines")
        for ref, rest in uses:
            m = re.fullmatch(r"[\w.-]+/[\w./-]+@([0-9a-f]{40})", ref)
            if not m:
                errors.append(f"{name}: action must be pinned by full commit SHA: {ref}")
                continue
            shas.add(m.group(1))
            if not re.search(r"#\s*v\d+\.\d+\.\d+\s*$", rest):
                errors.append(f"{name}: pin needs a trailing '# vX.Y.Z' comment: {ref}")
    if len(shas) > 1:
        errors.append("workflows pin more than one commit; re-pin every caller together")
    return errors


def role_errors(r):
    """Structural rules for one loaded role. Shared by the public tree and the private overlay.

    Content rules only: no filesystem access and no privacy sweep (the sweep is tree-wide and public-only)."""
    errors = []

    def require(ok, message):
        if not ok:
            errors.append(message)

    n = r["name"]
    require(r["file"] == f"{n}.md", f"{n}: file name must match name")
    require(
        set(r) == {"name", "description", "tier", "tools", "body", "source", "file"},
        f"{n}: unreviewed frontmatter fields",
    )
    require(
        isinstance(r.get("description"), str) and bool(r.get("description")) and bool(r.get("body")),
        f"{n}: empty description or body",
    )
    require(
        isinstance(r.get("tier"), str) and r.get("tier") in TIER_MODEL,
        f"{n}: tier must be one of {sorted(TIER_MODEL)} (the most capable tier is never a role default)",
    )
    tools = r.get("tools")
    ok_tools = isinstance(tools, list) and tools and all(t in CAPABILITY_ORDER for t in tools)
    require(ok_tools, f"{n}: tools must be a non-empty list of known capabilities")
    if ok_tools:
        require(("edit" in tools) == (n in EDITORS), f"{n}: edit capability mismatch")
        if n in RUNNERS:
            require("shell" in tools, f"{n}: must be able to run commands (read-only reviewers were rejected)")
    require("{{fragment:report-header}}" in r["source"], f"{n}: report must use the shared header fragment")
    require("{{fragment:" not in r["body"], f"{n}: unresolved fragment marker")
    for field in REPORT_FIELDS:
        require(field in r["body"], f"{n}: report header lacks {field}")
    if "edit" in (tools or []):
        require("{{fragment:never-weaken}}" in r["source"], f"{n}: editing roles share the never-weaken rule")
        require("Never push" in r["body"], f"{n}: editing roles must say they never push")
    if n == "transcriber":
        require(r.get("tier") == "cheap", "transcriber must be tier cheap")
    return errors


MAX_ADDITIONS_BYTES = 16 * 1024
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_HTML_RE = re.compile(r"<[A-Za-z/!?]")
_HASH_REF_RE = re.compile(r"&(?:#0*35|#[xX]0*23|num);")
_SETEXT_RE = re.compile(r"^ {0,3}(=+|-{2,})[ \t]*$")


def _norm_heading(text):
    return " ".join(text.casefold().split())


def public_headings(text):
    """Normalized text of every ATX heading in a rendered public file (fences and comments are NOT skipped)."""
    return {_norm_heading(m.group(2)) for ln in text.splitlines() if (m := _HEADING_RE.match(ln))}


def additions_errors(name, body, public_text):
    """Rules for the private text appended to a public persona or pack (see generate.additions_section).

    The public text is never edited, so nothing here protects it; these rules keep the added text from posing as part
    of it: size cap, no marker text, no raw HTML (a < before a letter, /, ! or ?, autolinks included), no encoded #,
    no fragment lines, no top-level (# or ##) or setext headings, and no heading that repeats a public heading or
    the additions heading itself. Prose that merely asks for something at odds with the public rules cannot be
    detected."""
    errors = []
    if not body.strip():
        errors.append(f"{name}: additions are empty")
    if len(body.encode("utf-8")) > MAX_ADDITIONS_BYTES:
        errors.append(f"{name}: additions too large (limit {MAX_ADDITIONS_BYTES} bytes)")
    if GENERATED_MARK in body:
        errors.append(f"{name}: additions may not contain the generator marker text")
    if _HTML_RE.search(body):
        errors.append(f"{name}: additions may not contain raw HTML (a < that starts a tag, comment or declaration)")
    if _HASH_REF_RE.search(body):
        errors.append(f"{name}: additions may not contain a character reference for #")
    taken = public_headings(public_text) | {_norm_heading(ADDITIONS_HEADING[3:])}
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if FRAGMENT_RE.fullmatch(ln.strip()) or "{{fragment:" in ln:
            errors.append(f"{name}: additions may not use fragment markers")
        m = _HEADING_RE.match(ln)
        if m and len(m.group(1)) <= 2:
            errors.append(f"{name}: additions heading must be level 3 or deeper: {ln.strip()[:60]}")
        if m and _norm_heading(m.group(2)) in taken:
            errors.append(f"{name}: additions heading repeats a public section name: {ln.strip()[:60]}")
        if i + 1 < len(lines) and ln.strip() and not _SETEXT_RE.match(ln) and _SETEXT_RE.match(lines[i + 1]):
            errors.append(f"{name}: additions may not use underlined (setext) headings: {ln.strip()[:60]}")
    return errors


def pack_errors(p):
    """Structural rules for one loaded pack. Shared by the public tree and the private overlay."""
    errors = []

    def require(ok, message):
        if not ok:
            errors.append(message)

    d = p["dir"]
    require(p.get("name") == d, f"{d}: name must match directory")
    require(
        isinstance(p.get("description"), str) and bool(p.get("description")) and bool(p["body"]),
        f"{d}: empty description or body",
    )
    require(set(p) == {"name", "description", "updated", "body", "dir"}, f"{d}: unreviewed frontmatter fields")
    require(
        isinstance(p.get("updated"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["updated"]) is not None,
        f"{d}: updated must be YYYY-MM-DD",
    )
    require("## How to verify" in p["body"], f"{d}: pack needs a '## How to verify' section")
    return errors


def validate(root=ROOT):
    root = Path(root)
    errors = []

    def require(ok, message):
        if not ok:
            errors.append(message)

    try:
        roles = load_roles(root)
        require({r["name"] for r in roles} == set(ROLE_NAMES), "roles/ must hold exactly the four roles")
        for r in roles:
            errors.extend(role_errors(r))
        require({r["name"]: r["tier"] for r in roles}.get("transcriber") == "cheap", "transcriber must be tier cheap")

        frag_dir = root / "fragments"
        require(
            {f.stem for f in frag_dir.glob("*.md")} == set(FRAGMENT_NAMES),
            "fragments/ must hold exactly the known fragments",
        )
        # The Copilot template is copied into other repos verbatim, so its opt-out list must carry every
        # token the shared fragment names.
        tokens = re.findall(r"`([^`]+)`", fragment_text("never-weaken", root))
        copilot = (root / "templates/copilot-instructions.md").read_text(encoding="utf-8")
        for tok in tokens:
            require(tok in copilot, f"templates/copilot-instructions.md lacks never-weaken token {tok!r}")
        # Copilot reports in a PR description, so the template must ask for the same local-model line.
        for tok in LOCAL_LLM_TOKENS:
            require(tok in copilot, f"templates/copilot-instructions.md lacks local-model report token {tok!r}")
        require({f.stem for f in (root / "templates").glob("*.md")} == set(TEMPLATES), "unexpected or missing template")
        lead = (root / "templates/lead.md").read_text(encoding="utf-8")
        for rule, phrase in LEAD_RULES.items():
            require(phrase in lead, f"templates/lead.md lacks the {rule} rule ({phrase!r})")

        packs = load_packs(root)
        require({p["dir"] for p in packs} == set(PACK_NAMES), "packs/ must hold exactly the six packs")
        for p in packs:
            errors.extend(pack_errors(p))

        for pol in POLICIES:
            require((root / f"policies/{pol}.md").is_file(), f"missing policy {pol}")
        require({f.stem for f in (root / "policies").glob("*.md")} == set(POLICIES), "unexpected policy file")

        # Adapters: byte-for-byte regeneration, then the host-specific guarantees.
        errors.extend(drift(root))
        version = (root / "VERSION").read_text().strip()
        for f in sorted((root / ".claude/agents").glob("*.md")):
            d, _ = frontmatter(f)
            require(set(d) == {"name", "description", "model", "tools"}, f"{f.name}: unreviewed Claude fields")
            require(d.get("model") in set(TIER_MODEL.values()), f"{f.name}: model must be explicit (never inherit)")
            require(
                "Agent" not in str(d.get("tools", "")).split(", "), f"{f.name}: must not be able to dispatch agents"
            )
        for f in sorted((root / ".github/agents").glob("*.agent.md")):
            d, _ = frontmatter(f)
            require(set(d) == {"name", "description", "tools"}, f"{f.name}: unreviewed Copilot fields")
        for f in sorted((root / CODEX_AGENTS_DIR).glob("*.toml")):
            try:
                d = tomllib.loads(f.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                errors.append(f"{f.name}: invalid TOML ({exc})")
                continue
            require(
                set(d) == {"name", "description", "developer_instructions"} and d.get("name") == f.stem,
                f"{f.name}: unreviewed Codex fields (a model, sandbox or approval key must not be set)",
            )
        for d_ in (".claude/skills", CODEX_SKILLS_DIR):
            for f in sorted((root / d_).glob("*/SKILL.md")):
                d, b = frontmatter(f)
                require(
                    set(d) == {"name", "description"} and d["name"] == f.parent.name and bool(b), f"{f}: invalid skill"
                )
        for d_ in (".claude/agents", ".claude/skills", ".github/agents", CODEX_AGENTS_DIR, CODEX_SKILLS_DIR):
            for f in sorted(p for p in (root / d_).rglob("*") if p.is_file()):
                require(
                    f"{GENERATED_MARK} (release {version})" in f.read_text(encoding="utf-8"),
                    f"{f.relative_to(root)}: generated marker lacks the current release",
                )

        # Version agreement.
        require(bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), "VERSION is not semver")
        require(f'version = "{version}"' in (root / "pyproject.toml").read_text(), "pyproject version mismatch")
        require(
            re.search(rf"^## \[?{re.escape(version)}\]?", (root / "CHANGELOG.md").read_text(), re.M) is not None,
            "CHANGELOG has no entry for VERSION",
        )

        errors.extend(workflow_errors(root))

        # docs/ is mirrored to the wiki; the privacy sweep and link check below already walk it.
        bench = root / "evals/benchmark"
        if (bench / "corpus.json").is_file():
            try:
                errors.extend(
                    f"benchmark corpus: {e}"
                    for e in corpus_errors(load_corpus(bench / "corpus.json"), True, bench / "probes", bench)
                )
            except (OSError, ValueError) as exc:
                errors.append(f"benchmark corpus: unreadable ({exc})")
        else:
            errors.append("evals/benchmark/corpus.json is missing")
        require((root / "docs/home.md").is_file(), "docs/home.md is missing: it becomes the wiki Home page")

        # Local Markdown links must resolve (URLs and anchors excluded).
        for f in tree_files(root):
            if f.suffix != ".md" or f.is_symlink():
                continue
            for dest in re.findall(r"\]\(([^)\s]+)\)", f.read_text(encoding="utf-8")):
                if "://" in dest or dest.startswith(("#", "mailto:")):
                    continue
                require((f.parent / dest.split("#", 1)[0]).exists(), f"{f.relative_to(root)}: broken link {dest}")

        # Packaging invariants: no symlinks, no raw corpus or key material.
        for f in tree_files(root):
            rel = f.relative_to(root)
            require(not f.is_symlink(), f"symlink in package: {rel}")
            bad = (
                f.suffix in FORBIDDEN_SUFFIXES
                or f.name.startswith(".env")
                or (f.name.startswith("mastodon") and f.suffix == ".json")
                or "transcripts" in rel.parts
            )
            require(not bad, f"forbidden data artifact: {rel}")

        for rel, n in privacy_hits(root):
            errors.append(f"private-identifier pattern at {rel}:{n}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return errors


if __name__ == "__main__":
    problems = validate(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    for e in problems:
        print("FAIL:", e)
    if problems:
        sys.exit(1)
    print("PASS: roles, packs, policies, adapter parity, workflow pins, links and privacy sweep")
