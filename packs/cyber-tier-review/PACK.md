---
name: "cyber-tier-review"
description: "Practice for security and adversarial reviews that run on a vendor's high-trust cyber-access tier (OpenAI Codex CLI is the worked example): use the tier model, never the default; a 'flagged for possible cybersecurity risk' cutoff means the wrong tier, so fix the tier and rerun, never reword the prompt and never skip the review; CLI flags by version with VERIFIED or INFERRED labels; the hardware-key error that arrives after the model has run; single-flight runs, the prompt shape that passes, a round budget, and a review that cannot show its answer is held, not clean. Tier, model and flag values live in the user's private config, not in this pack. Load when a brief asks for a security or adversarial review on a cyber-access tier, or when such a run returns a cutoff, an empty review or an authentication error. Not for ordinary code review, and not a way to get past a safety filter."
updated: "2026-10-26"
---

# Pack: reviewing on the cyber-access tier

Status: pilot pack, written 2026-10-26 from a maintainer's field notes, rewritten as generic guidance. Every CLI or vendor claim carries a label in the evidence table: VERIFIED names the page or command and the date it was read (2026-10-26); INFERRED means observed in use or reasoned from a verified fact, not confirmed by a vendor page. Vendor behavior changes, so re-read the pages before relying on a flag.

A high-trust cyber-access tier is a reviewed access program, not a model name: the vendor approves a person or organization for it, and the approval applies to that identity, workspace or project, model and product surface. This pack covers how to run a review on such a tier without losing hours to the failure modes below. It does not help anyone get around a safety control, and it grants no access.

## Where the values live

Persona text (the adversarial reviewer, the lead) says only "the high-trust tier". The tier name, the model id and the access-program flag value are the user's own and stay in the user's private config: a Codex `config.toml` or profile, a shell alias or wrapper script in their dotfiles, or a private overlay addition (see the overlay page). A brief says "run on the cyber-access tier per the user's config"; it does not paste values, and a report does not print account or enrollment details. In this pack, `<model>` and `<tier>` are placeholders for those values.

## Rules

1. **Security and adversarial reviews use the high-trust tier model, never the default.** The default model carries the standard cybersecurity safeguards, and a defensive review of exploit-shaped code can trip them.
2. **The wrong-tier symptom** is a cutoff saying the request was flagged for possible cybersecurity risk (INFERRED wording; see the table). The rule: **fix the tier and rerun. Never reword the prompt to get past the filter, and never skip the review.** Rewording to evade a safeguard is the behavior the safeguard exists to stop; a skipped review is a hole in the gate. If the tier is correct and a safeguard still blocks a legitimate request, stop, report it with the exact text and the time, and let the user use the vendor's feedback or appeal route.
3. **A review that cannot show its answer is a held review, not a clean one.** No findings section, an empty output, a cutoff or an authentication error all mean HELD. Report it as held with the reason. Never count it as an approval and never write "no findings". A stop caused only by a failed scan tool (the tool did not run, crashed or returned nothing) is also HELD, not clean.
4. **Single-flight.** One cyber-tier review at a time per user, guarded by a lock, so two runs do not share a session, race on authentication state or burn tokens twice. Example: `flock -n "$SCRATCH/cyber-review.lock" <command>`; if the lock is held, wait or report, do not start a second run.
5. **Least privilege.** Run the review read-only (`-s read-only`), against a scratch copy of the code, with no credentials in the environment. The vendor's recommended configuration page asks for isolation, narrow permissions and human oversight for higher-risk work.

## Running a review (command shape)

```
flock -n "$SCRATCH/cyber-review.lock" \
  codex exec --skip-git-repo-check -s read-only -m <model> --cyber-access-program <tier> \
  "<prompt>" < /dev/null > "$SCRATCH/review.txt" 2>&1; echo "review exit=$?"
```

