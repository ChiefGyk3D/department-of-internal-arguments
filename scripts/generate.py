"""Generate every host adapter from roles/, packs/ and policies/.

    python3 scripts/generate.py            write the adapters
    python3 scripts/generate.py --check    exit 1 if any adapter differs from what would be written

Adapters are never edited by hand. A test fails on drift.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

import evals_matrix
from lib import (
    CAPABILITY_ORDER,
    CLAUDE_TOOLS,
    CODEX_AGENTS_DIR,
    CODEX_SKILLS_DIR,
    COPILOT_TOOLS,
    GENERATED_DIRS,
    GENERATED_MARK,
    POLICIES,
    ROOT,
    TIER_MODEL,
    body_sha256,
    load_packs,
    load_roles,
    policy_text,
    split_marker,
    version,
)


def _q(value):
    return json.dumps(value, ensure_ascii=False)


def policies_block(root):
    parts = ["## Shared policies", ""]
    for name in POLICIES:
        text = policy_text(name, root)
        first, _, rest = text.partition("\n")
        if not first.startswith("# "):
            raise ValueError(f"policies/{name}.md must start with a '# ' title")
        parts.append("### " + first[2:])
        parts.append(rest.strip("\n"))
        parts.append("")
    return "\n".join(parts).rstrip()


def _marker(source, ver, extra="", digest=None):
    sha = f" sha256:{digest}." if digest else ""
    return f"<!-- {GENERATED_MARK} (release {ver}) from {source}{extra}.{sha} Do not edit; run the generator. -->"


def _seal(head, tail, source, ver, extra=""):
    """head + marker + tail, with the marker carrying the sha256 of everything except the marker line."""
    text = f"{head}\n{_marker(source, ver, extra)}\n\n{tail}"
    parts = split_marker(text)
    if parts is None:  # a body that quotes the marker text would make ownership unprovable
        raise ValueError(f"{source}: body must not contain the generator marker text")
    return f"{head}\n{_marker(source, ver, extra, body_sha256(parts[1]))}\n\n{tail}"


def _capabilities(role):
    caps = role["tools"]
    if not isinstance(caps, list) or any(c not in CAPABILITY_ORDER for c in caps):
        raise ValueError(f"roles/{role['file']}: unknown capability in tools")
    return [c for c in CAPABILITY_ORDER if c in caps]


ADDITIONS_HEADING = "## Maintainer additions (private overlay)"
ADDITIONS_NOTE = (
    "> Everything above this heading is the public persona and its rules. These additions extend it; "
    "they do not replace or relax anything above."
)


def additions_section(additions):
    """The only way private overlay text reaches a public persona: appended, labeled, never replacing."""
    return f"\n{ADDITIONS_HEADING}\n\n{ADDITIONS_NOTE}\n\n{additions.strip()}\n"


def render_claude_agent(role, policies, ver, source=None, additions=None):
    tier = role["tier"]
    if tier not in TIER_MODEL:
        raise ValueError(f"roles/{role['file']}: tier {tier!r} has no model mapping")
    tools = [t for c in _capabilities(role) for t in CLAUDE_TOOLS[c]]
    head = (
        "---\n"
        f"name: {role['name']}\n"
        f"description: {_q(role['description'])}\n"
        f"model: {TIER_MODEL[tier]}\n"
        f"tools: {', '.join(tools)}\n"
        "---\n"
    )
    tail = f"{role['body']}\n\n{policies}\n" + (additions_section(additions) if additions else "")
    return _seal(head, tail, source or "roles/" + role["file"], ver)


def render_copilot_agent(role, policies, ver, source=None, additions=None):
    tools = [COPILOT_TOOLS[c] for c in _capabilities(role) if c in COPILOT_TOOLS]
    head = f"---\nname: {role['name']}\ndescription: {_q(role['description'])}\ntools: {_q(tools)}\n---\n"
    tail = f"{role['body']}\n\n{policies}\n" + (additions_section(additions) if additions else "")
    return _seal(head, tail, source or "roles/" + role["file"], ver)


CODEX_NOTES_HEADING = "## Codex capability notes"
TOML_LITERAL_END = "'''"
CODEX_KEYS = {"name", "description", "developer_instructions"}


class CodexTomlError(ValueError):
    """A role, fragment, policy or overlay text that cannot be written as a valid Codex agent file."""


def codex_notes(role):
    """What Codex can and cannot honor for this role, written into the generated text. Source: OpenAI's Codex
    documentation (subagents, sandboxing and skills pages), retrieved 2026-10-26; nothing has been run in Codex."""
    caps = _capabilities(role)
    lines = [
        CODEX_NOTES_HEADING,
        "",
        "This is a Codex custom agent generated from the same role text as the Claude Code and Copilot agents. "
        "Where the text above names a Claude Code tool (the Skill tool, Bash, Grep), use the Codex equivalent. "
        "UNVERIFIED: no run in Codex is logged yet, so treat the handling below as documented, not measured.",
        "",
        "- Sandbox and approvals: a Codex custom agent inherits the parent session's sandbox and approval settings, "
        "and this file cannot set or change them. Run the parent in the sandbox the task needs.",
        "- Model: this file does not pin a model; the agent inherits the parent's model and reasoning effort. The "
        "`MODEL:` line of the report is the exact model id the session reports, never a family name.",
        "- read, search: honored through Codex's own file and search tools, inside the inherited sandbox.",
    ]
    if "edit" in caps:
        lines.append(
            "- edit: honored, limited by the inherited sandbox (`workspace-write` allows edits in the workspace)."
        )
    else:
        lines.append(
            "- edit: this role has no edit capability, and Codex cannot remove it from a custom agent. "
            "The rule that you never edit is therefore an instruction only. A `read-only` parent sandbox would "
            "enforce it but also blocks running commands without approval, so choose per task, and report any "
            "edit you were unable to avoid."
        )
    if "shell" in caps:
        lines.append("- shell: honored through Codex's shell tool, inside the inherited sandbox and approval policy.")
    if "skills" in caps:
        lines.append(
            "- skills: the domain packs are Codex skills named `pack-<name>`; a brief that names a pack loads it by "
            "explicit `$pack-<name>` mention or by selecting it in the skill picker."
        )
    else:
        lines.append(
            "- skills: this role has no skills capability; do not load packs. Whatever the brief pastes is all you get."
        )
    lines.append(
        "- Dispatch: this role never dispatches agents. Codex can spawn subagents when asked, and this file cannot "
        "turn that off, so the rule is an instruction only."
    )
    return "\n".join(lines)


def render_codex_agent(role, policies, ver, source=None, additions=None):
    """A Codex custom agent: one TOML file with name, description and developer_instructions.

    The ownership marker is the first line of developer_instructions (a TOML multi-line literal string, so nothing
    is escaped), which keeps the same line-based marker and sha256 as the Markdown adapters."""
    head = (
        f"name = {_q(role['name'])}\n"
        f"description = {_q(role['description'])}\n"
        f"developer_instructions = {TOML_LITERAL_END}"
    )
    tail = f"{role['body']}\n\n{codex_notes(role)}\n\n{policies}\n"
    tail += additions_section(additions) if additions else ""
    if TOML_LITERAL_END in tail:
        raise ValueError(f"roles/{role['file']}: text must not contain three single quotes (they end the TOML string)")
    label = source or "roles/" + role["file"]
    text = _seal(head, tail + TOML_LITERAL_END + "\n", label, ver)
    _check_codex_toml(text, role["name"], label)
    return text


def _check_codex_toml(text, name, label):
    """Parse what was just rendered and hold it to the exact schema before it can be returned or written."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CodexTomlError(f"{label}: would render invalid TOML ({exc}); remove control characters") from None
    if set(data) != CODEX_KEYS or data["name"] != name:
        raise CodexTomlError(f"{label}: rendered TOML does not have exactly {sorted(CODEX_KEYS)} for {name!r}")


