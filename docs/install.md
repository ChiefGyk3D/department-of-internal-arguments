# Install

## Project install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

`--adapter` is `claude`, `copilot`, `both` (Claude Code and Copilot) or `codex`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it does not overwrite a file that does not carry a generator marker, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: a file is replaced in place only when the installer's own record says it wrote those exact bytes (see [Ownership state](#ownership-state) below), and anything else at a destination path is left alone or is still a collision. A marker inside a file is not that record: ownership is the installer's own state record, and a clone, synced dotfiles or a copy carries a valid marker too. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

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

HEX is the sha256 of the text between the markers. A missing file is created. In an existing file the block is appended and nothing else in the file is touched. A re-run with the same text changes nothing and needs no `--update`; `--apply` records it in the [ownership state](#ownership-state). Ownership of the block is the installer's own state record, not HEX: the state holds an entry of kind `lead` for the instruction file (its path relative to the target; one state file per target and layout, so the same file in the project and user layouts has separate entries) with the sha256 of the block body. The block is `owned`, and `--update` may replace it, only when that entry agrees and the body matches it. A block whose begin line hashes its own body but has no entry (a CLAUDE.md that came by clone, synced dotfiles or a copy, or a forged block) is `unverified`: `--update` refuses it, the file stays byte-identical, and `--update --force-edited` replaces the block only. A block that differs from its entry, or that you edited, is `edited` and is refused the same way, and `--force-edited` with `--update` discards your edits to the block only, never the text around it. A block with a missing, duplicated or misordered marker is refused even with `--force-edited`, and so is any file that mentions `doia-lead:` in prose, a non-UTF-8 file, a symlink or a file over 1 MiB. `--list-managed --lead FILE` always prints a row for the named file, so it never reads as clean by omission: `current`, `stale` (older release), `differs`, `edited`, `unverified` (a consistent block with no state entry, or no usable state file), `missing` (no file, or no block in it) or `unsafe` (a symlink, not a regular file, over 1 MiB, not UTF-8, or unreadable); anything but `current` exits 3. The block is written with LF line endings, or CRLF when the file already uses CRLF; the hash is taken with LF endings and ignores a missing final newline, so an editor that converts either does not make the block read as edited. Replacing the file (any `--update`, including the lead block) writes a new file and renames it over the old one: it keeps the permission bits, but the new file belongs to the user running the installer, extended attributes and ACLs are not copied, and a hard link to the file stops sharing it. Just before the rename the installer re-reads the file and refuses, writing nothing, if it changed since the new content was built (`file changed while installing; re-run`); a write landing between that read and the rename is not caught, and no lock is taken because editors do not take one. The hash has the same limit as every marker here: it detects accidental edits, not a forger.

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

It writes `<target>/.codex/agents/<role>.toml` (one custom agent per role) and `<target>/.agents/skills/pack-<name>/SKILL.md` (one skill per pack, byte-identical to the Claude Code skill). Ownership markers, `--update`, `--force-edited`, `--overlay`, `--list-managed`, symlink refusal and whole-install collision refusal behave as for the other adapters, including the rule that ownership is the installer's own state record, not the marker, so a file that merely carries a marker is `unverified` and left alone. A target that is the filesystem root is refused for every adapter and layout. In a custom agent file the marker is the first line of `developer_instructions`, because TOML has no place for a Markdown comment outside that string. `--adapter both` keeps its meaning (Claude Code and Copilot); name `codex` to get Codex files. With `--layout user` the target is your home directory (or a tree laid out like it), because Codex keeps user agents in `~/.codex/agents` and user skills in `~/.agents/skills`, two roots with no common parent below home; the relative paths are the same as in the project layout.

What was verified, from OpenAI's own Codex documentation, retrieved 2026-10-26. Nothing below has been run in Codex. The pages first answered at developers.openai.com/codex/... and redirected (HTTP 308, then 200) to the learn.chatgpt.com addresses linked here, which are the addresses the pages use for each other.

- AGENTS.md discovery ([Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)): global `~/.codex/AGENTS.override.md` or `AGENTS.md` (the first non-empty one; `CODEX_HOME` can move the directory), then each directory from the project root down to the working directory (override, then `AGENTS.md`, then `project_doc_fallback_filenames`), at most one file per directory, concatenated root-down; reading stops at `project_doc_max_bytes`, 32 KiB by default. This adapter does not write an `AGENTS.md`: that file is the project's, and a single merged one would not hold five roles and their policies inside 32 KiB.
- Custom agents ([Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)): standalone TOML files in `.codex/agents/` (project) or `~/.codex/agents/` (personal); required keys `name`, `description` and `developer_instructions`; the page lists `model`, `model_reasoning_effort`, `model_reasoning_summary` and `model_verbosity` as the model settings, and says the file can also disable skills through `skills.config` (and bundled skills with `skills.bundled.enabled = false`, the skills instructions block with `skills.include_instructions = false`). Built-in agents are `default`, `worker` and `explorer`. Per-role definitions exist, so the five roles map one to one.
- Sandbox and approvals ([Sandboxing](https://learn.chatgpt.com/docs/sandboxing)): sandbox modes `read-only`, `workspace-write` and `danger-full-access`; approval policies `on-request` and `never`. The subagents page says a custom agent inherits the parent's sandbox and approval settings, that `sandbox_mode` and `approval_policy` in an agent file do not override the parent, and that agent instructions can ask for no edits but do not create a read-only sandbox. So the two reviewer roles, which have no edit capability, can only be told not to edit; their generated text says so.
- Skills ([Build skills](https://learn.chatgpt.com/docs/build-skills)): a directory with `SKILL.md` (`name` and `description` required), read from `$CWD/.agents/skills` up to the repository root, `$HOME/.agents/skills` and `/etc/codex/skills`; an explicit `$skill` mention invokes one.
- Tools ([Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)): the agent file can switch off `features.shell_tool`, `features.apps`, `features.plugins`, `features.memories` and `features.request_permissions_tool`, and individual skills. There is no per-tool allow list that matches `read`, `search` and `edit`, so those capabilities are not expressed in the file; every DoIA role needs a shell, so none of those keys is set.

UNVERIFIED: that Codex loads the generated TOML and the skills without complaint (the TOML parses with the Python standard library and has the three required keys, nothing more); that project-layout agents load at all in an untrusted project: the [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic) page says that for a project marked untrusted Codex skips project-scoped `.codex/` layers, including project-local config, hooks and rules, so agents installed in `.codex/agents` may not load until the project is trusted (documented, not tested; whether agent files count as part of that layer is not stated); where `CODEX_HOME` moves the user agents; the exact skill names Codex shows; whether `$pack-<name>` selects a pack the way the generated text says. The hand-run row for this is exercise 26 in `evals/behavioral-evals.md`. The tier-to-model mapping is not carried over: the agent files pin no model, so the agent inherits the parent's, and the report's `MODEL:` line names what the session reports.

## Keeping a dotfiles repo current

The project install writes `<project>/.claude/...`. A user-level Claude Code tree (the one a dotfiles repo keeps) has no `.claude` level, so `--layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md` instead. It is still additive, still refuses symlinked destinations and whole-install collisions, still previews by default, and `--update` still replaces only files the installer recorded (see [Overlay and ownership](overlay-and-ownership.md)); as above, ownership is the installer's own state record, not the marker, so a file that only carries a marker and a matching hash is left alone. It is for Claude Code and Codex (Copilot has no user layout, and asking for it is refused; the Codex form is described above), and a target inside this repository is refused.

The routine, from a checkout pinned by commit:

```
git -C /path/to/department-of-internal-arguments fetch --tags
git -C /path/to/department-of-internal-arguments checkout <commit>   # <release tag>; the current release is 0.4.0
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update
python3 /path/to/department-of-internal-arguments/scripts/install.py --target ~/dotfiles/claude --layout user --update --apply
git -C ~/dotfiles/claude status   # review the diff, then commit
```

A tag can be moved and a commit cannot, so pin the commit and keep the release tag in the comment, the way the GYST workflow pins do. Read the commit once, from a clone you trust, with `git -C /path/to/department-of-internal-arguments rev-parse <release tag>^{commit}`.

The first install run is the preview and writes nothing.

## Checking drift

To see drift without writing, `--list-managed` prints every file under the target that carries the marker, its release, its source (`public` or `overlay`) and whether it is `current`, `stale` (older release), `differs` (this release, content changed), `orphan` (this checkout no longer produces it) or `unchecked` (overlay-sourced and no `--overlay` was given). It exits 0 when everything is current and 3 otherwise, so a sync step can report drift and decide. When overlay-sourced files exist and `--overlay` was not given, they cannot be checked: they show as `unchecked` and the exit code is 3, so give `--overlay` to the check as well. An `edited` file is one the installer recorded but whose bytes changed since (or whose marker is malformed or no longer matches its body); an `unverified` file has a consistent marker but no record, or there is no usable state file at all, so nothing can be `current`; `legacy-unverified` is a release 0.2.0 file (see [Overlay and ownership](overlay-and-ownership.md)) with a body 0.2.0 never produced. A `state:` line after the rows says which state file was read, or why it could not be.

## Ownership state

The installer keeps a record of what it wrote, outside every install root and this repository: `$DOIA_STATE_DIR` if set (an absolute path), else `${XDG_STATE_HOME:-~/.local/state}/department-of-internal-arguments/`. There is one `0600` file per target and layout. It holds, for each installed path, the sha256 of the whole file as written. A file is **owned**, and so may be updated, only when its bytes match a recorded digest. Anything else is left alone and reported:

- `unverified (refused; --adopt records it, --force-edited replaces it)`: a well-formed marker, but no record. This is what a clone, synced dotfiles or a forged file looks like. `--adopt` arrives with the adopt step (#42); until then `--update --force-edited` replaces the file and records it.
- `edited`: a record exists but the bytes differ (you edited it, or someone replaced it). Put the recorded bytes back by hand and it is owned again. `--force-edited` discards your changes.
- A file without the marker is never touched, with or without `--force-edited`.

A destination whose bytes equal exactly what this release would write is recorded on `--apply` and not rewritten: the content is checked against this checkout, not against the marker. Release 0.2.0 files whose body is one of the known 0.2.0 outputs are still accepted and are recorded after replacement. After a run, entries for files that no longer exist are removed. A dry run and `--list-managed` write nothing, not even the state directory.

`--apply` takes a lock, saves the digests it is about to write as pending, writes each file, and promotes a digest only for a file whose write returned. If the process dies in between, new files stay `unverified` (a digest proves equal content, not who wrote it) and files that were already owned stay owned under both digests; run `--update --apply` again and it converges, or `--force-edited` for a file left half written. A second installer is refused while the lock is held.

If the state file is unreadable, invalid (wrong schema, another target's file, a bad path, a duplicate key), has the wrong owner or mode, is locked, or sits in a forbidden place, the whole run is refused before any write, `--force-edited` included. There is no fallback to marker-only ownership. `--list-managed` still works and reports everything `unverified` with a `state:` line. To recover from a corrupt file, delete it: everything then reads as `unverified`.

**Where the state may live.** Two rules, both enforced, `DOIA_STATE_DIR` included, before the ownership state is read and before any install or state output is written. (The installer may already have read your overlay and this release's sources by then; `--list-managed` has also scanned the target's files. A forbidden location makes an install run refuse and makes `--list-managed` report every marked file as `unverified` with a `state:` line; neither writes.) Directories are compared by identity as well as by name, so a differently cased spelling of the same directory on a case-insensitive filesystem (the macOS default) is the same place and is refused or checked like the original.

1. The state directory may not be, be inside, or contain an *install root*, the directories the installer writes managed files into, because a state file must not travel (clone, sync, backup) together with the files it vouches for. For the project layout the install root is the target. For `--layout user` it is each per-client directory: with `--adapter codex` on your home directory the roots are `~/.codex` and `~/.agents`; with `--adapter claude` the target (for example `~/.claude` or a dotfiles checkout) is the root. A target that is the home directory itself with `--adapter claude` makes the home directory the root, so set `DOIA_STATE_DIR` to a directory elsewhere. It may not be inside this repository either.
2. If the state directory is inside the *target* but not inside a root (for example the default `~/.local/state` when the target is your home directory, or an explicit `<target>/agents/state`), anyone who can write any directory on the way could plant a valid state file and make their own marked files read as owned. So the target, and every directory below it down to the state directory, must be a real directory (no symlink), owned by the effective user, and not group- or world-writable (sticky does not excuse it); the state file itself must be owned by the effective user with mode `0600`. A target that fails this (someone else's, or `0775`/`0777`) never hosts the state: the run is refused with a message naming the directory, and you set `DOIA_STATE_DIR` to a directory outside the target. Your own home directory, mode `0700` or `0755`, keeps working with the default location.

**Upgrading from a release without state.** Files installed before this release have no record. Those whose bytes equal the current release are recorded on the first `--update --apply`. Older ones show as `unverified` and are refused: review the diff, then run `--update --force-edited --apply` once. After that every update is verified. A lead block with no entry is `unverified` in the same way (see [The lead persona](#the-lead-persona)): a block equal to this release's text is recorded on `--apply`, any other needs `--update --force-edited` (or the adopt step, #42).

Out of scope, on purpose: anyone who can write the state file as you or as root, a rollback of the state file, and anyone who controls `HOME` or `DOIA_STATE_DIR`. See the threat model in `scripts/state.py`.

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
