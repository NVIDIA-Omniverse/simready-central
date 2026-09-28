# Submitting SimReady assets to the NVIDIA SimReady Catalog

This guide is a step-by-step walkthrough for an OEM who wants to submit an asset
for review and inclusion in the NVIDIA SimReady Catalog.

At the end of the process:

1. Your asset package is stored, at a fixed version, in your own Hugging Face
   dataset.
2. You have submitted a definitive list of the assets you want included in the
   SimReady Catalog, and NVIDIA has started a review process that keeps you
   updated through comments on a GitHub pull request.

Your packages must adhere to the SimReady Foundation packaging profile. They can
additionally adhere to further
[profiles](https://github.com/NVIDIA/simready-foundation/tree/main/nv_core/sr_specs/docs/profiles),
which validate them for use for various purposes (see
[Validate the asset for one or more given profiles](#validate-the-asset-for-one-or-more-given-profiles)
below).

A [skill](../.agents/skills/submit-simready-assets/SKILL.md) is also available to guide you through the process with agent support.

## Preconditions

Before you start, make sure that:

- You have completed the [OEM onboarding process with NVIDIA](./onboarding.md).
- You have a Hugging Face account.
- You have a GitHub account with write access to
  `NVIDIA-Omniverse/simready-central`.
- Git, Git LFS, and the GitHub CLI (`gh`) are available on your command-line
  path.
- Python 3.11 or 3.12 is available and on your command-line path. The
  toolchain does **not** work on Python 3.13 or newer: `simready-validate`
  depends on `usd-validation-nvidia`, which declares `Requires-Python:
  >=3.11,<3.13`.
- Your git identity is configured:
  ```bash
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```  

## One-time setup

You only need to do this setup once. Replace `<SimreadyDir>` with an absolute
path to a directory of your choosing. The directory must exist.

The setup below does the following:

- Authenticates the GitHub CLI with your account.
- Clones SimReady Central, which is later used to open the submission pull
  request.
- Clones the SimReady Foundation repository, which provides the validators and
  profiles used by `simready-package`.
- Creates and activates a Python virtual environment and installs
  `simready.package` (the packaging and validation tool) together with the Hugging
  Face CLI tools.
- Authenticates the Hugging Face CLI with your account.
- Creates a Hugging Face dataset repository which will store your assets and
  declares the license that applies to them.

### Linux

```bash
# Authenticate with GitHub and clone SimReady Central
gh auth login
gh auth setup-git
gh repo clone NVIDIA-Omniverse/simready-central <SimreadyDir>/simready-central

# Make the validators for SimReady packages available
git lfs install
git clone https://github.com/NVIDIA/simready-foundation.git <SimreadyDir>/simready-foundation

# Create and activate a Python venv with simready.package and the Hugging Face tools installed
python3 -m venv <SimreadyDir>/.venv   # or python3.11 - must not be 3.13+
source <SimreadyDir>/.venv/bin/activate
python -V                             # confirm 3.11-3.12 before installing
pip install --upgrade huggingface_hub numpy  # numpy is required by simready-validate
pip install --upgrade simready-package

# Authenticate with Hugging Face
# Note: you can ignore the "token not saved to git credentials" warning —
# this workflow uses hf upload and does not require git-level Hugging Face access.
hf auth login  # follow the instructions on the command line.
               # Documentation: https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login

# Create a (publicly accessible) Hugging Face dataset repository which will store
# the assets (skip if you already have one).
# <namespace>/<dataset> is your Hugging Face username or org name followed by the dataset name,
# e.g. nvidia/simready-assets. For a personal repo you may omit the namespace.
hf repos create --type dataset --public <namespace>/<dataset>

# Declare the dataset license in the dataset card (must match the license you use in simready-package).
# cc-by-4.0 is recommended. Create a local README.md and upload it:
cat > /tmp/hf-dataset-readme.md << 'EOF'
---
license: cc-by-4.0
---
EOF
hf upload <namespace>/<dataset> /tmp/hf-dataset-readme.md README.md --repo-type dataset
```

### Windows (PowerShell)

```powershell
# Authenticate with GitHub and clone SimReady Central
gh auth login
gh auth setup-git
gh repo clone NVIDIA-Omniverse/simready-central <SimreadyDir>\simready-central

# Make the validators for SimReady packages available
git lfs install
git clone https://github.com/NVIDIA/simready-foundation.git <SimreadyDir>\simready-foundation

# Create and activate a Python venv with simready.package and the Hugging Face tools installed
# Use the `py` launcher to pin the version. A bare `python` may resolve to 3.13+,
# which makes the package install below fail. `py -0p` lists what is installed.
py -3.12 -m venv <SimreadyDir>\.venv    # or -3.11 - must not be 3.13+
<SimreadyDir>\.venv\Scripts\Activate.ps1
python -V                               # confirm 3.11-3.12 before installing
pip install --upgrade huggingface_hub numpy  # numpy is required by simready-validate
pip install --upgrade simready-package

# Authenticate with Hugging Face
# Note: you can ignore the "token not saved to git credentials" warning —
# this workflow uses hf upload and does not require git-level Hugging Face access.
hf auth login  # follow the instructions on the command line.
               # Documentation: https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login

# Create a (publicly accessible) Hugging Face dataset repository which will store
# the assets (skip if you already have one).
# <namespace>/<dataset> is your Hugging Face username or org name followed by the dataset name,
# e.g. nvidia/simready-assets. For a personal repo you may omit the namespace.
hf repos create --type dataset --public <namespace>/<dataset>

# Declare the dataset license in the dataset card (must match the license you use in simready-package).
# cc-by-4.0 is recommended. Create a local README.md and upload it:
@"
---
license: cc-by-4.0
---
"@ | Set-Content hf-dataset-readme.md
hf upload <namespace>/<dataset> hf-dataset-readme.md README.md --repo-type dataset
```

> Note (Windows): if activating the venv fails with an execution-policy error,
> allow scripts for the current session and retry:
>
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
> ```
>
> Alternatively, from `cmd.exe`, activate with `<SimreadyDir>\.venv\Scripts\activate.bat`.

> Note (Windows): when staging files with `git add`, you may see a warning that
> "LF will be replaced by CRLF". This is harmless and can be ignored.

> Note: the commands in the rest of this guide use bash syntax. Windows users
> should run them in Git Bash, or adapt them to PowerShell.

> Note: if the `simready-package` install spends a long time backtracking
> ("pip is looking at multiple versions ... This could take a while") and then
> fails with:
>
> ```text
> ERROR: Cannot install simready-package because these package versions have conflicting dependencies.
>     simready-validate 2026.6.3 depends on usd-validation-nvidia>=1.20.0
> Additionally, some packages in these conflicts have no matching distributions
> available for your environment:
>     usd-validation-nvidia
> ```
>
> the venv is on an unsupported Python. The message is misleading: these are
> `py3-none-any` wheels, so this is a `Requires-Python` mismatch, not a platform
> or architecture one. Run `python -V`; if it reports 3.13 or newer, delete
> `.venv`, recreate it with a 3.11-3.12 interpreter as shown above, and rerun both
> `pip install` commands.

## Creating a SimReady package from your local data

Prepare assets for submission in this order:

1. Run `simready-validate` against either `Prop-Robotics-Physx` or
   `Prop-Robotics-Neutral` and stamp the feature-level results into each root USD.
2. Run `simready-package` to check that the asset can be packaged and add its standard
   package definition.

The resulting asset folder is what you upload to Hugging Face. Both commands
support processing one asset or a batch of assets. Asset paths in these commands
are absolute — your assets can live anywhere on your filesystem and do not need
to be inside `<SimreadyDir>`.

### Prepare the asset

- The data for each asset (and only the data for that asset) is in a folder on
  your local filesystem. Two placeholders are used throughout this guide:

  - `<logical-asset-name>` — the **folder name only**, without any directory
    components (for example `apple`). It is the asset name and **must not
    contain its version**.
  - `<path-to-asset>` — the **absolute path to that folder** (for example
    `/data/assets/apple`). Its last path component is `<logical-asset-name>`.

  The asset folder should look similar to this structure:

```text
<logical-asset-name>/
└── <intermediate-folder>/    # Any folder name is fine, but exactly one is required (per NP.005).
    │                         # The name simready_usd/ is conventional.
    ├── <root-usd-file>.usd
    ├── .thumbs/256x256/<root-usd-file>.usd.png
    ├── materials/
    └── textures/
```

  - Both `Prop-Robotics-Physx` and `Prop-Robotics-Neutral` require this directory
    structure.

### 1. Validate and stamp the root USD

Choose the profile that matches your asset type, e.g. `Prop-Robotics-Physx`.

Refer to the
[SimReady Foundation profile specifications](https://github.com/NVIDIA/simready-foundation/tree/main/nv_core/sr_specs/docs/profiles)
and the [SimReady Foundation profile validation workflow](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/profiles_validation_workflow.md)
for full details.

The `--stamp-asset-validation` flag writes the profile and feature-level pass or
fail results into the root USD's `customLayerData`.

For one asset:

```bash
simready-validate \
  --project-config <SimreadyDir>/simready-foundation/sample_content/project_config.toml \
  --profile Prop-Robotics-Physx \
  --stamp-asset-validation \
  --output validation-result.json \
  <path-to-asset>/<intermediate-folder>/<root-usd-file>.usd
```

Use `--profile Prop-Robotics-Neutral` instead when that is the intended target.

For a batch, create `root-usds.txt` with one absolute root-USD path per line,
then run:

```text
<path-to-asset-1>/simready_usd/<root-usd-file-1>.usd
<path-to-asset-2>/simready_usd/<root-usd-file-2>.usd
```

```bash
simready-validate \
  --project-config <SimreadyDir>/simready-foundation/sample_content/project_config.toml \
  --profile Prop-Robotics-Physx \
  --asset_list root-usds.txt \
  --stamp-asset-validation \
  --output validation-results.json
```

Use the same chosen profile for all assets in one batch. **A nonzero exit status
means at least one feature failed; fix all failures before proceeding to
`simready-package`.** The JSON report and stamped USD data retain the
feature-level results for review. Run `simready-validate --help` for all options
(e.g. `--version` to pin a profile version, or `-v` for per-requirement detail
on failures).

> **Note on verbose output:** `simready-validate` prints detailed INFO-level
> logging while it loads the profile ruleset. This is normal. To keep your
> terminal readable, redirect stderr to a log file:
> ```bash
> simready-validate ... 2>validate.log
> ```

> **Note on standard NVIDIA MDL modules:** Assets that use standard NVIDIA MDL
> shaders (such as `OmniPBR.mdl`, `OmniGlass.mdl`) as bare search-path
> references will produce NP.008 and VM.MDL.001 validation failures. These
> modules resolve at runtime in all NVIDIA platforms (OVRTX, Isaac Sim,
> Omniverse) and **do not need to be bundled** — these specific failures can be
> disregarded for submissions targeting NVIDIA platforms. Custom or
> non-standard MDL files must still use a relative `./` path and be bundled
> inside the asset folder.

### 2. Create the package

After stamping validation results, run `simready-package`. It validates that the
asset folder can be turned into a self-contained package. In particular, it
checks that:

- USD references use relative paths and resolve to files inside the asset folder;
  they must not use absolute paths, search paths, or escape the folder.
- The root USD has a representative PNG thumbnail at
  `.thumbs/256x256/<root-usd-file>.usd.png` next to it.

Failing either packaging check is a **hard failure**. `simready-package` exits
with a nonzero status, and submission must stop. Fix the rejected asset or
remove it from the batch before restarting; do not upload or submit it as a
package.

If these checks pass, `simready-package` adds
`com.nvidia.simready.packaging.json` and verifies the generated package
definition.

For a detailed walkthrough, see the
[SimReady packaging workflow guide](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/packaging_workflow.md).

For one asset:

```bash
simready-package \
  --project-config <SimreadyDir>/simready-foundation/sample_content/project_config.toml \
  --name simready.hf.<namespace>.<dataset>.<logical-asset-name> \
  --version <asset-version> \
  --license cc-by-4.0 \
  <path-to-asset>
```

`<asset-version>` can be any string. We recommend the format `YYYY.MM.DD_NN`
(e.g. `2026.07.29_00`), where `NN` is a two-digit sequence number for
same-day revisions.

You may also specify one or more `--root-usd <rel-path>` parameters to
explicitly list the main interface USD files of the asset. This is strongly
recommended when your asset has multiple root USD files that reference one
another:

```bash
simready-package \
  --project-config <SimreadyDir>/simready-foundation/sample_content/project_config.toml \
  --name simready.hf.<namespace>.<dataset>.<logical-asset-name> \
  --version <asset-version> \
  --license cc-by-4.0 \
  --root-usd <intermediate-folder>/<root-usd-file>.usd \
  <path-to-asset>
```

For a batch, create `packages.toml` with one block per asset:

```toml
[[packages]]
name = "simready.hf.<namespace>.<dataset>.<logical-asset-name>"
version = "<asset-version>"
license_id = "cc-by-4.0"
source = "<path-to-asset>"

[[packages]]
name = "simready.hf.<namespace>.<dataset>.<another-logical-asset-name>"
version = "<another-asset-version>"
license_id = "cc-by-4.0"
source = "<path-to-another-asset>"
```

Then run:

```bash
simready-package \
  --project-config <SimreadyDir>/simready-foundation/sample_content/project_config.toml \
  --batch-file packages.toml
```

The package name must have the form
`simready.hf.<HF dataset namespace>.<HF dataset name>.<logical-asset-name>` to
ensure global uniqueness. Together with `--version <asset-version>`, this writes
the following package ID into the package definition:

```text
simready.hf.<HF dataset namespace>.<HF dataset name>.<logical-asset-name>.<asset-version>
```

During submission, the package name, Hugging Face dataset, and top-level asset
folder are checked for consistency. Submission fails if they do not match, if
the folder name includes the asset version, or if no asset version is provided.

Example: for the dataset `nvidia/fruits`, folder `apple`, and asset version
`2026.07.10_00`, use package name `simready.hf.nvidia.fruits.apple` and version
`2026.07.10_00`. The resulting package ID is
`simready.hf.nvidia.fruits.apple.2026.07.10_00`.

The `--license` value is stored in the package definition and must match the
license [configured for the Hugging Face dataset](https://huggingface.co/docs/hub/en/repositories-licenses).
`cc-by-4.0` is recommended. In batch mode the same value is specified as
`license_id` in the TOML file.

After the command has successfully executed, the SimReady packaging files have
been added to your asset:

```text
<logical-asset-name>/
├── com.nvidia.simready.packaging.json
└── <intermediate-folder>/
    ├── <root-usd-file>.usd
    ├── .thumbs/256x256/<root-usd-file>.usd.png
    ├── materials/
    └── textures/
```

## Submitting a SimReady package to the SimReady Catalog

### Step 1: Upload the asset package to your Hugging Face dataset

Upload the package to Hugging Face using the Hugging Face CLI. Use the short
logical asset name as the folder in the dataset; the globally qualified package
ID remains inside `com.nvidia.simready.packaging.json`:

```bash
hf upload <namespace>/<dataset> <path-to-asset> <logical-asset-name> --repo-type dataset --delete "*"
```

- This copies the asset package to your Hugging Face dataset into the
  top-level folder `<logical-asset-name>`.
- The top-level folder must exactly match the logical asset name used in the
  package name. It must not include the asset version; otherwise submission
  fails.
- The `--delete "*"` option removes all files already in the folder
  `<logical-asset-name>`. It is safe to include on a first upload (it is a no-op
  when the folder is empty) and is required when updating an existing asset so
  that files removed in the new version do not linger. The pattern is matched
  relative to the upload folder, so it must be `"*"`; a repo-rooted pattern such
  as `"./<logical-asset-name>/*"` matches nothing. For the same reason, pass the
  folder as `<logical-asset-name>` and not `./<logical-asset-name>`: a leading
  `./` is normalized on upload but not when computing deletions, so it would
  leave the delete a silent no-op.
- It is required that each asset package is stored in its own folder, and that
  folder cannot contain other asset packages. Do **not** upload an asset into
  the subtree of another asset.

The last line of the output shows a URL for the commit you just created:

```text
url: https://huggingface.co/datasets/<namespace>/<dataset>/commit/<commit-hash>
```

Construct the fixed-version asset URL by replacing `/commit/` with `/resolve/`
and appending the asset path:

```text
# From upload output:
url: https://huggingface.co/datasets/nvidia/fruits/commit/abc123

# Fixed-version asset URL:
https://huggingface.co/datasets/nvidia/fruits/resolve/abc123/apple/com.nvidia.simready.packaging.json
```

This fixed-version Hugging Face URL is what you submit to NVIDIA to include the
asset in the SimReady Catalog.

### Step 2: Submit assets for review for inclusion in the SimReady Catalog

1. For any given dataset, list the asset versions you want to be part of the
   SimReady Catalog. For each asset version, add its fixed-version Hugging Face
   URL to a file, one line per asset.

   - This file is the definitive list of all assets from that dataset (from the
     current revision and from previous ones) that you want to be part of the
     SimReady Catalog.
   - If you previously submitted assets from the same dataset and their URLs are
     no longer in the list, those assets will be removed from the SimReady Catalog.

   The file must adhere to the following format:

   ```text
   https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash-1>/<logical-asset-name-1>/com.nvidia.simready.packaging.json
   https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash-1>/<logical-asset-name-2>/com.nvidia.simready.packaging.json
   https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash-1>/<logical-asset-name-3>/com.nvidia.simready.packaging.json
   https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash-2>/<logical-asset-name-1>/com.nvidia.simready.packaging.json
   https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash-2>/<logical-asset-name-2>/com.nvidia.simready.packaging.json
   ```

   Rules for the file:

   - The `<namespace>/<dataset>` must be identical on every line.
   - Each line, when entered into a browser, yields the contents of the package
     definition file.
   - Add later commit hashes to the bottom.
   - It is good practice to leave old versions in the file, unless there is a very
     good reason to remove them — users of your assets might still use the older
     versions.
   - The commit hashes are allowed to be different for different lines.
   - The asset name does not need to be unique across lines, and it does not need
     to be unique across submissions: two submitters may both publish a folder
     called `chair`. What must be unique is the package ID, which carries the
     namespace and dataset that tell those two apart.
   - The combination of commit hash + asset name MUST be unique on each line.
   - The package ID declared in each package definition MUST be
     `simready.hf.<namespace>.<dataset>.<asset name>.<version>`, matching the file
     name of this submission file and the asset folder in the URL, and no two
     lines may declare the same package ID. Package IDs are compared ignoring
     case. Publishing a changed asset therefore means giving it a new version,
     not re-using the old one at a new commit.
   - Neither the Hugging Face namespace nor the dataset name may contain a dot.
     This is a SimReady requirement, not a Hugging Face one, and it is what keeps
     the package ID unambiguous. A dataset whose name contains a dot has to be
     renamed before it can be submitted. Asset folder names and versions may
     contain dots.
   - There MUST be only one submission file per dataset, including files whose
     names differ only in capitalisation. Hugging Face finds a dataset whatever
     capitalisation you ask for, so `submissions/alice/assets` and
     `submissions/Alice/Assets` would be two entries for one dataset.
   - Every URL MUST use the host `huggingface.co`. Hugging Face also answers to
     `hf.co` and `www.huggingface.co`, and those links work, but the catalog uses
     one spelling.
   - The capitalisation of the namespace and dataset MUST be the same in the file
     name and in every URL in it, so that everyone downstream sees one spelling.
     Capitalisation inside a dataset is a different matter: the asset folder and
     file name in a URL are stored in git and MUST match exactly.
   - The dataset MUST be public. The checks download every package definition
     from Hugging Face without credentials, as everyone using the catalog does,
     so a private or gated dataset is rejected.
   - The preferred sorting order is: ascending date associated with the commit hash
     (newer commit hashes last), then ascending lexicographic order by asset name.

2. Update the `simready-central` folder created during setup and create a branch
   for the submission:

   ```bash
   cd <SimreadyDir>/simready-central
   git switch main
   git pull --ff-only
   git switch -c submit-<HF-dataset-namespace>-<HF-dataset-name>
   ```

3. Copy the complete definitive list to the path formed from `submissions/`
   followed by the Hugging Face dataset name, then open a pull request. Provide
   one short sentence describing what the submitted asset set contains; the
   dataset and package names alone may not give reviewers enough context.

   ```bash
   mkdir -p submissions/<HF-dataset-namespace>
   cp <file-name> submissions/<HF-dataset-namespace>/<HF-dataset-name>
   git add submissions/<HF-dataset-namespace>/<HF-dataset-name>
   git commit -s -m "Submit packages from <namespace>/<dataset>"
   git push -u origin HEAD
   gh pr create --title "Submit packages from <namespace>/<dataset>" --body "Submits <number> packaged assets from <namespace>/<dataset> for NVIDIA review. Assets: <one-sentence description of the asset set>. Validation profile: <profile>. Packaging failures excluded: <number or none>."
   ```

   > Note: `git commit -s` adds a Signed-off-by trailer to the commit, which is
   > required by this repository's contribution policy.

   Review the title and short description before running the command. Adjust
   them when the pull request covers more than one dataset.

   For example, the definitive list for `nvidia/simready-assets` is stored at
   `submissions/nvidia/simready-assets`.

NVIDIA will start its review from the GitHub pull request and keep you updated
through comments on that request.

## Updating a package

Use the same process described above for a new asset version:

1. [Validate and package the updated asset](#creating-a-simready-package-from-your-local-data)
   with a new `<asset-version>`.
2. [Upload it to the same logical asset folder](#step-1-upload-the-asset-package-to-your-hugging-face-dataset).
   The upload creates a new commit and fixed-version asset URL.
3. [Submit the updated definitive list](#step-2-submit-assets-for-review-for-inclusion-in-the-simready-catalog).
   First pull the latest list from the repository:

   ```bash
   cd <SimreadyDir>/simready-central
   git switch main
   git pull --ff-only
   ```

   Your current list is at `submissions/<namespace>/<dataset>`. Add the new URL
   to the bottom, keep the URLs for older versions that should remain available,
   and remove an old URL only if the new version should fully replace it in the
   catalog.

## Removing a package

Removing a package starts at
[Step 2 above](#step-2-submit-assets-for-review-for-inclusion-in-the-simready-catalog);
there is no need to validate, package, or upload asset data again. First pull
the latest list from the repository:

```bash
cd <SimreadyDir>/simready-central
git switch main
git pull --ff-only
```

Your current list is at `submissions/<namespace>/<dataset>`. Remove the
fixed-version URL lines for the package versions that should leave the catalog,
and submit the complete updated list to the same path.

Removal takes effect only after NVIDIA reviews and merges the pull request. Do
not delete the package data from your Hugging Face dataset merely to remove it
from the catalog: existing users may still depend on its immutable URL.

## References

- [SimReady profiles](https://github.com/NVIDIA/simready-foundation/tree/main/nv_core/sr_specs/docs/profiles)
- [SimReady packaging workflow guide](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/packaging_workflow.md)
