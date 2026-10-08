# Changelog

## [0.2.0] - 2026-10-08

The lead gets a persona, reports get a shared header, and installed copies can be upgraded.

- `templates/lead.md`: the controller's job (briefing, reading reports, the review loop, logging, what it never does). It was implied by every role and written nowhere.
- Every role report opens with one shared header (`fragments/report-header.md`): role, model, head, base, gate, other gates, packs loaded, tree, not verified. The adversarial reviewer now reports the clean-tree proof and the packs it loaded, which its rules already required. Both reviewers use one severity scale (blocking, important, minor).
- `MODEL:` is a required report line. Naming a model in a brief was never proof it ran.
- Role agents never push, merge, tag or release, and a brief cannot grant it; the human-approvals policy says so and the implementer and transcriber no longer carry "unless the brief says so".
- The implementer's round limit now has its input: the brief names the round and the findings it addresses, and the report echoes them.
- Both reviewers diff dependency manifests between base and head before installing anything, since installing from the head runs the code under review.
- A repository with no defined gate is a stated case (`not-run - none defined`, propose one) instead of an invitation to invent one.
- The adversarial reviewer keeps nine domain-neutral attack classes; the HTTP, parser, workflow and scanner specifics live only in the packs that already carried them.
- Tier models are pinned ids, not aliases, in `scripts/lib.py`; bumping one is a CHANGELOG entry.
- `fragments/`: the never-weaken list has one source, spliced into the editing roles; the validator checks the Copilot template carries every token.
- Packs carry `updated:` in frontmatter and every pack has a `## How to verify` section (threat-model gained one; rf-emcomm and linux-platform renamed theirs and gained grep lines).
- `scripts/install.py --update` replaces files that carry the generator marker and still refuses anything else; the marker now carries the release.
- `scripts/summarize_log.py` and `evals/log-schema.md`: the pilot table is reproducible from a JSON-line log; `evals/example-log.jsonl` is a synthetic fixture.
- Behavioral exercises 15 to 23 cover the adversarial reviewer, the three packs that had none, the no-push rule, the header and the no-gate case.
- `CLAUDE.md` for working on this repository. Frontmatter keys are stripped. The privacy sweep is word-bounded so a commit SHA cannot trip it. The workflow pin test reads the current pin instead of hardcoding it.
- Still unverified: the Copilot tool names in `scripts/lib.py` (no Copilot run has been logged).

## [0.1.0] - 2026-10-08

First release.

- Four roles (implementer, reviewer, adversarial-reviewer, transcriber) as canonical Markdown with a suggested model tier, tools and description.
- Six knowledge packs: python-untrusted-input, gha-security-dast, threat-model, rf-emcomm, linux-platform, local-llm-routing.
- Four policies: security-boundaries, human-approvals, quality-gates, model-routing.
- `scripts/generate.py` builds the Claude Code agents and skills, the Copilot custom agents, and feeds the prompt exporter. `--check` fails on drift.
- `scripts/install.py`: additive, collision and symlink safe project installer.
- `scripts/export_prompt.py`: standalone prompt for ChatGPT and local models.
- Evals: the behavioral exercises table and a summary of the 2026-10 pilot.
- CI through git-your-ship-together v1.16.0.
