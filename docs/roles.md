# Roles

Four roles, and they describe how work gets done, not what the work is about.

| Role | Tier | Does |
| --- | --- | --- |
| `implementer` | standard | Test first, commits, runs the full gate and reports the exit code, never weakens a check |
| `reviewer` | standard | Runs the gate, probes hostile inputs, reverts each claimed fix to prove its test bites, leaves a clean tree |
| `adversarial-reviewer` | standard | Assumes the change is wrong; every finding carries a reproduction |
| `transcriber` | cheap | Copies a plan's code byte for byte and reports every deviation |

The sources are [roles/implementer.md](../roles/implementer.md), [roles/reviewer.md](../roles/reviewer.md), [roles/adversarial-reviewer.md](../roles/adversarial-reviewer.md) and [roles/transcriber.md](../roles/transcriber.md). The dispatching "lead" is not a role file; see [Writing a brief](writing-a-brief.md).

## Layout and generation

```
roles/       canonical role Markdown (frontmatter: name, description, tier, tools)
packs/       one directory per pack, PACK.md inside (frontmatter: name, description, updated)
policies/    four policies, embedded in every generated agent
fragments/   text shared by several roles (report header, never-weaken list), spliced in by {{fragment:NAME}} lines
templates/   lead, brief, handoff, adr, copilot-instructions
evals/       behavioral exercises, the pilot summary, the log schema and a synthetic example log
docs/        these pages, mirrored to the wiki by scripts/gen_wiki.py
scripts/     generate.py, validate.py, install.py, overlay.py, export_prompt.py, summarize_log.py, gen_wiki.py
.claude/     GENERATED: agents and skills for Claude Code
.github/agents/  GENERATED: Copilot custom agents
```

Everything under `.claude/` and `.github/agents/` is generated from `roles/`, `packs/`, `policies/` and `fragments/` by `scripts/generate.py`. Do not edit those files; edit the source and run the generator. CI runs `generate.py --check` and fails on drift. The marker at the top of each generated file names the release it came from.

## Tiers and tools

A role's `tier` becomes an explicit model on Claude Code, pinned to an exact model id in `scripts/lib.py` rather than a floating alias (`standard` is the current Sonnet, `cheap` is the current Haiku); bumping a pin is a CHANGELOG entry. Its `tools` are capabilities (`read`, `search`, `edit`, `shell`, `skills`) that the generator maps to each host's own tool names. Copilot custom agents do not take a model from the file, so the human picks it and records it in the brief.
