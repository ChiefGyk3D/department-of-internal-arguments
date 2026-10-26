# Benchmark corpus, rubric and result records

The benchmark (issue #3) asks which model does which role well enough to route work to it. This page is the format for unit 1 (issue #47): the corpus of tasks, the scoring rubric and the result records. No model has been run against it. `python3 scripts/benchmark.py` checks the corpus and prints the table; it never writes a file, and it never changes `policies/model-routing.md`. The routing table changes only in a pull request that cites the numbers.

## The corpus: `evals/benchmark/corpus.json`

Seven tasks, 19 labelled findings, all drawn from this repository's own history, where the later findings are public. Every finding was shown to reproduce (below); five candidate seeds were dropped, each recorded with its reason in the `dropped` list, not adjusted to fit.

### What the model is given

A diff task gives the reviewer a checkout of the repository at the `head` commit and the diff `git diff base head`. The record's `tools_enabled` says how far it may look around beyond that (read, search, run). The diff alone is not enough for every finding, and the corpus says which: each finding has a `scope`.

- `in_diff`: some added or removed line of the diff matches `touched`, so a reviewer who reads only the diff can see the code. `--check` verifies the line is really in the diff. The defect may still have been observable at `base` (the diff rewrote the code and kept the behavior); `at_base` says so.
- `pre_existing`: no quote of the finding is a line the diff adds: the defect sits in code the diff never touches (the diff only exercises it). A reviewer who reads only the diff cannot be expected to find these, and a reviewer who finds them has looked at the checkout. `--check` verifies, for every `pre_existing` finding, that none of its quotes is an added line, whatever `at_base` says. 6 of the 16 diff findings are `pre_existing`, 10 are `in_diff`.
- `brief`: a finding of the brief-writing task (below).

The scorer reports recall for each scope separately, so a model's score on the diff is not mixed with its score on the surrounding code.

### Tasks and findings

A task is one unit of work for one or more roles:

| Field | Meaning |
| --- | --- |
| `id`, `title` | A slug and a one-line description |
| `roles` | `reviewer` and `adversarial-reviewer` for a diff task; `brief-writer` for a brief task (the planning column) |
| `kind` | `diff`: the model is handed the checkout at `head` and `git diff base head`. `brief`: the model is handed the recorded input file and writes the implementer brief |
| `base`, `head`, `diff_sha256` | Diff tasks: full commit ids and the sha256 of the diff in a fixed form (see "The digest"), so a moved or rewritten commit is caught |
| `input_file`, `input_sha256` | Brief tasks: the recorded problem statement under `evals/benchmark/inputs/` |
| `findings` | The known findings a later review produced |

A finding:

| Field | Meaning |
| --- | --- |
| `id`, `class`, `severity`, `summary`, `source` | The label. `class` is one of `input-validation`, `ownership-trust`, `filesystem-safety`, `output-injection`, `concurrency`, `error-reporting`, `test-gap`, `dead-code`, `doc-accuracy`, `convention`, `brief-gap`. `source` says which public review or issue produced it |
| `round` | The order of the fix round in the real history that fixed it (an unfixed finding takes the next number). Findings in one round were fixed together |
| `scope` | `in_diff`, `pre_existing` or `brief` (above) |
| `paths` | The files a correct report points at (`brief` for a brief task) |
| `evidence_any` | Regular expressions. A correct report's evidence must match at least `evidence_min` distinct ones (default 2; a finding may lower it to 1 only where a single phrase is genuinely specific) |
| `quotes` | Diff findings: short code lines, verbatim from the checkout at `head`, that the report's evidence must contain (whitespace is normalized). `--check` verifies every quote is in the head code |
| `touched` | `in_diff` findings: patterns, one of which must match an added or removed line of the diff |
| `fixed` | The commit that fixed it, or `null` |
| `at_base` | What the reproduction shows at the base commit: `observed`; `not-observed`; or `n/a`, meaning the reproduction cannot be run there because the code or feature it exercises did not exist at the base (a new adapter, a new option). It says nothing about whether the finding is in the diff; `scope` does |
| `repro` | How it reproduces: a `probe` (a script in `evals/benchmark/probes/`), a `pattern` (a regular expression over a file at a commit, present or absent) or a `mutation` (see below) |
| `proxy` | Required on every repro that only checks that something is absent, and nowhere else: what the check stands in for |
| `absent_from_input` | Brief findings only: patterns that must NOT match the recorded input, which proves the original brief did not already cover the gap |

### What "reproduces" means

`python3 scripts/benchmark.py --reproduce` (needs a full clone that has the commits; the pull-request heads come from `git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'`). For each finding it checks, in a temporary directory, that the defect is observed at `head`, matches `at_base` at `base`, and is gone at `fixed`. A brief finding reproduces through the diff finding it references and must also be absent from the recorded input. A seed that fails any step is dropped from the corpus and listed under `dropped`.

- A `probe` prints `OBSERVED` or `NOT-OBSERVED` on its last line and exits 0; a crash is a failure, not a pass.
- A `mutation` states the real change: `old` is replaced by `new` in `path` (exactly one occurrence) in a copy of the checkout, and the unit tests named by `tests_file` (and an optional `-k` filter) are run. The defect is that the tests pass both before and after the change, a survivor. If the code to change is gone, the defect is gone. Tests that are red on the unmutated tree are an error.
- A `pattern` with `present: false` is a proxy for a missing thing and says so in `proxy`. One finding (`scope-test-line-based`) is a proxy: its stated behavior is a false fail, which no mutation of the head shows.

**`--reproduce` executes pinned history UNSANDBOXED, as the invoking user.** The probes run code from this repository's own history with `python3 -I`, and the mutation repros run the unit tests of a mutated copy, in a scratch directory. There is no network block, `PATH` is inherited, and `TMPDIR` is the default. That nothing is written outside the scratch directory is an observed property of the current probes, not an enforced one. For an untrusted pull-request head, run it as a throwaway user or in a container; where `bwrap` is available, a wrapper is `bwrap --ro-bind / / --unshare-net --bind <tmp> /tmp python3 scripts/benchmark.py --reproduce` (with `<tmp>` an empty directory). Run it as an ordinary user: the `unreadable` probe reports `NOT-OBSERVED` as root.

### `--check`

`python3 scripts/benchmark.py --check` validates the corpus structure (always), then, when a full clone is present, confirms every recorded commit exists, every diff still hashes to its `diff_sha256`, every quote is in the head code, every `in_diff` finding touches a line of the diff, and no `pre_existing` finding has a quote on an added line. It uses local git only and needs no network. With no full clone (no repository, or a shallow one) it prints `not-run` and exits 0, and says no commit was checked. A missing commit or a changed diff exits 1 and names the fetch command; `scripts/validate.py` runs the structural part on every gate run.

Because `--check` exits 0 when it cannot run, and the gate never runs `--reproduce`, a seed that rots (a rewritten ref, a changed git behavior) would go unnoticed in CI. A periodic or manual run of `--reproduce` on a full clone is tracked in issue #52.

### The digest

`diff_sha256` is the sha256 of `git diff base head` between the two commit objects, with every output-changing option pinned on the command line (`--full-index --binary --no-color --no-ext-diff --no-textconv --no-renames --no-relative --indent-heuristic --diff-algorithm=myers -U3 --inter-hunk-context=0 -O/dev/null`, fixed prefixes), the matching `diff.*` and `core.*` keys pinned with `-c`, and no global or system git configuration read. Attributes are neutralized too: `core.attributesFile` points at the null device, `GIT_ATTR_NOSYSTEM=1`, and the diff runs in a temporary bare repository with no configuration, no `info/attributes` and no working tree that borrows the real objects through an alternates file, so a `-diff` or `binary` attribute, a diff driver or textconv selected by an attribute, a `.gitattributes` in the working tree or the index, and `.git/info/attributes` cannot change it. So the like of `diff.algorithm`, `diff.context`, `diff.noprefix`, `core.quotePath` or a user's attributes do not change it. A mismatch has two possible causes, and the message names both: rewritten history, or a git version or configuration that renders diffs differently.

## The rubric

A finding counts only if it is the labelled class and is reproduced by the model's own evidence. **The match is a keyword heuristic. It cannot tell a report written to hit the patterns from a report that understood the code.** The controls are these, and none is a proof:

**These controls stop accidental or label-only echoing. They do NOT stop a report that copies the public corpus.** `corpus.json` is public, and its labels, quotes and evidence patterns are in it. A report built from the quotes and pattern words of a finding scores a full match with no code reviewed (a test shows 16 of 16 on the diff tasks, and names it a known limit), and the brief task can be matched with a few phrases. The protection against that is operational, not in the scorer: **a scored run never shows the model `corpus.json`**, and every result record must say so with `corpus_hidden: true`; a record without it is refused for scoring. A copied report is a match, not a false finding, so the false-findings column cannot catch it.

The controls that remain:

1. The class label and the path must be the labelled ones.
2. On a diff task the evidence must contain a verbatim code line from the head checkout (`quotes`). This stops a report that only names the topic. It does not prove the code was read, because the quotes are public.
3. The evidence must match at least two different patterns (`evidence_min`), written with word boundaries, on different, non-overlapping pieces of text (two patterns satisfied by the same words count once).
4. A human reads the false-findings column: a true finding that is not in the labelled set counts as false, so the number is "not in the labelled set", not "wrong". This catches wrong and unlisted reports. It cannot catch a gamed one.

A test builds a report from the label alone (class and path copied, evidence the first word of the first pattern, and then the first word of every pattern) and asserts it scores zero recall on every task. The brief task has no code to quote, so it is the weakest: a brief that lists the right phrases can match. Treat brief-writer numbers as a screen to be confirmed by reading the briefs.

Matching is deterministic and does not depend on the order of the report. Each reported finding is a candidate for every expected finding it satisfies; the assignment is a maximum bipartite matching (augmenting paths, reports in a canonical order, expected findings in corpus order). So two overlapping labels and two reports cannot score differently because of which came first.

1. A reported finding is `{class, path, evidence}`. Each expected finding is matched once.
2. A report that is a candidate but loses its expected finding to another report is a duplicate. It is counted and is not false.
3. A report with a class outside the list, or a missing or empty path or evidence, is false and unevidenced.
4. Every other report with no candidate is false.

Numbers, each with its unit:

| Measure | Unit | How |
| --- | --- | --- |
| Recall | % of expected findings | matched / expected; also in-diff and pre-existing separately |
| Precision | % of reported findings | matched / (matched + false); `n/a` when nothing was reported |
| False findings | count | rules 3 and 4 |
| Fix rounds avoided | rounds of total | a history round counts when every finding of that round was matched |
| Wall clock | seconds, mean | from the record |
| Tokens | input and output, sums | from the record; required |
| Refusals | count | every refusal or filter cut-off entry |

A refused or cut-off run stays in the table with its findings (usually none); it is never dropped from the denominator. Runs of different client versions are different rows.

## Result records: one JSON object per line

This is the result format of the behavioral-evals matrix (issue #24: `client`, `client_version`, `model`, `quantization`, `context`, `tools_enabled`, `commit`, `date`, `outcome`, `cost`), kept as a local copy until that unit lands and extended with the benchmark fields. `task` replaces `case`.

| Field | Meaning |
| --- | --- |
| `task`, `role`, `run` | A corpus task id, one of its roles, and a run number from 1 to 1,000,000. The key (task, role, model, client, client_version, run) must be unique |
| `client`, `client_version` | Exact client name and version; both required |
| `model` | An exact id, stated once here (below) |
| `quantization`, `context` | Local models only (an Ollama tag), and then both required; `context` at most 100,000,000 |
| `date`, `commit` | A real calendar date `YYYY-MM-DD`, no later than tomorrow (UTC), and the commit of this repository the run used: 40 hex characters, or an abbreviation that resolves in a full clone |
| `tools_enabled` | List of tool names the model had |
| `outcome` | `completed`, `refused`, `filter-cutoff`, `error`, `timeout` |
| `findings` | List of exactly `{class, path, evidence}`, at most 50, evidence at most 2000 characters |
| `wall_clock_s` | A number above 0 and at most 10,000,000 |
| `tokens` | `{input, output}` integers from 0 to 10,000,000,000; required (cost) |
| `cost_usd` | Optional number from 0 to 10,000,000 |
| `approval_ref` | Required text: where the maintainer's per-run approval is recorded |
| `corpus_hidden` | Required, and must be `true`: the model was not shown `corpus.json` (nor the quotes and patterns in it) during the run. A record without it, or with any other value, is refused for scoring |
| `refusals` | List of `{kind, note}` with `kind` `refusal` or `filter-cutoff`; an outcome of `refused` or `filter-cutoff` needs at least one |
| `notes` | Optional, sanitized |

No text field may hold a control, format (zero-width or bidirectional), line-separator, unassigned or default-ignorable character (variation selectors, the combining grapheme joiner, the Hangul fillers, the braille blank, tag characters), or any space other than the ASCII one. Evidence is the one exception: it may hold line breaks and tabs, because it quotes code.

**Exact model id.** An Ollama tag needs a size or a version and may not be `latest`, a moving tag (`gemma4:12b`, `nomic-embed-text:v1.5`). A hyphenated API id needs a version number and a word after the vendor name (`claude-sonnet-5-5`, `gpt-5.1-codex`, `o3-mini`). Rejected: a family name (`sonnet`, `opus`), a bare vendor and major (`claude-3`, `gpt-5`), and any alias (an id with `latest`, `inherit` or `default` in it, `gemma:latest`).

The scorer fails the whole file on a malformed line, a missing model id, version or cost, a duplicate JSON key or a duplicate record key, so a typo cannot quietly shrink the sample.

## Private seeds

The private seeds listed in issue #3 are not in this repository and are not published here. Each would need a sanitized rewrite first (task text, diff and findings re-derived from public material, with the maintainer's word that it is publishable); until then they stay out. The corpus holds no private repository name, path or identifier, and `scripts/validate.py` sweeps it with the same scan as every other file.

## Limits, stated

- Seven tasks and 19 findings is direction at most; it will not separate close models.
- The brief task's input is the public issue text, not the full brief the implementer received, which is not public. The gaps were found by later reviews of the first implementation, so the column measures whether a model's brief covers what those reviews found, not whether the implementation was good.
- Evidence patterns and quotes are written by hand. A correct report that quotes none of the listed lines, or phrases the problem unusually, scores as false; the table shows false findings so a human can check them.
- Nothing here has been run against a model. The first run needs the maintainer's recorded approval per model (issue #47 is the tracker).
- The extraction of historical trees uses `tarfile` with the `data` filter where Python has it (3.11.4 and later, all of 3.12 and 3.13) and an equivalent member check (no absolute or `..` path, no links or devices) on earlier 3.11 patch releases. The CI matrix is 3.11, 3.12 and 3.13.
