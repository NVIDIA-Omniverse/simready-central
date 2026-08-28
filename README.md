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
guide the submission through validation, packaging, upload, and creation of the
SimReady Central pull request. It will ask for confirmation before modifying
source assets or making remote changes, and it stops after opening or updating
the pull request. NVIDIA then reviews the submission and manages the remaining
catalog process.

Users who want to inspect the underlying requirements and commands can refer to
the [detailed OEM asset submission guide](docs/oem-asset-submission-guide.md).

## Repository structure

```text
simready-central/
├── README.md
├── CODEOWNERS
├── .agents/
│   └── skills/
│       └── submit-simready-assets/
│           └── SKILL.md  # Agent entry point for OEM submissions
├── .github/
│   ├── PULL_REQUEST_TEMPLATE/
│   │   └── submission.md
│   ├── scripts/
│   │   └── validate_submissions.py
│   └── workflows/
│       └── validate-submissions.yml
├── docs/
│   ├── onboarding.md
│   ├── oem-asset-submission-guide.md
│   └── foundations-spec.md
└── submissions/
    └── <Hugging Face namespace>/
        └── <dataset name>  # Text file containing package-definition URLs
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

## Additional references

- [Submission requirements summary](docs/foundations-spec.md)
