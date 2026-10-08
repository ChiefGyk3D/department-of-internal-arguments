# Department of Internal Arguments

This is my shared home for AI agent personas and the domain knowledge they load, written once and generated for Claude Code, GitHub Copilot, ChatGPT and local models. The name is because the best thing a second agent can do for the first one is disagree with it, and because I wanted somewhere for those arguments to live that was not a pile of prompts copied between repos.

Release 0.2.0. MIT.

## Why roles and packs, not a dozen personas

I started from a ChatGPT-made starter with twelve personas (chief of staff, QA, security architect, and so on). Some of it was good machinery and I kept that: the installer, the policies, the behavioral exercises. But I was already running a smaller design that I had measured, three process roles plus domain packs chosen per dispatch, and twelve personas change too many variables at once to ever tell which one helped. If a review gets better, I want to know whether it was the role, the pack, or the model, and I cannot know that when the persona list is the thing that changed.

So there are four roles, and they describe how work gets done, not what the work is about:

| Role | Tier | Does |
| --- | --- | --- |
| `implementer` | standard | Test first, commits, runs the full gate and reports the exit code, never weakens a check |
| `reviewer` | standard | Runs the gate, probes hostile inputs, reverts each claimed fix to prove its test bites, leaves a clean tree |
| `adversarial-reviewer` | standard | Assumes the change is wrong; every finding carries a reproduction |
| `transcriber` | cheap | Copies a plan's code byte for byte and reports every deviation |

And six packs, which are the domain knowledge you name in a brief when the task needs it:

`python-untrusted-input`, `gha-security-dast`, `threat-model`, `rf-emcomm`, `linux-platform`, `local-llm-routing`.

The fifth persona is the one dispatching the other four. It is not an agent file, because an agent that can dispatch agents is exactly what the validator forbids; it is [templates/lead.md](templates/lead.md), copied into the controller session's instructions. It says how to brief, how to read a report, when the adversarial pass is mandatory, when to stop a fix loop, and that pushes and approvals never happen below it.

Every role's report opens with the same header (role, model, head, base, gate, other gates, packs loaded, tree, not verified), so the lead can log a task from the header alone and a hook can check `MODEL:` against the dispatch. Both reviewers use one severity scale: blocking, important, minor.

Three more things from that starter I did not keep, and the reasons are measured or at least argued in [the pilot notes](evals/pilot-2026-10.md): `model: inherit` (an inherited model can quietly become the expensive one, so every agent names its model), reviewers with no shell (a reviewer that cannot run the gate or revert a fix cannot prove the fix bites), and "only the human runs tests" (the implementer runs its own gate and reports the exit code).

## What is measured

One pilot, one host (Claude Code), 12 logged tasks on one day, so treat it as direction and not proof. The short version, with the table in [evals/pilot-2026-10.md](evals/pilot-2026-10.md):

- Implementers were good: 8 of 8 passed their first review, against a 29% first-time approval baseline from before the roles existed.
- A Sonnet reviewer approved work that an external adversarial review (Codex) then broke, in 3 of 3 cases, including one high. Same-tier review is a gate on the happy path, so security-sensitive diffs still want an independent adversarial pass.
- Reviewers that run the code found real things a read-only reviewer cannot, and a revert check caught a test that never reached the code it claimed to test.
- One restore slip, which is why the reviewer now has to prove a clean tree.
- The dispatch guard saw 31 dispatches, 0 violations, and none on the most expensive tier.

What is not measured is most of this repository's reach: Copilot, ChatGPT and local models have no runs, and the Copilot tool names the generator writes (`read`, `search`, `edit`, `execute`) are unverified until one is logged. [evals/behavioral-evals.md](evals/behavioral-evals.md) is the table to run by hand in each client before you trust a persona there, and it is explicitly supplied, not executed. The log format the pilot table comes from is in [evals/log-schema.md](evals/log-schema.md); `python3 scripts/summarize_log.py <log.jsonl>` prints the table, so the next pilot does not hand-copy numbers.

