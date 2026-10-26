# Behavioral-eval result schema

One JSON object per line. `python3 scripts/evals_matrix.py RESULTS.jsonl` validates every line and prints the matrix of case by client and model; one bad line fails the whole run (exit 1, nothing printed) rather than being skipped. The cases are in [cases.json](cases.json) and the generated table is [behavioral-evals.md](behavioral-evals.md). No model is called and nothing touches the network: the renderer only reads files. The real results file is private until reviewed; [example-results.jsonl](example-results.jsonl) is synthetic, and its `persona_hash` is a placeholder, so it renders as stale.

Reading is strict. A missing, extra, duplicated, `null` or mistyped key or value is an error, and so are `NaN` and a bool where an integer belongs.

| Field | Type | Meaning |
| --- | --- | --- |
| `case` | integer | An `id` in `cases.json`; the case must apply to the client |
| `client` | `claude-code` \| `codex` \| `copilot` \| `local` | Where the case ran |
| `client_version` | string | Exact version, at least one digit, for example `2.1.4`; `unspecified` only for `unsupported` and `not-run` |
| `model` | string | Exact model id, never a family name, `inherit` or `latest`; for `local`, an exact Ollama tag with a size such as `gemma4:12b`; `unspecified` only for `unsupported` and `not-run` |
| `quantization` | string | `local` only, required with `context` for any measured result, for example `Q4_K_M` |
| `context` | integer | `local` only, the context limit in tokens the model was loaded with |
| `tools_enabled` | list | Unique names from `read`, `search`, `edit`, `shell`, `skills`; an empty list means tools were off |
| `commit` | string | The 40-character hash of this repository the persona text came from |
| `persona_hash` | string | 64 hex characters from `python3 scripts/evals_matrix.py --persona-hash`: a hash of `roles/`, `policies/`, `packs/` and `fragments/` |
| `date` | `YYYY-MM-DD` | A real calendar day |
| `outcome` | enum | `pass`, `fail`, `needs-human`, `unsupported`, `refused`, `error`, `not-run` |
| `evidence` | string, 200 characters max | A short pointer, not a transcript: letters, digits and `. _ : / # = , ( ) + -` only |
| `cost` | integer, optional | Tokens used, non-negative |

## Rules the validator enforces

- A case whose `grading` is `judged` never takes `pass`; record `needs-human`. A human's verdict goes in a new record only after the case is reclassified in the registry.
- Every outcome except `unsupported` and `not-run` needs an exact model and client version. A family name (`sonnet`) is refused.
- `unsupported` means the client cannot run the case headless (Copilot, until an interface is verified); it is shown plainly and is never a pass. `not-run` is shown as `not run`.
- Privacy: text fields hold only synthetic identifiers. A hostname-shaped, address-shaped, home-path-shaped, callsign-shaped or grid-square-shaped string, or a 32-character token that is not hex, is refused. The placeholders `N0CALL`, `N0TST` and `FN31pr` are allowed. File names ending `.md`, `.json`, `.jsonl`, `.txt`, `.py` and a few others are not hostnames.

## The matrix

Columns are client and model, in client order. Each cell is the latest record by `date` (a later line wins a tie). A cell with no record is `not run`, never blank and never a pass. A case that does not apply to a client is `n/a`. A measured result whose `persona_hash` differs from the current checkout is marked `(stale)`: the prompt it measured is gone, so it proves nothing about the current text. Text echoed into the table is escaped.