def render_skill(pack, ver, source=None, additions=None):
    head = f"---\nname: pack-{pack['dir']}\ndescription: {_q(pack['description'])}\n---\n"
    updated = pack.get("updated")
    if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
        raise ValueError(f"packs/{pack['dir']}/PACK.md: frontmatter needs updated: YYYY-MM-DD")
    tail = f"{pack['body']}\n" + (additions_section(additions) if additions else "")
    return _seal(head, tail, source or "packs/" + pack["dir"] + "/PACK.md", ver, f", updated {updated}")


def render_all(root=ROOT):
    """Return {relative path: text} for every generated file."""
    root = Path(root)
    policies = policies_block(root)
    ver = version(root)
    out = {}
    for role in load_roles(root):
        out[f".claude/agents/{role['name']}.md"] = render_claude_agent(role, policies, ver)
        out[f".github/agents/{role['name']}.agent.md"] = render_copilot_agent(role, policies, ver)
        out[f"{CODEX_AGENTS_DIR}/{role['name']}.toml"] = render_codex_agent(role, policies, ver)
    for pack in load_packs(root):
        out[f".claude/skills/pack-{pack['dir']}/SKILL.md"] = render_skill(pack, ver)
        out[f"{CODEX_SKILLS_DIR}/pack-{pack['dir']}/SKILL.md"] = render_skill(pack, ver)
    return out


def existing_generated(root=ROOT):
    root = Path(root)
    found = set()
    for d in GENERATED_DIRS:
        base = root / d
        if base.is_dir():
            found.update(str(p.relative_to(root)) for p in base.rglob("*") if p.is_file() or p.is_symlink())
    return found


def drift(root=ROOT):
    """List of human-readable drift messages; empty means in sync."""
    root = Path(root)
    want = render_all(root)
    have = existing_generated(root)
    problems = []
    for rel, text in sorted(want.items()):
        p = root / rel
        if not p.is_file() or p.is_symlink():
            problems.append(f"missing generated file: {rel}")
        elif p.read_text(encoding="utf-8") != text:
            problems.append(f"adapter drift (run scripts/generate.py): {rel}")
    for rel in sorted(have - set(want)):
        problems.append(f"stray file in a generated directory: {rel}")
    problems.extend(evals_matrix.doc_drift(root))  # evals/behavioral-evals.md is generated from evals/cases.json
    return problems


def write(root=ROOT):
    root = Path(root)
    want = render_all(root)
    for rel in sorted(existing_generated(root) - set(want)):
        (root / rel).unlink()
    for rel, text in want.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    evals_matrix.write_doc(root)
    for d in GENERATED_DIRS:  # drop directories a removed pack left empty
        base = root / d
        if base.is_dir():
            for sub in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
                if not any(sub.iterdir()):
                    sub.rmdir()
    return sorted(want)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify the adapters are current; write nothing")
    args = ap.parse_args(argv)
    try:
        if args.check:
            problems = drift()
            for p in problems:
                print("FAIL:", p)
            if problems:
                return 1
            print(f"PASS: {len(render_all())} generated files are current")
            return 0
        files = write()
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"Wrote {len(files)} generated files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
