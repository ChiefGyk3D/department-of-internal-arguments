<!--
Template. Copy into the dispatch prompt (or a scratch file) for a role agent.
Adapted from the starter package this repository grew from. Keep it free of
hostnames, addresses and personal identifiers.
-->

# Work brief

- Task / owner:
- Outcome and who uses it:
- In scope / non-goals:
- Acceptance criteria, with observable examples:
- Base commit, worktree (absolute path) and permitted files:
- Role agent and model: reviewer | adversarial-reviewer | implementer | transcriber; tier standard (cheap only for mechanical work whose code is already written). On Claude Code the model is pinned by the role's tier; on every other host name the exact model id here. Never inherit. The most capable tier needs the human's recorded OK. Why this one:
- Fix round number and prior findings: round 1 (none) | round N addressing findings <numbers> from <report path>. A third round on the same finding class is not dispatched; the lead escalates instead.
- Domain packs to load (from packs/): <names, or none>. The report must say which it loaded.
- Local model step: <your local-model command for text-only drafts, or `n/a - <reason>`>
- Data class and what must not appear (hostnames, addresses, serials, callsigns, grid squares, employer, secrets):
- Gate command (the full gate, exactly as CI runs it):
- Stop conditions (ambiguous scope, a check that would have to be weakened, security-sensitive code the plan did not review): the agent returns its report instead of guessing.
- Required approvals: the agent never pushes, merges, tags or releases. Note here what the lead will do after the report and where the human's approval for it is recorded (the human's own message, not this file).
- Evidence the report must carry (gate exit code, red evidence, revert check, other gates as pass | fail | not-run | n-a):
- Report path or return format:
