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
[SimReady Foundation profiles](https://github.com/NVIDIA/simready-foundation), which validate them for use for various
purposes (see [Choosing a profile](#choosing-a-profile) below).

A [skill](../.agents/skills/submit-simready-assets/SKILL.md) is also available to guide you through the process with agent support.

## Preconditions

Before you start, make sure that:

- You have completed the [OEM onboarding process with NVIDIA](./onboarding.md).
- You have a Hugging Face account.
- You have a GitHub account with write access to
  `NVIDIA-Omniverse/simready-central`, and a token for it. The GitHub CLI (`gh`)
  is needed to [remove a package](#removing-a-package), and is one of two ways
  to [complete the pull request description](#submitting-a-simready-package-to-the-simready-catalog).
- Git is available on your command-line path.
- **Python 3.12** is available and on your command-line path. Not 3.11, and not
  3.13 or newer: the benchmark step requires 3.12, while `usd-validation-nvidia`
  (via `simready-validate`) declares `Requires-Python: >=3.11,<3.13`.
- Windows 10 or 11, or Linux, on a 64-bit x86 machine, with around 40 GB of free
  disk space. The benchmark step runs your assets in Isaac Sim, which is
  installed into the environment below.
- Your git identity is configured:
  ```bash
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```  

## One-time setup

You only need to do this setup once. Replace `<SimreadyDir>` with an absolute
path to a directory of your choosing. The directory must exist.

The setup below does the following:

- Creates **two** Python virtual environments: `.venv-submit` with the SimReady
  Foundation tier packages, `simready-manage` (which drives validation, the
  benchmark, packaging, and upload) and the Hugging Face CLI tools, and
  `.venv-isaac` with Isaac Sim. An `engines.toml` in your home directory tells
  the benchmark where Isaac Sim is (step 3).
- Creates a working directory that `simready-manage` uses for its own files,
  the packages it builds included, and the project configuration it reads.
- Authenticates the Hugging Face CLI with your account.
- Creates a Hugging Face dataset repository which will store your assets and
  declares the license that applies to them. The dataset starts **private**; you
  decide when to publish it, in
  [Publishing the dataset](#publishing-the-dataset) below.

### Linux

```bash
# 1. Submit venv: the tier packages (validators and profiles), the core tier's
#    [benchmark] extra (the benchmark and its bundled test suite), the benchmark's Isaac
#    Sim engine plugin, simready-manage and the Hugging Face tools. Isaac Sim
#    itself must NOT be installed here.
python3.12 -m venv <SimreadyDir>/.venv-submit
source <SimreadyDir>/.venv-submit/bin/activate
python -V                             # confirm 3.12 before installing
pip install --upgrade huggingface_hub
pip install "simready-foundation-tier-core[benchmark]~=2026.8.0" \
  "simready-foundation-tier-aif~=2026.8.0" "simready-foundation-tier-sensors~=2026.8.0" \
  "simready-manage~=2026.8.0" "simready-validate~=2026.8.0" "simready-package~=2026.8.0" \
  "simready-benchmark~=2026.8.0.post1" "simready-benchmark-engine-kit~=2026.8.0"
deactivate

# 2. Isaac Sim venv: Isaac Sim alone, from NVIDIA's package index (downloads
#    several GB).
python3.12 -m venv <SimreadyDir>/.venv-isaac
source <SimreadyDir>/.venv-isaac/bin/activate
python -V                             # confirm 3.12 before installing
pip install "isaacsim[all,extscache]==6.1.0.0" --extra-index-url https://pypi.nvidia.com
deactivate

# 3. Tell the benchmark where Isaac Sim is. simready-benchmark reads this file
#    from your home directory on every run. One entry per physics engine, both
#    pointing at the same Isaac Sim, which the benchmark starts through that
#    venv's Python.
mkdir -p ~/.simready-benchmark
cat > ~/.simready-benchmark/engines.toml << 'EOF'
[kit.isaac_sim]
executable_path = "<SimreadyDir>/.venv-isaac/bin/isaacsim"
python_executable = "<SimreadyDir>/.venv-isaac/bin/python"
version = "6.1.0.0"
tags = ["isaac", "kit"]

[kit.isaac_sim_newton]
executable_path = "<SimreadyDir>/.venv-isaac/bin/isaacsim"
python_executable = "<SimreadyDir>/.venv-isaac/bin/python"
version = "6.1.0.0"
tags = ["isaac", "kit"]
experience = "isaacsim.exp.full.newton"
EOF
source <SimreadyDir>/.venv-submit/bin/activate
simready-benchmark --show-config      # both engines must be listed as [ok]

# Create the working directory simready-manage uses for its own files, and its
# project configuration. The [validate] section is empty on purpose: with no
# paths in it, the validator and the packager load the profiles of the installed
# tier packages.
mkdir -p <SimreadyDir>/workspace
cat > <SimreadyDir>/workspace/project_config.toml << 'EOF'
[project_root]
setting = "."

[content_root]
name = "content"

[validate]
EOF

# Authenticate with Hugging Face
# Note: you can ignore the "token not saved to git credentials" warning.
hf auth login  # follow the instructions on the command line.
               # Documentation: https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login

# A GitHub token with write access to simready-central, used to open the
# submission pull request. Set it in every shell you run submit from.
export GITHUB_TOKEN=<your GitHub token>

# Create a Hugging Face dataset repository which will store the assets
# (skip if you already have one). It is created private, so you can prepare and
# check your assets before publishing anything.
# <namespace>/<dataset> is your Hugging Face username or org name followed by the dataset name,
# e.g. nvidia/simready-assets. For a personal repo you may omit the namespace.
hf repos create --type dataset --private <namespace>/<dataset>

# Declare the dataset license in the dataset card (must match the --license you pass to
# simready-manage submit). Choose cc-by-4.0. Create a local README.md and upload it:
cat > /tmp/hf-dataset-readme.md << 'EOF'
---
license: cc-by-4.0
---
EOF
hf upload <namespace>/<dataset> /tmp/hf-dataset-readme.md README.md --repo-type dataset
```

### Windows (PowerShell)

```powershell
# Use the `py` launcher to pin the version. A bare `python` may resolve to 3.13+,
# which makes the package install below fail. `py -0p` lists what is installed.

# 1. Submit venv: the tier packages (validators and profiles), the core tier's
#    [benchmark] extra (the benchmark and its bundled test suite), the benchmark's Isaac
#    Sim engine plugin, simready-manage and the Hugging Face tools. Isaac Sim
#    itself must NOT be installed here.
py -3.12 -m venv <SimreadyDir>\.venv-submit
<SimreadyDir>\.venv-submit\Scripts\Activate.ps1
python -V                               # confirm 3.12 before installing
pip install --upgrade huggingface_hub
pip install "simready-foundation-tier-core[benchmark]~=2026.8.0" `
  "simready-foundation-tier-aif~=2026.8.0" "simready-foundation-tier-sensors~=2026.8.0" `
  "simready-manage~=2026.8.0" "simready-validate~=2026.8.0" "simready-package~=2026.8.0" `
  "simready-benchmark~=2026.8.0.post1" "simready-benchmark-engine-kit~=2026.8.0"
deactivate

# 2. Isaac Sim venv: Isaac Sim alone, from NVIDIA's package index (downloads
#    several GB).
py -3.12 -m venv <SimreadyDir>\.venv-isaac
<SimreadyDir>\.venv-isaac\Scripts\Activate.ps1
python -V                               # confirm 3.12 before installing
pip install "isaacsim[all,extscache]==6.1.0.0" --extra-index-url https://pypi.nvidia.com
deactivate

# 3. Tell the benchmark where Isaac Sim is. simready-benchmark reads this file
#    from your home directory on every run. One entry per physics engine, both
#    pointing at the same Isaac Sim, which the benchmark starts through that
#    venv's Python. Write the paths with forward slashes, as TOML wants them.
New-Item -ItemType Directory -Force -Path $env:USERPROFILE\.simready-benchmark
@"
[kit.isaac_sim]
executable_path = "<SimreadyDir>/.venv-isaac/Scripts/isaacsim.exe"
python_executable = "<SimreadyDir>/.venv-isaac/Scripts/python.exe"
version = "6.1.0.0"
tags = ["isaac", "kit"]

[kit.isaac_sim_newton]
executable_path = "<SimreadyDir>/.venv-isaac/Scripts/isaacsim.exe"
python_executable = "<SimreadyDir>/.venv-isaac/Scripts/python.exe"
version = "6.1.0.0"
tags = ["isaac", "kit"]
experience = "isaacsim.exp.full.newton"
"@ | Set-Content $env:USERPROFILE\.simready-benchmark\engines.toml
<SimreadyDir>\.venv-submit\Scripts\Activate.ps1
simready-benchmark --show-config        # both engines must be listed as [ok]

# Create the working directory simready-manage uses for its own files, and its
# project configuration. The [validate] section is empty on purpose: with no
# paths in it, the validator and the packager load the profiles of the installed
# tier packages.
New-Item -ItemType Directory -Force -Path <SimreadyDir>\workspace
@"
[project_root]
setting = "."

[content_root]
name = "content"

[validate]
"@ | Set-Content <SimreadyDir>\workspace\project_config.toml

# A GitHub token with write access to simready-central, used to open the
# submission pull request. Set it in every shell you run submit from.
$env:GITHUB_TOKEN = "<your GitHub token>"

# Authenticate with Hugging Face
# Note: you can ignore the "token not saved to git credentials" warning.
hf auth login  # follow the instructions on the command line.
               # Documentation: https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login

# Create a Hugging Face dataset repository which will store the assets
# (skip if you already have one). It is created private, so you can prepare and
# check your assets before publishing anything.
hf repos create --type dataset --private <namespace>/<dataset>

# Declare the dataset license in the dataset card (must match the --license you pass to
# simready-manage submit). Choose cc-by-4.0. Create a local README.md and upload it:
@"
---
license: cc-by-4.0
---
"@ | Set-Content hf-dataset-readme.md
hf upload <namespace>/<dataset> hf-dataset-readme.md README.md --repo-type dataset
```

> **The dataset has to be public and ungated by the time you submit.** NVIDIA's
> submission checks download every package definition from Hugging Face without
> credentials, exactly as everyone using the catalog does, so a private or gated
> dataset is rejected. It does not have to be public while you prepare: uploading
> to a private dataset works, and making it public afterwards keeps every commit
> hash valid, so nothing needs re-uploading. Publishing is a deliberate step you
> take in [Publishing the dataset](#publishing-the-dataset), once you have seen
> what the dataset contains.

> Note (Windows): if activating the venv fails with an execution-policy error,
> allow scripts for the current session and retry:
>
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
> ```
>
> Alternatively, from `cmd.exe`, activate with `<SimreadyDir>\.venv-submit\Scripts\activate.bat`.

> Note (Windows): when staging files with `git add`, you may see a warning that
> "LF will be replaced by CRLF". This is harmless and can be ignored.

> Note: the commands in the rest of this guide use bash syntax. Windows users
> should run them in Git Bash, or adapt them to PowerShell.

> Note: if the install spends a long time backtracking
> ("pip is looking at multiple versions ... This could take a while") and then
> fails with:
>
> ```text
> ERROR: Cannot install simready-manage because these package versions have conflicting dependencies.
>     simready-validate 2026.6.3 depends on usd-validation-nvidia>=1.20.0
> Additionally, some packages in these conflicts have no matching distributions
> available for your environment:
>     usd-validation-nvidia
> ```
>
> the venv is on an unsupported Python. The message is misleading: these are
> `py3-none-any` wheels, so this is a `Requires-Python` mismatch, not a platform
> or architecture one. Run `python -V`; if it does not report 3.12, delete
> the venv, recreate it with a 3.12 interpreter as shown above, and rerun the
> `pip install` commands.

## Creating a SimReady package from your local data

`simready-manage submit` runs the whole local pipeline for each asset —
validation, the benchmark, packaging with a bill of materials — and then uploads
the package to your Hugging Face dataset. It finishes by writing a text file with one commit-pinned
URL per successfully submitted asset, which is the input to
[the submission step](#submitting-a-simready-package-to-the-simready-catalog).

This guide covers only what the SimReady Catalog requires of a submission. For
details on `simready-manage` itself, see `simready-manage submit --help`.

### Prepare the asset

The data for each asset (and only the data for that asset) is in a folder on
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

> `simready-manage submit` takes asset folders — the folder that *contains* the
> intermediate folder shown above. Name the root USD after the asset folder
> (`apple/simready_usd/apple.usd`): when several USD files sit side by side,
> that is how the root one is identified.

> If the thumbnail is missing, render one into the path shown above following
> the [Thumbnail Guidelines](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/thumbnail_guidelines.md)
> of SimReady Foundation.

### Choosing a profile

`--profile` is required, and its value is your choice: pick the profile that
matches what your asset is for. The available profiles, what each one requires,
and how to decide between them are documented in SimReady Foundation:

- [SimReady Foundation](https://github.com/NVIDIA/simready-foundation) — its README lists the
  production profiles with a description of what each one targets, and links to
  the *Getting Started* and *Profiles Validation Workflow* guides, which cover
  choosing a profile and reading its validation results.

A profile has versions. Validation always uses the **newest version in the
installed tier packages**; do not pass `--profile-version`. A newer version can
require features that an asset validated against an older version does not
have; the report `submit` writes names them.

A profile may also require provenance metadata authored in the asset itself;
what that is and where to find a sample is summarized in the
[submission requirements](foundations-spec.md#validation-requirements).

Use the same profile for every asset in one `submit` run. The version it was
validated against is recorded in the asset's `<logical-asset-name>_validation.json`
sidecar (`profile_version`).

### Submit one asset

```bash
simready-manage \
  --project-config <SimreadyDir>/workspace/project_config.toml \
  submit <path-to-asset> \
  --hf-dataset <namespace>/<dataset> \
  --profile <profile-id> \
  --asset-version <asset-version> \
  --license cc-by-4.0 \
  --url-file <SimreadyDir>/submission-<asset-version>.txt
```

- `--project-config` names the configuration file created during setup.
  `simready-manage` keeps its working files next to it, the packages it builds
  under `workspace/.packages/` included.
- The package carries a bill of materials: the list of every file in the
  package with its SHA-256 hash. The checks compare it with what was uploaded
  and reject a package without one or with a file that does not match
  ([SUB.028](acceptance-criteria.md#sub028---asset-path-does-not-match-its-bill-of-materials)).
- `--hf-dataset` is the public dataset you created during setup. It also
  determines the package name, which is always
  `simready.hf.<namespace>.<dataset>.<logical-asset-name>`.
- `--asset-version` sets the version for every asset in the run. We recommend the
  date, `YYYY.MM.DD` (e.g. `2026.07.29`); a `_NN` sequence number is appended
  automatically, so that becomes `2026.07.29_00`. To distinguish a second version
  of an asset on the same day, give the suffix yourself: `2026.07.29_01`.
  While you are still working on an asset, you can re-run `submit` with the same
  version as often as you like — each run simply uploads the current state. The
  same is true if a submission is rejected: the pull request is closed, and you
  submit again at the same version. Only once a pull request is **merged** is that
  version fixed in the catalog, and any later change needs a new one.
- To give one asset a different version from the rest of the run, append
  `@<asset-version>` to its path: `/data/assets/apple@2026.07.29_01`. This works
  for paths on the command line and inside a `--from-file` list.
- `--license` is stored in the package definition and must match the license
  [configured for the Hugging Face dataset](https://huggingface.co/docs/hub/en/repositories-licenses).
  Choose `cc-by-4.0`.
- `--url-file` is where the list of commit-pinned URLs is written. Without it the
  file is written into your working directory with a timestamped name.

Together, the package name and version form the package ID recorded in the
package definition:

```text
simready.hf.<namespace>.<dataset>.<logical-asset-name>.<asset-version>
```

Example: for the dataset `nvidia/fruits`, folder `apple`, and asset version
`2026.07.10`, the package ID is `simready.hf.nvidia.fruits.apple.2026.07.10_00`.

Neither the Hugging Face namespace nor the dataset name may contain a dot. This
is a SimReady requirement, not a Hugging Face one, and it is what keeps the
package ID unambiguous. A dataset whose name contains a dot has to be renamed
before it can be submitted. Asset folder names and versions may contain dots.

### Submit several assets

Pass several asset folders, or list them in a file with `--from-file` (one asset
folder per line; blank lines and `#` comments are ignored). You can combine both.

```text
# batch1_assets.txt - August 2026 submission
/data/assets/apple
/data/assets/pear
# /data/assets/wip_chair   <- not ready yet
```

```bash
simready-manage \
  --project-config <SimreadyDir>/workspace/project_config.toml \
  submit --from-file batch1_assets.txt \
  --hf-dataset <namespace>/<dataset> \
  --profile <profile-id> \
  --asset-version <asset-version> \
  --license cc-by-4.0 \
  --url-file <SimreadyDir>/submission-<asset-version>.txt
```

`--asset-version` applies to every asset in the run unless a path carries its own
`@<asset-version>` suffix, so a batch can mix versions:

```text
/data/assets/apple
/data/assets/pear@2026.07.29_01
```

A failure in one asset does not abort the run: the failing asset is skipped, its
error is printed, and the remaining assets are processed and uploaded. Only
successfully submitted assets appear in the URL file, and the command exits with
status 0 as long as one asset was uploaded, so read the printed errors. With
`--open-pr` (see [the submission step](#submitting-a-simready-package-to-the-simready-catalog))
the pull request is opened with the assets that passed. If that is not what you
want, close the pull request, fix the failed assets, and run the same command
again: a closed pull request puts nothing into the catalog, and the dataset
accepts a new submission as soon as it is closed.

### Controlling the validation and benchmark steps

`simready-manage submit --help` lists the flags that skip a step or stop its
result from blocking the rest. Two things matter for a submission:

- The skip and ignore flags are blunt: they disable a check for the whole asset,
  not for an individual requirement. Use them only when you have a specific
  reason, and say so in the pull request — NVIDIA reviews the submitted results.
- The benchmark step runs by default, and its results are part of every
  submission: a package without them is not accepted. It launches your asset in
  Isaac Sim and runs the test suite bundled with the Foundation tier, so it
  needs the Isaac Sim environment and the `engines.toml` from the setup
  section.
  `--skip-benchmark` leaves the results out, so it is for trying the pipeline,
  not for submitting. A failed test does not stop the submission: the asset is
  packaged and uploaded with its results, `submit`'s summary says so and names
  the report, and the pull request description gets a "Benchmark results"
  section for NVIDIA's review; add what you know about the failures to the
  notes. `--ignore-benchmark-results` matters only when the benchmark could not
  run at all: it uploads the asset without results, which is not accepted. Run
  `simready-benchmark --help` for its own options — its results land in
  `.metadata/` beside the asset, described under
  [What the command produces](#what-the-command-produces).
- The benchmark is slow: expect about **15 minutes per asset** on a desktop GPU
  (RTX 3080 Ti), since the suite runs once per physics engine, and `submit`
  prints nothing while it waits. Plan a batch of ten assets as a matter of hours,
  and run it detached or overnight.
- The benchmark's report (HTML pages, per-test results, engine logs, videos) is
  kept under `<SimreadyDir>/workspace/benchmark_reports/<logical-asset-name>/`,
  written afresh on every run; open its `index.html`. When the benchmark could
  not run at all, the failure line quotes its message. For the full event
  stream (per-test results, engine readiness), run the benchmark by hand from
  `.venv-submit`:
  ```bash
  simready-benchmark --assets <path-to-asset>/<intermediate-folder>/<root-usd-file>.usd \
    --output-dir <SimreadyDir>/benchmark-debug --no-stamp
  ```

> **When the benchmark could not start Isaac Sim**, `submit` stops with that
> asset and quotes the message. Run `simready-benchmark --show-config` from
> `.venv-submit`: both engines must be `[ok]`, with the launcher in
> `<SimreadyDir>/.venv-isaac`; if they are not, the `engines.toml` from the
> setup section is missing or names a wrong path. Every physics test on the
> `isaac_sim_newton` engine (the drop, joint and grasp tests) failed with
> "Newton could not initialize this asset's physics scene" is, by contrast, a
> finding about the asset: Newton could not build its physics scene from it,
> and the engine log in the benchmark's report says why.


> **Note on verbose output:** the tools print detailed INFO-level logging while
> they load the profile ruleset. This is normal. Pass `-v` for more detail, or
> redirect stderr to a log file to keep your terminal readable:
> ```bash
> simready-manage ... submit ... 2>submit.log
> ```

### What the command produces

`submit` runs the validator and the benchmark on your asset and stores their
results in a `.metadata/` folder inside your asset folder. Every run of `submit`
clears that folder and writes it afresh, so keep nothing of your own in it. Your
asset's own files are not changed. `submit` then builds the package under
`<SimreadyDir>/workspace/.packages/` and uploads it as the top-level folder
`<logical-asset-name>` of your dataset. What is uploaded:

```text
<logical-asset-name>/
├── com.nvidia.simready.packaging.json                         # the package definition
├── .metadata/
│   ├── com.nvidia.simready.packaging.bom.json                 # bill of materials: every file below, with its hash
│   ├── com.nvidia.simready.conformance.Package@<version>.json # the packaging profile's result
│   ├── <logical-asset-name>_validation.json                   # validation results
│   └── <logical-asset-name>_benchmark.json                    # benchmark results, when that step ran
└── <intermediate-folder>/                                     # your data, untouched
    ├── <root-usd-file>.usd
    ├── .thumbs/256x256/<root-usd-file>.usd.png
    ├── materials/
    └── textures/
```

The URL file receives one line per asset:

```text
https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash>/<logical-asset-name>/com.nvidia.simready.packaging.json
```

This fixed-version Hugging Face URL is what you submit to NVIDIA to include the
asset in the SimReady Catalog.

When validation fails, `submit` stops with that asset before packaging and
uploads nothing for it. Its results stay in `.metadata/`, the report
`<logical-asset-name>_validation.html` included: open that to see what failed.
A failed benchmark test does not stop `submit`: the asset is packaged and
uploaded with its `<logical-asset-name>_benchmark.json`, and the benchmark's
own report stays under `<SimreadyDir>/workspace/benchmark_reports/<logical-asset-name>/`.
Only a benchmark that could not run at all stops the asset the way a failed
validation does. For an asset that is uploaded, `.metadata/` ends up holding
the JSON results only: the validation report is removed when the package is
built.

## Submitting a SimReady package to the SimReady Catalog

`simready-manage submit` can carry the submission all the way to a pull request.
Add these options to the command from the previous section:

```bash
  --central-index-repo https://github.com/NVIDIA-Omniverse/simready-central \
  --central-index-branch main \
  --open-pr \
  --oem-org "<your organisation>" \
  --oem-contact "<your name> <you@example.com>"
```

With `--open-pr`, `submit` fetches the current
`submissions/<namespace>/<dataset>` list from SimReady Central, adds your new
package URLs to it, and opens a pull request with the updated list. The list
holds the assets that passed; when some failed, see
[Submit several assets](#submit-several-assets) for how to proceed. The commit
carries a `Signed-off-by` trailer taken from your git identity, which this
repository's contribution policy requires; without a git identity `submit`
does not open the request, prints why, and exits with status 1. The same
happens while another pull request for the dataset is open: `submit` uploads,
then stops with `Error during submission: central index: an open PR ... already
exists` and no summary — close or merge that request first.

The pull request description is generated from this repository's
[submission template](../.github/PULL_REQUEST_TEMPLATE/submission.md): the
profile, the number of assets added and the total listed, and the checklist
items `submit` can vouch for are filled in; when an asset's benchmark tests did
not all pass, a "Benchmark results" section says so. One part is left for you:
the "Notes for reviewers" placeholder. Replace it with one sentence on what the
asset set contains, any `--skip-*` / `--ignore-*` flags you passed and why, the
benchmark tests that failed and what you know about them, and anything else you
already know is wrong or unusual. Edit the description on the pull
request's page on GitHub, or with the GitHub CLI and the pull request URL that
`submit` printed:

```bash
gh pr view <pull-request-url> --json body --jq .body > pr-body.md
# replace the "Notes for reviewers" placeholder in pr-body.md, and change nothing else
gh pr edit <pull-request-url> --body-file pr-body.md
```

Set `GITHUB_TOKEN` in your environment first; `submit` uses it to push the branch
and open the request:

```bash
export GITHUB_TOKEN=<your GitHub token>   # needs write access to simready-central
```

### Publishing the dataset

Your dataset is private until you publish it, and NVIDIA's checks read it without
credentials, so a pull request cannot reference a private or gated dataset.

The first run therefore uploads your assets and then stops before opening the
pull request, with `Error: PR was not opened: the dataset is private ...` and
exit status 1. The uploads are kept; that is the point at which to look at what
you are about to publish. When you are satisfied:

```bash
hf repos settings <namespace>/<dataset> --repo-type dataset --no-private --gated false
```

Making a dataset public publishes everything in it, not only the assets you are
submitting — check that it holds nothing you did not intend to release.

The checks that gate a pull request read your dataset for themselves, so its
visibility decides the outcome — see
[SUB.018](acceptance-criteria.md#sub018---dataset-is-not-readable):

| Dataset | Accepted? |
|---|---|
| Public | Yes |
| Private | **No.** Only its owner can grant access, and the checks are not its owner. Sharing it with individuals does not help. |
| **Gated** — visitors must accept conditions | **No.** |

One warning about the error you may see. When the checks run with a token,
Hugging Face answers a request for a dataset it cannot read with **404, not 403**
— it hides the dataset's existence rather than admitting the refusal. The checks
translate that back into "exists but this CI cannot read it", so do not go
hunting for a typo in a name that is correct.

Then run exactly the same `submit` command again. The run re-validates and
re-uploads each asset — the validation sidecars are rewritten, so every asset
gets a new commit — and continues to open the pull request. The pull request
lists the URLs of this second run; the first run's URLs are superseded and can be
ignored.

### One open submission per dataset

While a pull request for `submissions/<namespace>/<dataset>` is open, do not
start another submission for the same dataset — not even for unrelated assets.
Each submission is built from the list the repository currently holds, so two open pull
requests would each omit the other's additions, and whichever merged second would
silently drop them. Wait for the open one to be merged or closed.

### What NVIDIA does next

NVIDIA reviews the pull request and keeps you updated through comments on it. One
of two things follows:

- **Accepted** — the pull request is merged, and those package versions are now
  fixed in the catalog.
- **Rejected** — the pull request is closed, with comments explaining what to
  fix. Correct the assets and submit again; that opens a fresh pull request.
  Nothing from a closed pull request reaches the catalog, so you can reuse the
  same asset version.

### About the definitive list

`submissions/<namespace>/<dataset>` is the complete, authoritative list of every
asset version from that dataset that you want in the SimReady Catalog — not just
the ones from your latest submission. Each line is an immutable URL to a package
definition:

```text
https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit-hash>/<logical-asset-name>/com.nvidia.simready.packaging.json
```

`submit` maintains that file for you, preserving the URLs already in it. Three
properties of it are worth knowing, because NVIDIA's checks enforce them:

- Older versions stay listed. Users of your assets may still depend on them, and
  an entry that disappears from the list is removed from the catalog.
- A package ID — `simready.hf.<namespace>.<dataset>.<asset>.<version>` — may
  appear only once in the file. That is why publishing a changed asset needs a
  new version rather than a new upload at the old one.
- There is one file per dataset, and the dataset must be public and ungated.

### Automated security checks

Opening the pull request runs two sets of checks. The first reads the submission
file itself, the package definition each URL names, and its bill of materials,
every file of which must be in the dataset with the recorded hash. The second
reads what those URLs publish, and enforces these rules on every submitted asset
folder:

- Every file MUST be one of `.usd`, `.usda`, `.usdc`, `.png`, `.jpeg`, `.jpg`,
  `.exr`, `.glb`, `.m4a`, `.mp3`, `.wav`, `.mdl`, `.toml`, `.json` or `.txt`. The
  extension is compared ignoring case, so `.PNG` is accepted. This applies only
  inside the folders you submit; the rest of the dataset, including its
  `README.md` dataset card, is not restricted.
- Every file MUST have content matching its extension. Renaming an archive or a
  program to look like a texture is rejected.
- The dataset MUST NOT have a `.lfsconfig` file at its root. That file tells Git
  LFS to fetch the dataset's content from a server other than Hugging Face, which
  the catalog cannot vouch for.

The content check reads the beginning of each file, which catches a file whose
name does not match what is in it. It cannot catch a file built to carry a valid
header and hide something after it. What the files hold is examined during the
review, which pulls every submitted package and audits its content — package
composition, credentials and internal paths left in text files, USD references
that leave the package, texture metadata and licence provenance — before
deciding the pull request.

Hugging Face scans uploads for leaked secrets and reports the results privately
to the dataset owner by email rather than publishing them. Acting on that email
stays your responsibility: never upload credentials, tokens, or private keys, in
any file.

## Updating a package

Use the same process for a new asset version: submit the updated asset with a new
`--asset-version`, and `submit` adds its URL to the list alongside the existing
entries. Remove an older URL only if the new version should fully replace it in
the catalog — see below.

## Removing a package

`submit` only adds entries, so removal is done by hand. There is no need to
validate, package, or upload asset data again — you are only editing the list.

```bash
gh repo clone NVIDIA-Omniverse/simready-central
cd simready-central
git switch -c remove-<HF-dataset-namespace>-<HF-dataset-name>
```

Your current list is at `submissions/<namespace>/<dataset>`. Delete the
fixed-version URL lines for the package versions that should leave the catalog,
keep every other line, then open a pull request:

```bash
git add submissions/<HF-dataset-namespace>/<HF-dataset-name>
git commit -s -m "Remove packages from <namespace>/<dataset>"
git push -u origin HEAD
gh pr create --base main --title "Remove packages from <namespace>/<dataset>" --body "Removes <number> package version(s) from <namespace>/<dataset>. Reason: <why>."
```

`git commit -s` adds the `Signed-off-by` trailer this repository requires; a
commit without it fails the checks
([SUB.027](acceptance-criteria.md#sub027---commit-is-not-signed-off)). The
one-open-submission rule applies here too: do not open a removal request while a
submission for the same dataset is open.

Removal takes effect only after NVIDIA reviews and merges the pull request. Do
not delete the package data from your Hugging Face dataset merely to remove it
from the catalog: existing users may still depend on its immutable URL.

## References

- [SimReady Foundation](https://github.com/NVIDIA/simready-foundation) — profiles, packaging
  requirements, and the validation guides. Start from its README, which links to
  each of them.
- [Submission requirements summary](foundations-spec.md)
- Every check, its id, and what it means: **[Acceptance criteria](acceptance-criteria.md)**.
  Quote the id when asking about a failure -- the id is stable, the wording under
  it is not.