- `< /dev/null`: a `codex exec` started in the background otherwise waits on stdin and returns an empty review. Always give it a closed stdin and judge the output, not the exit code.
- `--skip-git-repo-check`: needed when the review directory is not a Git repository (a scratch copy often is not).
- The flag is `--cyber-access-program`, from codex-cli 0.164.0. With codex-cli 0.160.1 the config key `access_programs.cyber` was ignored, so a run set up that way silently used the default tier. Check the version first: `codex --version`.
- The flag is not supported with `codex exec review`; send the review as a prompt to plain `codex exec`.
- The tier value follows the model, not your approval level. The vendor's API page says a model that takes one program value rejects another with `invalid_access_program`; read the error and use the value it names.

## The hardware-key error

Text, as observed: "You're approved for `<tier>`, but this response cannot be shown as this session was not authenticated with one of your authorized hardware keys. Please re-authenticate with your hardware key and try your request again."

- It appears AFTER the model has run and spent tokens, as the last thing in the output, with no findings section. A run that ends this way produced an answer you cannot see.
- `codex login status` printing "Logged in using ChatGPT", and a trivial call that returns text, do NOT prove the check passes. Only a real review that ends in a findings section does.
- **A runner that sees this error reports the exact error text and the time to the lead in that turn, and STOPS.** No retry loop, no rewording, no switch to the default model.
- It can appear after weeks of clean runs: a long clean history does not show the next run will pass.

Causes, in the order to try them (cause 1 MEASURED in use 2026-10-26; the others are reasoned; no vendor page read says any of this):

1. **The login used a software-held passkey, or an unauthenticated session, instead of the physical key.** Measured 2026-10-26 and confirmed by the maintainer: the vendor tightened authentication so a cyber-tier session needs the physical hardware security key, not a passkey held in a software password manager. A login made with a software-held passkey passed `login status` and trivial calls, but the real review failed at the end, after tokens were spent, with the error above ("not authenticated with one of your authorized hardware keys"). Two such logins (a browser flow and a device-code flow) both ended that way; a re-login with the physical key fixed it. That is what was observed; the vendor's reason for the change is not documented on any page read.
2. **The key assertion expired.** Not tested; reasoned from the error naming the session.
3. **Stale CLI-side processes** (a leftover `codex app-server`, broker or daemon from an older login). Lower likelihood: verified not the cause in one incident. If you do suspect them, list them with `ps`, check each one's command line and parent, and kill them by PID. Never kill by pattern (`pkill -f <text>` can match the caller's own shell), and never touch the vendor's desktop application, which may be doing the user's other work.

Fix: the user logs in again and authenticates with the physical key, then you verify with ONE real review (not a status line). Both the browser and the device-code flows exist; whichever you use, the page you authenticate on must prompt for the key. If it signs you in without asking for the key, it is the wrong credential.

What NOT to do:

- Do not spend tokens re-running a review after each guess. Change one variable, then run one real review.
- Do not theorize before reading this pack and the user's private notes.
- Do not treat a status line or a trivial call as the check, and do not reword the prompt or fall back to the default model.

## Security plugin interaction

One measured observation (2026-10-26), INFERRED, not found on a vendor page: when a security-review plugin is installed for the CLI, it can capture a security-review prompt and turn it into a "managed scan". In the Codex case that scan needed the CLI's config directory (`~/.codex`) to not be group-writable, and the failure looked like a permissions error, not a review result. The fix that worked was making the directory mode 0755.

- Record the previous mode first (`stat -c '%a' ~/.codex`; it was 0775 here), so you can restore it with `chmod 775 ~/.codex`.
- Change one variable, then run one real review. A permissions error is HELD, not a clean review.
- Verify against your vendor's current docs before relying on this; vendor behavior changes.

## Prompt shape that passes

- State what the code is and who trusts whom (for example: "a parser for files from untrusted contributors, run in CI with repository write access").
- Name the files and the base and head commits. The class list, the round-one sweep and the rest of the prompt shape are in the `review-protocol` pack; its sweep rule replaces any one-defect-class limit. Load `review-protocol` before writing the prompt.
- Ask for a failing input and the observed behavior, not an essay. A finding without an input that fails is SPECULATIVE.
- Avoid attacker-voice exploit lists ("how do I break in", step-by-step abuse). Write it as a defender reading code: what input makes this wrong, and what happens.
- Do not paste secrets, real hostnames or live target details into the prompt.

## Round budget

