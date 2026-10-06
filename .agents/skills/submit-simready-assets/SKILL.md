---
name: submit-simready-assets
description: Validate, package, and upload local SimReady assets with simready-manage, then open a SimReady Central PR. Use for OEM submissions. Do not use for NVIDIA review or merging.
license: Apache-2.0
metadata:
  author: NVIDIA Corporation <astremplat@nvidia.com>
  version: "2026.8.0"
---

<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Submit SimReady Assets

## Purpose

Turn a short request into a complete, auditable OEM asset submission while
using the current OEM guide as the source of truth.

## Prerequisites

Require access to the requested asset folder and the accounts, tools, and
destinations specified by the current OEM guide. Discover what is already
available before asking the user for anything missing.

Before touching any asset, smoke-test the tools the guide installs, from
`<SimreadyDir>`'s `.venv-submit`, as the guide's setup ends: `simready-manage -V`,
`simready-validate --help`, and `simready-benchmark --show-config` (the last one
must list the Isaac Sim engines as `[ok]`, with their launcher in
`<SimreadyDir>/.venv-isaac`). A failure here is never an asset problem. Two cases:

- **The tools are not installed**: there is no `<SimreadyDir>` holding the two
  environments, or the commands are not found. Tell the user, ask where
  `<SimreadyDir>` should be, and confirm before installing — the setup creates
  two virtual environments and downloads several GB. Then follow the guide's
  *One-time setup* for the user's platform literally and in its order. Commands
  that need the user's credentials (`hf auth login`) are the user's to run; the
  dataset is created under *Resolve the request* below. Repeat the smoke test
  afterwards.
- **The tools are installed but the smoke test fails** — `--show-config` lists
  no engine as `[ok]`, or a command lacks an option the guide uses: report it
  against the guide's setup section and stop.

## Inputs

Asked for once, in one question set, unless already discoverable (see *Resolve
the request*):

- `<SimreadyDir>`, the directory the guide's one-time setup filled: the
  commands run from its `.venv-submit`.
  When the tools are not installed yet, the directory to set them up in.
- The asset source directory: one folder per asset, each holding a single
  intermediate folder with the interface USD.
- The SimReady profile to validate against.
- The Hugging Face dataset, `<namespace>/<dataset>`, and a token that can write
  to that namespace.
- The asset version for this run: `YYYY.MM.DD`, with a `_NN` suffix only when
  that version is already listed.
- A one-sentence description of what the asset set contains.
- The submitter's organisation and contact, which go into the pull request.
- `GITHUB_TOKEN` with write access to SimReady Central, and a git identity for
  the `Signed-off-by` trailer.

## Outputs

- Each asset folder gains a `.metadata/` folder with the validation and
  benchmark results; `submit` clears and rewrites it on every run. When an asset
  fails validation, its `<asset>_validation.html` report stays there to read.
- One package per asset, built under the workspace's `.packages/`: the package
  definition, the bill of materials and the JSON results, laid out as the
  guide's *What the command produces* shows.
- The uploaded packages, each at an immutable commit of the dataset, with one
  URL per asset in the URL file `submit` writes.
- One pull request against `submissions/<namespace>/<dataset>` carrying the
  complete list, the profile and version, and a completed description.
- A report to the user: submitted, excluded and failed assets with their
  reasons, the upload links and the pull request link.

## Examples

```text
Help me submit the SimReady assets in /projects/factory-assets.
```

```text
Package and upload the assets in ./delivery, then open the SimReady Central PR.
```

## Instructions

Work through these in order.

### Use the authoritative guide

1. Locate the SimReady Central checkout independently of the current working
   directory. Start with the repository containing this `SKILL.md`; otherwise,
   look for a checkout containing `docs/oem-asset-submission-guide.md` in the
   current directory or one of its parents. If no checkout exists, retrieve the
   guide from the same repository and revision as this skill using authenticated
   GitHub access. If that fails, ask the user to authenticate or provide the
   guide; never treat an unrelated Git repository as SimReady Central.
