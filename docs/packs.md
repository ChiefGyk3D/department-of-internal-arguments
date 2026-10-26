# Packs

Packs are the domain knowledge you name in a brief when the task needs it. A pack the brief does not name is not loaded, and the report says which packs it loaded. Each pack is a `PACK.md` with a `## How to verify` section.

- [python-untrusted-input](../packs/python-untrusted-input/PACK.md): loading YAML or JSON from untrusted sources, HTTP and GraphQL clients, opening files by path.
- [gha-security-dast](../packs/gha-security-dast/PACK.md): GitHub Actions workflow review (SHA pinning, permissions, expression injection) and OWASP ZAP automation.
- [threat-model](../packs/threat-model/PACK.md): assets, actors, trust boundaries and abuse cases, with risks ranked and each given a mitigation and a verification.
- [rf-emcomm](../packs/rf-emcomm/PACK.md): amateur radio and emergency-communications work; never key a transmitter, synthetic identifiers only.
- [linux-platform](../packs/linux-platform/PACK.md): service, udev, systemd, firewall, package and kernel change planning; privileged commands are handed to the human.
- [local-llm-routing](../packs/local-llm-routing/PACK.md): sending text work to a local Ollama server; code does counting, the model writes prose.

On Claude Code a pack installs as a skill named `pack-<name>`. Copilot has no pack loader, so paste the pack into the brief or use the exporter on the [Install](install.md) page. Packs are named in the brief; see [Writing a brief](writing-a-brief.md).