The round budget, the stop rule, the base-severity rule (Critical and High are never accepted) and the evidence handoff are in the `review-protocol` pack; load it and follow it, and do not restate its numbers here. Nothing there loosens the rules above: the tier is fixed, never the prompt, and a held review is never a clean one.

## Clean export and next-round prompt

`scripts/review_export.py` in the department repository (standard library only) prepares the scratch copy and the round prompt. It names no tier, model or flag; those stay in your private config.

```
python3 scripts/review_export.py export --repo <repo> --ref <head> --base <base> --out <new-empty-dir> [--path <p>]...
python3 scripts/review_export.py next-prompt --base-prompt <prompt.txt> --findings <findings.txt> --output <next.txt>
```

- `export` writes the tree of the ref (read with `git ls-tree`, so `.gitattributes` rules such as export-ignore and export-subst do not apply) into a new or empty directory and writes `CHANGE.diff` against the base (`--path` limits it). It prints the file count and bytes. How it decides what to keep:
  1. Strip list (the clean case): agent and instruction files (`.codex`, `.agents`, `.claude`, `.mcp.json`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` and similar) are removed, matched by NFC and case-folded key; `--strip` replaces the list, `--add-strip` extends it.
  2. Allowlist of path shapes (the design rule): every kept path must match a strict shape, and the whole export is refused on any path outside it, so no list of bad names has to be complete. A component is ASCII letters, digits and `._-+@` only (`+` for names like `c++`, `@` for scoped package directories); it must not end in a dot, contain `~` (8.3 aliases such as `CLAUDE~1.MD`) or `:` (streams), be a Windows device name (`CON`, `NUL`, `COM1`...), or start with a dot unless the name is on the dot allow list (`.github`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.vscode`, `.idea`; `--allow-dot NAME` adds one). Caps: 100 characters per component, 240 per path, 16 deep. Non-ASCII names are refused unless `--allow-nonascii-paths` is given, and then only accented Latin is kept: the name must be NFC and every non-ASCII character must be a precomposed letter that decomposes to one ASCII letter plus at most two combining marks from U+0300 to U+036F. Han, kana, Hangul, Cyrillic, Greek and every other script are refused, as are variation selectors, other combining marks, invisible, format, private-use and unassigned code points; repositories with CJK-named paths use `--exclude-tree` for those paths or rename them (CJK is deliberately not offered). As defense in depth a kept non-ASCII path is also refused when its skeleton (NFKD, marks dropped, casefold) is, or reduces to, a stripped agent file name.
  3. On-disk backstop: after writing, the names actually on disk are checked again (NFC and case-folded strip match, plus an ASCII fold that drops every non-alphanumeric character: `A-G-E-N-T-S.md` and fullwidth `AGENTS.md` reduce to `agentsmd` and are refused; a plain name such as an `agents/` directory is not). The set of files on disk must equal the plan. Any hit rolls the whole output back.
  Symlinks are refused by default; `--allow-symlinks` keeps them. Under it the link name goes through the same decisions as any path, and the link target text is resolved lexically against the link's directory and every prefix of the result is checked against the strip list, the excluded trees, the disguise checks and the shape allowlist (a dangling target included); a target that is absolute, climbs out, or passes through another link is refused, and real-path verification still runs.
  Writing and publishing: the export is built in a hidden staging directory in the same parent as the requested output (random name `.NAME.staging-<hex>`, created with `mkdir` through the pinned parent descriptor, mode 0700), not under the requested name. Everything is created relative to the staging directory's open descriptor, one component at a time, with `O_NOFOLLOW` and `O_EXCL`; files are mode 0600 (0700 if executable) and directories 0700 until the end. Every created entry is recorded with its device and inode. When all files are written the link verification and the on-disk backstop run, then every file is re-opened and checked to be the inode that was created with `st_nlink == 1` (a hard link made elsewhere refuses), final modes are applied (files 0644 or 0755, directories 0755), and the completion marker `.EXPORT-COMPLETE.json` is written as the last file (counts, a sha256 of the sorted file list, the head and base commits; the printed manifest counts exclude the marker). What is created outside the requested directory: exactly one directory, the staging directory, directly in the existing parent; the parent itself is never created (a missing parent is refused), and nothing else is touched there. The last step is one atomic `rename` of the staging directory to the requested name, which must not exist or must be an empty directory (an empty directory is replaced by the rename; a non-empty one, a file or a link is refused, and the check is repeated just before the rename). A reader treats a directory without the marker as partial, and only the requested name is a deliverable: a staging directory is never one, even when it already holds the marker (a kill between the marker and the rename leaves exactly that). If the process is killed (SIGKILL, crash, power loss), the requested name is untouched and only the hidden staging directory remains; remove it by hand. Entries are fsynced only for the marker and the directory, so protection against power loss ordering is not claimed, only against process death. On a failure inside the process, rollback removes only recorded entries whose identity still matches, with `unlink` and `rmdir` relative to the descriptor and no link followed (never by path, never `rmtree`); an entry that was replaced, planted or sits under a substituted directory is left, and the message says so and names the staging directory. The tree listing is streamed and bounded while it is read (at most 4 times `--max-files` entries, kept or dropped, and one record under 8 KiB; git is killed on excess), the file-count and total-byte caps also apply as entries are classified, and every git call has an output cap and a timeout (300 s) with stderr discarded. Blobs are read one at a time; a file-count cap (`--max-files`, 20000), a total-bytes cap (`--max-total-bytes`, 256 MiB, counting `CHANGE.diff`) and the per-file cap (`--max-file-bytes`) are checked from the tree listing before any blob is read, and the diff is read in chunks and git is stopped at the cap. `next-prompt` reads its two input files without following links, only if they are regular files (a FIFO or device is refused, never opened for reading), at most 4 MiB each, and keeps line endings byte for byte (CRLF stays CRLF). Modes are built from `stat` constants. Every path, link target and path-like argument in a message is ASCII-escaped and truncated.
  `--exclude-tree PATH` (repeatable) drops a literal tree prefix (a whole component path, no patterns) from the export and from `CHANGE.diff`; it is not shape-checked, so use it for unrelated subtrees you do not want reviewed. Dot names the allow list does not know are refused, not guessed; add them with `--allow-dot`. Examples of commands only, not defaults: `--allow-dot .gitleaksignore --allow-dot .githooks --allow-dot .pre-commit-config.yaml --allow-dot .dockerignore --allow-dot .semgrepignore --allow-dot .python-version`, and `.editorconfig` and `.gitattributes` are already on the list. Or drop the subtree instead: `--exclude-tree .githooks`.
  Residuals, stated plainly: content-level instructions inside normal files (a README that tells the reviewer what to do) are out of scope and stay in the export. An accented Latin name that merely looks like an agent file name to a reader is only caught by the strip list and the skeleton check, which are not a visual-similarity proof. The backstop also refuses harmless names that merely reduce to an agent file name (for example `mcp.json`, `agents_md`). A hard link to a created file by another process of the same user in the instant between its creation and the link-count check is outside the threat model (the hostile party is the repository, not a same-user local attacker): the private 0700 staging directory stops other users, and the link-count check refuses a link that exists at the end, but a same-user attacker can still have written through a link they made. The checks assume Linux (link verification resolves the pinned directory through /proc; on a case-folding or normalizing file system the file-set comparison refuses rather than guesses). An attacker who renames entries in the output path's parent can make the export fail or leave files, but cannot make it write to or delete from another directory. A very large strip or exclude set is passed as argv pathspecs and could hit ARG_MAX, which refuses.