2. Read `docs/oem-asset-submission-guide.md` completely before acting and follow
   its current workflow exactly. The guide is the single source of truth for
   prerequisites, commands, validation rules, package identity, uploads, and
   the review request. Do not rely on remembered versions of those details.
3. Read `docs/acceptance-criteria.md` for the checks the submission must pass.
   Every rejection cites an id from it -- `SUB.018`, `SCAN.004` -- and each rule
   there is written to stand on its own: given an id, work from that rule alone
   rather than re-reading the whole guide. A finding with no id is not a rule
   that was broken; it means the checks could not run, and the answer is to
   re-run them.
4. Before inspecting the user's assets or running workflow commands, give the
   user a very brief, plain-language summary derived from the current guide.
   Describe only the major local and remote stages, disclose that the local
   processing modifies the source assets, and state that open questions and
   required choices will be clarified with the user before proceeding. Do not
   include commands or implementation details in this overview.

### Resolve the request

1. Resolve the asset source directory from the request. If it was not provided,
   ask for it rather than assuming a default folder.
2. Inspect the source, existing configuration, authenticated CLI state, and
   remote destinations without modifying them. Derive safely discoverable facts
   before asking questions.
3. Ask one concise set of questions for the required guide inputs that remain
   unknown. Do not invent values or create a remote destination the user did not
   authorize.
4. In that question set, request a one-sentence description of what the
   submitted asset set contains unless the user already provided one. Do not
   infer its meaning from package, folder, or dataset names.
5. Always ask the user which SimReady profile to validate against; never decide
   it yourself. Never pass `--profile-version`: validation uses the newest
   version in the installed tier packages, and an older version recorded in the
   asset's validation sidecar or in the root USD's `SimReady_Metadata` is not a
   reason to pass one. Point the user at the SimReady Foundation profile
   documentation linked from the guide. One profile applies to the whole run,
   and it goes into the pull request.
6. Every submission needs a Hugging Face dataset. Create a new one with the
   guide's command, which makes it private: uploading to a private dataset is
   fine, and it stays the user's decision when to publish. The dataset must be
   public and ungated by the time the pull request is opened, which is checked
   at the submission step below.
7. When submitting a new asset version, preserve all existing catalog URLs by
   default. Do not ask whether older versions should be removed; discuss
   replacement or removal only when the user explicitly requests it.

### Check before uploading

Run these checks before the first remote write. Each one fails NVIDIA's
submission checks only after the assets are already published, so catching them
early avoids burning immutable Hugging Face commits.

1. **Dataset name.** Neither the Hugging Face namespace nor the dataset name may
   contain a dot. Stop and ask the user to rename the dataset if either does.
2. **Asset folders.** Pass asset folders to `simready-manage submit`. Confirm
   each holds exactly one intermediate folder containing the interface USD, and
   that no folder name contains the asset version.
3. **Existing package IDs.** Read the current
   `submissions/<namespace>/<dataset>` file, if any, and note which versions of
   these assets are already listed. The dataset itself may hold the same package
   ID at any number of commits — the rule is that a submission file declares each
   package ID only once. So a version may be reused freely while iterating on
   Hugging Face, but publishing a changed asset to the catalog needs a version
   that is not already listed.
4. **Version.** Use the date, `YYYY.MM.DD`, adding a `_NN` suffix (`_01`, `_02`,
   ...) only when that version is already listed for one of these assets. One
   version applies to every asset in a run, so submitting assets at different
   versions means one run per version.
5. **No open submission.** A dataset may have only one open pull request at a
   time. Look for an open PR touching `submissions/<namespace>/<dataset>`; if one
   exists, stop before uploading anything and tell the user, naming it. Each
   submission is built from the list the repository holds, so two open PRs would each omit
   the other's additions and the later merge would drop them. `submit` refuses
   to open a second request itself, but only after it has uploaded, so this
   check is what saves the upload.
