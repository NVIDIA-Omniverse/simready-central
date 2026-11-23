# SimReady Central

**This repository is for reading only.** It is a mirror of the package lists
NVIDIA has reviewed and accepted for the NVIDIA SimReady Catalog, kept here for
the catalog pipeline and for browsing the lists. Pull requests cannot be
opened here.

**To contribute, go to the Hugging Face dataset
[`nvidia/simready-central-library`](https://huggingface.co/datasets/nvidia/simready-central-library).**
Submitting SimReady assets, updating a submission and removing one are all
pull requests on that dataset. Nothing can be contributed in this repository
directly. The dataset's page describes how to become a contributor. The
submission guide, the acceptance criteria and the submission skill are files
of the dataset, open to contributors once their access request there is
approved. If you submitted through this repository before, the same steps
apply to you.

## What this repository holds

```text
NVIDIA-Omniverse/simready-central
├── README.md
├── LICENSE
├── SECURITY.md
├── .agents/
│   └── skills/
│       ├── index-to-library/
│       │   └── SKILL.md  # Agent entry point for browsing a list's assets locally
│       └── submit-simready-assets/
│           └── SKILL.md  # Sends an agent asked to submit here to the dataset
├── docs/
│   ├── index-to-library-guide.md   # Pull a list's packages and browse them locally
│   └── examples/
│       ├── index_to_library.py     # Reads the pulled packages into library.json
│       └── library_page.py         # Renders library.json as a local web page
├── requirements-oem-submit.txt     # The pinned tools the guide installs
└── submissions/
    └── <Hugging Face namespace>/
        └── <dataset name>  # Text file containing package-definition URLs
```

Each file under `submissions/` is the complete list of the package versions
from one Hugging Face dataset that are in the catalog. Each non-empty line is
an immutable URL to a package definition:

```text
https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<40-character-commit>/<logical-asset-name>/com.nvidia.simready.packaging.json
```

The lists are copied here from the dataset after each pull request is merged
there, and are changed nowhere else.

## Browsing a list locally

A list is an index: each line pins one package to the Hugging Face commit it
was reviewed at. To see what those packages contain, pull them to your machine
and browse them as a local library. Clone this repository and tell your agent:

```text
Read and follow the skill at .agents/skills/index-to-library/SKILL.md in this
repository and build a local library of the assets in
submissions/<namespace>/<dataset>.
```

The agent downloads the listed packages into a directory of your choosing,
reads each one into `library.json`, one record per asset with its source URL,
owner, version, license, validation and benchmark results, and renders
`library.html`, a page that filters and groups the assets by those fields.
Nothing is uploaded or modified.

The [index-to-library guide](docs/index-to-library-guide.md) has the commands
behind the [skill](.agents/skills/index-to-library/SKILL.md) and traces every
field of the library to the file in the package and the tool that reads it.
The two scripts it runs are in `docs/examples/`, and its setup installs the
tools from `requirements-oem-submit.txt`.
