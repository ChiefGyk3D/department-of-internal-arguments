# Quality gates

- A plan or a reviewer's opinion is not proof of execution. Run the repository's FULL gate, exactly as CI runs it, and capture the exit code: `<gate> > "$SCRATCH/gate.log" 2>&1; echo "gate exit=$?"`. Never pipe a gate into grep or tail and chain on the result.
- **If no gate is defined** (neither the brief nor the repository documents one), the gate is `not-run - none defined`: run the test and lint commands you can find, list them under OTHER GATES, and propose a gate command in the report. Never invent a command and call it the full gate.
- Report every applicable gate (tests, lint, build, security scan, docs) with exactly one of four statuses: **pass**, **fail**, **not-run**, **n-a**. Each carries the command and a reason. A failure is never hidden as n-a; if a gate could not run, it is not-run.
- **Never weaken a check to get green** (opt-outs, skips, narrower scopes, loosened tests). If that is the only route, stop and escalate with the evidence.
- **Reviewers prove fixes bite.** For every claimed fix and every new test, revert the fix in a scratch copy, confirm the test goes red, restore, confirm green. A test that stays green on revert is a finding.
- **Reviewers finish with a clean tree.** After any revert or mutation, restore the exact bytes and show the proof (empty `git status --porcelain`, or a diff against head). A mutated line left behind is a defect the reviewer introduced.
- Guard tests collect loosely, assert strictly (including that something was collected) and include a negative case proving the guard bites.
- Every report starts with the shared header (role, model, head, base, gate, other gates, packs loaded, tree, not verified) so the lead can log it without re-reading the report.
- Say what you did not run (NOT VERIFIED). Never write "plausible" for something you could have run.
