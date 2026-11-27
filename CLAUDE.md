# Working in this repository

- `roles/`, `packs/`, `policies/` and `fragments/` are the sources. `.claude/agents/`, `.claude/skills/`, `.github/agents/`, `.codex/agents/` and `.agents/skills/` are generated from them. Never edit a generated file; edit the source and run `python3 scripts/generate.py`.
- A rule that several roles share lives in `fragments/` and is spliced in with a `{{fragment:NAME}}` line. Edit the fragment, not the copies.
- CI runs the full gate for this repository and is the judge. Locally run the targeted checks that cover your change (`generate.py --check`, `validate.py`, the tests you touched, `ruff`); the full gate is:

```
python3 scripts/generate.py --check && python3 scripts/gen_wiki.py --check && python3 scripts/validate.py && python3 -m unittest discover -s tests -v
```

- `docs/` is the source of the GitHub wiki (a generated mirror, published by `.github/workflows/wiki.yml`). Edit `docs/`, never the wiki; `gen_wiki.py --check` fails on a broken link.
- Lint is `ruff check .` and `ruff format --check .` (line length 120). Scripts are standard library only, Python 3.11 or later; the tests also need `pyyaml` (test-only).
- Keep hostnames, addresses, home paths, serials, callsigns and real grid squares out of every file; the validator sweeps the tree and fails on a hit. Use the placeholders the policies name.
- Release: bump `VERSION` and `pyproject.toml` together, add the CHANGELOG entry, regenerate (the generated marker carries the release), and let CI run the full gate.
- Workflow pins are full commit SHAs with a `# vX.Y.Z` comment, all callers on one commit; the validator checks it.