6. **Write access.** Confirm the active Hugging Face token can write to the
   target dataset's namespace before any local step: query
   `https://huggingface.co/api/whoami-v2` with the token and check that a
   fine-grained token carries `repo.write` for that namespace (an OAuth or
   classic write token has it implicitly). Reading the dataset proves nothing —
   a read-only token gets as far as the first upload and fails there with 403,
   after validation and packaging have run.
7. **File types.** Sweep every asset folder for files whose extension is not on
   the SCAN.004 list in `docs/acceptance-criteria.md`; `.wrapp` and `.zip` are
   the usual offenders. Stop and tell the user which files, since `submit`
   uploads whatever is present and the check only fails after publication.

### Execute with explicit boundaries

1. Treat the asset source as read-only until the user explicitly confirms
   source modifications. Before the first source write, identify the affected
   asset folders and explain what the applicable guide commands will modify.
2. Follow the guide in order. Do not silently skip failed steps, weaken its
   acceptance rules, or substitute different commands.
3. Never modify unrelated files or asset folders. Preserve detailed command
   output for diagnosis without exposing credentials.
4. Before remote writes, show the resolved destinations and affected assets.
   Obtain confirmation when a destination was inferred or existing remote data
   will be replaced.
5. Start `submit` so that it outlives the shell tool call. With the benchmark it
   runs for about fifteen minutes per asset and prints nothing until it ends,
   longer than such a call is usually allowed to run. Start it as a detached
   process that writes its output to a log and its exit status to a file, and
   wait for that file; or hand the command to the user to run in their own
   terminal, and continue when they say it has ended. Never stop a running
   `submit` by process name: the upload of an asset in progress would be left
   half done.
6. A failed asset does not abort a `simready-manage submit` run. After it
   completes, compare the URL file against the assets the user asked to submit,
   and report every asset that was skipped together with the reason.

### Assemble the definitive list and open the pull request

`simready-manage submit` does this itself when given `--central-index-repo` and
`--open-pr`: it fetches the current `submissions/<namespace>/<dataset>` list,
adds the new package URLs, and opens a pull request with a `Signed-off-by`
trailer taken from the user's git identity. Do not hand-edit the list or open the
request yourself.

1. **Supply the inputs it needs.** `GITHUB_TOKEN` must be set in the environment.
   Pass `--oem-org` and `--oem-contact` so the request carries the submitter's
   identity, asking the user for both if they are not already known. Confirm the
   git identity resolves, since the sign-off falls back to it.
2. **Expect the first run to stop before the pull request.** The dataset is
   private until the user publishes it, and a request cannot reference a private
   or gated dataset. `submit` then ends with `Error: PR was not opened: <reason>`
   and exit status 1; the uploads are kept. An open pull request for the dataset
   ends the run earlier, with `Error during submission: central index: an open
   PR ... already exists` and without the summary block, also exit 1, uploads
   kept. Neither is a tool failure — read the reason and act on it: a private or gated dataset means
   show the user what was uploaded, ask them to make the dataset public and
   ungated, and re-run the same command (do not change visibility yourself); an
   open pull request for the dataset means stop and name it; a missing git
   identity or `GITHUB_TOKEN` means fix that and re-run.
3. **Complete the pull request description.** `submit` generates it from the
   repository's template; the one part it cannot write is the "Notes for
   reviewers" placeholder. Show the user a replacement — the one-sentence
   description of the asset set, every `--skip-*` / `--ignore-*` flag passed and
   why, known failures and whether they are asset findings or known tool issues —
   and, once confirmed, put it in place as the guide's *Submitting a SimReady
   package to the SimReady Catalog* shows, changing nothing else in the
   generated body.
4. **Report what the run produced** — successful and excluded assets, any
   validation or packaging failures, the immutable upload links, and the pull
   request link, which is the hand-off to NVIDIA (the URL file is a local
   fallback only). When a benchmark sidecar (`.metadata/<asset>_benchmark.json`)
   contains failed tests, list them per asset, test and engine with the
   validator's message. Do not merge the request.
5. **If NVIDIA rejects a submission** the pull request is closed, not amended.
   Correct the assets and submit again, which opens a fresh request. The asset
   version can be reused, since nothing from a closed request reached the
   catalog.

