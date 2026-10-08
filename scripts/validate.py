"""Offline structural checks. Not a model-behavior or sandbox certification."""

import re
import sys
from pathlib import Path

from generate import drift
from lib import (
    CAPABILITY_ORDER,
    PACK_NAMES,
    POLICIES,
    ROLE_NAMES,
    ROOT,
    TIER_MODEL,
    frontmatter,
    load_packs,
    load_roles,
)

EDITORS = {"implementer", "transcriber"}  # the only roles that may carry the edit capability
RUNNERS = {"implementer", "reviewer", "adversarial-reviewer", "transcriber"}  # all must be able to run commands

# Built from pieces so this file does not trip the sweep it implements.
PRIVACY_PATTERNS = [
    r"([0-9]{1,3}\.){3}[0-9]{1,3}",
    "192" + r"\.168",
    "/ho" + "me/",
    "chiefgyk3d" + "/dotfiles",
    "llm" + "-local",
    "dot" + "sync",
    "agents" + "-status",
    "50" + "60",
    "30" + "50",
    "114" + "35",
]
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
            n = r["name"]
            require(r["file"] == f"{n}.md", f"{n}: file name must match name")
            require(
                set(r) == {"name", "description", "tier", "tools", "body", "file"},
                f"{n}: unreviewed frontmatter fields",
            )
            require(bool(r.get("description")) and bool(r.get("body")), f"{n}: empty description or body")
            require(
                r.get("tier") in TIER_MODEL,
                f"{n}: tier must be one of {sorted(TIER_MODEL)} (the most capable tier is never a role default)",
            )
            tools = r.get("tools")
            ok_tools = isinstance(tools, list) and tools and all(t in CAPABILITY_ORDER for t in tools)
            require(ok_tools, f"{n}: tools must be a non-empty list of known capabilities")
            if ok_tools:
                require(("edit" in tools) == (n in EDITORS), f"{n}: edit capability mismatch")
                if n in RUNNERS:
                    require("shell" in tools, f"{n}: must be able to run commands (read-only reviewers were rejected)")
        require({r["name"]: r["tier"] for r in roles}.get("transcriber") == "cheap", "transcriber must be tier cheap")

        packs = load_packs(root)
        require({p["dir"] for p in packs} == set(PACK_NAMES), "packs/ must hold exactly the six packs")
        for p in packs:
            require(p.get("name") == p["dir"], f"{p['dir']}: name must match directory")
            require(bool(p.get("description")) and bool(p["body"]), f"{p['dir']}: empty description or body")
            require(set(p) == {"name", "description", "body", "dir"}, f"{p['dir']}: unreviewed frontmatter fields")

        for pol in POLICIES:
            require((root / f"policies/{pol}.md").is_file(), f"missing policy {pol}")
        require({f.stem for f in (root / "policies").glob("*.md")} == set(POLICIES), "unexpected policy file")

        # Adapters: byte-for-byte regeneration, then the host-specific guarantees.
        errors.extend(drift(root))
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
        for f in sorted((root / ".claude/skills").glob("*/SKILL.md")):
            d, b = frontmatter(f)
            require(set(d) == {"name", "description"} and d["name"] == f.parent.name and bool(b), f"{f}: invalid skill")

        # Version agreement.
        version = (root / "VERSION").read_text().strip()
        require(bool(re.fullmatch(r"\d+\.\d+\.\d+", version)), "VERSION is not semver")
        require(f'version = "{version}"' in (root / "pyproject.toml").read_text(), "pyproject version mismatch")
        require(
            re.search(rf"^## \[?{re.escape(version)}\]?", (root / "CHANGELOG.md").read_text(), re.M) is not None,
            "CHANGELOG has no entry for VERSION",
        )

        errors.extend(workflow_errors(root))

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
