# Agent guide

What this repository is, and which skill to load before changing part of it.

SimReady Central holds OEM asset submissions. A submission is a text file under
`submissions/<namespace>/<dataset>` listing Hugging Face package URLs; checks run
on the pull request and report what is wrong with it.

## Load the skill first

| Touching | Load |
|---|---|
| Preparing or fixing a submission | [`.agents/skills/submit-simready-assets/SKILL.md`](.agents/skills/submit-simready-assets/SKILL.md) |
| Pulling a submission's packages and browsing them locally (`docs/index-to-library-guide.md`, `docs/examples/`) | [`.agents/skills/index-to-library/SKILL.md`](.agents/skills/index-to-library/SKILL.md) |

## The acceptance criteria

[`docs/acceptance-criteria.md`](docs/acceptance-criteria.md) is every check a
submission must pass. A rejection cites a rule id; look the id up there for what
is wrong, how to fix it, and why the rule exists.
