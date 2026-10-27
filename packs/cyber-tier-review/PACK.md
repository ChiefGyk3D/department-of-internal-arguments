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
3. **A review that cannot show its answer is a held review, not a clean one.** No findings section, an empty output, a cutoff or an authentication error all mean HELD. Report it as held with the reason. Never count it as an approval and never write "no findings".
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

Likely causes, in the order to try them (INFERRED / USER-REPORTED, 2026-10-26; no vendor page read says any of this):

1. **The login used a software-held passkey, or an unauthenticated session, instead of the physical key.** Reported 2026-10-26: the vendor tightened the requirement so the key assertion must come from the physical hardware key itself. A passkey held in a software password manager no longer satisfied it; two logins that way (a browser flow and a device-code flow) both ended in this error on real reviews, while the next login done with the physical key worked. Word it as "reported" until your own real review confirms it.
2. **The key assertion expired.** Not tested; reasoned from the error naming the session.
3. **Stale CLI-side processes** (a leftover `codex app-server`, broker or daemon from an older login). Lower likelihood: verified not the cause in one incident. If you do suspect them, list them with `ps`, check each one's command line and parent, and kill them by PID. Never kill by pattern (`pkill -f <text>` can match the caller's own shell), and never touch the vendor's desktop application, which may be doing the user's other work.

Fix: the user logs in again and authenticates with the physical key, then you verify with ONE real review (not a status line). Both the browser and the device-code flows exist; whichever you use, the page you authenticate on must prompt for the key. If it signs you in without asking for the key, it is the wrong credential.

What NOT to do:

- Do not spend tokens re-running a review after each guess. Change one variable, then run one real review.
- Do not theorize before reading this pack and the user's private notes.
- Do not treat a status line or a trivial call as the check, and do not reword the prompt or fall back to the default model.

## Prompt shape that passes

- State what the code is and who trusts whom (for example: "a parser for files from untrusted contributors, run in CI with repository write access").
- Name the files and exactly one defect class per run (for example: path confinement, or duplicate-key handling).
- Ask for a failing input and the observed behavior, not an essay. A finding without an input that fails is SPECULATIVE.
- Avoid attacker-voice exploit lists ("how do I break in", step-by-step abuse). Write it as a defender reading code: what input makes this wrong, and what happens.
- Do not paste secrets, real hostnames or live target details into the prompt.

## Round budget

Parsers and sandbox code usually need 3-4 rounds: each fix is the likeliest place for the next defect, so the last round reviews the fix, not the original. Stop at the budget and hand the remaining findings to the lead; do not loop. If two rounds on one finding class fail, recommend a different model, as the quality-gates policy says.

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
| The hardware-key assertion must come from the physical key; a passkey in a software password manager no longer satisfied it, in two logins (browser and device-code), and a login with the physical key worked | INFERRED / USER-REPORTED 2026-10-26: the user's report, pending confirmation by a real review; no vendor page | none |

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
