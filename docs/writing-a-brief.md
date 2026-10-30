# Writing a brief

Every dispatch carries a brief, written from [templates/brief.md](../templates/brief.md). Every field is filled or says `n/a - <reason>`. A brief with an empty gate command, model, or worktree is not ready.

## The fields

- `spend:`, on its own line at the start of a line, with one value: `none - free-only`, or `paid - approval in the human message at <time>, cap <n> USD`. The paid form names the human's own message and its time; a paid action has its own fresh, single-use approval that a sub-agent cannot inherit and that ends with the job (see [Policies](policies.md)). A brief that needs a paid call and carries no such approval says `none - free-only`, and the agent makes no paid call. The validator checks the template's line; the cap is counted in code, not by the agent.
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

The lead is [templates/lead.md](../templates/lead.md), installed into the controller session's instructions (`install.py --lead CLAUDE.md`; see [Install](install.md), including how to check that the session loaded it). It dispatches the five roles, reads their reports and decides; it does not write the code the agents are briefed for.

The orchestrator rules in it, with the reason for each (the template keeps them short because it is injected into a session):

- Waits. A CI run, a deploy or a polling loop in the lead's own thread blocks the human's next message. It goes to a background task or a read-only monitor agent, one watch per thing. A step silent for 5 minutes, or an agent that ended without a report, is reported, not watched.
- Implementing. Code changes go to an implementer agent; the lead verifies. A lead that edits files itself skips the review loop the rest of the template exists for.
- Look first. Before telling the human to click, run or configure anything: the project's notes, then the vendor's documentation page (dated; a search summary is a lead, not a source), then the live system through a read-only API. If a command can do the step inside what the human approved, run it. Mark claims verified or inferred and keep the mark when restating them.
- Access. List every call the whole job needs and ask once for the union, with each scope and why. A read-only grant followed by a second request, or a broader grant offered as a fallback, makes the human decide twice.
- Done. "Done" means observed through the path the user will use, in this turn: the real command, the public address, the merge target's run. A state read has a time; the human's newer report outranks it. A destructive step gets a fresh read of the target, a script that stops at the first error, and a read-back.
- Tracking. An issue and a board card exist before work starts, and every brief carries an `issue: owner/repo#N` line. Move the card in the same response as the dispatch, pull request, merge or block.
- Working directory. A worktree agent starts wherever the session's shell is. Run `cd <the issue's repository>` in the same command as the dispatch, or the agent lands in the wrong repository.
- Merge and release. Merge only through the tested watcher, pinned to the head SHA, with `CI green` required and every check finished; check the PR is still open before pushing to it; every PR links its issue. A hand-rolled loop can merge on a stale or partial result.
- Paid actions and data class. Each paid action has its own fresh, single-use approval from the human, shown after a quota and cost line and recorded in the brief's `spend:` line; caps are counted in code and a 429 ends the job. An unpaid tier may train on prompts and let humans read them, so it gets public material only; security and adversarial work goes only to the cyber-access tiers.
- The researcher answers one question from the web or from supplied material and never changes the repository. The brief states the question, the data class of anything it may send and the destination tier, and on Codex it is dispatched only under a read-only sandbox with approvals on request (Codex cannot remove its edit and shell capabilities); the report separates VERIFIED claims (URL and date read) from INFERRED ones, lists conflicts and says what was sent where.
- Never: use an approval token meant for the human (from a hook, gate or prompt) unless the human's own message supplies it, quoted with its time; write an unfixed weakness into a public issue, pull request or comment; re-suggest an option the human rejected.

- Name the role by what the work is: a plan whose code is complete goes to the transcriber; everything else that changes files goes to the implementer; a finished task goes to the reviewer; a diff touching credentials, parsers, network, CI, permissions or anything the threat model ranks high also goes to the adversarial reviewer, after the reviewer, never instead of it; a question that needs the web goes to the researcher.
- Name packs by the domain the diff touches. A pack named in a brief is not proof it was loaded; read `PACKS LOADED`.
- Read the report header first: `GATE` needs an exit code you believe, `TREE` must be clean for a reviewer, `MODEL` must match the dispatch. `NOT VERIFIED` is a list the lead now owns. `LOCAL-LLM` must say `n/a - <reason>` or name the exact model that answered, what its draft was checked against and the discrepancy count; a draft nobody checked is not used. The measured failure behind this is about gemma4:12b only (2 of 5 triage runs returned unrelated text on 2026-10-26; the attribution is inferred because the runs did not log the model).
- Severity is one scale: blocking, important, minor. After two failed rounds on the same finding class, stop and recommend a different model.
- Append one line per task to the pilot log (see [Evals and measurements](evals-and-measurements.md)).
- Never infer approval, fill in a missing report field, dispatch the most capable tier without the human's recorded OK, or let an agent push.
