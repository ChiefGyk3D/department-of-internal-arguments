# Changelog

## [0.1.0] - 2026-10-08

First release.

- Four roles (implementer, reviewer, adversarial-reviewer, transcriber) as canonical Markdown with a suggested model tier, tools and description.
- Six knowledge packs: python-untrusted-input, gha-security-dast, threat-model, rf-emcomm, linux-platform, local-llm-routing.
- Four policies: security-boundaries, human-approvals, quality-gates, model-routing.
- `scripts/generate.py` builds the Claude Code agents and skills, the Copilot custom agents, and feeds the prompt exporter. `--check` fails on drift.
- `scripts/install.py`: additive, collision and symlink safe project installer.
- `scripts/export_prompt.py`: standalone prompt for ChatGPT and local models.
- Evals: the behavioral exercises table and a summary of the 2026-10 pilot.
- CI through git-your-ship-together v1.16.0.