- `next-prompt` copies the base prompt unchanged, appends a fixed header and the previous findings as data, and carries the held rule above. It refuses an empty findings file and never overwrites its output.

## Evidence table

| Claim | Label | Source |
| --- | --- | --- |
| `codex exec` takes `-m/--model`, `-s/--sandbox` (read-only, workspace-write, danger-full-access) and `--skip-git-repo-check` | VERIFIED 2026-10-26 | https://developers.openai.com/codex/cli/reference and https://developers.openai.com/codex/noninteractive |
| `codex exec` runs in a read-only sandbox by default; `-s read-only` states it explicitly | VERIFIED 2026-10-26 | https://developers.openai.com/codex/noninteractive |
| `codex exec --cyber-access-program <tier>` exists in codex-cli 0.164.0; values listed by its own help: standard, daybreak_blue, daybreak_red | VERIFIED 2026-10-26 by `codex exec --help` on 0.164.0; the vendor CLI reference page did not list it that day | local help output |
| The flag is not supported with `codex exec review` | VERIFIED 2026-10-26 | `codex exec --help` on 0.164.0 |
| With codex-cli 0.160.1 the config key `access_programs.cyber` was ignored | INFERRED: observed in use by the maintainer, 0.160.1 not re-run | none |
| Tier access is per identity, workspace or project, model and surface; Blue approval does not grant Red | VERIFIED 2026-10-26 | https://developers.openai.com/codex/cyber-safety |
| The Responses API takes `access_programs.cyber` (standard, daybreak_blue, daybreak_red); the value must match the model or the call fails with `invalid_access_program`; that is the API field, separate from the CLI flag | VERIFIED 2026-10-26 | https://developers.openai.com/api/docs/guides/daybreak |
| Legitimate security work can still trigger a safeguard; the vendor says to inspect the client notice and report false positives through feedback | VERIFIED 2026-10-26 | https://developers.openai.com/codex/cyber-safety |
| The wrong-tier cutoff reads as "flagged for possible cybersecurity risk" | INFERRED: observed wording, not found on any vendor page read | none |
| The hardware-key error text and its timing (after the model ran, last in the output, no findings) | INFERRED: observed wording and timing, not found on any vendor page read | none |
| `codex login status` and a trivial call do not prove the hardware-key check passes | INFERRED: observed in use | none |
| A backgrounded `codex exec` without `< /dev/null` waits on stdin and returns an empty review | INFERRED: help says that with no prompt, or a piped stdin plus a prompt, stdin is read (VERIFIED in `codex exec --help` on 0.164.0); the hang and empty result were observed | local help output |
| Stale CLI-side processes can hold a bad session (lower likelihood; verified not the cause in one incident) | INFERRED: observed in use | none |
| A security-review plugin can turn a security prompt into a managed scan that fails with a permissions error while the CLI config directory is group-writable; mode 0755 fixed it (previous mode 0775 recorded first) | INFERRED: one observation 2026-10-26, no vendor page; verify against current vendor docs | none |
| The hardware-key assertion must come from the physical key; a passkey in a software password manager no longer satisfied it, in two logins (browser and device-code), and a login with the physical key worked | INFERRED: measured in use 2026-10-26 and confirmed by the maintainer (re-login with the physical key fixed it); no vendor page | none |

