# Department of Internal Arguments

The repository is my shared home for AI agent personas and the domain knowledge they load, written once and generated for Claude Code, GitHub Copilot, ChatGPT and local models. The name is because the best thing a second agent can do for the first one is disagree with it, and because I wanted somewhere for those arguments to live that was not a pile of prompts copied between repos.

Release 0.4.0. Apache-2.0.

## Read in this order

1. [What and why](what-and-why.md): why eight roles and nine packs, not a dozen personas.
2. [Install](install.md): project and user layouts, preview then apply, `--update`, and why you never symlink.
3. [Roles](roles.md): the eight roles, how they are generated, and what a tier means.
4. [Packs](packs.md): the nine domain packs and when to name one.
5. [Policies](policies.md): the four policies every generated agent carries.
6. [Writing a brief](writing-a-brief.md): the brief template and the lead's checklist.
7. [Overlay and ownership](overlay-and-ownership.md): the marker hash, the private overlay and file modes.
8. [Evals and measurements](evals-and-measurements.md): the pilot, what is not measured, and the behavioral evals.
9. [Relationship to SAFO and GYST](relationship-to-safo-and-gyst.md): hooks, CI and the validator's workflow checks.

## Privacy

Public repo, so the validator greps the whole tree, `docs/` included, for IP addresses, home paths, machine-specific tool names and port numbers, and fails on a hit. Examples use placeholders: `N0CALL`, `N0TST`, `FN31pr`, `host`, `http://localhost:11434`. Raw transcripts, caption files and exported social posts are gitignored and the validator rejects them if they show up. See [SECURITY.md](../SECURITY.md) for reporting.

## License

Apache-2.0, © ChiefGyk3D (see [LICENSE](../LICENSE) and [NOTICE](../NOTICE)). The starter this grew from was also mine and was MIT licensed; the whole of it is relicensed here.
