# Acceptance criteria

Every check a submission must pass. A rejection cites the id. [What a token means](#tokens), [how to add a rule](#adding-a-rule).

## 🔴 Submission errors

<sub><em>The submission file and the URLs in it. Each one blocks the merge until it is fixed.</em></sub>

### SUB.001 - Submission file is misnamed

- Name it `submissions/<namespace>/<dataset>`, after the dataset it catalogues.

### SUB.002 - Dot in the [name]

- Rename the dataset on Hugging Face.

> [!NOTE]
> A package id is built by joining these parts with dots, so a dot inside one splits the id in the wrong place and there is no way to tell afterwards which part was which.

### SUB.003 - Submission file is not UTF-8

- Re-save it as UTF-8.

### SUB.004 - Submission file is not an ordinary file

- Make it a plain file: replace a symbolic link or a submodule with the file's content.
- Clear an executable bit with `git update-index --chmod=-x <path>`, then commit.

### SUB.005 - Whitespace around the URL

- Remove the spaces or tabs so the URL is the whole line.

### SUB.006 - URL is for [dataset], not [expected dataset]

- The file name decides the dataset.
- Every URL in the file must name that same dataset.

### SUB.007 - Duplicate URL

- The same URL is listed twice. Remove one.

### SUB.008 - Commit block is split up

- Keep every URL for a commit together, in one run of lines.

> [!NOTE]
> The file is read as a history: one block per commit, in order. URLs for the same commit appearing in two places make the history ambiguous.

### SUB.009 - Assets out of order in [commit]

- Sort them by asset folder name.

### SUB.010 - Commit blocks are not oldest first

- Put the oldest first, so the file reads as history.

### SUB.011 - Package declares no licence

- Add one to `com.nvidia.simready.packaging.json` on Hugging Face.

### SUB.012 - Licence does not match [dataset]

- The package licence and the dataset licence disagree.
- Fix whichever is wrong; they are compared ignoring case.

> [!NOTE]
> The catalog publishes the licence it finds in the package. If that disagrees with the dataset it came from, one of the two is telling users the wrong thing about what they may do with the asset.

### SUB.013 - Unsupported package format [version]

- Re-package with a `format_version` these checks can read.

### SUB.014 - Package ID does not match where it is published

- Correct the `package_id` so it matches the dataset and asset folder it sits in.

### SUB.015 - Package ID has no version

- Give the `package_id` a version: `simready.hf.<namespace>.<dataset>.<asset folder>.<version>`.
- Upload the corrected package and submit the URL of the new commit.

### SUB.016 - Duplicate package ID

- Two lines declare the same one.
- Give the newer package a new version.

### SUB.017 - Two submission files for one dataset

- They differ only in capitalisation. Keep one.

> [!NOTE]
> Hugging Face answers to any capitalisation of a dataset name, so two files differing only that way would catalogue the same dataset twice. Paths inside a dataset are case-sensitive; that is SCAN.005.

### SUB.018 - [dataset] is not readable

- Make it public.

> [!NOTE]
> The catalog downloads packages from Hugging Face without special access, so a private dataset is one it cannot serve, and sharing it does not help -- only its owner can grant access. Hugging Face answers 404 rather than 403 here, so a name reported unreadable is not a typo to hunt for.

### SUB.019 - Pull request changes files outside submissions/

- Remove them. A submission changes files under `submissions/` and nothing else.

> [!NOTE]
> Documentation, the checks and everything else in the repository are changed by the people who maintain it, in their own pull requests. This is refused rather than reviewed, so a submission cannot carry an edit to the thing that is checking it.

### SUB.020 - [commit] is not on [dataset]'s main branch

- Merge it, or check the history was not rewritten.

### SUB.021 - Package definition has no package_id

- Add a `package_id` to `com.nvidia.simready.packaging.json`.
- Upload the corrected package and submit the URL of the new commit.

### SUB.022 - Package definition is not a JSON object

- The file has to be a JSON object. Re-run the [packaging workflow](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/packaging_workflow.md) rather than editing it by hand.
- Upload the result and submit the URL of the new commit.

### SUB.023 - Package definition is not valid JSON

- The file is not valid JSON. Re-run the [packaging workflow](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/packaging_workflow.md) rather than editing it by hand.
- Upload the result and submit the URL of the new commit.

A definition larger than 1 MB is refused the same way: a package definition is a few hundred bytes, so one that large was not written by the packaging workflow.

### SUB.024 - No package definition at the URL

- Check the asset folder name in the URL against the one in the dataset; they are case-sensitive.
- Check the URL names the commit the package was uploaded in.

### SUB.025 - Not a Hugging Face package URL

- Use the resolve URL of the package definition itself: `https://huggingface.co/datasets/<namespace>/<dataset>/resolve/<commit>/<asset folder>/com.nvidia.simready.packaging.json`.
- `resolve`, not `blob` or `commit`. The commit is the full 40-character hash, not a branch.

### SUB.027 - Commit is not signed off

- Add a `Signed-off-by: Your Name <you@example.com>` trailer, with the name and email of the commit's author, to every commit of the pull request: `git commit -s` writes it, `git commit --amend -s` adds it to an existing commit. Then push again.
- `simready-manage submit` writes the trailer itself, from your git identity or from `--commit-author-name` and `--commit-author-email`.

The trailer is the Developer Certificate of Origin sign-off that CONTRIBUTING.md asks of every contributor: it certifies that you may submit the change under the repository's licence. Merge commits are not checked, and neither is a pull request that changes no submission file.

### SUB.028 - [asset path] does not match its bill of materials

- Package with `simready-manage submit`, as the guide describes, so the upload carries `.metadata/com.nvidia.simready.packaging.bom.json` naming every file of the package with its SHA-256 hash.
- Do not change or re-upload files by hand after packaging. A file the bill names that is missing from the dataset, or differs from it, means the upload is not the package that was built: run `submit` again and submit the URL of the new commit.

NVIDIA's review compares a pulled package with its bill of materials; this check does the same against Hugging Face before review, so what the reviewer pulls is the package that was built. Files the bill does not name are not judged here; the package scan rules say what a package may hold. A bill larger than 64 MB is not read and fails the package as well; open an issue if an asset really has that many files.

## 🔴 Package scan errors

<sub><em>What a package actually publishes on Hugging Face. Usually fixed by re-uploading rather than by editing the submission.</em></sub>

### SCAN.003 - File content does not match its name

- Find the file the scan named and replace it with one whose content matches its extension.
- Upload the corrected package and submit the URL of the new commit.

### SCAN.004 - File type a package may not contain

- Only USD, image, audio, `.glb`, `.mdl`, `.toml`, `.json` and `.txt` files.
- The extension is compared ignoring case, so `.PNG` is fine.

> [!NOTE]
> This applies only inside the folders you submit. The rest of the dataset, including its `README.md` dataset card, is not restricted.

### SCAN.005 - [asset path] holds no files

- Check the capitalisation of the folder name.

> [!NOTE]
> Paths inside a dataset are case-sensitive, unlike the dataset's own name. `Assets/Chair` and `assets/chair` are different folders.

### SCAN.006 - [dataset] has a .lfsconfig at its root

- Remove it.

> [!NOTE]
> It redirects Git LFS downloads to a server other than Hugging Face, which the catalog cannot vouch for.

## NVIDIA review

<sub><em>Once the pull request has passed the checks above, NVIDIA reviews the submitted assets before merging it. These rules are applied in that review, not by the checks; a reviewer cites the id in the comment that closes the pull request. One failing asset closes the whole pull request, and the corrected set is submitted again as a new one.</em></sub>

### REVIEW.001 - [asset path] carries no validation results

- Every package must carry the validation results for its asset, `.metadata/<asset>_validation.json`. Results that cannot be read count as missing.
- Package with `simready-manage submit`, as the guide describes: it validates the asset against the profile you choose and uploads the results inside the package. Do not remove or edit anything under `.metadata/` after packaging.

How validation works, and what each profile requires, is documented in SimReady Foundation: the [validation workflow](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/validate_workflow.md) and the profile and feature pages, which the tier packages install alongside the profiles.

### REVIEW.002 - [asset path] failed validation

- Every feature the asset's profile marks mandatory must pass validation. A feature the profile marks optional may stay unmet. The reviewer names the features that failed.
- Fix the asset until the named features pass, then run `submit` again. What failed is in the report `submit` wrote to the asset's `.metadata/` folder on your machine, `<asset>_validation.html`; it is not part of the uploaded package.

`submit` does not upload an asset that failed validation unless told to ignore the result, so a package refused here was uploaded with that flag or changed afterwards. What a profile requires, feature by feature, is in SimReady Foundation: the [profiles validation workflow](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/profiles_validation_workflow.md) and the profile and feature pages the tier packages install.

### REVIEW.003 - [asset path] carries no benchmark results

- Every package must carry the benchmark results for its asset, `.metadata/<asset>_benchmark.json`. Results with no tests in them count as missing.
- Package with `simready-manage submit` with the benchmark step on, as the guide describes: it runs the asset through the SimReady Foundation benchmark in Isaac Sim and uploads the results inside the package. Do not pass `--skip-benchmark` or `--ignore-benchmark-results` for a submission; if the benchmark could not run, fix the engine setup the guide describes and run `submit` again.

A failed benchmark test is not this rule: the results are uploaded with the asset, and the reviewer reads them, knowing which tests matter for which profile. What the benchmark runs and how to read its results is in the SimReady Foundation [benchmark guide](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/benchmark/benchmark.md).

### REVIEW.004 - [asset path] thumbnail has no black background

- The thumbnail at `.thumbs/256x256/<root-usd-file>.usd.png` must show the asset on a black background, and must be at least the size its folder names.
- Render the thumbnail again following the SimReady Foundation [thumbnail guidelines](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/thumbnail_guidelines.md), replace the file, and run `submit` again.

### REVIEW.005 - [asset path] thumbnail does not show the asset

- The thumbnail must be a picture of this asset, not a blank image or one copied from another asset.
- Render it from the asset itself, following the SimReady Foundation [thumbnail guidelines](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/thumbnail_guidelines.md), replace the file, and run `submit` again.

### REVIEW.006 - [asset path] does not load or simulate

- The root USD must open and render in an RTX renderer such as Isaac Sim, and an asset with physics must survive a simple drop onto the ground with the PhysX engine without falling apart, exploding or passing through the ground.
- Open the asset in Isaac Sim, reproduce what the reviewer describes, fix the asset, and run `submit` again.

Validation and the benchmark catch most of this, but not all of it: a reviewer opens every asset once. The reviewer's comment says what was seen.

## 🟠 Warnings

<sub><em>Advisory; nothing here blocks a merge. `WARN.*` ids are reserved for these and there are none yet. A finding with no id at all means the checks could not run, so re-run them.</em></sub>

## Tokens

A `[token]` is a value the checks fill in before you see it.

| Token | Is |
|---|---|
| `[dataset]` | The Hugging Face dataset, `namespace/name` |
| `[expected dataset]` | The dataset the submission file is named after |
| `[asset path]` | The asset folder inside the dataset |
| `[commit]` | The commit a URL points at, shortened |
| `[name]` | Whichever part of the name is at fault |
| `[version]` | The `format_version` in the package definition |

## Adding a rule

```markdown
### SUB.026 - [dataset] has no dataset card

- Add a `README.md` at the root of the dataset.

> [!NOTE]
> Optional. Shown nowhere; it says why the rule exists, for whoever has to satisfy it.
```

| Line | Answers | |
|---|---|---|
| `### <id> - <title>` | **what** is wrong | the rule, found by that pattern; the link is built from the heading |
| `- <line>` | **how** to fix it | enough to clear the failure without reading anything else. Three at most reach a screen |
| `> [!NOTE]` | **why** the rule exists | optional, and shown nowhere |

- Ids are `AREA.NNN`, three digits, never reused or renumbered. A new area needs no code change.
- An id here that no check raises fails CI, and so does one raised that is not here.
- `REVIEW.*` is the exception: those rules are decided by NVIDIA's reviewer and cited by hand, so no check raises them, and a check that starts to must move the entry out of that section.
