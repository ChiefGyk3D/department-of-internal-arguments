# Behavioral-eval result schema

One JSON object per line. `python3 scripts/evals_matrix.py RESULTS.jsonl` validates every line and prints the matrix of case by client and model; one bad line fails the whole run (exit 1, nothing printed) rather than being skipped. The cases are in [cases.json](cases.json) and the generated table is [behavioral-evals.md](behavioral-evals.md). No model is called and nothing touches the network: the renderer only reads files. The real results file is private until reviewed; [example-results.jsonl](example-results.jsonl) is synthetic, and its `persona_hash` is a placeholder, so it renders as stale.

Reading is strict. A missing, extra, duplicated, `null` or mistyped key or value is an error, and so are `NaN` and a bool where an integer belongs.

| Field | Type | Meaning |
| --- | --- | --- |
| `case` | integer | An `id` in `cases.json`; the case must apply to the client |
| `client` | `claude-code` \| `codex` \| `copilot` \| `local` | Where the case ran |
| `client_version` | string | Exact version, at least one digit, for example `2.1.4`; `unspecified` only for `unsupported` and `not-run` |
| `model` | string | Exact model id in a documented shape for the client (next section); `unspecified` only for `unsupported` and `not-run` |
| `quantization` | string | `local` only, comes with `context` (both or neither), required for any measured result, for example `Q4_K_M` |
| `context` | integer | `local` only, the context limit in tokens the model was loaded with |
| `tools_enabled` | list | Unique names from `read`, `search`, `edit`, `shell`, `skills`; an empty list means tools were off |
| `commit` | string | The 40-character hash of this repository the persona text came from |
| `persona_hash` | string | 64 hex characters from `python3 scripts/evals_matrix.py --persona-hash` |
| `date` | `YYYY-MM-DD` | A real calendar day, not later than tomorrow in UTC (a future date would always win "latest") |
| `outcome` | enum | `pass`, `fail`, `needs-human`, `unsupported`, `refused`, `error`, `not-run` |
| `evidence` | string, 200 characters max | A short pointer, not a transcript: letters, digits and `. _ : / # = , ( ) + -` and spaces only; no `://`, no `..`, no word starting with `/` |
| `cost` | integer, optional | Tokens used, non-negative |

## Exact model ids

A digit does not make a family name exact. The code accepts only these shapes (anything else is refused, including `sonnet-4`, `claude-3`, `claude-sonnet-4`, `latest-2` and `inherit1`):

- `claude-code`: `claude-<opus|sonnet|haiku|fable|mythos>-<major>-<minor>` with an optional `-<yyyymmdd>`, `claude-<opus|sonnet|haiku|fable|mythos>-<major>-<yyyymmdd>`, or the older `claude-<major>[-<minor>]-<opus|sonnet|haiku>-<yyyymmdd>`. Examples: `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-4-1-20250805`.
- `codex`: `gpt-<major>[.<minor>][letter]` or `o<digits>`, then optional lowercase `-segments`, none of them `latest`, `default`, `inherit` or `auto`. Examples: `gpt-5`, `gpt-5.4`, `gpt-5.4-codex`, `gpt-4o`, `o4-mini`.
- `copilot`: either of the two shapes above (recorded by hand; see below).
- `local`: an exact Ollama tag with a size such as `gemma4:12b` or `qwen3.5:4b`, under the rules in [log-schema.md](log-schema.md); a tag of `latest` is never allowed here, because it names no artifact.

A new client shape or a new model family is a code change with a test, on purpose.

## Rules the validator enforces

- A case whose `grading` is `judged` never takes `pass`; record `needs-human`. The judged cases are 3, 6, 7, 11, 14, 15, 16 and 26. Moving a case in or out of that set is a deliberate edit of `cases.json`, the pinning test and the CHANGELOG.
- Every outcome except `unsupported` and `not-run` needs an exact model and client version.
- `unsupported` is only for a client whose entry in `cases.json` has `"headless": false`. Today that is `copilot`: no headless interface has been verified. A client with `"headless": true` cannot be `unsupported`. A client with `"headless": false` may hold only `unsupported`, `not-run` or `needs-human` (a hand run a person must grade), never `pass`, `fail`, `refused` or `error`. When a headless interface for Copilot is verified, flip its flag in `cases.json` (with the evidence in the PR): `unsupported` is then refused for it and measured results are accepted. `unsupported` is shown plainly and is never a pass; `not-run` is shown as `not run`.
- Privacy: text fields hold only synthetic identifiers. A hostname-shaped, address-shaped, home-path-shaped, callsign-shaped or grid-square-shaped string is refused, in any letter case: callsign shapes include digit-first prefixes, and grid shapes cover the 4- and 6-character forms. The placeholders `N0CALL`, `N0TST` and `FN31pr` are allowed. A name ending `.md`, `.sh` or `.py` is a file (not a host) only if this repository has a file of that name; `.json`, `.jsonl`, `.txt`, `.toml`, `.log`, `.yml`, `.yaml`, `.diff` and `.patch` names are always files. Sizes such as `12b` are not callsigns.
- The secret heuristic is shallow: a run of 32 or more letters, digits and `+/_=-` that is not hex is refused. Keys shorter than that, secrets split across words and encoded secrets are not caught, so the evidence field stays a pointer (a file name and a line), never a quote.

## The matrix

Columns are client and model, in client order; a local model that ran with different quantization or context gets its own column, labelled with both. Each cell is the latest record by `date` (a later line wins a tie). A cell with no record is `not run`, never blank and never a pass. A case that does not apply to a client is `n/a`. A measured result whose `persona_hash` differs from the current checkout is marked `(stale)`: the prompt it measured is gone, so it proves nothing about the current text. Text echoed into the table is escaped.

`persona_hash` covers `roles/`, `policies/`, `packs/` and `fragments/` (path and bytes of every file). It does not cover `templates/`, `scripts/lib.py` (which pins the model ids and tool names the generator writes), the generator itself or the installer, so a change there does not mark results stale; re-run the cases that depend on it by hand.
