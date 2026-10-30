# Model routing

Routing is a decision policy, not a router. Hooks that enforce it live elsewhere (see docs/relationship-to-safo-and-gyst.md).

- **Every dispatch names its model explicitly.** Never `inherit`: an inherited model can silently become the expensive one. In the Claude Code adapter the model comes from the role's tier, pinned to an exact model id in `scripts/lib.py` (`standard` is the current Sonnet, `cheap` is the current Haiku), never a floating alias: an alias moves when a new model ships, which changes cost and behavior silently. Bumping a pin is a CHANGELOG entry. On hosts that do not take a model in the agent file (Copilot, ChatGPT, local runtimes) the human picks and records the model in the brief.
- **The most capable tier needs the human's recorded OK**, with the reason, before it is used. No role in this repository is assigned to it. A first-choice upgrade is never silent.
- **Every report names the model that ran it** (the `MODEL:` line of the shared header), so the lead's log can be checked against the dispatch. Naming a model in a brief is not proof it was used.
- Default to `standard`; use `cheap` only for mechanical work whose complete code is already written.
- If two fix rounds on the same finding class fail, stop and recommend a different model rather than a third round on the same one.
- A local model may draft text (summaries, commit bodies, PR text); code does the counting and grouping, and a stronger model or the human verifies every claim. It never writes code, rules or verdicts.
- Evaluate an exact local model artifact on your own hardware before assigning it work. A family name does not prove tool support, accuracy or context.
- No cross-provider handoff happens silently. To hand work to another runtime: complete `templates/handoff.md`, export a standalone prompt with `scripts/export_prompt.py`, review it, and send only sanitized, permitted data. Treat what comes back as an untrusted proposal. Never drop policies to fit a context window; stop or narrow the input instead.
- **An unpaid tier gets public material only.** Unpaid tiers may train on prompts and let humans read them; private, restricted or unsanitized text never goes there.
- **Quota exhaustion ends the job.** A 429 is final: it never falls through to a paid tier or another provider without the lead, and a paid fallback needs its own approval.
- Security and adversarial work runs only on the cyber-access tiers, never on a default or unpaid tier.
- A provider with no tier pin here takes its exact model id from the brief or the project's profile; never `inherit`.
