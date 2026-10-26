# Overlay and ownership

## Ownership: the marker alone proves nothing

The marker line carries a sha256 of the file with that one line removed. `--update` treats a file as installer-owned only when the marker is well-formed (exactly one, strictly parsed) and the body still hashes to the value in it. A hand-edited file, or one that merely contains the marker text, is `edited` and refused; the whole install stops before writing anything. The preview labels every file `new`, `update`, `unchanged` or `edited (refused; --force-edited discards your changes)`. `--update --force-edited` replaces edited files and discards your local changes (it never touches a file with no marker at all, and never a symlink). Editing only the release in the marker does not count as an edit, so a stale copy still updates. The hash binds content to the marker; it is not a signature, so someone who can write the file can write a matching hash. Review the preview and the diff in the dotfiles repo, which is the real control.

This marker format is new in release 0.3.0. Files written by release 0.2.0 have no hash. They are recognized as legacy-owned only when their body is byte for byte one of the 14 outputs 0.2.0 generated (the installer carries those 14 hashes); a hashless marker on any other body is `legacy (unverified)` and needs `--force-edited`.

## Overlay: personas too sensitive for this public repo

`--overlay DIR` points at a private directory with the same shape as the sources here: `roles/<name>.md` and `packs/<name>/PACK.md`, read by the same parsers as the public ones. A **new name** is installed as a whole persona or pack and is held to the same structural rules as a public one (tier, tools, report header, packs need a `## How to verify` section); both kinds carry the generator marker, which for overlay files says `overlay` and the file's own name and never a path.

```
python3 scripts/install.py --target ~/dotfiles/claude --layout user --overlay ~/private-personas --update --apply
```

The overlay is private by definition, so the public-repo privacy sweep is the one rule that is not applied to it. It lives outside this repository and cannot be written into it: an overlay or a target inside this repository is refused, and so is any destination that resolves inside it. The overlay is read with the same confinement as everything else (no symlinks on the path, regular files only, size and entry-count caps, owned by you, not group- or world-writable) and anything wrong refuses the whole install before a byte is written. Under a umask of 0002 files are created group-writable (0664) and are refused; the error names the fix, `chmod -R g-w,o-w DIR`, and you run it once on the overlay directory. Edit capability stays limited to the `implementer` and `transcriber` names, as in the public tree. The maintainer may later move those private personas to their own private repository; the flag takes any directory, so nothing here changes when that happens.

## A name that is also public is append-only

An overlay `roles/reviewer.md` (or `packs/threat-model/PACK.md`) does not replace the public file. Its frontmatter may hold only `name:` (tools, tier, model and description stay the public ones; any other key is a validation error), and its body is installed as additions: the public generated file, unchanged and first, followed by a section the generator writes, `## Maintainer additions (private overlay)`, containing your text. The public part is byte for byte what the public install would write, so a comment or code fence in your additions cannot remove a public rule. The additions are held to a few rules: at most 16 KiB, no generator marker text, no fragment markers, no raw HTML (any `<` followed by a letter, `/`, `!` or `?`, which also refuses `<https://...>` autolinks, so write plain URLs or an ordinary Markdown link; `<` before a space, digit or `=` is fine), no character reference for `#` (`&#35;`, `&#x23;`, `&num;`; CommonMark renders these as literal text, not headings, but they disguise a heading to a model reading the raw text), no heading of level 1 or 2 and no underlined headings, and no heading that repeats a public section name (`## Hard rules`, `## Process`, `### Security boundaries` and so on) or the additions heading.

**What remains:** prose in the additions can still ask for something at odds with the public rules. The public rules come first and the section is labeled as an extension, but a model may weigh both, so read the overlay before installing it. For stronger separation use a distinct persona name, which is then a whole persona held to the usual rules.

## File modes

Overlay-sourced files (additions to a public persona, and whole overlay personas or packs) are created `0600` regardless of umask, so a dotfiles checkout on a shared machine does not expose them. Public files are created with the default mode (`0644` under umask 022). An `--update` never widens a destination: the temporary file is created `0600`, and before the rename it gets the existing file's mode (a `0600` file stays `0600`, a `0640` file stays `0640`); when the new content carries overlay text the mode is narrowed to owner-only. Directories the installer creates use the umask default.

See [Install](install.md) for the commands that use these rules.
