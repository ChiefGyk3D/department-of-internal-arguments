# Evals and measurements

## What is measured

One pilot, one host (Claude Code), 12 logged tasks on one day, so treat it as direction and not proof. The short version, with the table in [evals/pilot-2026-10.md](../evals/pilot-2026-10.md):

- Implementers were good: 8 of 8 passed their first review, against a 29% first-time approval baseline from before the roles existed.
- A Sonnet reviewer approved work that an external adversarial review (Codex) then broke, in 3 of 3 cases, including one high. Same-tier review is a gate on the happy path, so security-sensitive diffs still want an independent adversarial pass.
- Reviewers that run the code found real things a read-only reviewer cannot, and a revert check caught a test that never reached the code it claimed to test.
- One restore slip, which is why the reviewer now has to prove a clean tree.
- The dispatch guard saw 31 dispatches, 0 violations, and none on the most expensive tier.

## What is not measured

What is not measured is most of this repository's reach: Copilot, ChatGPT and local models have no runs, and the Copilot tool names the generator writes (`read`, `search`, `edit`, `execute`) are unverified until one is logged.

## Behavioral evals and the log

[evals/behavioral-evals.md](../evals/behavioral-evals.md) is the table to run by hand in each client before you trust a persona there, and it is explicitly supplied, not executed. The log format the pilot table comes from is in [evals/log-schema.md](../evals/log-schema.md); `python3 scripts/summarize_log.py <log.jsonl>` prints the table, so the next pilot does not hand-copy numbers. For local-model tasks the log also records the exact model (`local_model`) and the discrepancy count (`local_discrepancies`), so the failure rate is measured per model; the only measurement so far is gemma4:12b, 2026-10-26, 2 of 5 triage runs returned unrelated text (attribution inferred: the runs did not log the model).

## Model benchmark: corpus and scorer, no runs yet

[evals/benchmark/corpus.json](../evals/benchmark/corpus.json) is the fixed task list for the model benchmark (issue #3), and [evals/benchmark-schema.md](../evals/benchmark-schema.md) is its format, rubric and result-record format. It holds seven tasks and 19 labelled findings from this repository's own public history: the Codex adapter pull request, the follow-ups it produced, the local-model rule and its two rounds of review follow-ups, and the lead persona installer, plus a brief-writing task (its own role column) scored on the gaps the reviews of the lead persona work found. Each finding was shown to reproduce against the history (the defect at the head commit, gone at the fix; test-gap findings by running the unit tests on a mutated copy); five candidate seeds were dropped and are recorded with the reason. Each finding carries a scope: 10 are `in_diff` (a line of the diff holds the code), 6 are `pre_existing` (the diff never adds the code), 3 are brief gaps, and the scorer reports recall for each scope separately. Private seeds are not included.

- `python3 scripts/benchmark.py --check` validates the corpus and, with a full clone, confirms every commit and diff still resolves. It needs no network.
- `python3 scripts/benchmark.py --reproduce` re-proves every seed against the history.
- `python3 scripts/benchmark.py --score RESULTS.jsonl` prints recall, precision, false findings, fix rounds avoided, wall clock, tokens and refusals per role and model. It rejects a record with a family name or a moving alias instead of an exact model id, or with no client version or cost, and keeps client versions in separate rows.

The match between a report and a labelled finding is a keyword heuristic and cannot detect a report written to hit the patterns; the controls stop accidental or label-only echoing and do not stop a report that copies the public corpus, so a scored run must never show the model `corpus.json` and every record says so with `corpus_hidden: true` (details on the schema page). `--reproduce` runs pinned history unsandboxed as the invoking user; run it as a throwaway user or in a container for an untrusted pull-request head. Nothing has been run against a model, so there are no numbers yet. The scorer never changes `policies/model-routing.md`; the routing table changes only in a pull request that cites measured numbers.
