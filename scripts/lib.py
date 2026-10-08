"""Shared helpers. Frontmatter is a small subset: `key: value` lines where the
value is a JSON value or a bare word. No YAML library is needed or used."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ("security-boundaries", "human-approvals", "quality-gates", "model-routing")
ROLE_NAMES = ("implementer", "reviewer", "adversarial-reviewer", "transcriber")
PACK_NAMES = (
    "python-untrusted-input",
    "gha-security-dast",
    "threat-model",
    "rf-emcomm",
    "linux-platform",
    "local-llm-routing",
)

# Suggested model tier -> Claude model alias. The most capable tier is
# deliberately absent: no role may use it, and a human must record the OK.
TIER_MODEL = {"standard": "sonnet", "cheap": "haiku"}

# Abstract capability -> host tool names. Order is the order written out.
CLAUDE_TOOLS = {
    "read": ("Read",),
    "search": ("Grep", "Glob"),
    "edit": ("Edit", "Write"),
    "shell": ("Bash",),
    "skills": ("Skill",),
}
COPILOT_TOOLS = {"read": "read", "search": "search", "edit": "edit", "shell": "execute"}
CAPABILITY_ORDER = ("read", "search", "edit", "shell", "skills")

GENERATED_DIRS = (".claude/agents", ".claude/skills", ".github/agents")


def parse_frontmatter(text, label="<text>"):
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{label}: missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError(f"{label}: unterminated frontmatter") from None
    data = {}
    for line in lines[1:end]:
        if ":" not in line:
            raise ValueError(f"{label}: bad frontmatter line")
        key, value = line.split(":", 1)
        if key in data:
            raise ValueError(f"{label}: duplicate {key}")
        value = value.strip()
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
    return data, "\n".join(lines[end + 1 :]).strip()


def frontmatter(path):
    return parse_frontmatter(Path(path).read_text(encoding="utf-8"), str(path))


def load_roles(root=ROOT):
    roles = []
    for f in sorted((Path(root) / "roles").glob("*.md")):
        meta, body = frontmatter(f)
        meta = dict(meta)
        meta["body"] = body
        meta["file"] = f.name
        roles.append(meta)
    return roles


def load_packs(root=ROOT):
    packs = []
    for f in sorted((Path(root) / "packs").glob("*/PACK.md")):
        meta, body = frontmatter(f)
        meta = dict(meta)
        meta["body"] = body
        meta["dir"] = f.parent.name
        packs.append(meta)
    return packs


def policy_text(name, root=ROOT):
    return (Path(root) / "policies" / f"{name}.md").read_text(encoding="utf-8").strip()
