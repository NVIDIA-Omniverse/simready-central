<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# From a submission index to a local asset library

Each file under `submissions/` is an index: one line per package, pinned to the
Hugging Face commit it was reviewed at. This guide turns one such file into a
library you can browse on your own machine, in three steps:

1. Pull the packages with `simready-manage pull`.
2. Build `library.json`: one record per asset, holding what the package says
   about itself.
3. Render `library.html`: a page that filters and groups the records by license,
   category, validation result and benchmark result.

The point is to show where the data comes from. Every field in the library is
traced to a file in the package and to the tool or API that reads it, so you can
take the same reads into your own pipeline. It is an example over a handful of
assets, not a catalog service; see [Going further](#going-further) for that.

A [skill](../.agents/skills/index-to-library/SKILL.md) is also available to run
the steps with agent support.

## Preconditions

- The `.venv-submit` environment from the submission guide's
  [one-time setup](oem-asset-submission-guide.md#one-time-setup) (step 2 of the
  setup block). It provides `simready-manage`, the validator with its USD
  runtime, the SimReady Foundation tier package, and the Hugging Face tools.
  The benchmark environment and Isaac Sim are not needed here.
- A checkout of this repository, for the submission file and the two example
  scripts in `docs/examples/`.
- Read access to the dataset. A public dataset needs no account. For a private
  one, run `hf auth login` with an account that can read it.
- Disk space for the packages. They carry textures and the full benchmark
  result tree, roughly 150 MB per asset.
- A web browser.

The commands below use bash syntax; on Windows, run them from Git Bash as the
submission guide describes. Replace `<LibraryDir>` with an absolute path to a
directory of your choosing and `<checkout>` with the path of this repository.

```bash
source <SimreadyDir>/.venv-submit/bin/activate
simready-manage -V
mkdir -p <LibraryDir>/workspace
```

## Step 1: Pull the packages

```bash
simready-manage --root <LibraryDir>/workspace --content-root <LibraryDir>/workspace \
  pull --from-file <checkout>/submissions/<namespace>/<dataset> \
  --workspace <LibraryDir>/workspace --no-convert
```

For each URL in the file, `pull` downloads the asset folder at the commit pinned
in the URL, moves it to `<LibraryDir>/workspace/<asset>/` exactly as it was
published, and adds a line for it to `workspace/manifest.csv`. The run ends with:

```text
Pulled <N> / <N> package(s) into <LibraryDir>/workspace:
  <LibraryDir>/workspace/bin_waste_a01
  ...
```

`--root` and `--content-root` name the workspace `simready-manage` keeps its own
files in; every one of its commands needs them. `--no-convert` keeps each package
as the submitter published it. Without it, `pull` repackages the download, and
the repackaged copy carries a different package id and license than the one
listed in the index.

Check that every line of the submission file arrived: `manifest.csv` has one row
per pulled asset below its header. A URL that failed is reported in the log and
skipped, so a shorter manifest means a missing asset. A `401` on download means
the dataset is private and you are not logged in.

What one pulled package looks like:

```text
<LibraryDir>/workspace/
├── manifest.csv                                # one row per asset, written by pull
└── <asset>/
    ├── com.nvidia.simready.packaging.json      # package id and license
    ├── .metadata/
    │   ├── com.nvidia.simready.packaging.bom.json                 # bill of materials: every file with its hash
    │   ├── com.nvidia.simready.conformance.Package@<version>.json # the packaging profile's result
    │   ├── <asset>_validation.json             # per-feature validation results
    │   └── <asset>_benchmark.json              # per-test benchmark results
    └── simready_usd/                           # the asset's own folder, as submitted
        ├── <asset>.usd                         # root USD; SimReady_Metadata lives here
        ├── payloads/                           # the layers the root USD references
        ├── Textures/
        └── .thumbs/256x256/<asset>.usd.png     # thumbnail
```

The folder under `<asset>/` is whatever the submitter's asset folder held; the
name `simready_usd/` is SimReady Foundation's convention. Packages published
before `submit` kept that folder have the root USD, `payloads/` and `.thumbs/`
directly under `<asset>/`; the example script reads both layouts.

## Step 2: Build the library

```bash
python <checkout>/docs/examples/index_to_library.py \
  --workspace <LibraryDir>/workspace \
  --submission-file <checkout>/submissions/<namespace>/<dataset>
```

```text
<N> asset(s) written to <LibraryDir>/workspace/library.json
```

The script reads each package with the tools' own readers (see
[Where each field comes from](#where-each-field-comes-from)) and writes one
record per asset. The first five fields are the ones a consumer library has to
retain: where the package came from, who owns it, its version, its license, and
what it was validated as. The rest is everything else the package carries. A
trimmed record:

```json
{
  "asset_name": "box_cardboard_a01",
  "source_url": "https://huggingface.co/datasets/nvidia/simready-assets/resolve/2464ca.../box_cardboard_a01/com.nvidia.simready.packaging.json",
  "owner": "nvidia",
  "version": "2026.09.18_083633927809",
  "license": "cc-by-4.0",
  "validation": {
    "profile": "Robotics-Prop",
    "profile_version": "<version>",
    "passed": false,
    "features": [
      {"id": "FET_004_PHYSX", "display_name": "Simulate Multi-Body Physics (PhysX)",
       "runtime": "PhysX", "version": "0.4.0", "passed": false,
       "failing_requirements": [], "optional_requirements": ["com.nvidia.simready.RB.MB.001"]},
      "..."
    ],
    "failed_features": ["FET_000_MUJOCO", "FET_000_NEWTON", "FET_000_PHYSX", "FET_004_PHYSX", "..."]
  },
  "usd_author": "NVIDIA",
  "usd_license": "CC-BY 4.0",
  "category": "prop",
  "asset_type": "prop",
  "physics_variants": ["MuJoCo", "Newton", "PhysX"],
  "benchmark": {
    "passed": false,
    "results": [
      {"test_name": "grasp_and_lift", "engine": "isaac_sim", "engine_version": "6.1.0.0",
       "status": "fail", "passed": false, "duration": 41.2,
       "message": "All 1 grasp identifier(s) failed. ..."},
      {"test_name": "ground_drop", "engine": "isaac_sim_newton", "engine_version": "6.1.0.0",
       "status": "skipped", "passed": false, "duration": 0.0,
       "message": "Newton could not initialize this asset's physics scene ..."},
      "..."
    ]
  },
  "package": {"package_id": "simready.hf.nvidia.simready-assets.box_cardboard_a01.2026.09.18_083633927809", "..."},
  "usd_metadata": {"mass": 0.238, "qcode": "Q1395006", "rigid_body_count": 1, "..."},
  "thumbnail": "box_cardboard_a01/.thumbs/256x256/box_cardboard_a01.usd.png",
  "files": {"root_usd": "box_cardboard_a01/box_cardboard_a01.usd", "..."},
  "missing": []
}
```

`missing` names the sources the package did not carry: `packaging`,
`validation_sidecar`, `benchmark_sidecar`, `root_usd`, `usd_metadata`,
`thumbnail`, or `submission_url` when the asset is in the workspace but not in
the submission file. The record is written regardless, with `null` where the
data would be. An asset that lacks one of these is a finding about the asset,
and the page shows it as one; the script does not repair or invent anything.

The script also lists assets that are in the submission file but not in the
workspace (a URL that `pull` could not fetch) and the reverse.

## Step 3: Render the page

```bash
python <checkout>/docs/examples/library_page.py --library <LibraryDir>/workspace/library.json
```

This writes `library.html` next to the library. Open it directly in your
browser, or serve the workspace so the browser can load the thumbnails over
HTTP:

```bash
python -m http.server --directory <LibraryDir>/workspace 8000
# then open http://localhost:8000/library.html
```

The page has the library embedded in it and needs nothing else, but the
thumbnail paths in it are relative to the workspace. Keep it there, or the tiles
lose their pictures.

## Finding assets

The sidebar lists one collapsed group per facet; open one to see its values with
the number of assets behind each. Ticking values within a group widens the
selection; groups combine by narrowing it. The **Group by** selector splits the grid into one
section per value of a facet, so an asset failing three features appears under
each of the three. Clicking a tile opens the asset's record. It starts with a summary in plain
words (which features failed, which tests failed on which engine, what the
package lacks) and a link to the review report `verify()` wrote for the whole
workspace, which carries the link from each failing feature to its
specification, so the page does not repeat it. Below come the retained fields, every validation
feature with its result and the requirements it names, every benchmark test per
engine with its message, the USD metadata, and the paths of the files each part
was read from. The address bar carries the current
view, so a filtered, grouped page can be bookmarked or sent to someone who has
the same workspace.

Three searches worth trying, first on the page and then on the JSON, since a
consumer of the library does not need the page:

**Which assets may go into an index that admits CC-BY-4.0 only?** Tick
`cc-by-4.0` under *License (package definition)*. The license is the one the
package definition declares, which is what the submission checks compare against
the dataset card. The detail drawer shows the USD's own `asset_license` beside
it, because the two are written by different tools and spelled differently.

**Which assets fail which validation feature?** Set *Group by* to *Failed
validation feature*. Feature ids such as `FET_004_PHYSX` are shown with the
display name the installed tier gives them.

**Where does the benchmark disagree between engines?** Under *Benchmark test
result*, tick `ground_drop [isaac_sim]: fail` and `ground_drop [isaac_sim_newton]: skipped`.

The same three, over the library file:

```bash
python - <<'EOF'
import json, collections
library = json.load(open("<LibraryDir>/workspace/library.json"))
assets = library["assets"]

print("CC-BY-4.0:", [a["asset_name"] for a in assets if a["license"] == "cc-by-4.0"])

by_feature = collections.defaultdict(list)
for a in assets:
    for f in a["validation"]["features"]:
        if f["passed"] is False:
            by_feature[f["display_name"]].append(a["asset_name"])
for feature, names in sorted(by_feature.items()):
    print(f"{feature}: {len(names)} asset(s)")

for a in assets:
    for t in a["benchmark"]["results"]:
        if t["test_name"] == "ground_drop" and t["status"] != "pass":
            print(a["asset_name"], t["engine"], t["status"])
EOF
```

## Where each field comes from

Every reader is one function in `index_to_library.py`, named in the last column.

| Field(s) | Source | Read with | Function |
|---|---|---|---|
| `source_url`, `owner`, `package.repo_id`, `package.commit` | the submission file | `simready.manage.backends.hf_backend.parse_fixed_url`, the parser `pull` applies to the same lines | `read_submission_urls` |
| `validation.passed`, `validation.features`, `benchmark.passed`, `benchmark.results` | `manifest.csv`, then `.metadata/<asset>_validation.json` and `.metadata/<asset>_benchmark.json` in each package | `simready.manage.WorkspaceManager.verify()`, which returns one `VerifyResult` per manifest row and runs no tool | `read_sidecars` |
| `validation.profile`, `validation.profile_version`, `validation.simready_validate_version`, `validation.tier_versions` | the top level of `.metadata/<asset>_validation.json` | `json.load`; `verify()` does not return these | `read_sidecar_header` |
| `license`, `version`, `package.package_id` | `com.nvidia.simready.packaging.json` | `json.load`; the version is what follows `simready.hf.<namespace>.<dataset>.<asset>.` in the id | `read_packaging`, `package_version` |
| `usd_author`, `usd_license`, `category`, `asset_type`, `physics_variants`, `usd_metadata` | `SimReady_Metadata` in the root USD's `customLayerData` | `pxr.Sdf.Layer.FindOrOpen`; the same read `simready-manage` uses for its workspace cache. Only the root layer is opened | `find_root_usd`, `read_usd_metadata` |
| `thumbnail` | `.thumbs/256x256/<root usd>.png` | path check | `find_thumbnail` |
| `review_report` (library level) | `simready_review_report.html`, written into the workspace by `verify()` | path check | `collect` |
| `validation.features[].display_name`, `runtime` | the `features/` folder of every installed tier | the `simready.tier` entry-point group, which each tier registers under | `load_feature_names` |
| `tool_versions` | the environment | `importlib.metadata` | `tool_versions` |

Three things to know when reading the records:

- **Two licenses.** `license` is the package definition's, written by
  `simready-package` from the `--license` given at submission. `usd_license` is
  the asset's own `asset_license`, written when the asset was authored. The first
  is what the catalog checks; the second is shown so a mismatch is visible.
- **Two validation results.** `validation` is the submitted run, from the sidecar.
  The USD's `SimReady_Metadata` also carries a `validation` block, which the
  detail drawer shows under *stamped in the USD by the producer*: it is whatever
  was recorded when the asset was made, possibly against older feature ids, and
  it is not what the submission was checked with.
- **List fields are text in the sidecar.** The validator writes
  `failing requirements` and `optional requirements` as Python literals
  (`"['RB.MB.001']"`); `as_list` turns them into lists.

`verify()` also writes `simready_review_report.html` into the workspace, the same
per-asset report `simready-manage verify` produces, and the page links to it
from every asset. You can run that command yourself for the tabular view; the
library is built from the same readers.

## Going further

- **Another submission file.** Use one workspace per file: pull it, build its
  library, render its page. The scripts take the file and the workspace as
  arguments and hard-code neither.
- **Another facet.** A facet is one entry in the `FACETS` list at the top of
  `library_page.py`: a label and a small JavaScript expression over a record. To
  filter on a field the record does not carry yet, add it in `build_record` in
  `index_to_library.py` first.
- **More than a handful of assets.** The library is plain JSON, so the records
  can be loaded into whatever index you already run. NVIDIA's `simready.search`
  library filters the same feature and profile data at scale.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401` while pulling | the dataset is private | `hf auth login` with an account that can read it, then pull again |
| `No assets found in the workspace manifest.` | `pull` did not run, or ran against another directory | pull into the `--workspace` you pass to the script |
| `ModuleNotFoundError: No module named 'simready'` or `'pxr'` | the wrong Python | activate `.venv-submit` before running the scripts |
| tiles show *no thumbnail* although the packages have one | `library.html` was moved out of the workspace | keep it next to `library.json`, or serve the workspace over HTTP |
| an asset lists items under *Missing data* | the package does not carry that file or field | a finding about the asset; the submitter regenerates the package |

## References

- [OEM asset submission guide](oem-asset-submission-guide.md): the environment
  and how the packages, sidecars and thumbnails are produced.
- [Acceptance criteria](acceptance-criteria.md): the checks a submission passes
  before its URLs enter a submission file.
- [SimReady Foundation](https://github.com/NVIDIA/simready-foundation): the
  profiles, features and metadata the records refer to.
- `simready-manage pull --help` and `simready-manage verify --help`.
