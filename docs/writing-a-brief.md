# Writing a brief

Every dispatch carries a brief, written from [templates/brief.md](../templates/brief.md). Every field is filled or says `n/a - <reason>`. A brief with an empty gate command, model, or worktree is not ready.

## The fields

- Task and owner; outcome and who uses it; scope and non-goals.
- Acceptance criteria, with observable examples.
- Base commit, worktree (absolute path) and permitted files.
- Role agent and model: reviewer, adversarial-reviewer, implementer or transcriber; tier standard (cheap only for mechanical work whose code is already written). On Claude Code the model is pinned by the role's tier; on every other host name the exact model id. Never inherit. The most capable tier needs the human's recorded OK.
- Fix round number and prior findings. A third round on the same finding class is not dispatched; the lead escalates instead.
- Domain packs to load (see [Packs](packs.md)), or none. The report must say which it loaded.
- Local model step for text-only drafts, or `n/a` with a reason.
- Data class and what must not appear (hostnames, addresses, serials, callsigns, grid squares, employer, secrets).
- Gate command: the full gate, exactly as CI runs it.
- Stop conditions: ambiguous scope, a check that would have to be weakened, security-sensitive code the plan did not review. The agent returns its report instead of guessing.
- Required approvals: the agent never pushes, merges, tags or releases; the human's approval is recorded in the human's own message, not the brief.
- Evidence the report must carry, and the report path or return format.

## The lead

The lead is [templates/lead.md](../templates/lead.md), copied into the controller session's instructions. It dispatches the four roles, reads their reports and decides; it does not write the code the agents are briefed for.

- Name the role by what the work is: a plan whose code is complete goes to the transcriber; everything else that changes files goes to the implementer; a finished task goes to the reviewer; a diff touching credentials, parsers, network, CI, permissions or anything the threat model ranks high also goes to the adversarial reviewer, after the reviewer, never instead of it.
- Name packs by the domain the diff touches. A pack named in a brief is not proof it was loaded; read `PACKS LOADED`.
- Read the report header first: `GATE` needs an exit code you believe, `TREE` must be clean for a reviewer, `MODEL` must match the dispatch. `NOT VERIFIED` is a list the lead now owns. `LOCAL-LLM` must say `n/a - <reason>` or name the exact model that answered, what its draft was checked against and the discrepancy count; a draft nobody checked is not used. The measured failure behind this is about gemma4:12b only (2 of 5 triage runs returned unrelated text on 2026-10-26).
- Severity is one scale: blocking, important, minor. After two failed rounds on the same finding class, stop and recommend a different model.
- Append one line per task to the pilot log (see [Evals and measurements](evals-and-measurements.md)).
- Never infer approval, fill in a missing report field, dispatch the most capable tier without the human's recorded OK, or let an agent push.
