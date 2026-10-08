---
name: "local-llm-routing"
description: "Measured knowledge for sending work to a local Ollama server and for configuring one: send exactly the num_ctx a model is loaded with, think=false for cheap text tasks, what small local models do well and badly (code does grouping and counting; the model writes prose only), an evaluation record per model, reachability checks, and verification commands. Single GPU and small context first; every setting configurable. Load when routing text work to a local model, writing a client that calls Ollama, or sizing models and context. Not for cloud-model prompting."
---

# Pack: local LLM routing (Ollama)

Status: pilot pack, written 2026-10-08 from measurements taken 2026-08 to 2026-10-07. UNVERIFIED items were inferred or read, not measured. Start from the smallest setup: one GPU (or CPU), one model, a small context. Everything below must stay configurable; never bake in one person's layout of devices, ports or context sizes. The default endpoint in examples is `http://localhost:11434`; read the real one from your own config or environment.

A local model costs no credits but is slow and loose. Use it for text-in, text-out drafts that a stronger model or a human then checks. Anything that changes state, carries security risk, or must be exactly right stays with code or a stronger model.

## Division of labor (the rule that matters most)

- **Code does the grouping, counting, sorting, deduplication, and every statistic.** The model never produces a number, a count, or a category assignment that anything depends on.
- **The model writes prose only**, from facts that code already computed and passed in. Then someone verifies each claim in the prose against the source. Local drafts of grouped facts, technical prose, release notes and documentation all had to be rewritten by hand in the measured sessions.
- Never let it write rules, code, or security judgments.
- Classification with free-text labels is unreliable: a small model's role labels were right about 85% of the time and domain labels about 46% (it named projects instead of technical areas). Give it a closed list of allowed labels and validate its output against the list in code.

## Measured: what it is good and bad at (2026-10-07, 4B and 12B class models)

- Usable as a first draft: issue triage of a batch, one-line summaries of facts you supply, PR-body drafts.
- Poor: sorting facts into groups; precise technical prose; release notes (it missed the headline feature); documentation drafts.
- Failure modes seen: misspelled repository names, the same CVE given different priorities on different runs, "false positive" verdicts it cannot justify from the input, dropped headline items. Check names and identifiers against the source; do not accept a verdict the input does not support.
- Latency: roughly 55 to 215 seconds for a long draft on the measured setup. Run it in the background or with a generous timeout.

## Checklist: calling Ollama

1. **Send exactly the `num_ctx` the model is already loaded with.** A different value reloads the model and can evict a neighbour that shares the GPU (measured twice). Find the loaded value with `ollama ps` or `GET /api/ps`.
2. **`think: false` for cheap tasks.** A thinking model spends hundreds of tokens before a one-sentence answer (436 evaluation tokens for one sentence on a 12B-class model; about 15 times fewer with thinking off on a 4B-class model). Opt in to thinking only when the task needs reasoning.
3. **Structured output.** Native `format` accepts a JSON schema; still validate the result in code, and ground the schema in the prompt. Schema-valid is not correct.
4. **Role aliases and Modelfiles.** Whether a `FROM` alias shares a loaded runner with its base model is UNVERIFIED (source reading suggests reuse when weights and runner options match; not reproduced). Until you test it, send the role as the request `system` message to the resident base model.
5. **Models that cannot co-reside** evict each other; check VRAM fit before adding a model to a shared box. Measure with `ollama ps` after a warm-up. In the measured case two models fit together only up to a reduced context, not the default.
6. **Configurable, not hard-coded.** Take host, model, context and timeouts from environment or config with a single-instance default.

## Evaluation record for a local model

Before assigning a task class to a local model, record the following, and re-measure when any field changes. A family name proves nothing about tool support, accuracy or context.

- Exact model artifact (tag and digest, not the family name).
- Quantisation.
- Context limit: the loaded `num_ctx`, and the model's own maximum.
- Runtime and version (for example the Ollama release).
- Licence, checked against the intended use.
- Hardware class (accelerator type and VRAM class only; no hostnames).
- Measured result per task class: pass or fail against a stated check, number of runs, sanitised inputs. "Seemed fine" is not a result.
- Prompt fit: confirm policies and instructions were not dropped to fit the context.

## Optional: more than one GPU or instance

Skip this on a single-GPU setup. If you do split models across devices, one measured trap: on a multi-GPU NVIDIA host, an Ollama release (0.33.2) also enumerated devices through Vulkan, which ignores `CUDA_VISIBLE_DEVICES`, so a per-device split put models on the wrong devices. Setting `OLLAMA_VULKAN=0` in each service unit fixed it. Verify placement from the service log (`journalctl -u <ollama-unit>` and look for the device lines), not only `ollama ps`, and confirm each model shows fully on GPU after warm-up. After changing an instance's default context, update every client that pins a value. UNVERIFIED: whether newer releases change this behavior.

## Checklist: reachability and dispatch

1. Probe before assuming: check whether the local server answers before you promise it to an agent. If it is unreachable, write `local model: n/a - <reason>` in the brief and use another route; do not hang on a timeout.
2. A brief that names the local step does not make an agent use it: only about 5 of 14 briefs that asked for it produced a logged call. Verify use from your tool's own call log instead of trusting the report.
3. Subagents without network access to the server cannot call it; do not promise it to them.
4. Using a coding agent CLI directly against Ollama may be possible; its tool use is UNVERIFIED, so trial it before relying on it.

## How to verify (commands)

- Is it up and what is loaded: `curl -s "$OLLAMA_HOST/api/ps"` with `OLLAMA_HOST` set from your own config (default `http://localhost:11434`); `ollama ps`.
- Context match: compare the `context_length` shown by `ollama ps` with the `num_ctx` your client sends.
- Reload detection: send the same prompt twice and compare `load_duration` in the response; a large second `load_duration` means a reload.
- Prose check: after a local draft, diff every name, number and claim against the source before keeping a sentence.
