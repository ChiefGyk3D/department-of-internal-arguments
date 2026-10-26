# Pilot log schema

One JSON object per line, appended by the lead after each task from the report's shared header. `scripts/summarize_log.py` validates every line and prints the table used in the pilot notes; a malformed line fails the run rather than being skipped. The real log is private (task labels can leak work); [example-log.jsonl](example-log.jsonl) is synthetic and only exists so the summariser has a fixture.

| Field | Type | Meaning |
| --- | --- | --- |
| `date` | `YYYY-MM-DD` | Day the task closed |
| `task` | string, 64 chars max | A label that means something to you and nothing to anyone else; no paths, hostnames or customer names |
| `host` | `claude-code` \| `copilot` \| `chatgpt` \| `local` | Where the role ran |
| `role` | role name | From the report's `ROLE:` line |
| `model` | exact model id | From the report's `MODEL:` line, never `inherit` or a family name |
| `packs` | list of pack names | From `PACKS LOADED:`, not from the brief |
| `first_review` | `approved` \| `changes` \| `n-a` | Outcome of the first reviewer pass on an implementation task; `n-a` for review tasks |
| `fix_rounds` | integer | Implementer rounds after the first, 0 if none |
| `findings` | `{"blocking": n, "important": n, "minor": n}` | Counts from the reviewer and adversarial reports for this task |
| `violations` | integer | Rule slips (masked check, wrong trailer, reviewer edit, missing gate exit code, leaked identifier, unclean tree) |
| `gate_exit` | integer or `null` | From `GATE:`; `null` when the gate was not-run or n-a |
| `local_model` | string, 64 chars max, optional | The exact tag of the local model that answered, with its size (for example `gemma4:12b`), from the report's `LOCAL-LLM:` line. Never a family name or `ollama`. Omit when no local model was used. Must come with `local_discrepancies` |
| `local_discrepancies` | integer, optional | The report's `DISCREPANCIES:` count: claims in the local output that did not match the source and were corrected or dropped. Zero is a real result and must be logged as 0 |
| `notes` | string, optional | One sentence, sanitised |

The two local fields exist so the discrepancy rate is measured per model before the rule is expanded: the summariser prints the number of local-model tasks, the total discrepancies and the count per model. A task that used a local model and has no `local_model` field is a logging slip, not a clean run.

A line that names a private host, a real callsign or a secret fails the repository's privacy sweep if it is ever committed. Keep the real log outside the repository or under a gitignored path.
