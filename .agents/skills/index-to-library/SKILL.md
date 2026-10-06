---
name: index-to-library
description: Pull the packages a SimReady Central submission file lists into a local JSON library and a page to browse them. Use to inspect a submission locally. Do not use for submitting or reviewing.
license: Apache-2.0
metadata:
  author: NVIDIA Corporation <astremplat@nvidia.com>
  version: "2026.8.0"
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Index to Library

## Purpose

Turn a request such as "show me the assets in this submission" into a local
library and page, following the current guide as the source of truth, and
report what the packages carry, including what they lack.

## Prerequisites

Require the environment, checkout and dataset access the current guide names.
Discover what is already available before asking the user for anything missing.

Before touching anything, smoke-test the tools the guide relies on:
`simready-manage -V` and `python -c "from pxr import Sdf"`, both in the
activated environment. If the dataset is private, `hf auth whoami` must show an
account that can read it. A failure here is an environment problem: report it
against the guide's preconditions and stop; it is never an asset problem.

## Inputs

- The submission file, `submissions/<namespace>/<dataset>`.
- A workspace directory that is empty or was filled by this skill earlier.
- Read access to the dataset: none needed for a public one, a login for a
  private one.

## Outputs

All inside the workspace:

- `library.json`, one record per pulled package with the fields the guide's
  *Where each field comes from* lists: source URL, owner, version, license,
  validation and benchmark results among them.
- `library.html`, the page that browses those records.
- The review report the reader writes while collecting the records.
- A report to the user: counts, licenses, validation and benchmark failures,
  and every asset with missing data.

## Examples

```text
Build a local library of the assets in submissions/<namespace>/<dataset>.
```

```text
Pull the packages listed in submissions/acme/props into ~/acme-library and show me a page of them.
```

```text
Which of the assets in this submission file are CC-BY-4.0, and which fail a PhysX feature?
```

## Instructions

Work through these in order.

### Use the authoritative guide

1. Locate the SimReady Central checkout independently of the current working
   directory. Start with the repository containing this `SKILL.md`; otherwise,
   look for a checkout containing `docs/index-to-library-guide.md` in the
   current directory or one of its parents. If no checkout exists, retrieve the
   guide and both scripts, `docs/examples/index_to_library.py` and
   `docs/examples/library_page.py`, from the same repository and revision as
   this skill using authenticated GitHub access. If that fails, ask the user to
   authenticate or provide them; never treat an unrelated Git repository as
   SimReady Central.
2. Read `docs/index-to-library-guide.md` completely before acting and follow its
   current workflow exactly. The guide is the single source of truth for the
   environment, the commands, the record layout and where each field comes
   from. Do not rely on remembered versions of those details.
3. Before running anything, give the user a very brief, plain-language summary
   derived from the guide: that the listed packages will be downloaded into a
   directory of their choosing, roughly how much that is for the number of URLs
   in the file, that nothing is uploaded or modified, and that the result is a
   JSON file and a page on their machine. Do not include commands in this
   overview.

### Resolve the request

1. Resolve the submission file from the request. If the checkout holds exactly
   one file under `submissions/`, offer it as the default; otherwise ask.
2. Resolve the workspace directory. Propose one and confirm it. It must be empty
   or a workspace this skill filled earlier; never a directory holding other
   work, since `pull` writes package folders and a manifest into it.
3. If the workspace already holds `manifest.csv`, offer to reuse it and skip the
   pull, or to start in a fresh directory. Do not delete anything.
4. Check free disk space against the number of URLs in the file, using the size
   the guide gives per asset as the estimate.
5. Ask nothing that the guide or the checkout already answers.

### Execute with explicit boundaries

1. Follow the guide's steps in order with its commands. Pull with `--no-convert`,
   as the guide does; do not repackage, re-validate, re-benchmark or upload
   anything.
2. Treat the pulled packages as read-only. The library and the page are the only
   files this skill produces, plus the review report the reader writes as a side
   effect, all inside the workspace.
3. When a record lists items under `missing`, or the library names assets that
   were not pulled, report them as findings about the assets or the download.
   Do not patch a record, invent a value, or fetch the file another way.
4. Compare the number of records against the number of URLs in the submission
   file and report any difference with the reason the tool logged.
5. Preserve command output for diagnosis without exposing credentials.

### Report

From `library.json`, not from memory:

1. The paths of the library, the page and the review report, and the serve
   command from the guide.
2. How many assets there are, and how many carry each license, with the package
   definition's license as the count and the USD spelling noted when it differs.
3. The validation failures grouped by feature, using the display names in the
   records, and the benchmark tests that failed or were skipped per engine.
4. Every asset with a non-empty `missing` list, and what it lacks.

When the user asks a question about the assets, answer it from the records and
say which fields the answer came from, so they can repeat it on the page or on
the JSON.

## Limitations

- The skill is not made for large sets of assets. Try it only on a submission
  file with a small number of lines; a large one takes hours and a lot of disk.
- Stop after the page is rendered and the report given. Do not submit, review,
  approve or comment on any pull request, publish anything, or change a
  dataset's visibility.
- Do not modify pulled packages or the submission file.
- Do not repair an asset whose data is missing or failing; that is the
  submitter's work, and the guide says how it is reported.
- Do not extend the scripts into a catalog service. For more than a handful of
  assets, point at the guide's *Going further* section.
- Do not add charts to the page. Counts and labels are what it shows.

## Troubleshooting

- If the guide or the scripts cannot be read, ask the user to authenticate or
  provide them.
- A `401` while pulling means a private dataset without a login; stop and ask the
  user to log in with an account that can read it.
- A `ModuleNotFoundError` for `simready` or `pxr` means the scripts ran outside
  the environment; activate it and run again.
- Tiles without pictures after the page was moved: keep `library.html` next to
  `library.json` inside the workspace, or serve the workspace over HTTP.

## Supported versions

- Skill: `version` in the frontmatter, the release series this skill belongs to.
- Tools: the `.venv-submit` environment of the submission guide, at the versions
  its one-time setup installs; the benchmark environment is not needed.
- Scripts: `docs/examples/index_to_library.py` and `docs/examples/library_page.py`
  from the same repository revision as this file. The guide, the scripts and
  this skill change together.
