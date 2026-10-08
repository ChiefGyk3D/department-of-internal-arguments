# Working in this repository

- `roles/`, `packs/`, `policies/` and `fragments/` are the sources. `.claude/agents/`, `.claude/skills/` and `.github/agents/` are generated from them. Never edit a generated file; edit the source and run `python3 scripts/generate.py`.
- A rule that several roles share lives in `fragments/` and is spliced in with a `{{fragment:NAME}}` line. Edit the fragment, not the copies.
- The gate for this repository, exactly as CI runs it:

```
python3 scripts/generate.py --check && python3 scripts/validate.py && python3 -m unittest discover -s tests -v
```

- Lint is `ruff check .` and `ruff format --check .` (line length 120). Standard library only, Python 3.11 or later.
- Keep hostnames, addresses, home paths, serials, callsigns and real grid squares out of every file; the validator sweeps the tree and fails on a hit. Use the placeholders the policies name.
- Release: bump `VERSION` and `pyproject.toml` together, add the CHANGELOG entry, regenerate (the generated marker carries the release), and run the gate.
- Workflow pins are full commit SHAs with a `# vX.Y.Z` comment, all callers on one commit; the validator checks it.
