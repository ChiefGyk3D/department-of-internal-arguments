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
.codex/agents/   GENERATED: Codex custom agents (TOML)
.agents/skills/  GENERATED: Codex skills (the packs)
```

Everything under `.claude/`, `.github/agents/`, `.codex/agents/` and `.agents/skills/` is generated from `roles/`, `packs/`, `policies/` and `fragments/` by `scripts/generate.py`. Do not edit those files; edit the source and run the generator. CI runs `generate.py --check` and fails on drift. The marker at the top of each generated file names the release it came from.

## Tiers and tools

A role's `tier` becomes an explicit model on Claude Code, pinned to an exact model id in `scripts/lib.py` rather than a floating alias (`standard` is the current Sonnet, `cheap` is the current Haiku); bumping a pin is a CHANGELOG entry. Its `tools` are capabilities (`read`, `search`, `edit`, `shell`, `skills`) that the generator maps to each host's own tool names. Copilot custom agents do not take a model from the file, so the human picks it and records it in the brief. Codex custom agents could take a model, but the generator does not map tiers to OpenAI models (not verifiable here): the agent inherits the parent's model and reasoning effort and the report names the model the session reports.

Codex cannot honor every capability. It has no per-tool allow list, and a custom agent inherits the parent's sandbox, so a role without `edit` (the two reviewers) is told not to edit but is not prevented; a role without `skills` (the transcriber) is told not to load packs; the rule that a role never dispatches agents is an instruction only. Each generated Codex agent states this in its own "Codex capability notes" section. Whether a read-only researcher or planner role belongs here is not decided by this adapter; the issue that waited on the Codex research input (#20) is closed, and a provider-neutral `researcher` role is planned but not built (see the design review on issue #65).
