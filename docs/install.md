# Install

## Project install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

`--adapter` is `claude`, `copilot`, `both` (Claude Code and Copilot) or `codex`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it does not overwrite a file that does not carry a generator marker, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: files whose marker is well-formed and whose body still matches the hash in the marker are replaced in place (see [Overlay and ownership](overlay-and-ownership.md)), and anything else at a destination path is still a collision. The check is only as strong as the marker: files that self-identify as generated may be overwritten by `--update`, and the hash is not a signature, so anyone who can write a file can also write a marker and hash that match it. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

## The lead persona

`templates/lead.md` is not an agent file, so the role installs above never load it, and a session that never loaded it is not governed by it. Install it as a marked block in the instruction file the controller session reads at start:

```
python3 scripts/install.py --target /path/to/project --adapter none --lead CLAUDE.md           # preview
python3 scripts/install.py --target /path/to/project --adapter none --lead CLAUDE.md --apply
```

`--lead FILE` is a path under the target (no absolute path, no `..`); use `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, `.github/copilot-instructions.md` for Copilot. `--adapter none` installs only the lead block. With any other adapter `--lead` rides on the role install (same collision rules, same `--update`), and the preview prints a note saying so. The installed text is `templates/lead.md` without its leading comment, between two marker lines:

```
<!-- doia-lead:begin release R sha256:HEX -->
...lead text...
<!-- doia-lead:end -->
```

HEX is the sha256 of the text between the markers. A missing file is created. In an existing file the block is appended and nothing else in the file is touched. A re-run with the same text changes nothing and needs no `--update`. `--update` replaces a block whose hash still matches; a block you edited is refused, and `--force-edited` with `--update` discards your edits to the block only, never the text around it. A block with a missing, duplicated or misordered marker is refused even with `--force-edited`, and so is any file that mentions `doia-lead:` in prose, a non-UTF-8 file, a symlink or a file over 1 MiB. `--list-managed --lead FILE` always prints a row for the named file, so it never reads as clean by omission: `current`, `stale` (older release), `differs`, `edited`, `missing` (no file, or no block in it) or `unsafe` (a symlink, not a regular file, over 1 MiB, not UTF-8, or unreadable); anything but `current` exits 3. The block is written with LF line endings, or CRLF when the file already uses CRLF; the hash is taken with LF endings and ignores a missing final newline, so an editor that converts either does not make the block read as edited. Replacing the file (any `--update`, including the lead block) writes a new file and renames it over the old one: it keeps the permission bits, but the new file belongs to the user running the installer, extended attributes and ACLs are not copied, and a hard link to the file stops sharing it. Just before the rename the installer re-reads the file and refuses, writing nothing, if it changed since the new content was built (`file changed while installing; re-run`); a write landing between that read and the rename is not caught, and no lock is taken because editors do not take one. The hash has the same limit as every marker here: it detects accidental edits, not a forger.

Check that it loaded: start a new session in that project and ask it to state its rule about waiting, or the first thing the lead never does. A session that cannot has not loaded the text, whatever the file says.

Where each host reads it, and what is verified:

- Claude Code: `CLAUDE.md` in the project and `CLAUDE.md` in the user's Claude directory are read into the session's instructions at start. Observed in the session that produced this change (both files were present in its context); the documentation page was not re-read for this change, and how a subagent inherits them was not tested.
- Codex: `AGENTS.md` discovery is documented in the Codex section below (32 KiB combined by default; the lead block is about 6 KB). UNVERIFIED: nothing was run in Codex.
- Copilot: `.github/copilot-instructions.md` is the file `templates/copilot-instructions.md` is meant for. UNVERIFIED: not run in Copilot, and a Copilot session may not be able to act as a controller.
- ChatGPT or a local model: paste the block into the project or system instructions by hand. UNVERIFIED: no installer path, nothing run.

## Reading the preview: it is the control

The preview prints, for every file it would write, the action, the path, whether the content comes from this checkout (`public`) or a private `--overlay` (`overlay`), and the sha256 of each file's exact bytes. Personas and packs are installed as model instructions (for Codex, skills and custom agents), and nothing but structure checks stands between a pack and the model, so treat a pack like code: read the preview, and for an overlay pack read the pack itself. Overlay packs and overlay additions are unreviewed unless you review them. To pin a reviewed pack, record its digest from the preview and compare it on the next run. Public files are covered by this repository's review and CI; the digest lets you confirm the installer is about to write exactly the bytes you reviewed.

An overlay is fully trusted policy authority over model behavior, for every adapter (Claude Code, Copilot and Codex alike). The append-only rules for public names guard against accidental structural damage, not against a hostile overlay: an addition can still say to ignore every instruction above it, and the validators check Markdown structure, not what the words mean. See [Overlay and ownership](overlay-and-ownership.md) for who must not write to an overlay directory.

## Codex

`--adapter codex` writes Codex custom agents and skills from the same roles, packs, policies and report header:

```
python3 scripts/install.py --target /path/to/project --adapter codex
python3 scripts/install.py --target /path/to/project --adapter codex --apply
```

It writes `<target>/.codex/agents/<role>.toml` (one custom agent per role) and `<target>/.agents/skills/pack-<name>/SKILL.md` (one skill per pack, byte-identical to the Claude Code skill). Ownership markers, `--update`, `--force-edited`, `--overlay`, `--list-managed`, symlink refusal and whole-install collision refusal behave as for the other adapters, including the limit that files which self-identify as generated may be overwritten by `--update`. A target that is the filesystem root is refused for every adapter and layout. In a custom agent file the marker is the first line of `developer_instructions`, because TOML has no place for a Markdown comment outside that string. `--adapter both` keeps its meaning (Claude Code and Copilot); name `codex` to get Codex files. With `--layout user` the target is your home directory (or a tree laid out like it), because Codex keeps user agents in `~/.codex/agents` and user skills in `~/.agents/skills`, two roots with no common parent below home; the relative paths are the same as in the project layout.

What was verified, from OpenAI's own Codex documentation, retrieved 2026-10-26. Nothing below has been run in Codex. The pages first answered at developers.openai.com/codex/... and redirected (HTTP 308, then 200) to the learn.chatgpt.com addresses linked here, which are the addresses the pages use for each other.

- AGENTS.md discovery ([Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)): global `~/.codex/AGENTS.override.md` or `AGENTS.md` (the first non-empty one; `CODEX_HOME` can move the directory), then each directory from the project root down to the working directory (override, then `AGENTS.md`, then `project_doc_fallback_filenames`), at most one file per directory, concatenated root-down; reading stops at `project_doc_max_bytes`, 32 KiB by default. This adapter does not write an `AGENTS.md`: that file is the project's, and a single merged one would not hold four roles and their policies inside 32 KiB.
- Custom agents ([Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)): standalone TOML files in `.codex/agents/` (project) or `~/.codex/agents/` (personal); required keys `name`, `description` and `developer_instructions`; the page lists `model`, `model_reasoning_effort`, `model_reasoning_summary` and `model_verbosity` as the model settings, and says the file can also disable skills through `skills.config` (and bundled skills with `skills.bundled.enabled = false`, the skills instructions block with `skills.include_instructions = false`). Built-in agents are `default`, `worker` and `explorer`. Per-role definitions exist, so the four roles map one to one.
- Sandbox and approvals ([Sandboxing](https://learn.chatgpt.com/docs/sandboxing)): sandbox modes `read-only`, `workspace-write` and `danger-full-access`; approval policies `on-request` and `never`. The subagents page says a custom agent inherits the parent's sandbox and approval settings, that `sandbox_mode` and `approval_policy` in an agent file do not override the parent, and that agent instructions can ask for no edits but do not create a read-only sandbox. So the two reviewer roles, which have no edit capability, can only be told not to edit; their generated text says so.
- Skills ([Build skills](https://learn.chatgpt.com/docs/build-skills)): a directory with `SKILL.md` (`name` and `description` required), read from `$CWD/.agents/skills` up to the repository root, `$HOME/.agents/skills` and `/etc/codex/skills`; an explicit `$skill` mention invokes one.
- Tools ([Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)): the agent file can switch off `features.shell_tool`, `features.apps`, `features.plugins`, `features.memories` and `features.request_permissions_tool`, and individual skills. There is no per-tool allow list that matches `read`, `search` and `edit`, so those capabilities are not expressed in the file; every DoIA role needs a shell, so none of those keys is set.

UNVERIFIED: that Codex loads the generated TOML and the skills without complaint (the TOML parses with the Python standard library and has the three required keys, nothing more); that project-layout agents load at all in an untrusted project: the [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic) page says that for a project marked untrusted Codex skips project-scoped `.codex/` layers, including project-local config, hooks and rules, so agents installed in `.codex/agents` may not load until the project is trusted (documented, not tested; whether agent files count as part of that layer is not stated); where `CODEX_HOME` moves the user agents; the exact skill names Codex shows; whether `$pack-<name>` selects a pack the way the generated text says. The hand-run row for this is exercise 26 in `evals/behavioral-evals.md`. The tier-to-model mapping is not carried over: the agent files pin no model, so the agent inherits the parent's, and the report's `MODEL:` line names what the session reports.

## Keeping a dotfiles repo current

The project install writes `<project>/.claude/...`. A user-level Claude Code tree (the one a dotfiles repo keeps) has no `.claude` level, so `--layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md` instead. It is still additive, still refuses symlinked destinations and whole-install collisions, still previews by default, and `--update` still replaces only files that carry a well-formed marker and a matching hash (see [Overlay and ownership](overlay-and-ownership.md)); as above, files that self-identify as generated may be overwritten by `--update`, because the hash is not a signature. It is for Claude Code and Codex (Copilot has no user layout, and asking for it is refused; the Codex form is described above), and a target inside this repository is refused.

The routine, from a checkout pinned to a release tag:

```
git -C /path/to/department-of-internal-arguments fetch --tags
git -C /path/to/department-of-internal-arguments checkout <tag>   # <tag> is a placeholder for the release tag you pin; the current release is 0.4.0
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
