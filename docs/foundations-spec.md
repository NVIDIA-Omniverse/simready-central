# SimReady submission requirements

This page summarizes the requirements enforced by the
[OEM asset submission guide](oem-asset-submission-guide.md), with every check
stated as a criterion in [Acceptance criteria](acceptance-criteria.md).
The guide contains the definitive
commands and examples.

## Asset layout

Each asset must be stored in a folder named for its logical asset name, without
the asset version. The supported submission profiles require exactly one
intermediate folder between that asset folder and its interface USD:

```text
<logical-asset-name>/
└── <intermediate-folder>/
    ├── <interface-usd>.usd
    ├── .thumbs/256x256/<interface-usd>.usd.png
    ├── materials/
    └── textures/
```

The intermediate folder may have any name. `simready_usd` is used in the guide's
examples.

## Validation requirements

Before packaging, the asset is validated against a SimReady profile. Which
profile to use is the submitter's choice and depends on what the asset is for;
the available profiles and how to choose between them are documented in
[SimReady Foundation](https://github.com/NVIDIA/simready-foundation); its README lists the
production profiles and links to the guides covering how to choose between them. The profile used must be stated in
the submission pull request.

A profile can also require SimReady provenance metadata authored in the root
USD: who made the asset, what it is, under which license, and a few measured
properties such as its size and mass. What is needed, where it may be stored,
and a helper that derives the measurable values are described in the
[FET_033_STANDARD conformance skill](https://github.com/NVIDIA/simready-foundation/blob/main/skills/simready-foundation-conform-fet-033-standard/SKILL.md);
[`sm_obs_lamp_revolute_a01_01.usda`](https://github.com/NVIDIA/simready-foundation/blob/main/sample_content/common_assets/props_general/obs_lamp_revolute_a01/simready_usd/sm_obs_lamp_revolute_a01_01.usda)
is a complete sample asset with the metadata in place, and the
[`FET_033_STANDARD` feature page](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/tiers/simready_foundation_tier_core/simready/foundation/tier_core/features/FET_033_STANDARD.md)
holds the contract itself. When the metadata is missing, validation reports it
under `FET_033_STANDARD` in the validation sidecar.

Validation records the profile and its feature-level results in a sidecar
published with the asset; the asset's USD files are not modified. A profile may
contain feature-level failures, which are recorded too, for NVIDIA review.

After validation, packaging must succeed. This is a hard requirement. In
particular:

- USD references must be relative, resolve inside the asset folder, and must not
  use absolute paths, search paths, or escape the folder.
- Every root USD must have its representative PNG thumbnail at
  `.thumbs/256x256/<root-usd-file>.usd.png` beside it.
- The generated `com.nvidia.simready.packaging.json` must be valid.

An asset that fails packaging must be fixed or excluded from the submission.

## Files published alongside the asset

`simready-manage submit` writes the package definition
`com.nvidia.simready.packaging.json` and a `.metadata/` folder at the asset
root, beside the intermediate folder, and they are published as part of the
package:

- `.metadata/com.nvidia.simready.packaging.bom.json` — the bill of materials:
  every file of the package with its SHA-256 hash.
- `.metadata/com.nvidia.simready.conformance.Package@<version>.json` — the
  result of the packaging profile the package was checked against.
- `.metadata/<logical-asset-name>_validation.json` — per-feature validation
  results.
- `.metadata/<logical-asset-name>_benchmark.json` — per-test benchmark results,
  present only when the benchmark step ran.

The validation sidecar is the record of which profile the asset was validated
against and how it fared; the bill of materials is what NVIDIA's review checks
the uploaded files against.

## Identity and license

- Package names use
  `simready.hf.<namespace>.<dataset>.<logical-asset-name>`.
- The local and Hugging Face folder name must equal the logical asset name and
  must not contain the asset version.
- The package ID is the package name plus the asset version, for example
  `simready.hf.nvidia.fruits.apple.2026.07.10`.
- Each package ID may appear only once in a submission file. A version that has
  already been accepted into the catalog therefore cannot be reused: changing an
  asset means giving it a new version.
- The package license must match the license declared by the Hugging Face
  dataset.
- The Hugging Face dataset must be public and ungated: the submission checks
  read it without credentials.
- The Hugging Face package URL submitted for review must contain an immutable
  40-character commit hash.
