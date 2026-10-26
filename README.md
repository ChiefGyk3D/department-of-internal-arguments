# Department of Internal Arguments

This is my shared home for AI agent personas and the domain knowledge they load, written once and generated for Claude Code, GitHub Copilot, ChatGPT and local models. The name is because the best thing a second agent can do for the first one is disagree with it, and because I wanted somewhere for those arguments to live that was not a pile of prompts copied between repos.

Release 0.3.1. Apache-2.0.

Four roles (`implementer`, `reviewer`, `adversarial-reviewer`, `transcriber`) describe how work gets done, and six packs hold the domain knowledge you name in a brief when the task needs it.

## 60-second install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

Python 3.11 or later, standard library only. Never symlink the installed files into a dotfiles repo; see [Install](docs/install.md).

## Documentation

The pages live in [docs/](docs/home.md) and are mirrored to the wiki.

- [What and why](docs/what-and-why.md): why roles and packs, not a dozen personas.
- [Install](docs/install.md): project and user layouts, `--update`, drift checks, never symlink, exporting a prompt.
- [Roles](docs/roles.md): the four roles, repository layout, tiers and tools.
- [Packs](docs/packs.md): the six packs.
- [Policies](docs/policies.md): the four policies, one line each.
- [Writing a brief](docs/writing-a-brief.md): the brief template and the lead.
- [Overlay and ownership](docs/overlay-and-ownership.md): the marker hash, the private overlay, file modes.
- [Evals and measurements](docs/evals-and-measurements.md): the pilot, what is not measured, behavioral evals.
- [Relationship to SAFO and GYST](docs/relationship-to-safo-and-gyst.md): hooks and CI.

Privacy and license are on the [Home page](docs/home.md). To check your own copy: `python3 scripts/generate.py --check`, `python3 scripts/validate.py`, and `python3 -m unittest discover -s tests -v`. See [SECURITY.md](SECURITY.md) for reporting.
