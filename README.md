# SimReady Central


SimReady Central is NVIDIA's review entry point for OEM SimReady asset
submissions. OEMs store their packages in publicly accessible Hugging Face
datasets and use this repository to submit the exact package versions they want
NVIDIA to review.

## Submission flow

Before submitting assets, complete the [OEM onboarding](docs/onboarding.md)
process. Provide your GitHub username to your NVIDIA contact and accept the
collaborator invitation so you have access to the repository.

After onboarding is complete, the recommended way to submit assets is with an
agent. Tell your agent:

```text
Read and follow the submission skill at
https://github.com/NVIDIA-Omniverse/simready-central/blob/main/.agents/skills/submit-simready-assets/SKILL.md
and help me submit the assets in <asset-source-folder> to NVIDIA SimReady
Central.
```

The agent will inspect the local environment, identify any missing inputs, and
guide the submission through `simready-manage submit` — which validates,
packages, and uploads each asset — and then through creation of the SimReady
Central pull request. It will ask for confirmation before modifying source
assets or making remote changes, and it stops after opening or updating the pull
request. NVIDIA then reviews the submission and manages the remaining catalog
process.

Users who want to inspect the underlying requirements and commands can refer to
the [detailed OEM asset submission guide](docs/oem-asset-submission-guide.md),
and every check it must pass in [Acceptance criteria](docs/acceptance-criteria.md).

## Repository structure

```text
simready-central/
├── README.md
├── AGENTS.md                 # Which skill an agent loads before touching what
├── CLAUDE.md                 # Includes AGENTS.md
├── CODEOWNERS
├── CONTRIBUTING.md           # The sign-off every commit needs
├── LICENSE
├── .gitignore
├── .agents/
│   └── skills/
│       ├── index-to-library/
│       │   └── SKILL.md  # Agent entry point for browsing a submission's assets locally
│       └── submit-simready-assets/
│           └── SKILL.md  # Agent entry point for OEM submissions
├── .github/
│   ├── PULL_REQUEST_TEMPLATE/
│   │   └── submission.md
│   ├── scripts/
│   │   ├── security_scan.py
│   │   └── validate_submissions.py
│   └── workflows/
│       ├── test-scripts.yml          # Runs tests/ on pull requests that are not submissions
│       └── validate-submissions.yml  # Runs the checks on a submission pull request
├── docs/
│   ├── onboarding.md
│   ├── acceptance-criteria.md    # Every check, its id, and what it means
│   ├── oem-asset-submission-guide.md
│   ├── index-to-library-guide.md   # Pull a submission's packages and browse them locally
│   ├── examples/
│   │   ├── index_to_library.py     # Reads the pulled packages into library.json
│   │   └── library_page.py         # Renders library.json as a local web page
│   └── foundations-spec.md
├── submissions/
│   └── <Hugging Face namespace>/
│       └── <dataset name>  # Text file containing package-definition URLs
└── tests/                    # Tests of the two scripts and of the library examples
```

Each dataset entry under `submissions/` is a text file, not a directory or a
copy of the dataset. It contains the complete, authoritative package list for
that Hugging Face dataset. For example, packages stored in
`nvidia/simready-assets` are submitted in:

```text
submissions/nvidia/simready-assets
```

Each non-empty line is an immutable URL to a package definition:

```text
https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<40-character-commit>/<logical-asset-name>/com.nvidia.simready.packaging.json
```

To add or update packages, edit the same dataset file and submit the complete
new list. To remove package versions, remove their URL lines but retain every
other version that should remain available. Changes take effect only after the
pull request is reviewed and merged.

## Browsing a submission locally

A submission file is an index: each line pins one package to the Hugging Face
commit it was reviewed at. To see what those packages contain, whether it is
your own submission or one that is already merged, pull them to your machine
and browse them as a local library. Tell your agent:

```text
Read and follow the skill at
https://github.com/NVIDIA-Omniverse/simready-central/blob/main/.agents/skills/index-to-library/SKILL.md
and build a local library of the assets in submissions/<namespace>/<dataset>.
```

The agent downloads the listed packages into a directory of your choosing,
reads each one into `library.json`, one record per asset with its source URL,
owner, version, license, validation and benchmark results, and renders
`library.html`, a page that filters and groups the assets by those fields.
Nothing is uploaded or modified. The agent stops once the page is rendered and
reports what the packages carry, including what they lack.

The [index-to-library guide](docs/index-to-library-guide.md) has the commands
behind the skill and traces every field of the library to the file in the
package and the tool that reads it, so the same reads can go into your own
pipeline. The two scripts it runs are in `docs/examples/`. It needs the
`.venv-submit` environment from the submission guide's one-time setup and read
access to the dataset.

## Additional references

- [Submission requirements summary](docs/foundations-spec.md)
