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

## The case registry and the matrix

The 26 exercises live in [evals/cases.json](../evals/cases.json), the single source; [evals/behavioral-evals.md](../evals/behavioral-evals.md) is generated from it (`python3 scripts/evals_matrix.py --write`, and `python3 scripts/generate.py --check` fails on drift). Each case records its roles and packs, the clients it applies to, a fixture name, a cost class (`prompt-only`, `fixture`, `interactive`) and whether it is `mechanical` (a check over the transcript or the fixture tree) or `judged` (a human decides; it never auto-passes).

A run records one JSON line per case in the format of [evals/results-schema.md](../evals/results-schema.md). `python3 scripts/evals_matrix.py RESULTS.jsonl` prints the matrix of case by client and model. It calls no model and uses no network. A cell with no record reads `not run`, Copilot reads `unsupported` until a headless interface is verified, and a result recorded against older persona text reads `(stale)`: each record stores the hash of `roles/`, `policies/`, `packs/` and `fragments/` it measured, so a pass cannot outlive the prompt it measured. Nothing in the matrix has been run against a model yet; [evals/example-results.jsonl](../evals/example-results.jsonl) is synthetic.
