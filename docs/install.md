# Install

## Project install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

`--adapter` is `claude`, `copilot`, `both` (Claude Code and Copilot) or `codex`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it never overwrites a file it did not generate, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: files whose marker is well-formed and whose body still matches the hash in the marker are replaced in place (see [Overlay and ownership](overlay-and-ownership.md)), and anything else at a destination path is still a collision. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

## Codex

`--adapter codex` writes Codex custom agents and skills from the same roles, packs, policies and report header:

```
python3 scripts/install.py --target /path/to/project --adapter codex
python3 scripts/install.py --target /path/to/project --adapter codex --apply
```

It writes `<target>/.codex/agents/<role>.toml` (one custom agent per role) and `<target>/.agents/skills/pack-<name>/SKILL.md` (one skill per pack, byte-identical to the Claude Code skill). Ownership markers, `--update`, `--force-edited`, `--overlay`, `--list-managed`, symlink refusal and whole-install collision refusal behave as for the other adapters. In a custom agent file the marker is the first line of `developer_instructions`, because TOML has no place for a Markdown comment outside that string. `--adapter both` keeps its meaning (Claude Code and Copilot); name `codex` to get Codex files. With `--layout user` the target is your home directory (or a tree laid out like it), because Codex keeps user agents in `~/.codex/agents` and user skills in `~/.agents/skills`, two roots with no common parent below home; the relative paths are the same as in the project layout.

What was verified, from OpenAI's own Codex documentation, retrieved 2026-10-26. Nothing below has been run in Codex.

- AGENTS.md discovery ([Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md)): global `~/.codex/AGENTS.override.md` or `AGENTS.md` (the first non-empty one; `CODEX_HOME` can move the directory), then each directory from the project root down to the working directory (override, then `AGENTS.md`, then `project_doc_fallback_filenames`), at most one file per directory, concatenated root-down; reading stops at `project_doc_max_bytes`, 32 KiB by default. This adapter does not write an `AGENTS.md`: that file is the project's, and a single merged one would not hold four roles and their policies inside 32 KiB.
- Custom agents ([Subagents](https://developers.openai.com/codex/subagents)): standalone TOML files in `.codex/agents/` (project) or `~/.codex/agents/` (personal); required keys `name`, `description` and `developer_instructions`; optional `model` and `model_reasoning_effort`. Built-in agents are `default`, `worker` and `explorer`. Per-role definitions exist, so the four roles map one to one.
- Sandbox and approvals ([Sandboxing](https://developers.openai.com/codex/concepts/sandboxing)): sandbox modes `read-only`, `workspace-write` and `danger-full-access`; approval policies `on-request` and `never`. The subagents page says a custom agent inherits the parent's sandbox and approval settings, that `sandbox_mode` and `approval_policy` in an agent file do not override the parent, and that agent instructions can ask for no edits but do not create a read-only sandbox. So the two reviewer roles, which have no edit capability, can only be told not to edit; their generated text says so.
- Skills ([Build skills](https://developers.openai.com/codex/skills)): a directory with `SKILL.md` (`name` and `description` required), read from `$CWD/.agents/skills` up to the repository root, `$HOME/.agents/skills` and `/etc/codex/skills`; an explicit `$skill` mention invokes one.
- Tools: the agent file can switch off `features.shell_tool`, `features.apps`, `features.plugins`, `features.memories` and `features.request_permissions_tool`, and individual skills. There is no per-tool allow list that matches `read`, `search` and `edit`, so those capabilities are not expressed in the file; every DoIA role needs a shell, so none of those keys is set.

UNVERIFIED: that Codex loads the generated TOML and the skills without complaint (the TOML parses with the Python standard library and has the three required keys, nothing more); that a project's `.codex/` layer is skipped when the project is marked untrusted (the config page says so; not tested); where `CODEX_HOME` moves the user agents; the exact skill names Codex shows; whether `$pack-<name>` selects a pack the way the generated text says. The hand-run row for this is exercise 26 in `evals/behavioral-evals.md`. The tier-to-model mapping is not carried over: the agent files pin no model, so the agent inherits the parent's, and the report's `MODEL:` line names what the session reports.

## Keeping a dotfiles repo current

The project install writes `<project>/.claude/...`. A user-level Claude Code tree (the one a dotfiles repo keeps) has no `.claude` level, so `--layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md` instead. It is still additive, still refuses symlinked destinations and whole-install collisions, still previews by default, and `--update` still replaces only files it can prove it generated (see [Overlay and ownership](overlay-and-ownership.md)). It is for Claude Code and Codex (Copilot has no user layout, and asking for it is refused; the Codex form is described above), and a target inside this repository is refused.

The routine, from a checkout pinned to a release tag:

```
git -C /path/to/department-of-internal-arguments fetch --tags
git -C /path/to/department-of-internal-arguments checkout <tag>   # <tag> is a placeholder for the release tag you pin; the current release is 0.3.1
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update --apply
git -C ~/dotfiles/claude status   # review the diff, then commit
```

The first install run is the preview and writes nothing.

## Checking drift

To see drift without writing, `--list-managed` prints every file under the target that carries the marker, its release, its source (`public` or `overlay`) and whether it is `current`, `stale` (older release), `differs` (this release, content changed), `orphan` (this checkout no longer produces it) or `unchecked` (overlay-sourced and no `--overlay` was given). It exits 0 when everything is current and 3 otherwise, so a sync step can report drift and decide. When overlay-sourced files exist and `--overlay` was not given, they cannot be checked: they show as `unchecked` and the exit code is 3, so give `--overlay` to the check as well. An `edited` file is one whose marker is present but whose body no longer matches it (or whose marker is malformed); `legacy-unverified` is a release 0.2.0 file (see [Overlay and ownership](overlay-and-ownership.md)) with a body 0.2.0 never produced.

## Never symlink

**Never symlink the installed files into a dotfiles repo.** Git stores the link, not the content; every other machine that pulls gets a dangling link to a path that does not exist there; and a secret scan over the repo sees a link target string, never the persona text. The backup is only a backup if the repo holds real files, which is what this installer writes.

## Export for ChatGPT or a local model

Export one self-contained prompt (policies, one role, the packs you pick):

```
python3 scripts/export_prompt.py --role reviewer --pack threat-model --output /tmp/reviewer-prompt.md
```

It makes a file and nothing else. No API calls, no tool access, and it will not overwrite an existing file. Read it before you paste it anywhere, and send only data the destination is allowed to see.

## Checking your own copy

Run `python3 scripts/generate.py --check`, `python3 scripts/validate.py`, and `python3 -m unittest discover -s tests -v`. Python 3.11 or later, standard library only. Contributors also run `python3 scripts/gen_wiki.py --check`, which renders `docs/` and fails on a broken link.
