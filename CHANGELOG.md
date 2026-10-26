# Changelog

## [Unreleased]

Benchmark corpus and scoring rubric (issue #47, unit 1 of #3). No model is run and nothing is spent.

- `evals/benchmark/corpus.json`: seven tasks and 19 labelled findings from this repository's own history (the Codex adapter #30, the local-model rule #26 and its follow-ups #28 and #34, the lead persona installer #35), each with a base commit, the defect-introducing head, the fixing commit where there is one, the known finding by class and its expected evidence. A brief-writing task is its own role column (`brief-writer`), scored on the gaps the reviews of #35 found. Every finding was shown to reproduce against the history by a probe or a pattern check; five candidate seeds were dropped and are recorded with the reason. Private seeds are not included.
- `scripts/benchmark.py`: `--check` (corpus well formed; commits and diffs resolve with local tools only, `not-run` without a full clone), `--reproduce` (re-proves each seed), `--score RESULTS.jsonl` (recall, precision, false findings, fix rounds avoided, wall clock, tokens, refusals per role and model; rejects a family name for an exact model id and a missing client version or cost). Read-only; never touches `policies/model-routing.md`. `scripts/validate.py` checks the corpus structure.
- `evals/benchmark-schema.md`, a section in `docs/evals-and-measurements.md`, `tests/test_benchmark.py`.

Round 2 of the review of the benchmark corpus (issue #47):

- Matching is a deterministic maximum bipartite assignment (augmenting paths, canonical report order), so the score no longer depends on the order of the report when evidence patterns overlap (the rubric text said it did not; the greedy version did).
- The evidence match is stated as a keyword heuristic that cannot detect gaming, and is tightened: patterns carry word boundaries, at least two distinct patterns must hit (`evidence_min` lowers it per finding), and on a diff task the evidence must contain a verbatim code line from the head checkout (`quotes`, verified by `--check`). A test builds a report from the label alone and asserts it scores zero recall on every diff task.
- `diff_sha256` no longer depends on the user's git configuration: every output-changing diff option is pinned on the command line and in `-c`, global and system configuration are not read, and `GIT_*` variables of the caller are dropped. The existing digests are unchanged. A mismatch message names both causes (rewritten history, or a different git version or configuration).
- Every finding has a `scope` (`in_diff`, `pre_existing`, `brief`); `--check` verifies an `in_diff` finding touches a line of the diff and a finding absent at the base does not have its quote on an added line; the schema says the reviewer gets a checkout at `head` plus the diff; the table reports recall per scope.
- The test-gap findings are reproduced by the real mutation (a change applied to a copy of the head, unit tests run, expected to stay green); `lead-lost-update` has a probe that races an in-place edit; the one absence-only repro is marked `proxy`.
- `exact_model_id` rejects `:latest` tags and a bare vendor and major (`claude-3`, `gpt-5`); the `lead_list` probe fails on a crash instead of confirming a finding.
- Result records: calendar-valid dates, a 40-character commit or a resolvable abbreviation, bounded tokens, seconds, cost, context and run, no control or invisible characters in text fields, a duplicate JSON key is an error, and client versions are separate rows.
- `tarfile` extraction uses the `data` filter where Python has it (3.11.4 and later) and an equivalent member check on earlier 3.11 patch releases.

## [0.4.0] - 2026-10-26

The documentation is split into pages under `docs/` and published as the project wiki (issues #18 and #19): ten pages, `README.md` trimmed to a landing page, `scripts/gen_wiki.py` (with `--check`) and a `wiki.yml` caller of the shared wiki-publish workflow. `scripts/validate.py` requires `docs/home.md`. The wiki itself is enabled by the maintainer after merge.

The lead persona is installable and carries the orchestrator rules (issues #32 and #33).

- `templates/lead.md`: new rules, kept short (the installed text is about 6 KB, up from 4.3 KB): "You do not wait" (waits go to a background task or a monitor agent, one watch per thing); two dispatch items (the tracking item on an `issue:` line with its card moved in the same response, and a `cd` into the issue's repository in the same command as a worktree dispatch); "Before you tell the human to do something" (project notes, then the vendor page, then the live read-only API; verified versus inferred); "Access" (ask once for the union, no broader fallback); "Done" (observed through the path the user will use); and three never-do lines (an approval token held for the human, an unfixed weakness in a public place, a rejected option). The long form with the reasons is in `docs/writing-a-brief.md`.
- `scripts/install.py --lead FILE`: installs the lead text as a marked block (begin line with release and sha256, end line) in an instruction file under the target. Creates the file, or appends and leaves the rest alone (the writer now re-reads the file just before the rename and refuses, writing nothing, if its bytes changed since the new content was built); idempotent; `--update` replaces a block whose hash matches; an edited block is refused (`--force-edited` discards only the block); malformed markers, symlinks, non-UTF-8 files and paths outside the target are refused. `--adapter none --lead FILE` installs only the lead block (without it the role agents are planned too, and the preview says so). `--list-managed --lead FILE` always reports a row for the named file: `current`, `stale`, `differs`, `edited`, `missing` (no file, or no block) or `unsafe` (symlink, not a regular file, over 1 MiB, not UTF-8); anything but `current` exits 3. The block hash ignores CRLF and a missing final newline, and a CRLF file gets a CRLF block. `tests/test_lead.py` covers each, with the rules guard and its negative case.
- `docs/install.md`: "The lead persona" section: how to load it, how to check the session loaded it (ask for a rule), and which hosts' loading is UNVERIFIED (Codex, Copilot, ChatGPT, local models; Claude Code observed only).

Local-model output is a draft, and the report says whether and how it was checked (issue #21). The measured findings are about gemma4:12b (12B) on 2026-10-26 only; nothing here claims anything about local models in general.

- `local-llm-routing` pack: new section "Local output is a draft: verify it against the source". Local output is never applied or quoted unverified; four check shapes (echo-and-compare identifiers through an Ollama `format` schema, quotes found verbatim in the source, identifiers in the prose present in the source, instructions in `system` and text in `user`). The evidence is stated with model, size and date: gemma4:12b, 2026-10-26, 2 of 5 triage runs returned unrelated text, attribution inferred because the runs did not log the model, cause untested. The 4096 default context on GPUs under 24 GiB and the rule to always send `num_ctx` are stated. New verification commands: read the answering model from the response, compare `prompt_eval_count` with the input size.
- `fragments/report-header.md`: a required `LOCAL-LLM: used - <model, size>; VERIFIED-AGAINST: <source>; DISCREPANCIES: <n> | n/a - reason` line. All four roles carry it through the fragment; `scripts/validate.py` requires `LOCAL-LLM:`, `VERIFIED-AGAINST:` and `DISCREPANCIES:` in every role report and in `templates/copilot-instructions.md`, which gains a "Local-model output is a draft" section.
- Pilot log: optional `local_model` and `local_discrepancies` fields (both or neither); `scripts/summarize_log.py` validates them and prints a "Local-model runs" row; the synthetic example log gains a local-model fixture.
- `evals/behavioral-evals.md`: rows 24 and 25 for the new line.
- A SAFO hook or audit check for the line is a separate SAFO issue.
- Review follow-ups (issue #27). The pack-scope test now asserts the scope phrase on the same line as each measured figure (85%, 46%, 55 to 215 seconds, 436 evaluation tokens, the 8192 context), with a negative case. `local_model` accepts real Ollama tags (`gemma3n:e4b`, `mixtral:8x7b`, `llama3.2:latest`, `hf.co/org/m:7b`, `gemma3:270m`) and still requires a size or an explicit `latest`; one-character names, bare family names, lookalike and zero-width characters are refused (rule in `evals/log-schema.md`). The American-spelling guard now also scans comments and docstrings in `scripts/` and `tests/` (identifiers, including the pinned summarizer function name, are not scanned); three British words fixed.

Codex adapter (issue #22). Everything about Codex below comes from OpenAI's Codex documentation, retrieved 2026-10-26, and has not been run in Codex.

- `scripts/generate.py` now also writes `.codex/agents/<role>.toml` (a Codex custom agent per role: `name`, `description`, `developer_instructions`, no model, sandbox or approval key) and `.agents/skills/pack-<name>/SKILL.md` (the packs, byte-identical to the Claude Code skills). The instructions are the role text, the shared report header with the `LOCAL-LLM:` line, the four policies and a generated "Codex capability notes" section that says what Codex cannot honor (no per-tool allow list, sandbox and approvals inherited from the parent so a role without edit is only told not to edit, no way to forbid dispatch). The ownership marker is the first line of `developer_instructions`; the sha256 rule is unchanged.
- `scripts/install.py --adapter codex`, with `--update`, `--force-edited`, `--overlay` (append-only for public names), `--list-managed`, symlink and collision refusal as for the other adapters. `--adapter both` keeps its meaning. `--layout user` is accepted for Codex with the home directory as the target (Codex keeps user agents and skills under two roots).
- `scripts/validate.py` parses each Codex agent with `tomllib`, requires exactly the three keys and checks the release in every generated Codex file.
- `evals/behavioral-evals.md` row 26 for a hand run in Codex (not executed). `docs/install.md` records what was verified, with the page URLs, and what is UNVERIFIED.
- Round 2 (external adversarial review of the Codex adapter):
  - `install.py` refuses a target that is, or resolves to, the filesystem root (for every adapter and layout; `..` is resolved, symlinks were already refused).
  - A role, fragment, policy or overlay text with a control character other than tab and newline can no longer produce an invalid Codex TOML file: `render_codex_agent` parses what it rendered and checks the exact three-key schema before returning, and raises `CodexTomlError` naming the source.
  - The install preview prints the source (`public` or `overlay`) and the sha256 of each file it would write.
  - Documentation states the trust model without overstating the code: an overlay is fully trusted policy authority and the append-only rules do not stop a hostile overlay; packs are model instructions and the preview is the review control; files that self-identify as generated may be overwritten by `--update` (the hash is not a signature; a state-file design is a separate issue). The earlier claim that the installer leaves alone any file it did not generate is withdrawn.
  - New isolated validator tests prove the TOML parse and the schema check each turn red when removed.
- Round 4 (second external adversarial pass):
  - A target is also refused as the filesystem root when its `(st_dev, st_ino)` equals that of `/`, which catches a bind mount of `/` under another name. The test mocks `os.stat`; a real bind-mount test needs privileges and was not run.
  - Every path and message the installer prints (preview, refusals, `--list-managed`) goes through one escaper: printable ASCII passes, everything else (control characters, ESC, CSI, CR, LF, bidi and zero-width characters, line separators) and the backslash print as visible escapes such as `\x1b` and `\u202e`. A hostile filename can no longer forge an extra preview record. Non-ASCII file names now print escaped instead of as `?`.
- Round 5: `install.py` argument errors (unknown option or positional, bad choice, missing value) now go through the same escaper as the rest of its output (a small `argparse.ArgumentParser` subclass overriding `error()`); the usage text and exit code 2 are argparse's own.
- Minor residuals (issue #29). The per-figure scope test is wrap-tolerant (a bullet is tested as one logical line, so a hard-wrapped scope phrase passes and a figure whose scope sits in another bullet still fails). `local_model` also accepts a plain version tag (`nomic-embed-text:v1.5`) and now refuses `..` and `.` path segments and leading, trailing or doubled hyphens; `gemma:4b` stays accepted because only a registry can say whether a size exists. The dead `//` check is gone and the spelling-guard bite test covers `async def` docstrings. `install.py` escapes the program name (from `sys.argv[0]`) in the argparse error prefix, usage line and `-h`; the doubled backslash on a lone surrogate is documented as accepted.
- Not done here: an `AGENTS.md` (the project's own file; a merged one would not fit the 32 KiB default), and a read-only researcher or planner role (waits for issue #20).

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
