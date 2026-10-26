# Relationship to SAFO and GYST

## Hooks are not here

The routing hooks, the dispatch guard that checks a brief names a model and the session probe, are being built in [SAFO](https://github.com/ChiefGyk3D/scrum-around-and-find-out) as `safo hooks`. Prompts guide behavior and do not enforce it, so those belong with the project tooling and not with the personas. SAFO will install personas from this repository, pinned by commit. See [Policies](policies.md).

## GYST and CI

CI is the reusable workflows in [git-your-ship-together](https://github.com/ChiefGyk3D/git-your-ship-together) (GYST), pinned by commit, with the gate named `CI green`. This repo runs its own checks (generator drift, validator, unit tests) as the python-ci test command and adds nothing else locally apart from the wiki publish. There is no hand-written validate workflow. The validator also checks that every workflow pin is a full commit SHA with a version comment.

## The wiki

These pages are the `docs/` folder. `scripts/gen_wiki.py` turns them into a flat wiki tree and GYST's `wiki-publish.yml` replaces the wiki's contents with it, from `.github/workflows/wiki.yml`, on the default branch only. Edit `docs/`, never the wiki: the next publish overwrites it.
