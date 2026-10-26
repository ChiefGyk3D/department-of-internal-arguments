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

[evals/behavioral-evals.md](../evals/behavioral-evals.md) is the table to run by hand in each client before you trust a persona there, and it is explicitly supplied, not executed. The log format the pilot table comes from is in [evals/log-schema.md](../evals/log-schema.md); `python3 scripts/summarize_log.py <log.jsonl>` prints the table, so the next pilot does not hand-copy numbers.
