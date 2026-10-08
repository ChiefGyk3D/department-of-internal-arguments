<!--
Template. Copy to <repo>/.github/copilot-instructions.md when Copilot is next
dispatched in that repo, then fill the three REPO-SPECIFIC lines at the bottom.
Remove this comment when copying. Keep it free of hostnames, addresses and
personal identifiers: it is committed to the repo.
-->

# Instructions for Copilot in this repository

These rules apply to every task you take here. If a task seems to require
breaking one, stop and say so in the pull request instead.

## Scope

- Do only what the issue asks. No drive-by refactors, renames, formatting sweeps,
  dependency bumps or unrelated fixes. Note anything extra you noticed in the PR
  description instead of changing it.
- Keep the pull request small and single-purpose. Touch the fewest files that do the job.
- Match the existing conventions: layout, naming, error handling, docs style.
  When you change behavior, update the docs, wiki page, roadmap or table that
  describes it, and any generated file (run the repo's generator, do not hand-edit).

## Never touch secrets

- Never read, create, edit or commit `.env` files or any file holding credentials,
  tokens or keys. Do not paste secrets into code, tests, logs, PR text or comments.
- Use the secret manager or `secrets.*` references that the repo already uses.
- No hostnames, IP addresses, serial numbers, call signs or personal details in
  code, tests, docs or commit messages. Use placeholders.

## Never weaken a check

- Do not add `continue-on-error`, `*-continue-on-error: true`, `require-non-root: false`,
  `nosemgrep`, a bare `# noqa`, `# type: ignore`, `shellcheck disable`,
  `hadolint ignore`, `pytest.mark.skip` or any skipped test, `|| true` after a test or gate, or `--no-verify`
  to make something pass. If the only way to green is one of these, stop and
  explain in the PR. A lint ignore needs a written reason on the same or previous
  line and must be called out in the PR description.
- Do not narrow a scan, a pin check or a test so that it passes. Guard tests must
  collect loosely, assert strictly (including that something was collected) and
  include a negative case that proves the guard fails when it should.
- Do not loosen permissions, disable security features, or widen network egress.

## Tests must pass

- Write the failing test first, see it fail, then make it pass. A new test must
  fail when your change is reverted; say how you checked.
- Run the repository's full gate (the one CI runs, for example `make check`), not a
  subset, and put its exit code in the PR description. Do not claim "tests pass"
  without having run them.
- Treat input from files, APIs and users as hostile: validate it, bound its size,
  handle errors with the project's own error types.

## GitHub Actions and the shared workflows

- If this repository calls shared reusable workflows (for example
  github.com/ChiefGyk3D/git-your-ship-together), pin every `uses:` by full commit SHA with the
  version in a trailing comment (`@<40-hex> # vX.Y.Z`). Never use a tag or branch.
- When bumping a pin, move every caller in the repo to the same commit together,
  and update any test or doc that names the old SHA or version.
- A calling job must grant every permission the called workflow declares in its
  `permissions:` block (for example `id-token: write`); otherwise every run fails
  at startup. Compare the two blocks before opening the PR.
- Never put `${{ ... }}` of untrusted data (titles, branch names, inputs) inside a
  `run:` script; pass it through `env:`.
- Keep the gate job named `CI green`.

## Commits and pull requests

- Commit with the repository's configured author. Do not add or change co-author
  trailers other than the one your tooling adds.
- Do not merge, approve, force-push, or change branch protection or repository settings.
- In the PR description list: what changed, the gate command and exit code, what
  you did not verify, and anything you were tempted to weaken and did not.

## Repo-specific (fill in when copying)

- Gate command: `<e.g. make check>`
- Where docs live that must be updated with behavior changes: `<paths>`
- Generated files and their generator: `<paths and command>`