Removal is not covered by `submit`: it is a hand-edit of the list plus a pull
request, as the guide describes. Treat it as a separate task and follow the
guide's removal section.

### Handle validation failures

1. When profile validation fails, preserve its structured report and the
   validator's evidence. Record which tier packages were installed and at which
   versions (`pip list | grep simready-foundation-tier`),
   since the profile, feature, and requirement definitions come from them. Do not
   upgrade or change those packages while diagnosing the result.
2. Resolve the reported profile and version, then the feature versions and
   requirement IDs behind it, from the installed tier packages: their
   `profiles/`, `features/` and `capabilities/` directories carry the machine-readable
   definitions and the requirement documentation. Use those to distinguish
   required features from optional ones.
3. Give the user one short, human-readable summary covering **all** remaining
   failed required features. Group affected assets by feature and requirement.
   For each group, use the matching feature and requirement documentation to
   state what capability failed, what the requirement means, which assets are
   affected, and the validator's specific reason.
4. After the complete summary, ask one concise question: whether to exclude the
   affected assets and continue with those that passed, or look more closely at
   the failures first.
5. Do not reach for `--skip-validate` or `--ignore-validation-results` to work
   around a failure. They disable checking for the whole asset rather than for
   the failing requirement, and `--ignore-validation-results` also disables
   packaging's own pre-validation. Use them only when the user explicitly asks,
   and say so in the pull request description. Failed benchmark tests need no
   flag: the asset is uploaded with its results and the pull request says so.
   `--ignore-benchmark-results` only lets through an asset whose benchmark could
   not run at all, without results, which is not accepted, so treat it the same
   way; `--skip-benchmark` always needs the user's explicit request, and a
   submission made with it is not accepted: it is for trying the pipeline.
6. If the user chooses exclusion, omit a new asset from packaging, upload, and
   the pull request. For a failed update, skip the new version but preserve every
   previously submitted immutable URL by default; exclusion must not
   unintentionally remove an existing catalog version. Report all excluded
   assets and stop if none remain eligible for submission.
7. The installed tier packages carry the specifications but no repair workflows.
   When a failure needs more than the requirement documentation explains, point
   the user at SimReady Foundation rather than improvising a fix: say what the
   requirement demands, what the asset does instead, and leave the repair to
   them. Investigation stays read-only unless the user explicitly asks for the
   asset to be changed.

## Limitations

- Stop after opening or updating the OEM submission PR; do not perform NVIDIA's
  internal review, approve the PR, merge it, or publish packages to the central
  repository.
- Do not modify source assets without the user's explicit confirmation.
- Do not change the visibility of a Hugging Face dataset on the user's behalf.

## Troubleshooting

- If the guide cannot be read, ask the user to authenticate or provide it.
- If validation, packaging, upload, or PR creation fails, stop at that stage,
  preserve the diagnostic output, and continue only after the cause is fixed or
  the affected assets are explicitly excluded where the guide permits it.
- An upload that fails with "returned no commit hash" means the content is
  byte-identical to what is already in the dataset. Bump `--asset-version` or
  confirm with the user that the asset really changed.
- Every physics test on the `isaac_sim_newton` engine skipped with
  "Newton could not initialize this asset's physics scene" is a finding about
  the asset, not a setup problem, and `submit` does not stop on skipped tests:
  say so in the pull request's notes. A benchmark that could not start Isaac
  Sim at all stops the asset: check the engines with
  `simready-benchmark --show-config`, as the guide's benchmark section says.

## Supported versions

- Skill: `version` in the frontmatter, the release series this skill belongs to.
- Tools: the versions the guide's one-time setup installs. The guide and this
  skill are released together from one repository revision, so the guide in the
  same checkout is the matching one; a guide fetched separately must come from
  the same revision as this file.
- The skill drives `simready-manage submit` with `--central-index-repo` and
  `--open-pr` and reads the sidecars that command writes. A tool without those
  options is an environment problem: report it against the guide's setup section
  and stop, as for a smoke test that fails with the tools installed.
