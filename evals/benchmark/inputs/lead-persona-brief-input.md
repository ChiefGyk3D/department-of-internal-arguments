# Input for the brief-writing task: the lead persona work

This is the public problem statement the implementation started from: the text of issues #32 and #33 of this repository, with references to repositories outside this one removed. The task is to write the brief for the implementer (scope, behavior to build, edge cases, acceptance tests) against the repository at commit 78ab5a0.

## Issue 32: Install the lead persona (templates/lead.md is never loaded into the lead session)

**Problem.** `templates/lead.md` is a template the operator must copy into the controller session's instructions. In the one installation audited (2026-10-26) it was never installed: the role agents were, but the lead text was in no instruction file. The day before, that lead session did work the template says it must not do (it made about 25 direct edits to workflow, test and doc files in one evening).

**Proposal.** The installer offers `--lead <path>`: it appends a marked, hash-owned block containing lead.md to a named instruction file, and `--list-managed` and the ownership marker cover it like the role agents. The README states that a lead which never loaded the template is not governed by it.

**Acceptance.** Installer tests (append, idempotent re-run, ownership marker, refuse to overwrite a hand-edited block); docs.

## Issue 33: lead.md: add the orchestrator rules (waits, look first, access, done, cards)

**Problem.** lead.md lacks the orchestrator rules the doctrine pages now state, so a lead that loads the persona is not told them.

**Proposal.** Add to lead.md: "You do not wait" (waits go to background or a monitor agent); two dispatch items (a tracking card exists; start the worktree from the right repository); "Before you tell the human to do something" (look at prior art, vendor docs, then the live API); "Access" (ask once for the union of scopes, no broader fallback); "Done" (verified end to end through the user's path); and three matching "never do" lines.

**Acceptance.** The DoIA gate passes, including the American-English guard. Every added sentence traces to a doctrine page.
