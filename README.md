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
scripts/     generate.py, validate.py, install.py, overlay.py, export_prompt.py, summarize_log.py
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

`--adapter` is `claude`, `copilot` or `both`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it never overwrites a file it did not generate, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: files whose marker is well-formed and whose body still matches the hash in the marker are replaced in place (see "Ownership" below), and anything else at a destination path is still a collision. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

## Keeping a dotfiles repo current

The project install writes `<project>/.claude/...`. A user-level Claude Code tree (the one a dotfiles repo keeps) has no `.claude` level, so `--layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md` instead. It is still additive, still refuses symlinked destinations and whole-install collisions, still previews by default, and `--update` still replaces only files it can prove it generated (see "Ownership"). It is Claude only (Copilot has no user layout, and asking for it is refused), and a target inside this repository is refused.

The routine, from a checkout pinned to a release tag:

```
git -C /path/to/department-of-internal-arguments fetch --tags
git -C /path/to/department-of-internal-arguments checkout <tag>   # <tag> is a placeholder for the release tag you pin; the current release is 0.2.0, and a tag is only valid once cut
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update --apply
git -C ~/dotfiles/claude status   # review the diff, then commit
```

The first install run is the preview and writes nothing. To see drift without writing, `--list-managed` prints every file under the target that carries the marker, its release, its source (`public` or `overlay`) and whether it is `current`, `stale` (older release), `differs` (this release, content changed), `orphan` (this checkout no longer produces it) or `unchecked` (overlay-sourced and no `--overlay` was given). It exits 0 when everything is current and 3 otherwise, so a sync step can report drift and decide. When overlay-sourced files exist and `--overlay` was not given, they cannot be checked: they show as `unchecked` and the exit code is 3, so give `--overlay` to the check as well. An `edited` file is one whose marker is present but whose body no longer matches it (or whose marker is malformed); `legacy-unverified` is a release 0.2.0 file (see below) with a body 0.2.0 never produced.

**Never symlink the installed files into a dotfiles repo.** Git stores the link, not the content; every other machine that pulls gets a dangling link to a path that does not exist there; and a secret scan over the repo sees a link target string, never the persona text. The backup is only a backup if the repo holds real files, which is what this installer writes.

### Ownership: the marker alone proves nothing

The marker line carries a sha256 of the file with that one line removed. `--update` treats a file as installer-owned only when the marker is well-formed (exactly one, strictly parsed) and the body still hashes to the value in it. A hand-edited file, or one that merely contains the marker text, is `edited` and refused; the whole install stops before writing anything. The preview labels every file `new`, `update`, `unchanged` or `edited (refused; --force-edited discards your changes)`. `--update --force-edited` replaces edited files and discards your local changes (it never touches a file with no marker at all, and never a symlink). Editing only the release in the marker does not count as an edit, so a stale copy still updates. The hash binds content to the marker; it is not a signature, so someone who can write the file can write a matching hash. Review the preview and the diff in the dotfiles repo, which is the real control.

This marker format is new in the next release. Files written by release 0.2.0 have no hash. They are recognised as legacy-owned only when their body is byte for byte one of the 14 outputs 0.2.0 generated (the installer carries those 14 hashes); a hashless marker on any other body is `legacy (unverified)` and needs `--force-edited`.

### Overlay: personas too sensitive for this public repo

`--overlay DIR` points at a private directory with the same shape as the sources here: `roles/<name>.md` and `packs/<name>/PACK.md`, read by the same parsers as the public ones. A **new name** is installed as a whole persona or pack and is held to the same structural rules as a public one (tier, tools, report header, packs need a `## How to verify` section); both kinds carry the generator marker, which for overlay files says `overlay` and the file's own name and never a path.

```
python3 scripts/install.py --target ~/dotfiles/claude --layout user --overlay ~/private-personas --update --apply
```

The overlay is private by definition, so the public-repo privacy sweep is the one rule that is not applied to it. It lives outside this repository and cannot be written into it: an overlay or a target inside this repository is refused, and so is any destination that resolves inside it. The overlay is read with the same confinement as everything else (no symlinks on the path, regular files only, size and entry-count caps, owned by you, not group- or world-writable) and anything wrong refuses the whole install before a byte is written. Under a umask of 0002 files are created group-writable (0664) and are refused; the error names the fix, `chmod -R g-w,o-w DIR`, and you run it once on the overlay directory. Edit capability stays limited to the `implementer` and `transcriber` names, as in the public tree. The maintainer may later move those private personas to their own private repository; the flag takes any directory, so nothing here changes when that happens.

**A name that is also public is append-only.** An overlay `roles/reviewer.md` (or `packs/threat-model/PACK.md`) does not replace the public file. Its frontmatter may hold only `name:` (tools, tier, model and description stay the public ones; any other key is a validation error), and its body is installed as additions: the public generated file, unchanged and first, followed by a section the generator writes, `## Maintainer additions (private overlay)`, containing your text. The public part is byte for byte what the public install would write, so a comment or code fence in your additions cannot remove a public rule. The additions are held to a few rules: at most 16 KiB, no generator marker text, no fragment markers, no heading of level 1 or 2 and no underlined headings, and no heading that repeats a public section name (`## Hard rules`, `## Process`, `### Security boundaries` and so on) or the additions heading. **What remains:** prose in the additions can still ask for something at odds with the public rules. The public rules come first and the section is labelled as an extension, but a model may weigh both, so read the overlay before installing it. For stronger separation use a distinct persona name, which is then a whole persona held to the usual rules.

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
