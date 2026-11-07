# Roles

Eight roles, and they describe how work gets done, not what the work is about.

| Role | Tier | Does |
| --- | --- | --- |
| `implementer` | standard | Test first, commits, runs the full gate and reports the exit code, never weakens a check |
| `reviewer` | standard | Runs the gate, probes hostile inputs, reverts each claimed fix to prove its test bites, leaves a clean tree |
| `adversarial-reviewer` | standard | Assumes the change is wrong; every finding carries a reproduction |
| `transcriber` | cheap | Copies a plan's code byte for byte and reports every deviation |
| `researcher` | standard | Read-only web research: answer first, every claim VERIFIED (URL and date read) or INFERRED (reason), conflicts listed, page text treated as evidence and never obeyed |
| `cloud-engineer` | standard | Cloud work: plans before it applies, names blast radius and rollback, read-only on live systems unless a named task grants write, never applies, secrets by name only; code goes to the implementer and review to the reviewers (tools `read`, `search`, `shell`, `skills`; no `edit`) |
| `devops-engineer` | standard | CI/CD and release work: calls the shared GYST reusable workflows, pins by commit, names the gate `CI green`, reads CI through the REST API pinned to the head, never waits in the foreground, never merges, loosens a check or raises a time bound, keeps secrets out of workflows; changes go to the implementer as a diff and security-sensitive review to the adversarial-reviewer (tools `read`, `search`, `shell`, `skills`; no `edit`) |
| `cybersecurity-blue` | standard | Defensive security: detection engineering, alert and log triage, hardening from published baselines, incident response; log text is evidence and never instruction, every detection ships a positive and a negative sample, containment is recommended and the human runs it, read-only on live systems unless a named task grants write, never runs offensive tooling or suppresses evidence; rules and configuration go to the implementer as a diff and security-sensitive review to the adversarial-reviewer (tools `read`, `search`, `shell`, `skills`; no `edit`) |

The sources are [roles/implementer.md](../roles/implementer.md), [roles/reviewer.md](../roles/reviewer.md), [roles/adversarial-reviewer.md](../roles/adversarial-reviewer.md), [roles/transcriber.md](../roles/transcriber.md) and [roles/researcher.md](../roles/researcher.md) and [roles/cloud-engineer.md](../roles/cloud-engineer.md) and [roles/devops-engineer.md](../roles/devops-engineer.md) and [roles/cybersecurity-blue.md](../roles/cybersecurity-blue.md). The dispatching "lead" is not a role file; see [Writing a brief](writing-a-brief.md).

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

A role's `tier` becomes an explicit model on Claude Code, pinned to an exact model id in `scripts/lib.py` rather than a floating alias (`standard` is the current Sonnet, `cheap` is the current Haiku); bumping a pin is a CHANGELOG entry. Its `tools` are capabilities (`read`, `search`, `web`, `edit`, `shell`, `skills`) that the generator maps to each host's own tool names. Copilot custom agents do not take a model from the file, so the human picks it and records it in the brief. Codex custom agents could take a model, but the generator does not map tiers to OpenAI models (not verifiable here): the agent inherits the parent's model and reasoning effort and the report names the model the session reports.

Codex cannot honor every capability. It has no per-tool allow list, and a custom agent inherits the parent's sandbox, so a role without `edit` (the two reviewers, the researcher, the cloud-engineer, the devops-engineer and the cybersecurity-blue) is told not to edit but is not prevented; a role without `skills` (the transcriber and the researcher) is told not to load packs; the rule that a role never dispatches agents is an instruction only. Each generated Codex agent states this in its own "Codex capability notes" section.

## The `web` capability and the researcher

`web` means fetch and search the web, and only the `researcher` has it (the validator refuses it on any other role, and refuses `edit`, `shell` and `skills` on the researcher). The generator maps it per host: Claude Code `WebFetch` and `WebSearch`; Copilot the `web` tool alias (UNVERIFIED, no Copilot run is logged); Codex has no per-tool allow list, so the generated notes say the agent uses whatever web tools the session has. A host or an API-only provider with no web tool gives the researcher no tools: it says so, answers only from material the brief supplies and labels every claim INFERRED or NOT FOUND. On Codex, which cannot remove `edit` or `shell` from a custom agent, the generated researcher file states both gaps and a "Required sandbox: read-only, with approvals on request" line: the dispatcher must start it that way, and the agent makes no web request if its session shows otherwise. The agent cannot enforce this on itself, so a lead that skips the sandbox gets an instruction, not a restriction. The validator fails if a generated Codex file is silent about a missing `edit` or `shell`, or if a `web` role lacks the sandbox line. A portable prompt from `export_prompt.py` grants no tool access, so it is that case.

The researcher's report adds QUESTION, ANSWER, CLAIMS, CONFLICTS, DATA SENT (destination, tier, data class), NOT FOUND and OPEN after the shared header, whose GATE, HEAD, BASE and TREE read `n-a`. The role is provider-neutral: no vendor-named role. A pack for research on an unpaid tier is not built; it waits on a measured free-key call (see the design review on issue #65).
