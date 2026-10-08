"""Generate every host adapter from roles/, packs/ and policies/.

    python3 scripts/generate.py            write the adapters
    python3 scripts/generate.py --check    exit 1 if any adapter differs from what would be written

Adapters are never edited by hand. A test fails on drift.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from lib import (
    CAPABILITY_ORDER,
    CLAUDE_TOOLS,
    COPILOT_TOOLS,
    GENERATED_DIRS,
    GENERATED_MARK,
    POLICIES,
    ROOT,
    TIER_MODEL,
    load_packs,
    load_roles,
    policy_text,
    version,
)


def _q(value):
    return json.dumps(value, ensure_ascii=False)


def _policies_block(root):
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


def _marker(source, ver, extra=""):
    return f"<!-- {GENERATED_MARK} (release {ver}) from {source}{extra}. Do not edit; run the generator. -->"


def _capabilities(role):
    caps = role["tools"]
    if not isinstance(caps, list) or any(c not in CAPABILITY_ORDER for c in caps):
        raise ValueError(f"roles/{role['file']}: unknown capability in tools")
    return [c for c in CAPABILITY_ORDER if c in caps]


def render_claude_agent(role, policies, ver):
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
    return f"{head}\n{_marker('roles/' + role['file'], ver)}\n\n{role['body']}\n\n{policies}\n"


def render_copilot_agent(role, policies, ver):
    tools = [COPILOT_TOOLS[c] for c in _capabilities(role) if c in COPILOT_TOOLS]
    head = f"---\nname: {role['name']}\ndescription: {_q(role['description'])}\ntools: {_q(tools)}\n---\n"
    return f"{head}\n{_marker('roles/' + role['file'], ver)}\n\n{role['body']}\n\n{policies}\n"


def render_skill(pack, ver):
    head = f"---\nname: pack-{pack['dir']}\ndescription: {_q(pack['description'])}\n---\n"
    updated = pack.get("updated")
    if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
        raise ValueError(f"packs/{pack['dir']}/PACK.md: frontmatter needs updated: YYYY-MM-DD")
    return f"{head}\n{_marker('packs/' + pack['dir'] + '/PACK.md', ver, f', updated {updated}')}\n\n{pack['body']}\n"


def render_all(root=ROOT):
    """Return {relative path: text} for every generated file."""
    root = Path(root)
    policies = _policies_block(root)
    ver = version(root)
    out = {}
    for role in load_roles(root):
        out[f".claude/agents/{role['name']}.md"] = render_claude_agent(role, policies, ver)
        out[f".github/agents/{role['name']}.agent.md"] = render_copilot_agent(role, policies, ver)
    for pack in load_packs(root):
        out[f".claude/skills/pack-{pack['dir']}/SKILL.md"] = render_skill(pack, ver)
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
