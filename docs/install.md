# Install

## Project install

Preview first, it writes nothing without `--apply`:

```
python3 scripts/install.py --target /path/to/project --adapter claude
python3 scripts/install.py --target /path/to/project --adapter both --apply
```

`--adapter` is `claude`, `copilot` or `both`, and `--pack NAME` (repeatable) limits which packs are installed. The installer is additive: it never overwrites a file it did not generate, it refuses the whole install if any destination exists, it refuses symlinked destinations, and it refuses to install into this repository. To move a project to a newer release, add `--update`: files whose marker is well-formed and whose body still matches the hash in the marker are replaced in place (see [Overlay and ownership](overlay-and-ownership.md)), and anything else at a destination path is still a collision. Copilot gets the agents only; there is no pack loader there, so paste a pack into the brief or use the exporter below.

## Keeping a dotfiles repo current

The project install writes `<project>/.claude/...`. A user-level Claude Code tree (the one a dotfiles repo keeps) has no `.claude` level, so `--layout user` writes `<target>/agents/<role>.md` and `<target>/skills/pack-<name>/SKILL.md` instead. It is still additive, still refuses symlinked destinations and whole-install collisions, still previews by default, and `--update` still replaces only files it can prove it generated (see [Overlay and ownership](overlay-and-ownership.md)). It is Claude only (Copilot has no user layout, and asking for it is refused), and a target inside this repository is refused.

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
