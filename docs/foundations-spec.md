# SimReady submission requirements

This page summarizes the requirements enforced by the
[OEM asset submission guide](oem-asset-submission-guide.md). The guide contains the definitive
commands and examples.

## Asset layout

Each asset must be stored in a folder named for its logical asset name, without
the asset version. Both supported submission profiles require exactly one
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

Before packaging, run `simready-validate` against one of these profiles and use
`--stamp-asset-validation` to record the profile and feature-level results in
the root USD. For more information about profiles, please check
[the profile guide in SimReady Foundation](https://github.com/NVIDIA/simready-foundation/blob/main/nv_core/sr_specs/docs/guides/profiles_validation_workflow.md)

A profile may contain feature-level failures. Failures will also be stamped 
into the root USD for NVIDIA review.

After stamping, `simready-package` must successfully validate that the asset
folder can become a self-contained package. This packaging check is a hard
requirement. In particular:

- USD references must be relative, resolve inside the asset folder, and must not
  use absolute paths, search paths, or escape the folder.
- Every root USD must have its representative PNG thumbnail at
  `.thumbs/256x256/<root-usd-file>.usd.png` beside it.
- The generated `com.nvidia.simready.packaging.json` must be valid.

An asset that fails packaging must be fixed or excluded from the submission.

## Identity and license

- Package names use
  `simready.hf.<namespace>.<dataset>.<logical-asset-name>`.
- The local and Hugging Face folder name must equal the logical asset name and
  must not contain the asset version.
- The package license must match the license declared by the Hugging Face
  dataset.
- The Hugging Face package URL submitted for review must contain an immutable
  40-character commit hash.

No separate submission manifest, `metadata.json`, or security signature is
required.
