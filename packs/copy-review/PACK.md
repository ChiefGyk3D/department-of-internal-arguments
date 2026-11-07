---
name: "copy-review"
description: "Review checks for published copy, scripts and show notes: claims traced to recorded sources, URL resolution, affiliate/sponsored/merch disclosure checked against the live platform or regulator text, secret scan, techniques tied to an authorized scope, per-platform length table, owner banned-phrase grep, and a pointer to the private voice skill. Pilot pack, no item measured. Load when reviewing a script, post, description or show notes before publish. Not for writing the copy."
updated: "2026-11-07"
---

# Pack: copy review

Status: pilot pack, written 2026-11-07. No item here was measured; the rules are constraints, not findings. The pack reviews copy; it does not write it and it does not decide what the owner's voice is.

## Rules

1. **Every factual or security claim traces to a recorded source.** Keep a claims table: claim, source (URL or document), date read. A claim with no row is flagged. A source read from memory is not a source.
2. **Every URL resolves with at most 2 redirects and lands on the intended domain.** Check with `curl -sIL --max-redirs 2 -o /dev/null -w '%{http_code} %{url_effective}\n' <url>`; a failure, a third redirect, or a final domain other than the intended one is flagged. Use `example.test` in any sample.
3. **Disclosure comes before the first link.** Affiliate, sponsored and merch links are disclosed before the first such link appears. **The disclosure wording and placement rule is ALWAYS checked against the live platform or regulator text, never quoted from memory:** fetch the current page, record its URL and the date read, and compare. If it cannot be fetched, list it under facts to verify and give no clearance.
4. **No live credentials, tokens, real hostnames or addresses** in screenshots, code or commands. Run a secret scan over the scripts and show notes (a scanner such as `gitleaks detect --no-git --source <dir>` if installed, plus `grep -nEi '(token|secret|passw|api[_-]?key)\s*[:=]' <files>`). Screenshots are read by eye for terminal prompts, tabs and address bars. A hit is reported by location and kind, never by echoing the value.
5. **Techniques shown are tied to a lab, an owned target or an authorized scope.** Each demonstrated technique names its target and why that target is in scope. A technique with no stated scope is flagged.
6. **Per-platform length limits live in a table that must be re-verified before use.** Keep the table in a file with a column for the source URL and the date read. A row older than the review, or with no source, is treated as unknown; fetch the platform's current text, then check the copy's length (`wc -m`) against it.
7. **Owner banned-phrase list by grep.** The owner keeps a list (one phrase per line) in a file the brief names. Run `grep -nFi -f <list> <copy>` and report each hit with line number. Filler phrases are flagged, not judged: report the hit and stop; do not rewrite or rule on style.
8. **Voice:** if the copy is public-facing and the owner's voice matters, tell the reviewer to call the private voice skill for first-person drafts. Never restate, summarize or embed its rules or samples in this pack, a report or a brief.

## Deliverable

1. **Claims table** (claim, source, date read, status).
2. **URL table** (URL, status, redirects, final domain).
3. **Disclosure check** (platform text URL, date read, result).
4. **Scan results** (tool, files scanned, hits by location).
5. **Scope check** for each technique shown.
6. **Length check** and **banned-phrase hits**.
7. **Not verified** (a source that could not be fetched, a tool not installed).

## How to verify (before you hand it back)

- The URL check ran on every URL in the copy; the count in the report equals `grep -oE 'https?://[^ )]+' <copy> | sort -u | wc -l`.
- The disclosure row cites a platform or regulator page fetched this session, with its date; no wording was quoted from memory.
- The secret scan lists the files it covered, and the report contains no secret value.
- The banned-phrase grep ran with the owner's current list file, and the report says which file.
- Nothing from the voice skill appears in the output.

## What this pack does not cover

Legal advice, factual research (use the `researcher`), copywriting, and the owner's voice and brand facts, which live in the private voice skill and a private overlay.
