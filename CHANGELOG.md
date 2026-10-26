# Changelog

## [Unreleased]

Local-model output is a draft, and the report says whether and how it was checked (issue #21). The measured findings are about gemma4:12b (12B) on 2026-10-26 only; nothing here claims anything about local models in general.

- `local-llm-routing` pack: new section "Local output is a draft: verify it against the source". Local output is never applied or quoted unverified; four check shapes (echo-and-compare identifiers through an Ollama `format` schema, quotes found verbatim in the source, identifiers in the prose present in the source, instructions in `system` and text in `user`). The evidence is stated with model, size and date: gemma4:12b, 2026-10-26, 2 of 5 triage runs returned unrelated text, attribution inferred because the runs did not log the model, cause untested. The 4096 default context on GPUs under 24 GiB and the rule to always send `num_ctx` are stated. New verification commands: read the answering model from the response, compare `prompt_eval_count` with the input size.
- `fragments/report-header.md`: a required `LOCAL-LLM: used - <model, size>; VERIFIED-AGAINST: <source>; DISCREPANCIES: <n> | n/a - reason` line. All four roles carry it through the fragment; `scripts/validate.py` requires `LOCAL-LLM:`, `VERIFIED-AGAINST:` and `DISCREPANCIES:` in every role report and in `templates/copilot-instructions.md`, which gains a "Local-model output is a draft" section.
- Pilot log: optional `local_model` and `local_discrepancies` fields (both or neither); `scripts/summarize_log.py` validates them and prints a "Local-model runs" row; the synthetic example log gains a local-model fixture.
- `evals/behavioral-evals.md`: rows 24 and 25 for the new line.
- A SAFO hook or audit check for the line is a separate SAFO issue.

## [0.3.1] - 2026-10-11

- `local-llm-routing` pack: upstreamed generic measured knowledge from a maintainer's private copy. `keep_alive` does not stop a context-change eviction; the 12B-plus-second-model fit measurement (8192 yes, 16384 no, Ollama 0.33.2); the `using device` journal grep and the 100% GPU check for multi-GPU placement; stale-probe and brief-versus-call guard notes; the Codex-over-Ollama command shape (still UNVERIFIED). A comparison of the `linux-platform` and `rf-emcomm` private copies found nothing to add.

## [0.3.0] - 2026-10-11

Install into a user-level tree (a dotfiles checkout) and keep private personas out of the public repo.

- `scripts/install.py --layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md`; the default `--layout project` output is unchanged byte for byte. Claude only: `--adapter copilot|both` with the user layout is refused. A target inside this repository is now refused (it used to refuse only the repository root).
- `--overlay DIR`: a private directory (`roles/`, `packs/`) outside this repository. A new name is installed as a whole persona or pack; a name that is also public is append-only (below); marker records `overlay` and the file name, never a path. Read with confinement (no symlinks, regular files, size and entry caps, owner and mode checks) by the new `scripts/overlay.py`, validated by the same role and pack rules, and exempt from the public-repo privacy sweep on purpose. Nothing can be written inside this repository.
- `--list-managed`: read-only report of marker files under a target with release, source and status (current, stale, differs, orphan, unchecked); exit 3 on drift.
- `scripts/validate.py` exposes `role_errors` and `pack_errors` so the overlay uses the public rules; `scripts/generate.py` render functions take an optional marker source; `ensure_safe` moved to `scripts/lib.py`.
- The generated marker now carries `sha256:` of the file minus the marker line (a format change: every generated file's marker line differs from 0.2.0, nothing else does). Ownership is content-bound: `--update` replaces a file only when the marker is well-formed and the body matches its hash; otherwise it is `edited` and refused. New `--force-edited` (with `--update`) discards local edits. The preview labels `new`, `update`, `unchanged`, `edited`. Release 0.2.0 files are legacy-owned only when their body is one of the 14 known 0.2.0 outputs, else `legacy-unverified`. `--list-managed` also reports `edited` and `legacy-unverified`.
- Append-only overlay for public names: an overlay `roles/<public>.md` or `packs/<public>/PACK.md` may carry only `name:` in its frontmatter and its body is appended, unchanged public text first, under a generator-written `## Maintainer additions (private overlay)` section (size cap, no marker text or fragment markers, no level 1-2 or setext headings, no heading repeating a public section name). The earlier override rules (`override_errors`) are gone. Prose in the additions can still conflict with the public rules; the README says so. Frontmatter nesting is capped (a depth error, not "empty description"). A group-writable overlay file is refused with the exact `chmod -R g-w,o-w` remedy.
- File modes: public files are created `0644` (before: `0666` masked by the umask, so `0664` under umask 002) and updates no longer widen permissions (temp file `0600`, then the destination's mode is applied before the rename); overlay-sourced files are created or narrowed to `0600`.
- Additions may not contain raw HTML (`<` before a letter, `/`, `!`, `?`; autolinks included) or an encoded `#` (`&#35;`, `&#x23;`, `&num;`).
- Installer writes are race-safe: every directory component is opened `O_DIRECTORY|O_NOFOLLOW` relative to the previous descriptor, and the existing-file read, temp file (`O_EXCL|O_NOFOLLOW`) and rename are done relative to those descriptors, with the destination's device and inode re-checked, so a parent or file swapped for a symlink after the hash check cannot redirect a write; the run fails.
- README: "Keeping a dotfiles repo current", including why symlinks must not be used.

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
