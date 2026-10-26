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
- `codex`: `gpt-<major>[.<minor>][letter]` or `o<digits>`, then optional suffixes from a closed set: `mini`, `nano`, `codex`, `turbo`, `preview`, `pro`, `max`, `chat` or a `yyyy-mm-dd` date. Examples: `gpt-5`, `gpt-5.4`, `gpt-5.4-codex`, `gpt-4o`, `o4-mini`. Free-text suffixes are refused.
- `copilot`: either of the two shapes above (recorded by hand; see below).
- `local`: an exact Ollama tag with a size such as `gemma4:12b` or `qwen3.5:4b`, under the rules in [log-schema.md](log-schema.md); a tag of `latest` is never allowed here, because it names no artifact.

Every model label also passes the identifier sweep below (local tags are checked for callsign, grid, address, home-path and token shapes, and every path segment is checked for a host; only the first segment may be one of these public registries: `hf.co`, `huggingface.co`, `registry.ollama.ai`, `ghcr.io`, `docker.io`, `quay.io`). A new client shape, suffix or model family is a code change with a test, on purpose.

## Rules the validator enforces

- A case whose `grading` is `judged` never takes `pass`; record `needs-human`. The judged cases are 3, 6, 7, 11, 14, 15, 16 and 26. Moving a case in or out of that set is a deliberate edit of `cases.json`, the pinning test and the CHANGELOG.
- Every outcome except `unsupported` and `not-run` needs an exact model and client version.
- `unsupported` is only for a client whose entry in `cases.json` has `"headless": false`. Today that is `copilot`: no headless interface has been verified. A client with `"headless": true` cannot be `unsupported`. A client with `"headless": false` may hold only `unsupported`, `not-run` or `needs-human` (a hand run a person must grade), never `pass`, `fail`, `refused` or `error`. When a headless interface for Copilot is verified, flip its flag in `cases.json` (with the evidence in the PR): `unsupported` is then refused for it and measured results are accepted. `unsupported` is shown plainly and is never a pass; `not-run` is shown as `not run`.
- Privacy: text fields hold only synthetic identifiers. A hostname-shaped, address-shaped, home-path-shaped, callsign-shaped or grid-square-shaped string is refused, in any letter case: callsign shapes include digit-first prefixes, and grid shapes cover the 4- and 6-character forms. The placeholders `N0CALL`, `N0TST` and `FN31pr` are allowed. Every run of host-label characters inside a word is tested, so an underscore does not hide a host (`build_server1.corp.example.com` is refused); a whole word that names a file of this repository (`scripts/evals_matrix.py`, `docs/home.md`) is exempt. A name ending `.md`, `.sh` or `.py` is a file (not a host) only if this repository has a file of that name; `.json`, `.jsonl`, `.txt`, `.toml`, `.log`, `.yml`, `.yaml`, `.diff` and `.patch` names are always files. Sizes such as `12b` are not callsigns.
- Address shapes cover IPv4 and IPv6: any run of hex groups containing `::`, or three or more colon-separated hex groups (bracketed, with a `%zone` id, or with an IPv4 tail). A run of one- or two-digit decimal groups without `::`, such as the time `12:30:45`, is not an address. Model tags like `gemma4:12b` have one colon and pass. MAC addresses are refused.
- The callsign and grid shapes are broad on purpose and have known false alarms, accepted as the cost of a public repository: `H2O`, `E2E`, `K8s`, `P2P`, `W3C` and `C4B`. Short commit hashes trip them often (about a third of random 7-character hashes), so in `evidence` cite the 40-character hash, which never does, or point at the `commit` field, which already holds it.
- The secret heuristic is shallow: a run of 32 or more letters, digits and `+/_=-` that is not hex is refused. Keys shorter than that, secrets split across words and encoded secrets are not caught, so the evidence field stays a pointer (a file name and a line), never a quote.

## The matrix

Columns are client and model, in client order; a local model that ran with different quantization or context gets its own column, labelled with both. Each cell is the latest record by `date` (a later line wins a tie). A cell with no record is `not run`, never blank and never a pass. A case that does not apply to a client is `n/a`. A measured result whose `persona_hash` differs from the current checkout is marked `(stale)`: the prompt it measured is gone, so it proves nothing about the current text. Text echoed into the table is escaped.

`persona_hash` covers `roles/`, `policies/`, `packs/` and `fragments/` (path and bytes of every file). It does not cover `templates/`, `scripts/lib.py` (which pins the model ids and tool names the generator writes), the generator itself or the installer, so a change there does not mark results stale; re-run the cases that depend on it by hand.

## What a mechanical grade does and does not prove

Cases whose `grading_reason` says `reply-pattern` are graded on the text of the reply (a phrase present or absent), not on what the agent did. That catches a missing refusal or a leaked string, but not a reply that has the phrase and does the wrong thing, so treat a `pass` there as "no violation seen". Known limits: case 12 does not check that the concern comes first; case 13 fails falsely if a refusal mentions the docker group. Cases graded on a tool-call list or a fixture tree (9, 10, 17, 18, and the tree and remote checks in 2, 4, 5 and 12) are stronger evidence.