## How to verify (commands)

- CLI version and flag present: `codex --version` and `codex exec --help | grep -n -A3 'cyber-access-program'` (expect the flag and its allowed values on 0.164.0 or later).
- Vendor pages unchanged since this pack: fetch the URLs in the table and compare against the labels; update the labels and the `updated:` date when they differ.
- The review actually answered: the output file ends in a findings section (a findings heading, or an explicit statement of no findings with the list of what was attacked). `grep -n -i 'findings\|verdict' "$SCRATCH/review.txt"` returns a line; if not, the review is HELD.
- No hardware-key error: `grep -n -i 'hardware key' "$SCRATCH/review.txt"` returns nothing. If it matches, stop and report.
- No cutoff: `grep -n -i 'cybersecurity risk' "$SCRATCH/review.txt"` returns nothing. If it matches, the tier is wrong: fix it and rerun with the same prompt.
- Single-flight held: only one lock holder at a time (`flock -n` returns non-zero for the second run).
- Stale processes are named by PID before they are killed: `ps -eo pid,ppid,etime,args | grep -i '[c]odex'`, then `kill <pid>` for each PID you have checked.
- No private value in a report or brief: run the placeholder sweep from the security-boundaries policy and read the text for account names, hostnames and enrollment details.

## Deliverable

1. The command shape used, with `<model>` and `<tier>` as placeholders, and the CLI version.
2. The exact end of the output: the findings section, or the exact error text and the time, with the review marked HELD.
3. Findings, each with a failing input and the observed behavior.
4. Rounds used against the budget, and what was not covered.
5. The `PACKS LOADED` line naming this pack.
