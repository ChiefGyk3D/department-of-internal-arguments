# Packs

Packs are the domain knowledge you name in a brief when the task needs it. A pack the brief does not name is not loaded, and the report says which packs it loaded. Each pack is a `PACK.md` with a `## How to verify` section.

- [python-untrusted-input](../packs/python-untrusted-input/PACK.md): loading YAML or JSON from untrusted sources, HTTP and GraphQL clients, opening files by path.
- [gha-security-dast](../packs/gha-security-dast/PACK.md): GitHub Actions workflow review (SHA pinning, permissions, expression injection), GYST callers and release rules (one pin, `CI green`, API limits) and OWASP ZAP automation.
- [threat-model](../packs/threat-model/PACK.md): assets, actors, trust boundaries and abuse cases, with risks ranked and each given a mitigation and a verification.
- [rf-emcomm](../packs/rf-emcomm/PACK.md): amateur radio and emergency-communications work; never key a transmitter, synthetic identifiers only.
- [linux-platform](../packs/linux-platform/PACK.md): service, udev, systemd, firewall, package and kernel change planning; privileged commands are handed to the human.
- [local-llm-routing](../packs/local-llm-routing/PACK.md): sending text work to a local Ollama server; code does counting, the model writes prose, and its output is verified against the source and reported on the `LOCAL-LLM:` line (measured on gemma4:12b only).
- [cyber-tier-review](../packs/cyber-tier-review/PACK.md): security and adversarial reviews on a vendor's high-trust cyber-access tier; use the tier model, fix the tier and rerun on a cutoff (never reword, never skip), the hardware-key error, single-flight runs, and a review that cannot show its answer is held, not clean. Tier and model values stay in your private config. CLI claims carry VERIFIED or INFERRED labels dated 2026-10-26.

On Claude Code a pack installs as a skill named `pack-<name>`. Copilot has no pack loader, so paste the pack into the brief or use the exporter on the [Install](install.md) page. Packs are named in the brief; see [Writing a brief](writing-a-brief.md).