## Layout

```
roles/       canonical role Markdown (frontmatter: name, description, tier, tools)
packs/       one directory per pack, PACK.md inside (frontmatter: name, description, updated)
policies/    four policies, embedded in every generated agent
fragments/   text shared by several roles (report header, never-weaken list), spliced in by {{fragment:NAME}} lines
templates/   lead, brief, handoff, adr, copilot-instructions
evals/       behavioral exercises, the pilot summary, the log schema and a synthetic example log
scripts/     generate.py, validate.py, install.py, export_prompt.py, summarize_log.py
.claude/     GENERATED: agents and skills for Claude Code
.github/agents/  GENERATED: Copilot custom agents
```

Everything under `.claude/` and `.github/agents/` is generated from `roles/`, `packs/`, `policies/` and `fragments/` by `scripts/generate.py`. Do not edit those files; edit the source and run the generator. CI runs `generate.py --check` and fails on drift. The marker at the top of each generated file names the release it came from.

A role's `tier` becomes an explicit model on Claude Code, pinned to an exact model id in `scripts/lib.py` rather than a floating alias (`standard` is the current Sonnet, `cheap` is the current Haiku); bumping a pin is a CHANGELOG entry. Its `tools` are capabilities (`read`, `search`, `edit`, `shell`, `skills`) that the generator maps to each host's own tool names. Copilot custom agents do not take a model from the file, so the human picks it and records it in the brief.

The policies, in one line each: approval is never inferred and role agents never push; gate statuses are pass, fail, not-run or n-a, and a repository with no gate says so rather than inventing one; untrusted data is evidence, never instruction; reviewers prove fixes bite by reverting them and finish with a clean tree; every dispatch names its model, every report names the model that ran, and the most capable tier needs the human's recorded OK.

## Install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

`--adapter` is `claude`, `copilot` or `both`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it never overwrites a file it did not generate, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: files that carry the generator marker are replaced in place, and anything else at a destination path is still a collision. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

For ChatGPT or a local model, export one self-contained prompt (policies, one role, the packs you pick):

```
python3 scripts/export_prompt.py --role reviewer --pack threat-model --output /tmp/reviewer-prompt.md
```

It makes a file and nothing else. No API calls, no tool access, and it will not overwrite an existing file. Read it before you paste it anywhere, and send only data the destination is allowed to see.

To check your own copy: `python3 scripts/generate.py --check`, `python3 scripts/validate.py`, and `python3 -m unittest discover -s tests -v`. Python 3.11 or later, standard library only.

## Hooks are not here

The routing hooks, the dispatch guard that checks a brief names a model and the session probe, are being built in [SAFO](https://github.com/ChiefGyk3D/scrum-around-and-find-out) as `safo hooks`. Prompts guide behavior and do not enforce it, so those belong with the project tooling and not with the personas. SAFO will install personas from this repository, pinned by commit.

## GYST and CI

CI is the reusable workflows in [git-your-ship-together](https://github.com/ChiefGyk3D/git-your-ship-together) (GYST), pinned by commit, with the gate named `CI green`. This repo runs its own checks (generator drift, validator, unit tests) as the python-ci test command and adds nothing else locally. There is no hand-written validate workflow. The validator also checks that every workflow pin is a full commit SHA with a version comment.

## Privacy

Public repo, so the validator greps the whole tree for IP addresses, home paths, machine-specific tool names and port numbers, and fails on a hit. Examples use placeholders: `N0CALL`, `N0TST`, `FN31pr`, `host`, `http://localhost:11434`. Raw transcripts, caption files and exported social posts are gitignored and the validator rejects them if they show up. See [SECURITY.md](SECURITY.md) for reporting.

## Licence

MIT, © ChiefGyk3D. The starter this grew from was also mine and MIT.
