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
- Role agent and model: reviewer | adversarial-reviewer | implementer | transcriber; tier standard (cheap only for mechanical work whose code is already written). Name the model explicitly; never inherit. The most capable tier needs the human's recorded OK. Why this one:
- Domain packs to load (from packs/): <names, or none>. The report must say which it loaded.
- Local model step: <your local-model command for text-only drafts, or `n/a - <reason>`>
- Data class and what must not appear (hostnames, addresses, serials, callsigns, grid squares, employer, secrets):
- Gate command (the full gate, exactly as CI runs it):
- Required approvals and stop conditions (push, merge, release, privileged or destructive steps stop and ask):
- Evidence the report must carry (gate exit code, red evidence, revert check, other gates as pass | fail | not-run | n-a):
- Report path or return format:
