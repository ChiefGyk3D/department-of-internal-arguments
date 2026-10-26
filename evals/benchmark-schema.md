# Benchmark corpus, rubric and result records

The benchmark (issue #3) asks which model does which role well enough to route work to it. This page is the format for unit 1 (issue #47): the corpus of tasks, the scoring rubric and the result records. No model has been run against it. `python3 scripts/benchmark.py` checks the corpus and prints the table; it never writes a file, and it never changes `policies/model-routing.md`. The routing table changes only in a pull request that cites the numbers.

## The corpus: `evals/benchmark/corpus.json`

Seven tasks, 19 labelled findings, all drawn from this repository's own history, where the later findings are public. Every finding was shown to reproduce (below); five candidate seeds were dropped, each recorded with its reason in the `dropped` list, not adjusted to fit.

A task is one unit of work for one or more roles:

| Field | Meaning |
| --- | --- |
| `id`, `title` | A slug and a one-line description |
| `roles` | `reviewer` and `adversarial-reviewer` for a diff task; `brief-writer` for a brief task (the planning column) |
| `kind` | `diff`: the model is handed `git diff base head`. `brief`: the model is handed the recorded input file and writes the implementer brief |
| `base`, `head`, `diff_sha256` | Diff tasks: full commit ids and the sha256 of `git diff --full-index --binary --no-renames base head` in a fixed form, so a moved or rewritten commit is caught |
| `input_file`, `input_sha256` | Brief tasks: the recorded problem statement under `evals/benchmark/inputs/` |
| `findings` | The known findings a later review produced |

A finding:

| Field | Meaning |
| --- | --- |
| `id`, `class`, `severity`, `summary`, `source` | The label. `class` is one of `input-validation`, `ownership-trust`, `filesystem-safety`, `output-injection`, `concurrency`, `error-reporting`, `test-gap`, `dead-code`, `doc-accuracy`, `convention`, `brief-gap`. `source` says which public review or issue produced it |
| `round` | The order of the fix round in the real history that fixed it (an unfixed finding takes the next number). Findings in one round were fixed together |
| `paths` | The files a correct report points at (`brief` for a brief task) |
| `evidence_any` | Regular expressions; a correct report's own evidence must match at least one |
| `fixed` | The commit that fixed it, or `null` |
| `at_base` | `observed`, `not-observed` or `n/a` (the code did not exist yet): what the reproduction shows at the base commit |
| `repro` | How it reproduces: a `probe` (a script in `evals/benchmark/probes/`) or a `pattern` (a regular expression over a file at a commit, present or absent) |
| `absent_from_input` | Brief findings only: patterns that must NOT match the recorded input, which proves the original brief did not already cover the gap |

### What "reproduces" means

`python3 scripts/benchmark.py --reproduce` (needs a full clone that has the commits; the pull-request heads come from `git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'`). For each finding it checks, in a temporary directory, that the defect is observed at `head`, matches `at_base` at `base`, and is gone at `fixed`. A probe prints `OBSERVED` or `NOT-OBSERVED` on its last line and exits 0; a crash is a failure, not a pass. A brief finding reproduces through the diff finding it references and must also be absent from the recorded input. A seed that fails any step is dropped from the corpus and listed under `dropped`.

The probes run code from this repository's own history with `python3 -I`, in a scratch directory, and write nothing outside it. The `unreadable` probe needs an ordinary user (it reports `NOT-OBSERVED` as root, so run `--reproduce` as an ordinary user).

### `--check`

`python3 scripts/benchmark.py --check` validates the corpus structure (always), then, when a full clone is present, confirms every recorded commit exists and every diff still hashes to its `diff_sha256`. It uses local git only and needs no network. With no full clone (no repository, or a shallow one) it prints `not-run` and exits 0, and says no commit was checked. A missing commit or a changed diff exits 1 and names the fetch command. `scripts/validate.py` runs the structural part on every gate run.

## The rubric

A finding counts only if it is the labelled class and is reproduced by the model's own evidence. Matching is mechanical and does not depend on the order of the report:

1. A reported finding is `{class, path, evidence}`. It matches an expected finding when the class is the same, the path is one of the finding's `paths`, and the evidence matches one `evidence_any` pattern. Each expected finding is matched once.
2. A second report of an already matched finding is a duplicate. It is counted and is not false.
3. A report with a class outside the list, or a missing or empty path or evidence, is false and unevidenced.
4. Every other unmatched report is false. The corpus lists only the labelled findings, so a true but unlisted finding counts as false in the number and is listed for a human to triage; do not read the precision figure as "wrong", read it as "not in the labelled set".

Numbers, each with its unit:

| Measure | Unit | How |
| --- | --- | --- |
| Recall | % of expected findings | matched / expected |
| Precision | % of reported findings | matched / (matched + false); `n/a` when nothing was reported |
| False findings | count | rule 3 and 4 |
| Fix rounds avoided | rounds of total | a history round counts when every finding of that round was matched |
| Wall clock | seconds, mean | from the record |
| Tokens | input and output, sums | from the record; required |
| Refusals | count | every refusal or filter cut-off entry |

A refused or cut-off run stays in the table with its findings (usually none); it is never dropped from the denominator.

## Result records: one JSON object per line

This is the result format of the behavioral-evals matrix (issue #24: `client`, `client_version`, `model`, `quantization`, `context`, `tools_enabled`, `commit`, `date`, `outcome`, `cost`), kept as a local copy until that unit lands and extended with the benchmark fields. `task` replaces `case`.

| Field | Meaning |
| --- | --- |
| `task`, `role`, `run` | A corpus task id, one of its roles, and a positive run number. The key (task, role, model, client, client_version, run) must be unique |
| `client`, `client_version` | Exact client name and version; both required |
| `model` | An exact id (`claude-sonnet-5-5`, `gemma4:12b`). A family name (`sonnet`, `claude-sonnet`) or an alias (anything with `latest`, `inherit`, `default`) is rejected |
| `quantization`, `context` | Local models only (an Ollama tag), and then both required |
| `date`, `commit` | `YYYY-MM-DD`, and the commit of this repository the run used |
| `tools_enabled` | List of tool names the model had |
| `outcome` | `completed`, `refused`, `filter-cutoff`, `error`, `timeout` |
| `findings` | List of exactly `{class, path, evidence}`, at most 50, evidence at most 2000 characters |
| `wall_clock_s` | Positive number of seconds |
| `tokens` | `{input, output}` non-negative integers; required (cost) |
| `cost_usd` | Optional non-negative number |
| `approval_ref` | Required text: where the maintainer's per-run approval is recorded |
| `refusals` | List of `{kind, note}` with `kind` `refusal` or `filter-cutoff`; an outcome of `refused` or `filter-cutoff` needs at least one |
| `notes` | Optional, sanitized |

The scorer fails the whole file on a malformed line, a missing model id, version or cost, or a duplicate key, so a typo cannot quietly shrink the sample.

## Private seeds

The private seeds listed in issue #3 are not in this repository and are not published here. Each would need a sanitized rewrite first (task text, diff and findings re-derived from public material, with the maintainer's word that it is publishable); until then they stay out. The corpus holds no private repository name, path or identifier, and `scripts/validate.py` sweeps it with the same scan as every other file.

## Limits, stated

- Seven tasks and 19 findings is direction at most; it will not separate close models.
- The brief task's input is the public issue text, not the full brief the implementer received, which is not public. The gaps were found by later reviews of the first implementation, so the column measures whether a model's brief covers what those reviews found, not whether the implementation was good.
- Evidence patterns are written by hand. A correct report phrased unusually can miss every pattern and score as false; the table shows false findings so a human can check them.
- Nothing here has been run against a model. The first run needs the maintainer's recorded approval per model (issue #47 is the tracker).
